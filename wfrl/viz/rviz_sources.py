"""FLORIS 与 FAST.Farm 数据源及风况请求传递。"""
import numpy as np

from wfcrl import environments as envs
from wfrl import multimodal as mm
from wfrl.viz.field import get_floris, extract_wake_plane


# ---------------------------------------------------------------------------
# 数据源 —— 直接驱动 FLORIS（可移动机组、可换策略），每步向各 channel 发布
# ---------------------------------------------------------------------------
class FarmSource:
    def __init__(self, env_id="Ablaincourt_Floris", policy="auto",
                 wind_speed=None, wind_direction=None):
        raw = envs.make(env_id, controls=["yaw"], max_num_steps=10_000)
        raw.reset()
        self.fi = get_floris(raw)
        # FLORIS 是稳态求解器，风况直接改 flow_field 即可（不像 FAST.Farm 要在
        # spawn 前写进 InflowWind）。风向这条只有本后端支持。
        if wind_speed is not None or wind_direction is not None:
            kw = {}
            if wind_speed is not None:
                kw["wind_speeds"] = [float(wind_speed)]
            if wind_direction is not None:
                kw["wind_directions"] = [float(wind_direction)]
            self.fi.reinitialize(**kw)
        self.hub_h = float(np.ravel(self.fi.floris.farm.hub_heights)[0])
        self.lx = np.asarray(self.fi.layout_x, dtype=float)
        self.ly = np.asarray(self.fi.layout_y, dtype=float)
        self.n = len(self.lx)
        self.yaws = np.zeros(self.n)
        self.policy = policy
        self._t = 0
        self._solve()

    def _solve(self):
        """按当前 layout + yaw 重算 FLORIS 稳态。"""
        ya = self.fi.floris.farm.yaw_angles * 0.0
        ya[..., :] = self.yaws
        self.fi.calculate_wake(yaw_angles=ya)
        self._yaw_angles = ya

    def move_turbine(self, i, x, y):
        """把第 i 台机组挪到 (x,y)，重建 FLORIS layout 并重算尾流。"""
        self.lx[i], self.ly[i] = x, y
        self.fi.reinitialize(layout_x=self.lx.tolist(), layout_y=self.ly.tolist())
        self._solve()

    def _auto_yaw(self):
        """无策略时的演示用 yaw：缓慢正弦摆动，制造可见的尾流偏转。"""
        phase = self._t * 0.06
        return 25.0 * np.sin(phase + np.linspace(0, 1.2, self.n))

    def step(self, hub):
        self._t += 1
        if self.policy == "auto":
            self.yaws = self._auto_yaw()
        # （策略模式在 set_policy_fn 里注入，见 MainWindow）
        elif callable(self.policy):
            self.yaws = np.clip(self.policy(self), -40, 40)
        self._solve()

        powers = np.ravel(self.fi.get_turbine_powers()) / 1e6      # MW
        vib = mm.vibration_features(self.fi)                        # (n,6)
        _, _, _, U = extract_wake_plane(self.fi, self.hub_h, self._yaw_angles)

        nan = np.full(self.n, np.nan)
        hub.publish("yaw", self.yaws.copy())
        hub.publish("power", powers)
        hub.publish("vibration", vib)
        hub.publish("wake", U)
        # FLORIS 是稳态解析尾流模型，没有传动链状态：桨距/转矩/转速都测不到，
        # 发 nan 让面板显示 "-"，而不是编一个看着像真的数
        hub.publish("pitch", nan)
        hub.publish("torque", nan)
        hub.publish("rotor_speed", nan)
        hub.publish("duty", {})


