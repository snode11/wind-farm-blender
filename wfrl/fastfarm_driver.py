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
import copy
import logging
import os
from numbers import Real

import numpy as np

from wfrl.mpi_runtime import configure_local_mpi

configure_local_mpi()

from wfcrl import environments as envs
from wfcrl.rewards import StepPercentage

from wfrl import paths
from wfrl.safety import DUTY_LIMIT

_LOG = logging.getLogger(__name__)

# wfcrl 用 CWD 相对路径解析可执行文件与工作目录（interface.py:342 的
# "simulators/fastfarm/bin/FAST.Farm_x64_OMP_2023.exe"、420/555 行的
# "__simul__/{fastfarm,floris}/"），两者都在仓库根。切过去，否则换个 cwd 起进程就找不到。
os.chdir(paths.ROOT)


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
# yaw 默认只累计最近 W 步的动作占比；零动作随窗口滚动释放预算。
# pitch 及关闭滑动窗口的 yaw 使用 env 的回合累计判据，上限为 DUTY_LIMIT。
# 测量 duty_limit 与各控制量实际使用的配置一致。
YAW_DUTY_WINDOW = 20        # 滑动窗口步数（dt=3 s ⇒ 60 s，贴合散热时间尺度）
YAW_DUTY_LIMIT = 0.30       # yaw 滑动窗口默认占空比上限
YAW_RATE = 0.3             # 偏航电机速率 (°/s)，与 mdp.ACTUATORS_RATE["yaw"] 同源

# 一次训练里删不掉的算例目录（Windows 句柄没释放）。下一次 purge 时一并重试。
_PENDING_PURGE = []


