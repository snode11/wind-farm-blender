"""
FAST.Farm 动态后端的统一驱动器（训练 & 可视化共用）。

把 WFCRL 的 PettingZoo **AEC** 接口封装成"一次 step = 推进一个控制步 dt（走完一圈
n 个 agent）"的简洁 API，并在一轮结束后回读全场真实测量：

    driver = FastFarmDriver("Dec_Turb3_Row1_Fastfarm", max_steps=120)
    m = driver.reset()                      # dict: yaw/power/load/wind_speed/...
    m = driver.step(yaw_delta)              # yaw_delta: (n,) 每台每步偏航增量(°)
    ...
    driver.close()                          # 关键：排空 FAST.Farm 的迭代预算，避免孤儿进程

设计要点（见 memory: wfcrl-fastfarm-bringup）：
  - 仿真只在一轮里最后一个 agent 动作后推进（multiagent_env: _agent_selector.is_last()
    → mdp.take_action），之后 infos[agent]["power"/"load"]、observations、rewards 才就绪。
  - 因此一次 driver.step() 内部对 n 个 agent 各调一次 env.step()，凑满一圈推进 dt，
    再统一回读。
  - FAST.Farm 的 SC_DLL 在 t=0 收到固定迭代预算（≈ max_iter），必须把回合走完（或
    close() 排空），否则子进程会卡在 MPI_RECV 变孤儿。
  - 必须在 `mpiexec -n 1 python ...` 下运行（MPI_Comm_spawn 需要进程管理器）。
"""
import collections
import os

import numpy as np

from wfcrl import environments as envs
from wfcrl.rewards import StepPercentage

from wfrl import paths

# wfcrl 用 CWD 相对路径解析可执行文件与工作目录（interface.py:342 的
# "simulators/fastfarm/bin/FAST.Farm_x64_OMP_2023.exe"、420/555 行的
# "__simul__/{fastfarm,floris}/"），两者都在仓库根。切过去，否则换个 cwd 起进程就找不到。
os.chdir(paths.ROOT)


# NREL 5MW 传动链常数，用来把「功率 + 发电机转矩」反解成转速（见 _read）。
# 出处：inputs/template/FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat:100
#       inputs/template/FarmInputs/NRELOffshrBsline5MW_Onshore_ServoDyn_WT1.dat:21
GB_RATIO = 97.0        # 齿轮箱速比 (-)
GEN_EFF = 0.944        # 发电机效率 (-)：avrSWAP(15) 是**电功率**，反解转速要先除掉
RPM_MAX = 20.0         # 转子转速显示上限 (rpm)；NREL 5MW 额定 12.1，超出即非物理

# NREL 5MW 轮毂高度 (m)：ElastoDyn 的 TowerHt=87.6 加上轴倾后的悬伸抬升，取 90。
# 用途是从 .bts 的竖向剖面上插出**自由来流**（剪切很强，取错一层差 0.35 m/s）。
HUB_HEIGHT = 90.0

# `mdp.py:283` 把 load 六元组除了 1e7 才放进 infos（和功率除 1e6 一个套路），
# 要还原成 N·m 得乘回来。六元组的物理含义见 DISCON.F90:363-368：
#   to_SC(7:9)   = avrSWAP(30,31,32) → RootMyc(1..3)  三叶片根部**挥舞**弯矩
#   to_SC(10:12) = avrSWAP(69,70,71) → RootMxc(1..3)  三叶片根部**摆振**弯矩
# 记录号语义按 Bladed avrSWAP 标准；由 scripts/experiments/calib_blade_flex.py
# 拿 OutList 的 RootMyc1/RootMxc1 实测核对。
LOAD_SCALE = 1e7