# ---------------------------------------------------------------------------
# 数据源 —— FAST.Farm 动态后端（真实气弹仿真）+ 同布局 FLORIS-proxy 尾流平面
# ---------------------------------------------------------------------------
class FastFarmSource:
    """duck-type FarmSource：MainWindow 无需改动即可复用。

    - **真实动态/功率/载荷/偏航** 来自 FAST.Farm（MPI 驱动，见 fastfarm_driver）。
    - **3D 尾流平面** 两条路径，`wake_vtk` 选：
      * `True`（推荐）：FAST.Farm 自己落盘的 DisXY 扰动风切面 —— 真实输运时延、
        蜿蜒、湍流结构。准实时：每 WrDisDT 一帧，比 MPI 慢一个 I/O 往返，且恒定
        落后一帧（见 wakevtk.DisXYReader）。
      * `False`：同布局 FLORIS 稳态 proxy（喂实测风速+实测偏航）。画面干净但物理
        不对 —— 稳态模型没有时延也没有蜿蜒，UI 里必须标 proxy。
      两种情况下机组姿态、功率、载荷都是 FAST.Farm 真值。
    - 机组布局在 .fstf 里烧死，运行中不可移动 → move_turbine 为 no-op。
    """

    IS_PROXY_WAKE = True
    PITCH_DERATE = 3.0          # 阶跃保持的幅度 (°)。真机运行区间 0~25°，
                                # 且额定以下基线本来贴 0°，这里只抬 3° 做降载。

    def __init__(self, env_id="Dec_Turb3_Row1_Fastfarm", policy="auto",
                 max_steps=200, controls=("yaw",), wake_vtk=False,
                 wake_vtk_z=90.0, wind_time_series=None, wind_speed=None,
                 pitch_demo="hold", wind_direction=None, upstream_request=None):
        from wfrl.fastfarm_driver import FastFarmDriver
        # 1) 真实 FAST.Farm 动态后端。wake_vtk 必须在这里就给 —— driver 在 __init__
        # 里改案例 .fstf，而 exe 是 reset() 才 spawn 的，之后改文件不生效。
        self.drv = FastFarmDriver(env_id, max_steps=max_steps,
                                  controls=controls, wake_vtk=wake_vtk,
                                  wake_vtk_z=wake_vtk_z,
                                  wind_time_series=wind_time_series)
        self.controls = list(controls)
        self.turb_box = wind_time_series
        self.pitch_demo = pitch_demo
        # 求解请求由底层逐项验证，成功与拒绝都保留原始配置及用途。
        self._m = self.drv.reset(wind_speed=wind_speed, wind_direction=wind_direction,
                                 upstream_request=upstream_request)

        # 1b) 真实切面轮询器（reset 之后建：case_dir 此时才确定有 exe 在写）
        self.wake_reader = self.wake_z = None
        if wake_vtk:
            from wfrl.viz.wakevtk import DisXYReader
            self.wake_reader = DisXYReader(self.drv.case_dir)
            self.wake_z = self.drv.wake_vtk["z"]
            self.IS_PROXY_WAKE = False      # 实例属性盖掉类属性

        # 2) 同布局 FLORIS-proxy（仅用于渲染尾流平面）
        proxy_id = env_id.replace("Fastfarm", "Floris")
        raw = envs.make(proxy_id, controls=["yaw"], max_num_steps=10_000)
        raw.reset()
        self.fi = get_floris(raw)
        self.hub_h = float(np.ravel(self.fi.floris.farm.hub_heights)[0])
        self.lx = np.asarray(self.fi.layout_x, dtype=float)
        self.ly = np.asarray(self.fi.layout_y, dtype=float)
        self.n = len(self.lx)

        self.policy = policy
        self.yaws = np.asarray(self._m["yaw"], dtype=float)
        self._t = 0
        self._ws0 = float(np.nanmean(self._m["wind_speed"]))
        # proxy 必须跟着真实来流走。算例默认 8 m/s，而 --wind strong 下 FAST.Farm
        # 实际吹 14 m/s —— 不同步的话尾流平面按 8 m/s 解，色带上限印成 9.8，
        # 整片饱和成一个颜色，等于在截图上写了个错数字。u_inf 是 InflowWind 的
        # HWindSpeed（湍流盒子下是 .bts 轮毂层），拿不到就退回转子处测速最大值。
        u0 = getattr(self.drv, "u_inf", None)
        if u0 is None or not np.isfinite(u0):
            u0 = float(np.nanmax(self._m["wind_speed"]))
        if np.isfinite(u0) and u0 > 0:
            self.fi.reinitialize(wind_speeds=[float(u0)])
            self._ws0 = float(u0)       # 和 step() 里的判据同一口径，别一步就漂回去
        self._solve()

    # 与 FarmSource._solve 同款：按当前 yaw 重算 proxy 稳态尾流
    def _solve(self):
        ya = self.fi.floris.farm.yaw_angles * 0.0
        ya[..., :] = self.yaws
        self.fi.calculate_wake(yaw_angles=ya)
        self._yaw_angles = ya

    def move_turbine(self, i, x, y):
        # FAST.Farm 布局固定，运行中不可迁移
        self.lx[i], self.ly[i] = self.drv.xcoords[i], self.drv.ycoords[i]

    def _pitch_target(self):
        """外部桨距目标角 (n,)，度。

        真机的变桨不是连续摆动的执行机构：
          - 额定风速(11.4)以下,基线桨距贴 0° **整段不动**,靠变转速追最佳叶尖速比;
          - 额定以上才由基线 PI 慢慢顺桨限功率,正常运行区间 0~25°;
          - 顺桨到 90° 是**停机/保护**工况,不是运行状态。
        所以外部策略做成**阶跃保持**:只在上游机组抬几度并保持一段(轴向诱导
        控制 —— 牺牲自身出力换下游来流),其余时间归零。一个 90 s 周期里只动
        两次、幅度 3°,和早前那版每步都在摆的正弦完全是两回事。

        `sweep` 保留原来的连续正弦：不真实,但"变桨接没接上后端"一眼能看出来,
        自检时有用。`off` 则完全不给外部指令,画面上桨距全部来自基线控制器 ——
        大风档要看基线顺桨行为就用这个。
        """
        n = self.n
        if self.pitch_demo == "off":
            return np.zeros(n)
        if self.pitch_demo == "sweep":
            ph = self._t * 0.06
            return 4.0 * (1.0 - np.cos(ph + np.linspace(0, 2.1, n)))
        # hold：周期 30 步(dt=3 s ⇒ 90 s)，3 步爬升 / 12 步保持 / 3 步撤回 / 12 步归零
        k = self._t % 30
        if k < 3:
            lvl = self.PITCH_DERATE * (k + 1) / 3.0
        elif k < 15:
            lvl = self.PITCH_DERATE
        elif k < 18:
            lvl = self.PITCH_DERATE * (18 - k) / 3.0
        else:
            lvl = 0.0
        tgt = np.zeros(n)
        tgt[0] = lvl                       # 只降上游那台，下游承接它让出的来流
        return tgt

    def _auto_delta(self):
        """演示用增量：偏航正弦摆动；桨距走 `_pitch_target` 的阶跃保持。

        返回 (yaw, pitch, torque) 三个增量，未激活的给 None。torque 不自动演示
        —— DISCON.F90:440 是 `GenTrq = TorqueRef`，外部转矩**完全替换**基线变速
        控制器，默认边界 ±2e4 N·m 又低于额定的 ~4.3e4，自动摆会让转子飞车。
        """
        phase = self._t * 0.06
        target = 25.0 * np.sin(phase + np.linspace(0, 1.2, self.n))
        lo, hi = self.drv.act_bounds["yaw"]
        d_yaw = np.clip(target - self.yaws, lo, hi)
        d_pitch = None
        if "pitch" in self.controls:
            plo, phi = self.drv.act_bounds["pitch"]
            d_pitch = np.clip(self._pitch_target()
                              - np.asarray(self._m["pitch"]), plo, phi)
        return d_yaw, d_pitch, None

    def step(self, hub):
        self._t += 1
        d_pitch = d_torque = None
        if self.done:                       # 回合预算耗尽后保持静止
            delta = np.zeros(self.n)
        elif self.policy == "auto":
            delta, d_pitch, d_torque = self._auto_delta()
        elif callable(self.policy):
            delta = np.clip(np.asarray(self.policy(self)).ravel(),
                            self.drv.act_low, self.drv.act_high)
        else:
            delta = np.zeros(self.n)

        # 推进真实 FAST.Farm 一个控制步
        self._m = self.drv.step(delta, d_pitch, d_torque)
        self.yaws = np.asarray(self._m["yaw"], dtype=float)

        # proxy 的来流要跟**自由来流**走，不能用全场测速均值：下游机组测到的是
        # 尾流内速度，尾流一建立均值就往下掉（14 m/s 下掉到 ~12），色带上限跟着
        # 缩水。这里取逐机组测速的**最大值**——一列布局里最上游那台没被遮，它
        # 就是自由来流的最好估计；湍流盒子下它还会随时间起伏，正是要跟的。
        ws = float(np.nanmax(self._m["wind_speed"]))
        if np.isfinite(ws) and abs(ws - self._ws0) > 0.2:
            self.fi.reinitialize(wind_speeds=[ws])
            self._ws0 = ws
        self._solve()

        powers = np.asarray(self._m["power"], dtype=float)          # 真实 MW
        vib = mm.vibration_features(self.fi)                        # proxy TI 等
        vib = np.array(vib, dtype=float)
        vib[:, 4] = np.asarray(self._m["load"], dtype=float)       # 1P 载荷用真实值
        # 尾流：真实切面走轮询器（没有新帧就不发，画面保持上一帧 —— 比回退到
        # proxy 好，混着两种物理的图没法解读）；否则发 proxy 的稳态标量场。
        if self.wake_reader is not None:
            fr = self.wake_reader.latest()
            if fr is not None:
                hub.publish("wake", fr)
        else:
            _, _, _, U = extract_wake_plane(self.fi, self.hub_h,
                                            self._yaw_angles)
            hub.publish("wake", U)

        hub.publish("yaw", self.yaws.copy())
        hub.publish("power", powers)
        hub.publish("vibration", vib)
        # 传动链：发电机转矩是 MPI 12 通道里的真值；转速由 P/(η·T) 反解。
        # 桨距发两路：`pitch` 是外部累加的**指令**（被控时 MDP 不会用测量覆盖它），
        # `pitch_meas` 是 avrSWAP(4) 回读的**实际**桨距。叶片几何要用后者，
        # 否则大风档基线自己在顺桨、画面上却纹丝不动。
        hub.publish("pitch", np.asarray(self._m["pitch"], dtype=float))
        pmeas = self._m.get("pitch_meas")
        if pmeas is not None:
            hub.publish("pitch_meas", np.asarray(pmeas, dtype=float))
        hub.publish("torque", np.asarray(self._m["torque"], dtype=float))
        hub.publish("rotor_speed",
                    np.asarray(self._m["rotor_speed"], dtype=float))
        hub.publish("duty", self._m.get("duty", {}))
        # 逐叶片根部弯矩 (n,3)，N·m：驱动柔性叶片变形。挥舞=RootMyc、摆振=RootMxc，
        # 就是 12 个 MPI 通道里的后 6 个（原来被压成一个 load 标量丢掉了）。
        for key in ("m_flap", "m_edge"):
            v = self._m.get(key)
            if v is not None:
                hub.publish(key, np.asarray(v, dtype=float))

    @property
    def u_inf(self):
        """本回合自由来流 (m/s)，给尾流色带定上界；测不到返回 None。"""
        u = getattr(self.drv, "u_inf", None)
        return float(u) if u is not None and np.isfinite(u) else None

    def last_wind_speed(self):
        """各机组**转子处**测速 (n,)。注意这不是自由来流：下游机组测到的是尾流
        内速度，做 P/u³ 的分母要用 `u_inf`（见 memory: wfcrl-inflow-control）。"""
        return np.asarray(self._m.get("wind_speed", []), dtype=float)

    @property
    def done(self):
        return bool(self._m.get("done", False))

    def close(self):
        try:
            # RViz cases are disposable runtime state.  Keeping them after every
            # desktop session makes __simul__/ grow by tens of MB per launch.
            self.drv.close(purge=True)
        except Exception as e:                                      # noqa: BLE001
            print(f"[rviz] FAST.Farm 关闭出错（忽略）: {e}", flush=True)