def _power_scalar(value):
    """Decode one real power reading; invalid or missing samples remain NaN."""
    if np.ma.is_masked(value):
        return float("nan")
    try:
        raw = np.asarray(value, dtype=object)
        if raw.shape not in ((), (1,)):
            return float("nan")
        scalar = raw.item()
        if isinstance(scalar, (bool, np.bool_, np.timedelta64)) or not isinstance(scalar, Real):
            return float("nan")
        result = float(scalar)
        return result if np.isfinite(result) else float("nan")
    except (TypeError, ValueError, OverflowError):
        return float("nan")


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
                 yaw_duty_limit=YAW_DUTY_LIMIT, physics=None):
        from wfrl.physics_options import validate_physics
        self.physics = validate_physics({} if physics is None else physics)
        if 'floris' in env_id.lower() and any(self.physics.values()):
            raise ValueError('physics options require the FAST.Farm backend')
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
            itf = getattr(self.env.mdp, "interface", None)
            if getattr(itf, "_simul_file", None) is not None:
                try:
                    self.case_dir = wakevtk.case_dir_of(itf)
                except TypeError:
                    _LOG.warning("无法从仿真输入路径确定算例目录", exc_info=True)

        # 布局坐标（来自 farm_case，画机组 / 建 FLORIS-proxy 用）
        fc = self.env.mdp.farm_case
        self.xcoords = np.asarray(fc.xcoords, dtype=float)
        self.ycoords = np.asarray(fc.ycoords, dtype=float)
        self.dt = float(getattr(fc, "dt", 3))
        self.hub_height = HUB_HEIGHT

        # 实际加载的风文件与统计由 reset 时的接口记录确定。
        self.bts = None

        # 每个控制量各自的连续动作边界（每步增量）。默认值见 data_cases.py:20-23：
        # yaw ±5°/步、pitch ±1°/步、torque ±1e3 N·m/步。
        aspace = self.env.action_space(self.agents[0])
        self.act_bounds = {
            c: (float(np.ravel(aspace[c].low)[0]),
                float(np.ravel(aspace[c].high)[0]))
            for c in self.controls
        }
        # train_fastfarm 使用 yaw 的标量边界，其他控制量按 act_bounds 读取。
        self.act_low, self.act_high = self.act_bounds.get(
            "yaw", (-5.0, 5.0))

        self.obs_keys = list(self.env.observation_space(self.agents[0]).keys())
        self.done = False
        self._steps = 0
        self._solver_owned = False
        self._started = False
        self.cleanup_confirmed = False
        self.cleanup_error = None
        self._prepare_solver_outputs()

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

    def _prepare_solver_outputs(self):
        self._solver_outputs = None
        self.physics_record = None
        from wfcrl.interface import FastFarmInterface
        if isinstance(self.env.mdp.interface, FastFarmInterface):
            from wfrl.solver_outputs import SolverOutputs
            from wfcrl.fastfarm_physics import configure_physics
            farm_path = self.env.mdp.interface._simul_file
            physics = getattr(self, 'physics', {})
            self._solver_outputs = SolverOutputs(farm_path, physics=physics)
            self.physics_record = configure_physics(farm_path, physics)

    # ------------------------------------------------------------------
    def reset(self, wind_speed=None, wind_direction=None, *, background_bts=None,
              bts_role="unspecified", upstream_request=None, **wind_parameters):
        """启动工况并返回包含 ``wind_conditions`` 三层记录的测量。

        FAST.Farm 仅 WindType=1 应用正有限风速；BTS 风速由算例实际引用文件决定。
        与固定坐标一致的显式风向可用于核对，不执行旋转。其他风向、TI、剪切、
        阵风、种子等未接入参数抛 WindRequestError，异常的
        record 含逐项 not_applied 原因，不启动求解器。无风速请求的稳态算例保留
        环境采样，用户请求与环境抽样分别记录。固定坐标约定见接口 WIND_COORDINATES。

        bts_role 仅声明所选文件为 background / gust_superposed / unspecified；
        background_bts 是阵风叠加前的独立证据文件，不改变求解输入。缺少来源时
        不推断背景或阵风统计。记录保存在 results/runs/wind_conditions，算例清理
        不删除它；wind_conditions 返回独立快照，异常也带同一记录路径。
        upstream_request 保存上层原始配置及用途，不作为求解参数；实际求解请求
        仍由 wind_speed、wind_direction 和 wind_parameters 逐项验证。
        """
        from wfcrl.interface import FastFarmInterface, WindRequestError, _wind_json
        itf = getattr(self.env.mdp, "interface", None)
        if getattr(self, "cleanup_error", None) is not None:
            raise RuntimeError("FAST.Farm cleanup is unconfirmed") from self.cleanup_error
        self._started = False
        self.cleanup_confirmed = False
        upstream_request = _wind_json(upstream_request)
        options = {}
        if isinstance(itf, FastFarmInterface):
            requested = {"wind_speed": wind_speed, "wind_direction": wind_direction,
                         "wind_time_series": self.wind_time_series, **wind_parameters}
            itf.set_wind_request_context(
                requested, record_dir=os.path.join(paths.RUNS, "wind_conditions"),
                background_bts=background_bts, bts_role=bts_role, hub_height=self.hub_height,
                episode_duration_s=self.max_steps * self.dt)
            # 显式 None 保留固定风向，避免 MDP 自动抽样一个不可应用的风向。
            options["wind_direction"] = wind_direction
            if wind_speed is not None:
                options["wind_speed"] = wind_speed
            else:
                from wfcrl.simul_utils import FASTInputFile, get_inflow_file_path
                try:
                    inflow = FASTInputFile(str(get_inflow_file_path(itf._simul_file)))
                    wind_type = int(inflow["WindType"])
                except (OSError, KeyError, TypeError, ValueError):
                    # 接口负责将输入读取失败保存为结构化拒绝记录。
                    wind_type = None
                if wind_type != 1:
                    options["wind_speed"] = None
        else:
            if wind_parameters or background_bts is not None or bts_role != "unspecified":
                raise ValueError("未应用：此后端不支持这些风况或 BTS 来源参数")
            for name, value in (("wind_speed", wind_speed),
                                ("wind_direction", wind_direction)):
                if value is None:
                    continue
                if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
                    raise ValueError(f"{name} 必须是有限数值")
                try:
                    value = float(value)
                except (ValueError, OverflowError) as exc:
                    raise ValueError(f"{name} 必须是有限数值") from exc
                if not np.isfinite(value) or (name == "wind_speed" and value <= 0):
                    raise ValueError(f"{name} 必须是有限数值，风速须大于 0")
                options[name] = value
        previous_record = getattr(itf, "wind_record", None)
        reset_error = None
        try:
            self.env.reset(options=options or None)
        except WindRequestError as exc:
            reset_error = exc
            raise
        finally:
            # Spawn can succeed before env.reset or its metadata writes fail.
            self._solver_owned = self._has_solver()
            record = getattr(itf, "wind_record", None)
            if (isinstance(itf, FastFarmInterface)
                    and record is not None and record is not previous_record):
                if upstream_request is not None:
                    record["upstream_request"] = upstream_request
                if getattr(self, 'physics_record', None) is not None:
                    record['physics'] = copy.deepcopy(self.physics_record)
                if upstream_request is not None or getattr(self, 'physics_record', None) is not None:
                    itf._save_wind_record()
                if reset_error is not None:
                    reset_error.record = copy.deepcopy(record)
        if isinstance(itf, FastFarmInterface):
            itf.mark_wind_control_start()
        self._telemetry_servo_steps = None
        if getattr(self, '_solver_outputs', None) is not None:
            self._solver_outputs.start(itf.wind_record['loaded_inputs'])
        self._steps = 0
        self.done = self._episode_ended()
        self._yaw_win.clear()          # 新回合：偏航占空比窗口清空
        self.u_inf = self._freestream(fallback=wind_speed)
        measurement = self._read(reward=0.0)
        self._started = True
        return measurement

    def _freestream(self, fallback=None):
        """用于归一化的输入参考风速 (m/s)。

        BTS 使用实际选定文件完整时域的轮毂层平均，不是本回合独立采样的场内来流。

        归一化功率 P/u^3 的分母必须用它,不能用 obs["wind_speed"] —— 后者是
        机组转子处的测量值,下游机组测到的是尾流内速度,拿它做分母会把尾流亏损
        同时算进分子和分母,把差异抹平。
        """
        itf = getattr(self.env.mdp, "interface", None)
        fi = getattr(itf, "fi", None)                       # FLORIS
        if fi is not None:
            try:
                return float(np.ravel(fi.floris.flow_field.wind_speeds)[0])
            except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
                _LOG.warning("FLORIS 参考风速不可用，尝试其余风速来源", exc_info=True)
        record = getattr(itf, "wind_record", None)
        if record is not None:
            settings = record["loaded_inputs"]["settings"]
            if int(settings["WindType"]) == 3:
                mean = record["statistics"].get("loaded_bts", {}).get("mean_streamwise_speed_mps")
                return float(mean) if mean is not None else float("nan")
            if int(settings["WindType"]) == 1:
                return float(settings["HWindSpeed"]) * (self.hub_height / float(settings["RefHt"])) ** float(settings["PLExp"])
        inflow = getattr(itf, "_inflow_file", None)         # FAST.Farm: HWindSpeed
        if inflow is not None:
            from openfast_toolbox.io.file import BrokenFormatError, EmptyFileError, WrongFormatError
            from wfcrl.simul_utils import read_inflow_info
            try:
                if os.path.getsize(inflow) == 0:
                    raise EmptyFileError(f"Empty inflow file: {inflow}")
                return float(read_inflow_info(inflow))
            except (OSError, KeyError, TypeError, ValueError, OverflowError,
                    BrokenFormatError, EmptyFileError, WrongFormatError):
                _LOG.warning("无法读取入流文件 %s 的参考风速，使用后备值", inflow,
                             exc_info=True)
        return float(fallback) if fallback is not None else float("nan")

    def _raw_measure(self, name):
        """直接问 interface 要原始测量（绕开 MDP 的状态字典）。

        被控量在 MDP 里存的是**累加的指令值**（`get_controlled_state_transition`），
        不是仿真回读值；算转速要的是真实测量，所以走这条路。测不到返回 None。
        """
        itf = getattr(self.env.mdp, "interface", None)
        if name not in getattr(itf, "measure_map", {}):
            return None
        try:
            v = np.ravel(itf.get_measure(name)).astype(float)
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            _LOG.warning("原始测量 %s 不可用", name, exc_info=True)
            return None
        return v if v.size == self.n else None

    def _raw_blade_moments(self):
        """Keep the six MPI moments in N m, including a squeezed single-turbine row."""
        interface = getattr(self.env.mdp, 'interface', None)
        if 'load' not in getattr(interface, 'measure_map', {}):
            return None
        try:
            values = np.asarray(interface.get_measure('load'), dtype=float)
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            _LOG.warning('原始叶根弯矩不可用', exc_info=True)
            return None
        if self.n == 1 and values.shape == (6,):
            values = values.reshape(1, 6)
        return values.copy() if values.shape == (self.n, 6) else None

    def _duty(self):
        """各机组各控制量的**执行机构占空比**，≥上限时动作被清零。

        yaw 滑动窗口只看最近 yaw_duty_window 步，使用 yaw_duty_limit。
        其余受约束控制量及关闭滑动窗口的 yaw 使用 env 累计版，
        acc/rate/(k·dt)，上限 DUTY_LIMIT。对应上限由 _duty_limits 提供。
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

    def _duty_limits(self):
        """各受约束控制量的生效上限；env 累计版固定使用 DUTY_LIMIT。"""
        limits = {c: DUTY_LIMIT for c in self.controls
                  if c in self.env.mdp.ACTUATORS_RATE}
        if self.yaw_duty_sliding and "yaw" in self.controls:
            limits["yaw"] = self.yaw_duty_limit
        return limits

    def _read(self, reward):
        """Read a physical round; *_valid masks describe each turbine's sample.

        load_valid requires all supplied load components to be finite. yaw is
        the command in degrees when controlled, otherwise the observation in
        degrees; yaw_command and yaw_measured keep these roles separate.
        Missing samples stay NaN, including the reset infos before any action.
        """
        n = self.n
        yaw = np.full(n, np.nan); power = np.full(n, np.nan); load = np.full(n, np.nan)
        ws = np.zeros(n); wd = np.zeros(n); pitch = np.zeros(n)
        mflap = np.full((n, 3), np.nan); medge = np.full((n, 3), np.nan)
        for ag in self.agents:
            i = self._idx[ag]
            obs = self.env.observe(ag)
            info = self.env.infos.get(ag, {})
            yaw[i] = _power_scalar(obs.get("yaw"))
            ws[i] = float(np.ravel(obs.get("wind_speed", [np.nan]))[0])
            wd[i] = float(np.ravel(obs.get("wind_direction", [np.nan]))[0])
            pitch[i] = float(np.ravel(obs.get("pitch", [np.nan]))[0])
            power[i] = _power_scalar(info.get("power"))
            # 保留 info 载荷的 mean|·| 标量；逐叶片弯矩优先使用下方 MPI 六分量原值。
            if "load" in info:
                try:
                    lv = np.ma.asarray(info["load"], dtype=float).filled(np.nan).ravel()
                except (TypeError, ValueError, OverflowError):
                    lv = np.array([np.nan])
                if lv.size and np.isfinite(lv).all():
                    load[i] = float(np.abs(lv).mean())
                if lv.size == 6:
                    mflap[i] = lv[:3] * LOAD_SCALE
                    medge[i] = lv[3:] * LOAD_SCALE
        root_moments = self._raw_blade_moments()
        if root_moments is not None:
            mflap, medge = root_moments[:, :3], root_moments[:, 3:]
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
        ym = self._raw_measure("yaw")
        from wfcrl.interface import FastFarmInterface
        native_yaw_radians = isinstance(getattr(self.env.mdp, "interface", None), FastFarmInterface)
        if "yaw" not in self.controls and native_yaw_radians:
            yaw = np.degrees(yaw)
        yaw_measured = np.full(n, np.nan) if ym is None else (
            np.degrees(ym) if native_yaw_radians else ym)
        yaw_command = yaw.copy() if "yaw" in self.controls else None
        tq = self._raw_measure("torque")
        torque = tq if tq is not None else np.full(n, np.nan)
        duty = self._duty()
        duty_limits = self._duty_limits()
        duty_arr = {c: np.array([duty[ag].get(c, 0.0) for ag in self.agents])
                    for c in duty_limits}
        clock = self._solver_clock()
        measurement = {
            "yaw": yaw, "power": power, "load": load,
            "load_valid": np.isfinite(load), "yaw_valid": np.isfinite(yaw),
            "yaw_command": yaw_command,
            "yaw_command_valid": np.isfinite(yaw) if yaw_command is not None else np.zeros(n, dtype=bool),
            "yaw_measured": yaw_measured,
            "yaw_measured_valid": np.isfinite(yaw_measured),
            "m_flap": mflap, "m_edge": medge,
            "wind_speed": ws, "wind_direction": wd, "pitch": pitch,
            "pitch_meas": pitch_meas,
            "torque": torque, "rotor_speed": np.full(n, np.nan),
            "duty": duty_arr,
            "duty_limit": duty_limits,
            "reward": float(reward), "done": self.done, "step": self._steps,
            "solver_clock": clock,
            "wind_conditions": copy.deepcopy(getattr(
                getattr(self.env.mdp, "interface", None), "wind_record", None)),
        }
        if getattr(self, '_solver_outputs', None) is not None:
            values, records = self._solver_outputs.sample(clock['value'], clock['interval_seconds'])
            measurement.update(values)
            measurement['output_channels'] = records
        if getattr(self, 'physics_record', None) is not None:
            measurement['physics'] = copy.deepcopy(self.physics_record)
            for turbine in measurement.get('output_channels', ()):
                for record in turbine.values():
                    record['provenance']['physics_identity'] = self.physics_record['identity']
        return measurement

    def _solver_clock(self):
        """Use the completed MPI count, including initialization and warmup."""
        from wfcrl.interface import FastFarmInterface
        from wfrl.fastfarm_clock import coupling_timestamp, servo_time_steps
        interface = getattr(self.env.mdp, 'interface', None)
        if not isinstance(interface, FastFarmInterface):
            return None
        loaded = interface.wind_record['loaded_inputs']
        stamp = coupling_timestamp(interface._num_iter, loaded['farm_settings']['DT_Low'])
        if getattr(self, '_telemetry_servo_steps', None) is None:
            self._telemetry_servo_steps = servo_time_steps(loaded)
        stamp['servo_time_steps'] = list(self._telemetry_servo_steps)
        stamp['power_window_steps'] = int(interface._default_avg_window)
        return stamp

    # ------------------------------------------------------------------
    def _has_solver(self):
        """A live child communicator owns solver resources independently of reset."""
        from wfcrl.interface import MPI
        env = getattr(self, "_uenv", getattr(self, "env", None))
        interface = getattr(getattr(env, "mdp", None), "interface", None)
        comm = getattr(interface, "_comm", None)
        return MPI is not None and isinstance(comm, MPI.Intercomm) and bool(comm)

    def _episode_ended(self):
        """AEC flags and the exchange budget both end physical transitions.

        WFCRL reset uses start_iter + 1 exchanges out of start_iter + max_steps,
        leaving max_steps - 1 control rounds. Dead-agent cleanup is not a round.
        """
        for name in ("terminations", "truncations"):
            if any(getattr(self.env, name, {}).values()):
                return True
        interface = getattr(getattr(self.env, "mdp", None), "interface", None)
        count, budget = getattr(interface, "_num_iter", None), getattr(interface, "max_iter", None)
        return count is not None and budget is not None and count >= budget

    def _clip(self, name, delta):
        """把某个控制量的增量补齐成 (n,) 并裁到它自己的动作边界。"""
        try:
            bounds = np.asarray(self.act_bounds[name], dtype=float)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} 缺少有效动作边界") from exc
        if bounds.shape != (2,) or not np.isfinite(bounds).all() or bounds[0] > bounds[1]:
            raise ValueError(f"{name} 动作边界须为有序的有限上下界")
        lo, hi = bounds
        if delta is None:
            return np.zeros(self.n, dtype=np.float32)
        try:
            raw = np.asarray(delta)
            if raw.dtype.kind not in "iuf":
                raise ValueError(f"{name} 动作必须为数值")
            d = np.asarray(raw, dtype=float)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} 动作必须为数值") from exc
        if d.shape not in ((), (1,), (self.n,), (self.n, 1)):
            raise ValueError(f"{name} 动作须为标量或 {self.n} 台机组的向量，收到 {d.shape}")
        if not np.isfinite(d).all():
            raise ValueError(f"{name} 动作必须为有限数值")
        d = d.ravel()
        if d.size == 1:
            d = np.repeat(d, self.n)
        return np.clip(d, lo, hi).astype(np.float32)

    def step(self, yaw_delta=None, pitch_delta=None, torque_delta=None):
        """推进一个控制步，各增量按各自动作边界裁剪；未激活的控制量忽略。

        yaw_delta   (n,) 偏航增量 (°)
        pitch_delta (n,) 变桨增量 (°)     —— 需 controls 里含 "pitch"
        torque_delta(n,) 发电机转矩增量 (N·m) —— 需 controls 里含 "torque"

        FLORIS 后端只实现了 yaw（interface.py:576-581），给 pitch/torque 会在
        构造 env 时就报错，不会静默吞掉。
        """
        if self.done or self._episode_ended():
            self.done = True
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
                return self._read(reward=0.0)
            i = self._idx[ag]
            self.env.step({c: np.array([v[i]], dtype=np.float32)
                           for c, v in cmd.items()})
        self._steps += 1
        self.done = self._episode_ended() or self._steps >= self.max_steps
        rw = self.env.rewards.get(self.agents[0], 0.0)
        reward = float(np.ravel(rw)[0]) if np.size(rw) else 0.0
        return self._read(reward=reward)

    # ------------------------------------------------------------------
    def close(self, drain_cap=None, purge=False, *, strict=False):
        """排空 FAST.Farm 剩余迭代预算，让子进程干净退出（避免 MPI_RECV 孤儿）。

        早退时调用；用零增量把回合走完（有上限，防止极长回合把关闭卡死）。
        `purge=True` 额外删掉本次 `envs.make()` 建的算例目录 —— 按回合重启训练时
        每回合一个 ~37 MB 的目录，不清会把 `__simul__/` 撑爆（README §8 第 5 条）。
        返回值和 cleanup_confirmed 表示排空、环境关闭和 MPI 断开均已确认。
        失败时 cleanup_error 保留原因，任何 strict 值都禁止 purge 和重复排空；
        strict=True 重新抛出异常，非 strict 返回 False，中断始终重新抛出。
        """
        prior_error = getattr(self, "cleanup_error", None)
        if prior_error is not None:
            if strict:
                raise RuntimeError("FAST.Farm close previously failed; cleanup is unconfirmed") from prior_error
            return False
        if getattr(self, "cleanup_confirmed", False):
            if purge:
                self._purge_case()
            return True
        self.cleanup_confirmed = False
        cap = drain_cap if drain_cap is not None else getattr(self, "max_steps", 0)
        drained = 0
        close_error = None
        interruption = None
        try:
            self._solver_owned = getattr(self, "_solver_owned", False) or self._has_solver()
            interface = getattr(getattr(getattr(self, "env", None), "mdp", None), "interface", None)
            if self._solver_owned and callable(getattr(interface, "update_command", None)):
                # A partial reset need not have an AEC selector or observations.
                # Hold the interface's last commands until its fixed MPI budget ends.
                while interface._num_iter < interface.max_iter and drained < cap:
                    interface.update_command()
                    drained += 1
                if interface._num_iter < interface.max_iter:
                    raise RuntimeError("FAST.Farm drain budget exhausted before shutdown")
                self.done = True
            elif getattr(self, "_started", False):
                while not self.done and drained < cap:
                    if self._episode_ended():
                        self.done = True
                        break
                    for _ in range(self.n):
                        _, _, term, trunc, _ = self.env.last()
                        if term or trunc:
                            self.done = True
                            break
                        self.env.step({c: np.array([0.0], dtype=np.float32)
                                       for c in self.controls})
                    drained += 1
                    self.done = (self.done or self._episode_ended()
                                 or self._steps + drained >= self.max_steps)
                if not self.done:
                    raise RuntimeError("FAST.Farm drain budget exhausted before shutdown")
        except BaseException as e:
            _LOG.exception("FAST.Farm 排空失败（strict=%s）", strict)
            close_error = e
            if not isinstance(e, Exception):
                interruption = e
        try:
            self._safe_close(strict=True)
        except BaseException as e:
            _LOG.exception("FAST.Farm 环境关闭未完成")
            close_error = close_error or e
            if interruption is None and not isinstance(e, Exception):
                interruption = e
        if close_error is not None:
            self.cleanup_error = close_error
            if interruption is not None:
                raise interruption
            if strict:
                raise close_error
            return False
        self._solver_owned = self._started = False
        self.cleanup_confirmed = True
        if purge:
            self._purge_case()
        return True

    def _purge_case(self):
        """删掉本次算例目录，连同之前删不掉的那些一起重试。

        FAST.Farm 退出与 Windows 释放 `5MW_Baseline/ServoData/DISCON_WT*.dll`
        的句柄之间有几秒延迟，close() 之后立刻删会吃到 WinError 5。所以这里
        重试几次，仍失败就挂到待删队列，下一回合（或下一次训练）再收。
        文件系统错误写入日志并重试；其他异常向调用方传播。
        """
        import shutil
        import time
        if getattr(self, "case_dir", None):
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
                except OSError:
                    _LOG.warning("算例目录清理失败（尝试 %d/3）：%s", attempt + 1, d,
                                 exc_info=True)
                    if attempt == 2:
                        left.append(d)
                    else:
                        time.sleep(1.5)
        _PENDING_PURGE[:] = left

    def _safe_close(self, *, strict=False):
        try:
            env = getattr(self, "env", None)
            if env is not None:
                env.close()
            if strict:
                # WFCRL 的 env.close 可以是空操作；FAST.Farm 的最后一次通信负责
                # Disconnect。仍有效的子通信器表示不能确认旧求解器已释放。
                if self._has_solver():
                    raise RuntimeError("FAST.Farm MPI communicator is still connected")
        except Exception:                            # noqa: BLE001
            if strict:
                raise
            _LOG.exception("FAST.Farm 环境关闭失败")


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