# --- 偏航执行机构占空比：滑动窗口版（贴合真实散热恢复）-----------------------
# WFCRL 原版占空比是"回合累计、永不衰减"：早期偏得多，配额被历史永久占用，
# 剩下整个回合的偏航全被清零 —— 偏航到不了 wake steering 需要的 20~30°。这不符合
# 真实风机（偏航电机偏完散热恢复后可再偏）。这里改成滑动窗口：只看最近 W 步的
# 动作占比，偏完停几步配额就还回来。同时把上限从 10% 放宽到 30%（wake steering
# 风场对风动作更频繁，30% 可辩护）⇒ 可持续量 0.3·rate·dt = 0.27°/步，170 步可
# 偏到 ~45°，真正解锁 30°。偏航速率 0.3°/s 不动（贴合 NREL 5MW，Jonkman 2009）。
YAW_DUTY_WINDOW = 20        # 滑动窗口步数（dt=3 s ⇒ 60 s，贴合散热时间尺度）
YAW_DUTY_LIMIT = 0.30       # 占空比上限（放宽自 WFCRL 原版的 0.10）
YAW_RATE = 0.3             # 偏航电机速率 (°/s)，与 mdp.ACTUATORS_RATE["yaw"] 同源

# 一次训练里删不掉的算例目录（Windows 句柄没释放）。下一次 purge 时一并重试。
_PENDING_PURGE = []


def check_mpi_launcher():
    """确认由 MPI 进程管理器启动，Windows 下额外校验 MS-MPI。

    两种错法都会表现为「无声挂死」，而不是抛异常，非常难查：
      1) 直接 `python -m ...` 不经 mpiexec —— MPI_Comm_spawn 没有进程管理器；
      2) conda env 自带一份预发布版 mpiexec/smpd（Library/bin），激活 env 后它
         排在 PATH 前面被优先选中，而配套的 msmpi.dll 已被我们改名让位给
         System32 的官方版（见 memory: wfcrl-fastfarm-bringup）⇒ spawn 卡死。
         已把那三个 exe 一并改名成 .condabak，这里是防它们被恢复的兜底。
    子进程继承父 shell 的 PATH，所以 MSMPI_BIN 就能反推出用的是哪一份。
    """
    # Open MPI exports OMPI_COMM_WORLD_*, while MPICH/MS-MPI exports PMI_*.
    launcher_markers = ("OMPI_COMM_WORLD_SIZE", "PMI_SIZE", "MPI_LOCALNRANKS")
    if not any(marker in os.environ for marker in launcher_markers):
        raise RuntimeError(
            "FAST.Farm 需要 MPI_Comm_spawn，必须经 mpiexec 启动，直接 python 会挂死。\n"
            "  正确命令：mpiexec -n 1 python -m <你的模块> ...")

    # The MS-MPI DLL/path check is meaningful only on Windows. macOS/Linux
    # should use the platform's native MPI launcher and shared libraries.
    if os.name != "nt":
        return

    official = r"C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"
    bin_dir = os.environ.get("MSMPI_BIN", "")
    if bin_dir and "program files" not in bin_dir.lower():
        if not os.environ.get("SKIP_MPI_CHECK"):
            raise RuntimeError(
                f"用的是非官方 mpiexec（MSMPI_BIN={bin_dir}），它的 msmpi.dll 已让位给\n"
                "System32 的官方版，spawn 会无声卡死。\n"
                f'  正确命令：& "{official}" -n 1 python -m <你的模块> ...')


class FastFarmDriver:
    def __init__(self, env_id="Dec_Turb3_Row1_Fastfarm", max_steps=120,
                 controls=("yaw",), load_coef=1.0, reward_shaper=None,
                 wake_vtk=False, wake_vtk_z=90.0, wind_time_series=None,
                 yaw_duty_sliding=True, yaw_duty_window=YAW_DUTY_WINDOW,
                 yaw_duty_limit=YAW_DUTY_LIMIT):
        # 只有 FAST.Farm 后端需要 MPI_Comm_spawn；FLORIS 是纯 Python 稳态解，
        # 不经 MPI，直接 python 就能跑（D5 的奖励/观测设计正靠它秒级迭代）。
        if "floris" not in env_id.lower():
            check_mpi_launcher()  # launcher 不对就当场报错，别拖到 spawn 里挂死
        self.env_id = env_id
        self.max_steps = int(max_steps)
        self.controls = list(controls)
        kw = {}
        if wind_time_series is not None:
            # registration.py:95-97 把它塞进 farm_case ⇒ simul_utils.py:174 切
            # WindType=3 并按 .bts 反算低/高分辨率盒子范围。文件名相对模板的
            # FarmInputs/（模板自带 90m_08mps.bts）。
            kw["wind_time_series"] = wind_time_series
        self.env = envs.make(
            env_id, controls=self.controls, max_num_steps=self.max_steps,
            reward_shaper=reward_shaper or StepPercentage(), load_coef=load_coef,
            **kw,
        )
        self.wind_time_series = wind_time_series
        # 真实扰动风切面：只能改**已生成**的案例文件，且必须在 reset(spawn exe) 前。
        self.case_dir = self.wake_vtk = None
        if wake_vtk:
            from wfrl.viz import wakevtk
            itf = self.env.mdp.interface
            z, wdt = wakevtk.enable_disturbed_wind(itf._simul_file, z=wake_vtk_z)
            self.case_dir = wakevtk.case_dir_of(itf)
            self.wake_vtk = {"z": z, "dt": wdt}
            print(f"[fastfarm-driver] DisXY 切面已开：z={z} m, 每 {wdt} s 一帧 → "
                  f"{self.case_dir}", flush=True)
        # PettingZoo 的 wrapper 禁止透传下划线属性（占空比要读 _num_steps）
        self._uenv = getattr(self.env, "unwrapped", self.env)
        self.agents = list(self.env.possible_agents)
        self.n = len(self.agents)
        self._idx = {ag: i for i, ag in enumerate(self.agents)}
        if self.case_dir is None:
            # 每次 envs.make() 新建一个 __simul__/fastfarm/<case> 目录（~37 MB）。
            # 按回合重启训练时这会线性增长，close(purge=True) 要用它来清理。
            from wfrl.viz import wakevtk
            try:
                self.case_dir = wakevtk.case_dir_of(self.env.mdp.interface)
            except Exception:                            # noqa: BLE001
                self.case_dir = None

        # 布局坐标（来自 farm_case，画机组 / 建 FLORIS-proxy 用）
        fc = self.env.mdp.farm_case
        self.xcoords = np.asarray(fc.xcoords, dtype=float)
        self.ycoords = np.asarray(fc.ycoords, dtype=float)
        self.dt = float(getattr(fc, "dt", 3))
        self.hub_height = HUB_HEIGHT

        # 湍流盒子：报一遍实际来流与 TI，并检查回合会不会跑出盒子的时间跨度。
        self.bts = None
        if wind_time_series is not None:
            try:
                from wfrl.inflow import bts_info
                self.bts = bts_info(wind_time_series, z_hub=self.hub_height)
            except Exception as e:                           # noqa: BLE001
                print(f"[fastfarm-driver] .bts 读不了（仿真仍会跑）: {e!r}",
                      flush=True)
            if self.bts is not None:
                need = self.max_steps * self.dt
                print(f"[fastfarm-driver] 湍流来流 {wind_time_series}: "
                      f"u_hub={self.bts.u_hub:.2f} m/s, TI={self.bts.ti:.1f}%, "
                      f"盒子 {self.bts.t_span:.0f}s", flush=True)
                if need > self.bts.t_span:
                    print(f"[fastfarm-driver] 警告：回合需 {need:.0f}s 风场 > 盒子 "
                          f"{self.bts.t_span:.0f}s，超出部分由 InflowWind 处理"
                          f"（周期回绕或报错），长训练需重新生成 .bts", flush=True)

        # 每个控制量各自的连续动作边界（每步增量）。默认值见 data_cases.py:20-23：
        # yaw ±5°/步、pitch ±1°/步、torque ±1e3 N·m/步。
        aspace = self.env.action_space(self.agents[0])
        self.act_bounds = {
            c: (float(np.ravel(aspace[c].low)[0]),
                float(np.ravel(aspace[c].high)[0]))
            for c in self.controls
        }
        # 向后兼容：老调用方（rviz_app / train_fastfarm）拿的是 yaw 的标量边界
        self.act_low, self.act_high = self.act_bounds.get(
            "yaw", (-5.0, 5.0))

        self.obs_keys = list(self.env.observation_space(self.agents[0]).keys())
        self.done = False
        self._steps = 0
        self._started = False

        # 偏航占空比：滑动窗口接管。把 yaw 从 mdp.ACTUATORS_RATE 移除 ⇒ env 的
        # `if not (control in ACTUATORS_RATE): continue`（multiagent_env.py:200）
        # 让它跳过对 yaw 的"累计永不衰减"清零；改由本 driver 用滑动窗口判定。
        # pitch 仍留在 env 的占空比里，不动。
        self.yaw_duty_sliding = bool(yaw_duty_sliding)
        self.yaw_duty_window = int(yaw_duty_window)
        self.yaw_duty_limit = float(yaw_duty_limit)
        self._yaw_win = collections.deque(maxlen=self.yaw_duty_window)
        if self.yaw_duty_sliding and "yaw" in self.controls:
            rate = dict(getattr(self._uenv.mdp, "ACTUATORS_RATE", {}))
            rate.pop("yaw", None)
            # 实例属性覆盖类属性，只影响这个 env，不动上游默认
            self._uenv.mdp.ACTUATORS_RATE = rate

    # ------------------------------------------------------------------
    def reset(self, wind_speed=None, wind_direction=None):
        """wind_speed/wind_direction 给定则制死来流，否则走 benchmark 的随机采样。

        默认行为(None)下 `mdp.reset` 每次都会重抽 `8*rng.weibull(8)`,而功率 ~ u^3
        —— 一次训练只 reset 一次,等于一次抽签决定整段的功率量级。做后端对照时
        必须显式钉住,否则比的是两次抽签而不是两种物理。
        FAST.Farm 侧风速会被写进 InflowWind 的 HWindSpeed;风向它无法设定(忽略)。
        """
        # 湍流模式下来流由 .bts 决定：interface.py:608-619 会 warn 后把请求丢掉。
        # 这里提前拦下并说明，否则调用方以为自己钉住了风速，其实钉的是别的东西。
        if self.wind_time_series is not None and (
                wind_speed is not None or wind_direction is not None):
            print("[fastfarm-driver] 湍流模式（WindType=3）下来流由 .bts 决定，"
                  "wind_speed/wind_direction 请求被忽略", flush=True)
            wind_speed = wind_direction = None
        options = {}
        if wind_speed is not None:
            options["wind_speed"] = float(wind_speed)
        if wind_direction is not None:
            options["wind_direction"] = float(wind_direction)
        self.env.reset(options=options or None)
        self.done = False
        self._steps = 0
        self._started = True
        self._yaw_win.clear()          # 新回合：偏航占空比窗口清空
        self.u_inf = self._freestream(fallback=wind_speed)
        return self._read(reward=0.0)

    def _freestream(self, fallback=None):
        """本回合实际生效的**自由来流**风速(m/s)。

        归一化功率 P/u^3 的分母必须用它,不能用 obs["wind_speed"] —— 后者是
        机组转子处的测量值,下游机组测到的是尾流内速度,拿它做分母会把尾流亏损
        同时算进分子和分母,把差异抹平。
        """
        itf = self.env.mdp.interface
        fi = getattr(itf, "fi", None)                       # FLORIS
        if fi is not None:
            try:
                return float(np.ravel(fi.floris.flow_field.wind_speeds)[0])
            except Exception:                               # noqa: BLE001
                pass
        # WindType=3（湍流盒子）下 HWindSpeed **从没被写过**，读它拿到的是模板旧值。
        # 自由来流只能从 .bts 的轮毂高度层算（见 wfrl/inflow.py）。
        if self.wind_time_series is not None:
            return self.bts.u_hub if self.bts is not None else float("nan")
        inflow = getattr(itf, "_inflow_file", None)         # FAST.Farm: HWindSpeed
        if inflow is not None:
            try:
                from wfcrl.simul_utils import read_inflow_info
                return float(read_inflow_info(inflow))
            except Exception:                               # noqa: BLE001
                pass
        return float(fallback) if fallback is not None else float("nan")

    def _raw_measure(self, name):
        """直接问 interface 要原始测量（绕开 MDP 的状态字典）。

        被控量在 MDP 里存的是**累加的指令值**（`get_controlled_state_transition`），
        不是仿真回读值；算转速要的是真实测量，所以走这条路。测不到返回 None。
        """
        itf = self.env.mdp.interface
        if name not in getattr(itf, "measure_map", {}):
            return None
        try:
            v = np.ravel(itf.get_measure(name)).astype(float)
        except Exception:                                # noqa: BLE001
            return None
        return v if v.size == self.n else None

    def _rotor_speed(self, power_mw):
        """转子转速 (rpm)，由电功率与发电机转矩反解：ω_gen = P /(η·T)。

        FAST.Farm 经 MPI 传回来的只有 12 个通道（DISCON.F90:356-368），里面**没有**
        转速；但 to_SC(2)=avrSWAP(15) 是电功率、to_SC(6)=avrSWAP(23) 是发电机转矩，
        两者相除就得高速轴角速度，再除齿轮箱速比即转子转速。这比可视化里原来
        「每帧固定 +18°」的假自转真实：转速会随风速与转矩指令变。

        转矩接近 0（启动瞬态 / 空转）时商会发散，这里裁到 RPM_MAX 并置 nan 保护。
        """
        tq = self._raw_measure("torque")
        if tq is None:
            return np.full(self.n, np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            omega_gen = (power_mw * 1e6) / (GEN_EFF * tq)      # rad/s，高速轴
            rpm = omega_gen * 60.0 / (2.0 * np.pi) / GB_RATIO
        rpm[~np.isfinite(rpm)] = np.nan
        rpm[np.abs(tq) < 1.0] = np.nan                          # 转矩≈0，商无意义
        return np.clip(rpm, 0.0, RPM_MAX)

    def _duty(self):
        """各机组各控制量的**执行机构占空比**，≥上限时动作被清零。

        yaw 走本 driver 的**滑动窗口**（见 YAW_DUTY_* 常量）：只看最近 W 步的动作
        占比，偏完停几步配额恢复；上限 0.30。其余控制量（pitch）仍走 env 的累计版
        （`multiagent_env.py:196-207`，acc/rate/(k·dt)，上限 0.10）。
        yaw 关掉滑动窗口时回退到 env 累计版，与旧行为一致。
        """
        rate = self._uenv.mdp.ACTUATORS_RATE
        dt = float(self._uenv.farm_case.dt)
        out = {}
        # yaw 的滑动窗口占空比（当前窗口，预测"下一步会不会被清零"）
        yaw_frac = None
        if self.yaw_duty_sliding and "yaw" in self.controls:
            w = len(self._yaw_win)
            if w > 0:
                yaw_frac = np.sum(self._yaw_win, axis=0) / YAW_RATE / (w * dt)
            else:
                yaw_frac = np.zeros(self.n)
        for j, ag in enumerate(self.agents):
            acc = getattr(self._uenv, "accumulated_actions", {}).get(ag, {})
            k = self._uenv._num_steps.get(ag, 0) + 1
            d = {c: float(acc[c]) / rate[c] / k / dt
                 for c in acc if c in rate}
            if yaw_frac is not None:
                d["yaw"] = float(yaw_frac[j])
            out[ag] = d
        return out

    def _read(self, reward):
        """一轮结束后回读全场真实测量。"""
        n = self.n
        yaw = np.zeros(n); power = np.zeros(n); load = np.zeros(n)
        ws = np.zeros(n); wd = np.zeros(n); pitch = np.zeros(n)
        mflap = np.full((n, 3), np.nan); medge = np.full((n, 3), np.nan)
        for ag in self.agents:
            i = self._idx[ag]
            obs = self.env.observe(ag)
            info = self.env.infos.get(ag, {})
            yaw[i] = float(np.ravel(obs.get("yaw", [0.0]))[0])
            ws[i] = float(np.ravel(obs.get("wind_speed", [np.nan]))[0])
            wd[i] = float(np.ravel(obs.get("wind_direction", [np.nan]))[0])
            pitch[i] = float(np.ravel(obs.get("pitch", [np.nan]))[0])
            power[i] = float(np.ravel(info.get("power", [0.0]))[0]) \
                if "power" in info else 0.0
            # load 是**六元组**（见下方 root_moments），reward 用的是它的 mean|·|，
            # 这里保留标量以兼容既有调用方，逐叶片值另开两个键。
            if "load" in info:
                lv = np.ravel(info["load"]).astype(float)
                load[i] = float(np.abs(lv).mean())
                if lv.size == 6:
                    mflap[i] = lv[:3] * LOAD_SCALE
                    medge[i] = lv[3:] * LOAD_SCALE
        # pitch 单位：作为**测量**时来自 avrSWAP(4)，是弧度；作为**被控量**时 MDP
        # 自己按度累加（DEFAULT_BOUNDS pitch=[0,360]）。统一成度再往外给。
        if "pitch" not in self.controls:
            pitch = np.degrees(pitch)
        # 被控量在 obs 里存的是**累加的指令**，不是仿真回读：mdp.py:100-104 把
        # controls 里的键排除出 measures，step_interface 就不会用测量覆盖它。
        # 后果是 --controls yaw,pitch 时 obs["pitch"] 与风速无关（5 和 14 m/s
        # 逐步打印一模一样）。真实桨距要绕开 MDP 直接问 interface 要第 4 通道。
        pm = self._raw_measure("pitch")
        pitch_meas = np.degrees(pm) if pm is not None else np.full(n, np.nan)
        tq = self._raw_measure("torque")
        torque = tq if tq is not None else np.full(n, np.nan)
        duty = self._duty()
        # yaw 已从 env 的 ACTUATORS_RATE 移除（滑动窗口接管），但它的占空比仍要
        # 上报给 obs-duty/D5 ⇒ 显式把 yaw 并进来。
        duty_ctrls = [c for c in self.controls
                      if c in self.env.mdp.ACTUATORS_RATE]
        if self.yaw_duty_sliding and "yaw" in self.controls \
                and "yaw" not in duty_ctrls:
            duty_ctrls.append("yaw")
        duty_arr = {c: np.array([duty[ag].get(c, 0.0) for ag in self.agents])
                    for c in duty_ctrls}
        return {
            "yaw": yaw, "power": power, "load": load,
            "m_flap": mflap, "m_edge": medge,
            "wind_speed": ws, "wind_direction": wd, "pitch": pitch,
            "pitch_meas": pitch_meas,
            "torque": torque, "rotor_speed": self._rotor_speed(power),
            "duty": duty_arr,
            "reward": float(reward), "done": self.done, "step": self._steps,
        }

    # ------------------------------------------------------------------
    def _clip(self, name, delta):
        """把某个控制量的增量补齐成 (n,) 并裁到它自己的动作边界。"""
        lo, hi = self.act_bounds[name]
        if delta is None:
            return np.zeros(self.n, dtype=np.float32)
        d = np.asarray(delta, dtype=np.float32).ravel()
        if d.size == 1:
            d = np.repeat(d, self.n)
        return np.clip(d, lo, hi)

    def step(self, yaw_delta=None, pitch_delta=None, torque_delta=None):
        """推进一个控制步，各增量按各自动作边界裁剪；未激活的控制量忽略。

        yaw_delta   (n,) 偏航增量 (°)
        pitch_delta (n,) 变桨增量 (°)     —— 需 controls 里含 "pitch"
        torque_delta(n,) 发电机转矩增量 (N·m) —— 需 controls 里含 "torque"

        FLORIS 后端只实现了 yaw（interface.py:576-581），给 pitch/torque 会在
        构造 env 时就报错，不会静默吞掉。
        """
        if self.done:
            return self._read(reward=0.0)
        deltas = {"yaw": yaw_delta, "pitch": pitch_delta,
                  "torque": torque_delta}
        cmd = {c: self._clip(c, deltas[c]) for c in self.controls}
        # 偏航滑动窗口占空比：只看最近 W 步的动作占比，超上限的机组本步 yaw 清零。
        # 与 WFCRL 的"累计永不衰减"不同 —— 窗口滚动 ⇒ 偏完停几步配额自动恢复。
        if self.yaw_duty_sliding and "yaw" in cmd:
            dt = float(self._uenv.farm_case.dt)
            w = len(self._yaw_win)
            if w > 0:
                win_sum = np.sum(self._yaw_win, axis=0)          # (n,) 各机组窗口内 |Δyaw|
                frac = win_sum / YAW_RATE / (w * dt)             # 窗口占空比
                over = frac >= self.yaw_duty_limit
                cmd["yaw"] = np.where(over, 0.0, cmd["yaw"])
            self._yaw_win.append(np.abs(cmd["yaw"]).astype(float))  # 记实际执行量
        # 走完一圈 n 个 agent = 推进一个 dt
        for _ in range(self.n):
            ag = self.env.agent_selection
            _, _, term, trunc, _ = self.env.last()
            if term or trunc:
                self.done = True
                self.env.step(None)
                continue
            i = self._idx[ag]
            self.env.step({c: np.array([v[i]], dtype=np.float32)
                           for c, v in cmd.items()})
        self._steps += 1
        # 截断轮里 AEC 会清空 rewards 字典 → 用 .get 防御
        rw = self.env.rewards.get(self.agents[0], 0.0)
        reward = float(np.ravel(rw)[0]) if np.size(rw) else 0.0
        # 回合预算耗尽 → 截断
        if self._steps >= self.max_steps:
            self.done = True
        return self._read(reward=reward)

    # ------------------------------------------------------------------
    def close(self, drain_cap=None, purge=False):
        """排空 FAST.Farm 剩余迭代预算，让子进程干净退出（避免 MPI_RECV 孤儿）。

        早退时调用；用零增量把回合走完（有上限，防止极长回合把关闭卡死）。
        `purge=True` 额外删掉本次 `envs.make()` 建的算例目录 —— 按回合重启训练时
        每回合一个 ~37 MB 的目录，不清会把 `__simul__/` 撑爆（README §8 第 5 条）。
        """
        if not self._started or self.done:
            self._safe_close()
            if purge:
                self._purge_case()
            return
        cap = drain_cap if drain_cap is not None else self.max_steps
        drained = 0
        try:
            while not self.done and drained < cap:
                for _ in range(self.n):
                    _, _, term, trunc, _ = self.env.last()
                    if term or trunc:
                        self.done = True
                        self.env.step(None)
                        continue
                    self.env.step({c: np.array([0.0], dtype=np.float32)
                                   for c in self.controls})
                drained += 1
                if self._steps + drained >= self.max_steps:
                    self.done = True
        except Exception as e:                       # noqa: BLE001
            print(f"[fastfarm-driver] drain 出错（忽略）: {e}", flush=True)
        self._safe_close()
        if purge:
            self._purge_case()

    def _purge_case(self):
        """删掉本次算例目录，连同之前删不掉的那些一起重试。

        FAST.Farm 退出与 Windows 释放 `5MW_Baseline/ServoData/DISCON_WT*.dll`
        的句柄之间有几秒延迟，close() 之后立刻删会吃到 WinError 5。所以这里
        重试几次，仍失败就挂到待删队列，下一回合（或下一次训练）再收。
        为了清磁盘把训练搞崩不值 —— 任何一步失败都只打印，不抛。
        """
        import shutil
        import time
        if self.case_dir:
            _PENDING_PURGE.append(self.case_dir)
        left = []
        for d in _PENDING_PURGE:
            if not d or not os.path.isdir(d):
                continue
            # 只允许删 __simul__ 底下的东西，防止 case_dir 反推错了把别的目录端掉
            if "__simul__" not in os.path.normpath(d).replace("\\", "/"):
                print(f"[fastfarm-driver] 算例目录不在 __simul__ 下，不删：{d}",
                      flush=True)
                continue
            for attempt in range(3):
                try:
                    shutil.rmtree(d)
                    break
                except Exception as e:               # noqa: BLE001
                    if attempt == 2:
                        left.append(d)
                        print(f"[fastfarm-driver] 算例目录暂时删不掉，排队重试："
                              f"{os.path.basename(d)}（{e.__class__.__name__}）",
                              flush=True)
                    else:
                        time.sleep(1.5)
        _PENDING_PURGE[:] = left

    def _safe_close(self):
        try:
            self.env.close()
        except Exception:                            # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 独立冒烟：mpiexec -n 1 python fastfarm_driver.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import time
    t0 = time.time()
    drv = FastFarmDriver("Dec_Turb3_Row1_Fastfarm", max_steps=5)
    m = drv.reset()
    print(f"reset OK in {time.time()-t0:.1f}s  n={drv.n}  "
          f"layout_x={drv.xcoords.tolist()}", flush=True)
    for k in range(6):
        m = drv.step(np.ones(drv.n))          # 每台 +1°/步
        print(f"  step {m['step']}  yaw={np.round(m['yaw'],1).tolist()}  "
              f"power(MW)={np.round(m['power'],2).tolist()}  "
              f"ws={np.round(m['wind_speed'],2).tolist()}  "
              f"reward={m['reward']:.3f}  done={m['done']}  "
              f"t={time.time()-t0:.1f}s", flush=True)
        if m["done"]:
            break
    drv.close()
    print("DRIVER_OK", flush=True)
