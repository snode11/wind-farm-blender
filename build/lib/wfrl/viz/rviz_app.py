"""
RViz 类风场可视化桌面应用（PyVistaQt，无 ROS 依赖）。

核心 RViz 范式：
  - 3D 场景可鼠标轨道/缩放/平移；机组可点击选中并在地面拖拽重定位（回填 FLORIS 重算尾流）
  - "channel"（等价 ROS topic）：数据源每步把 yaw/power/vibration/wake 发布到各通道，
    面板订阅通道、数据到就刷新。后台 QTimer 驱动"数据流"，随时可开关某路流。

运行：
    python rviz_app.py                 # 自由运行（yaw 自动轻摆，看尾流随动）
    python rviz_app.py --model ippo    # 用训练好的 IPPO 策略驱动 yaw
    python rviz_app.py --model central # 用训练好的集中式 critic MAPPO 驱动

依赖：pyvistaqt PyQt5（已装）。
"""
import argparse
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor
from qtpy import QtCore, QtWidgets

from wfcrl import environments as envs
from wfrl import paths
from wfrl.viz.field import (get_floris, add_wake_plane, extract_wake_plane,
                            add_vtk_wake_plane, vtk_plane_scalars)
from wfrl.viz.animate import (build_turbine, update_turbine, turbine_actors,
                              move_turbine_actors)
from wfrl.viz.geometry import CALIB, load_turbine_geometry
from wfrl.viz.terrain import label_font as _cjk_font
from wfrl import multimodal as mm

# 机组显示放大倍数。整场跨数千米、机组只有百米量级，1:1 画出来是几根针。
# 凡是要和机组尺寸对齐的东西（取景包围盒、随机镜头的机位）都必须乘上它，
# 否则相机会按真实尺寸取景、却对着一台放大 5 倍的机器 —— 镜头埋进塔筒里。
TURB_SCALE = 5.0


# ---------------------------------------------------------------------------
# Channel Hub —— 极简发布/订阅（等价 ROS topic）
# ---------------------------------------------------------------------------
class ChannelHub:
    def __init__(self):
        self._subs = {}          # topic -> [callbacks]
        self.enabled = {}        # topic -> bool（面板可随时开关某路流）

    def topic(self, name):
        self._subs.setdefault(name, [])
        self.enabled.setdefault(name, True)
        return name

    def subscribe(self, name, cb):
        self.topic(name)
        self._subs[name].append(cb)

    def publish(self, name, data):
        if not self.enabled.get(name, True):
            return
        for cb in self._subs.get(name, []):
            cb(data)


class BufferHub:
    """后台线程专用的发布缓冲，接口和 ChannelHub 兼容。

    为什么要它：`ChannelHub.publish` 会同步回调到 Qt 控件（遥测表格 setText），
    而在工作线程里碰 Qt 控件是未定义行为。所以后台的控制步往这里发，主线程在
    渲染回调里 `drain()` 出来再转发给真 hub。
    """

    def __init__(self, hub):
        self._hub = hub
        self._q = []
        self._lock = threading.Lock()

    def __getattr__(self, name):        # topic/subscribe/enabled 一律转给真 hub
        return getattr(self._hub, name)

    def publish(self, name, data):
        with self._lock:
            self._q.append((name, data))

    def drain(self):
        with self._lock:
            out, self._q = self._q, []
        return out


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
                 pitch_demo="hold"):
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
        # 不钉风速的话 reset 会重抽 Weibull（P∝u³ ⇒ 单次抽签定整段量级），
        # 前后对照就变成比两次抽签。湍流模式下来流由 .bts 定，driver 会忽略这个请求。
        self._m = self.drv.reset(wind_speed=wind_speed)

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
            self.drv.close()
        except Exception as e:                                      # noqa: BLE001
            print(f"[rviz] FAST.Farm 关闭出错（忽略）: {e}", flush=True)


# ---------------------------------------------------------------------------
# 遥测表格面板（订阅 yaw/power/vibration）
# ---------------------------------------------------------------------------
class TelemetryPanel(QtWidgets.QTableWidget):
    # 列 = (表头, 订阅的通道, 取值方式, 格式)
    COLS = [
        ("机组", None, None, None),
        ("yaw°", "yaw", lambda d, i: d[i], "%+.1f"),
        # 桨距分两列：指令是外部策略累加的 PitchRef（下界语义），实测是
        # avrSWAP(4) 回读。大风档基线自己顺桨，两列会明显分开 —— 这正是要看的。
        ("桨距指令°", "pitch", lambda d, i: d[i], "%.2f"),
        ("桨距实测°", "pitch_meas", lambda d, i: d[i], "%.2f"),
        ("转矩kNm", "torque", lambda d, i: d[i] / 1e3, "%.1f"),
        ("转速rpm", "rotor_speed", lambda d, i: d[i], "%.2f"),
        ("功率MW", "power", lambda d, i: d[i], "%.2f"),
        ("TI", "vibration", lambda d, i: d[i, 0], "%.3f"),
        ("1P载荷", "vibration", lambda d, i: d[i, 4], "%.3f"),
        # 叶尖挠度给**真值**（不含 flex_scale 显示放大），三片取均值。
        # 弯矩→挠度用 geometry.CALIB 的实测标定。
        ("挠度m", "m_flap",
         lambda d, i: CALIB["flap"][0] * np.nanmean(d[i]) + CALIB["flap"][1],
         "%+.2f"),
        # 扫塔净空：三片叶掠塔时的最小间隙，同样是 **1× 真值**（不含显示放大）。
        # 叶片有预弯 + 锥角 + 悬伸，8 m/s 下净空 4.4 m，只有风速大到叶尖挠度
        # 翻倍才会逼近塔筒 —— 所以低于 CLR_WARN 才染色。
        ("净空m", "clearance", lambda d, i: np.nanmin(d[i]), "%.2f"),
    ]
    DUTY_LIMIT = 0.1        # multiagent_env.py:206，超过环境会把该动作清零
    CLR_WARN = 1.5          # 净空低于此值染黄（设计态 4.5 m，1.5 m 已是异常工况）

    def __init__(self, n, hub):
        super().__init__(n, len(self.COLS))
        self.setHorizontalHeaderLabels([c[0] for c in self.COLS])
        self.verticalHeader().setVisible(False)
        # 不撑开的话 dock 只给两列的宽度，后面几列会被截掉
        self.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Stretch)
        self.setMinimumWidth(560)
        for i in range(n):
            self.setItem(i, 0, QtWidgets.QTableWidgetItem(f"T{i+1}"))
            for c in range(1, len(self.COLS)):
                self.setItem(i, c, QtWidgets.QTableWidgetItem("-"))
        self._buf = {}
        self._duty = {}
        for _, topic, _, _ in self.COLS:
            if topic:
                hub.subscribe(topic, lambda d, t=topic: self._set(t, d))
        hub.subscribe("duty", lambda d: self._set_duty(d))

    def _set(self, k, d):
        self._buf[k] = d
        self._refresh()

    def _set_duty(self, d):
        """执行机构占空比：逼近 10% 的格子染黄，说明"下一步会被环境清零"。"""
        self._duty = d or {}
        self._refresh()

    def _refresh(self):
        from qtpy.QtGui import QColor
        warn = QColor(255, 235, 150)
        plain = QColor(255, 255, 255)
        for i in range(self.rowCount()):
            for c, (_, topic, get, fmt) in enumerate(self.COLS):
                if topic is None:
                    continue
                d = self._buf.get(topic)
                if d is None:
                    continue
                try:
                    v = float(get(d, i))
                except (IndexError, TypeError, ValueError):
                    continue
                self.item(i, c).setText("-" if not np.isfinite(v) else fmt % v)
            for c, key in ((1, "yaw"), (2, "pitch")):
                du = self._duty.get(key)
                hot = du is not None and i < len(du) and du[i] >= self.DUTY_LIMIT
                self.item(i, c).setBackground(warn if hot else plain)
            # 净空告警：黄=接近塔筒，红=已扫塔
            cl = self._buf.get("clearance")
            col = len(self.COLS) - 1
            if cl is not None and i < len(cl):
                v = float(np.nanmin(cl[i]))
                bg = plain if not np.isfinite(v) or v >= self.CLR_WARN else (
                    QColor(255, 170, 170) if v <= 0 else warn)
                self.item(i, col).setBackground(bg)


# ---------------------------------------------------------------------------
# 通道开关面板（像 RViz 左侧 Displays 勾选框）
# ---------------------------------------------------------------------------
class ChannelPanel(QtWidgets.QWidget):
    def __init__(self, hub, topics):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("<b>数据通道 (Channels)</b>"))
        for t in topics:
            cb = QtWidgets.QCheckBox(t)
            cb.setChecked(True)
            cb.stateChanged.connect(
                lambda s, name=t: hub.enabled.__setitem__(name, bool(s)))
            lay.addWidget(cb)
        lay.addStretch(1)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, source, policy_fn=None, allow_drag=True, tick_ms=120,
                 flex_scale=5.0, render_ms=33, async_step=False,
                 terrain_kind=None, dual_view=False, focus=0):
        super().__init__()
        # 标题要能一眼看出尾流是哪来的：三种组合各自不同的物理可信度。
        title = "WFCRL — RViz 类风场可视化"
        if not hasattr(source, "drv"):
            title += "  [FLORIS 稳态]"
        elif getattr(source, "IS_PROXY_WAKE", False):
            title += "  [FAST.Farm 动态 · 尾流=FLORIS proxy]"
        else:
            title += "  [FAST.Farm 动态 · 尾流=真实 DisXY 切面]"
        self.setWindowTitle(title)
        self.resize(1400, 900)
        self.source = source
        self.allow_drag = allow_drag
        self.hub = ChannelHub()
        if policy_fn is not None:
            source.policy = policy_fn
        self._spin = 0.0
        self._drag_idx = None
        self._focus_idx = int(np.clip(focus, 0, source.n - 1))  # 右视图锁定谁
        self.tick_ms = tick_ms
        self.render_ms = render_ms
        # 物理步与渲染解耦：FAST.Farm 一个 3 s 控制步要 2~3 s 墙钟，压在渲染
        # 回调里就等于把帧率钉在 0.4 fps。异步时步进丢给单线程池，渲染定时器
        # 按真实流逝时间插值方位角，转子照 1× 实时匀速转。
        self._pool = ThreadPoolExecutor(max_workers=1) if async_step else None
        self._pending = None        # 在跑的控制步 future
        self._m_dirty = True        # 有新弯矩 ⇒ 下一帧重算柔性变形（贵，别每帧做）
        self._rpm = None            # 由 rotor_speed 通道喂，驱动叶片自转
        self._pitch = None          # 驱动叶片绕展向轴转；实测优先，退回指令
        self._pitch_cmd = None      # 外部累加的桨距指令 (PitchRef 下界)
        self._pitch_meas = None     # avrSWAP(4) 回读的实际桨距
        self._m_flap = None         # (n,3) 挥舞弯矩，驱动柔性变形
        self._m_edge = None         # (n,3) 摆振弯矩
        # 叶尖挠度真值 3.4 m 对 63 m 叶片，在整场尺度（机组间距数千米）下几乎
        # 看不出来 —— 机组本身已经按 scale=5 放大了，变形也跟着放大同样倍数才
        # 与几何一致。放大是显示手段，遥测面板里给的仍是真值。
        self.flex_scale = float(flex_scale)

        # --- 中央 3D 场景 ---
        # 双视图（Gazebo 式）：左「世界」自由轨道，右「随机」锁在 T1 上。两个
        # renderer **共享同一批 actor**（VTK 支持一个 prop 挂多个 renderer），
        # 所以柔性叶片那 8833 个顶点和净空搜索的代价只付一次；如果各建一套
        # 几何，帧率直接对半。相机是各自独立的。
        self.dual = bool(dual_view)
        self.plotter = (QtInteractor(self, shape=(1, 2))
                        if self.dual else QtInteractor(self))
        self.setCentralWidget(self.plotter)
        self.plotter.set_background("white")
        if self.dual:                     # 两边都要白底，set_background 只作用于当前
            for r in self.plotter.renderers:
                r.SetBackground(1.0, 1.0, 1.0)
            self.plotter.subplot(0, 0)    # 主场景建在左视图里

        fi, hub_h = source.fi, source.hub_h
        # 真实 DisXY 路径下**先不建**尾流网格：网格范围/分辨率写在 VTK 文件头里，
        # 而第一张盘要等 FAST.Farm 跑两个控制步才落地。等首帧到了再建（_on_wake），
        # 在那之前场景里只有机组 —— 比先画一张 proxy 再换掉要诚实。
        self._wake_z = getattr(source, "wake_z", None) or hub_h
        self.sgrid = None
        if getattr(source, "wake_reader", None) is None:
            self.sgrid, wake_actor = add_wake_plane(
                self.plotter, fi, hub_h, return_actor=True)
            self._share(wake_actor)
        self.turbines = [build_turbine(self.plotter, x, y, hub_h,
                                       scale=TURB_SCALE)
                         for x, y in zip(source.lx, source.ly)]
        for t in self.turbines:
            for a in turbine_actors(t):
                self._share(a)
        # 地面拾取平面（半透明，供拖拽时求鼠标落点）
        cx, cy = source.lx.mean(), source.ly.mean()
        span = max(source.lx.ptp(), source.ly.ptp()) * 1.6 + 1000
        self.ground = pv.Plane(center=(cx, cy, 0), i_size=span, j_size=span,
                               i_resolution=1, j_resolution=1)
        self.ground_actor = self.plotter.add_mesh(self.ground, color="gainsboro",
                                                  opacity=0.25, pickable=True)
        # 地形：**装饰**，不参与流场（terrain.py 开头讲了为什么以及怎么挡住越界）。
        # 拾取仍靠上面那张 z=0 平面，所以地形加在它之后、且 pickable=False。
        self.terrain_actor = None
        if terrain_kind:
            from wfrl.viz import terrain as _terr
            # 远景山脊压到「画出来的机组」高度的一半：机组按 TURB_SCALE 放大后
            # 塔顶在 ~765 m，山脊若也是 700 m，画面主体就从风场变成山。
            hub_z, _r = self._draw_dims()
            mesh = _terr.build(source.lx, source.ly, kind=terrain_kind,
                               z_wake=self._wake_z, h_far=0.5 * hub_z)
            self.terrain_actor = _terr.add(self.plotter, mesh, kind=terrain_kind)
            self._share(self.terrain_actor)
        # 网格与相机都必须钉在**风场**范围上，不能让它们去适配地形：地形跨 7 km、
        # 远景山脊 700 m，自动适配的结果是刻度轴量程失真、机组缩成几个点。
        fb = self._farm_bounds()
        # z 向量程只有百米、xy 有数千米，默认刻度数会让 z 标签叠成一团
        self.plotter.show_grid(color="gray", n_xlabels=4, n_ylabels=4,
                               n_zlabels=2, bounds=fb)
        self.plotter.add_axes(line_width=3)
        self.plotter.camera_position = "yz"
        self.plotter.camera.azimuth = -60
        self.plotter.camera.elevation = 25
        self.plotter.reset_camera(bounds=fb)
        if self.dual:
            # 视图标题写清楚两边各是什么；右视图只放随机镜头，不放网格/坐标轴
            # ——它是「贴着机组看」的，一套 3 km 量程的刻度在那儿毫无意义。
            self.plotter.add_text("世界视图 World", position="upper_left",
                                  font_size=9, color="black",
                                  font_file=_cjk_font())
            self.plotter.subplot(0, 1)
            self.plotter.add_text(f"随机镜头 Chase · T{self._focus_idx + 1}",
                                  position="upper_left", font_size=9,
                                  color="black", font_file=_cjk_font())
            self._chase_camera()
            self.plotter.subplot(0, 0)    # 交互焦点还给世界视图
        self._redraw_turbines()

        # --- 侧栏面板 ---
        self.telemetry = TelemetryPanel(source.n, self.hub)
        self._add_dock("遥测 Telemetry", self.telemetry, QtCore.Qt.RightDockWidgetArea)
        topics = ["yaw", "pitch", "pitch_meas", "torque", "rotor_speed",
                  "power", "vibration", "wake", "clearance"]
        self._add_dock("通道 Channels", ChannelPanel(self.hub, topics),
                       QtCore.Qt.LeftDockWidgetArea)
        self.hub.subscribe("wake", self._on_wake)
        self.hub.subscribe("rotor_speed", self._on_rpm)
        self.hub.subscribe("pitch", self._on_pitch)
        self.hub.subscribe("pitch_meas", self._on_pitch_meas)
        self.hub.subscribe("m_flap", lambda d: setattr(self, "_m_flap", d))
        self.hub.subscribe("m_edge", lambda d: setattr(self, "_m_edge", d))

        # --- 底部控制条 ---
        self._build_toolbar()

        # --- 拖拽交互（FAST.Farm 布局固定，禁用拖拽） ---
        if self.allow_drag:
            self._wire_drag()
        else:
            self.ground_actor.SetPickable(False)

        # --- 双定时器：物理步进 / 画面刷新各跑各的 ---
        self._buf = BufferHub(self.hub)
        self._clock = QtCore.QElapsedTimer()
        self._clock.start()
        self._last_ns = self._clock.nsecsElapsed()
        self._playing = True
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(tick_ms)
        self.rtimer = QtCore.QTimer(self)
        self.rtimer.timeout.connect(self._render_tick)
        self.rtimer.start(render_ms)

    # ------------------------------------------------------------------
    def _share(self, actor):
        """把左视图的 actor 也挂进右视图 —— 共享，不复制。

        单视图下是 no-op。共享的直接后果是 `user_matrix`/标量数组改一次两边
        同时生效，所以 `_redraw_turbines` / `_on_wake` 完全不用知道有第二个视图。
        """
        if self.dual and actor is not None:
            self.plotter.renderers[1].AddActor(actor)

    def _chase_camera(self):
        """右视图：锁定 T1 的「随机镜头」。

        看点取轮毂，机位偏南 + 抬高一点，视场收窄到能看清叶片扭角与变形的程度。
        每帧都调是因为 FLORIS 后端允许拖拽机位，锁定目标会跟着动。
        """
        if not self.dual:
            return
        i = self._focus_idx
        hub_z, r = self._draw_dims()
        fx, fy = float(self.source.lx[i]), float(self.source.ly[i])
        cam = self.plotter.renderers[1].camera
        cam.focal_point = (fx, fy, hub_z)
        cam.position = (fx - 2.2 * r, fy - 3.4 * r, hub_z + 1.1 * r)
        cam.up = (0.0, 0.0, 1.0)
        self.plotter.renderers[1].ResetCameraClippingRange()

    def _draw_dims(self):
        """画面上机组的 (轮毂高度, 转子半径) —— 已乘 TURB_SCALE 的**显示**尺寸。

        取景和相机都要用这个而不是物理尺寸：机组按 5 倍画，拿 90/63 去摆镜头
        会把相机放进塔筒内部。遥测里给的仍然是物理真值。
        """
        hub_h = float(self.source.hub_h)
        try:
            r = float(load_turbine_geometry().tip_rad)   # 63 m，ElastoDyn 真值
        except Exception:                                # noqa: BLE001
            r = 0.7 * hub_h        # 解析不出来时的名义半径，只影响取景不影响物理
        return hub_h * TURB_SCALE, r * TURB_SCALE

    def _farm_bounds(self):
        """风场本身的 (xmin,xmax,ymin,ymax,zmin,zmax)，用来钉住网格与相机。

        机位包围盒外扩一个转子直径，竖向到轮毂 + 一个转子半径 —— 也就是"画面里
        真正要看的东西"的范围，与地形网格无关：地形跨 7 km、远景山脊 700 m，
        让相机去适配它的结果是机组缩成几个点。尺寸取显示尺寸（见 _draw_dims）。
        """
        hub_z, r = self._draw_dims()
        return (float(np.min(self.source.lx)) - 2.0 * r,
                float(np.max(self.source.lx)) + 2.0 * r,
                float(np.min(self.source.ly)) - 2.0 * r,
                float(np.max(self.source.ly)) + 2.0 * r,
                0.0, hub_z + r)

    def _add_dock(self, title, widget, area):
        dock = QtWidgets.QDockWidget(title, self)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)

    def _build_toolbar(self):
        tb = self.addToolBar("controls")
        self.play_btn = QtWidgets.QPushButton("⏸ 暂停")
        self.play_btn.clicked.connect(self._toggle_play)
        step_btn = QtWidgets.QPushButton("⏭ 单步")
        step_btn.clicked.connect(self._tick)
        self.status = QtWidgets.QLabel("  就绪  ")
        for w in (self.play_btn, step_btn, self.status):
            tb.addWidget(w)
        if self.dual:
            # 分屏比例。QSplitter 那条路走不通：actor 的 OpenGL 资源绑在创建它的
            # render window 上，跨窗口共享的那一侧画出来是全白（实测 ink=0）。
            # 同一 render window 内改 renderer 的 viewport 才行。
            tb.addSeparator()
            tb.addWidget(QtWidgets.QLabel("  分屏 "))
            self.split_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self.split_slider.setRange(15, 85)
            self.split_slider.setValue(50)
            self.split_slider.setFixedWidth(160)
            self.split_slider.valueChanged.connect(self._set_split)
            tb.addWidget(self.split_slider)
            self.split_lbl = QtWidgets.QLabel(" 50% ")
            tb.addWidget(self.split_lbl)
            for pct, name in ((25, "世界小"), (50, "对半"), (80, "世界大")):
                b = QtWidgets.QPushButton(name)
                b.setToolTip(f"左视图占 {pct}%")
                b.clicked.connect(lambda _=False, v=pct:
                                  self.split_slider.setValue(v))
                tb.addWidget(b)

    def _set_split(self, pct):
        """左右视口按 pct% 分。相机的纵横比跟着 viewport 变，要重算裁剪面。"""
        f = max(0.05, min(0.95, pct / 100.0))
        self.plotter.renderers[0].SetViewport(0.0, 0.0, f, 1.0)
        self.plotter.renderers[1].SetViewport(f, 0.0, 1.0, 1.0)
        for r in self.plotter.renderers:
            r.ResetCameraClippingRange()
        self.split_lbl.setText(f" {pct}% ")
        self.plotter.render()

    def _toggle_play(self):
        self._playing = not self._playing
        # 重置时钟：不然恢复播放时 dt 会把整段暂停时长算进方位角
        self._last_ns = self._clock.nsecsElapsed()
        self.play_btn.setText("⏸ 暂停" if self._playing else "▶ 播放")

    # ------------------------------------------------------------------
    def _redraw_turbines(self, deform=False):
        """刷新各机组姿态。`deform=False` 时只改 user_matrix（几微秒）。

        柔性变形要重算 8000+ 顶点再叠扫塔间隙搜索，每帧做会把 30 fps 拖垮，而
        弯矩本来就只在控制步边界更新 —— 所以只在有新数据的那一帧算一次。
        """
        for i, (turb, yw) in enumerate(zip(self.turbines, self.source.yaws)):
            spin = self._spin[i] if isinstance(self._spin, np.ndarray) \
                else self._spin
            pit = 0.0
            if self._pitch is not None and np.isfinite(self._pitch[i]):
                pit = float(self._pitch[i])
            mf = me = None
            if deform and self._m_flap is not None:
                mf = self._m_flap[i]
                me = self._m_edge[i] if self._m_edge is not None else None
            update_turbine(turb, float(yw), float(spin), pit,
                           m_flap=mf, m_edge=me, flex_scale=self.flex_scale)
        if deform:
            clr = [t.get("clearance") for t in self.turbines]
            if all(c is not None for c in clr):
                self.hub.publish("clearance", np.asarray(clr, dtype=float))

    def _on_wake(self, U):
        """proxy 发 (nx,ny,1) 标量场；真实路径发 DisXYFrame（首帧顺带建网格）。"""
        if not isinstance(U, np.ndarray):                     # DisXYFrame
            if self.sgrid is None:
                self.sgrid, wake_actor = add_vtk_wake_plane(
                    self.plotter, U, self._wake_z,
                    u_free=getattr(self.source, "u_inf", None))
                self._share(wake_actor)     # 首帧才建，别漏了右视图
                self.status.setText(f"  真实尾流切面已接入 "
                                    f"{U.u.shape[1]}×{U.u.shape[0]}  ")
            else:
                self.sgrid["wind_speed"] = vtk_plane_scalars(U)
            return
        self.sgrid["wind_speed"] = U.flatten(order="F")

    def _on_rpm(self, rpm):
        self._rpm = np.asarray(rpm, dtype=float)

    def _on_pitch(self, p):
        """指令桨距。只有在拿不到实测（FLORIS 后端）时才用它驱动叶片几何。"""
        self._pitch_cmd = np.asarray(p, dtype=float)
        if self._pitch_meas is None:
            self._pitch = self._pitch_cmd

    def _on_pitch_meas(self, p):
        """avrSWAP(4) 回读的实际桨距 —— 叶片几何以它为准。

        被控时 `pitch` 通道发的是外部累加的指令值，和仿真里桨距实际到了哪儿是
        两回事：大风档基线控制器自己在顺桨，指令却可能还贴着 0（PitchRef 是
        下界，见 memory: wfcrl-control-channels）。用指令画叶片，14 m/s 下画面
        会显示"没变桨"，与遥测的功率限幅自相矛盾。
        """
        v = np.asarray(p, dtype=float)
        if np.isfinite(v).any():
            self._pitch_meas = v
            self._pitch = v

    def _advance_spin(self, dt):
        """按**真实流逝时间** dt(s) 推进方位角，1× 实时：rpm × 6 = °/s。

        这样各机组转速的差别（下游落在尾流里，转得慢）在画面上直接看得见。
        原来乘的是 `tick_ms`，但那个回调里还压着一整个 FAST.Farm 控制步（2~3 s
        墙钟），实际触发间隔远大于名义周期，于是转子既转得慢又一顿一顿 ——
        现在用 QElapsedTimer 的真实增量，和定时器有没有按时触发无关。
        FLORIS 后端没有传动链、测不到转速 ⇒ 退回 90°/s，纯示意。
        """
        if self._rpm is None or not np.any(np.isfinite(self._rpm)):
            base = self._spin if np.isscalar(self._spin) else 0.0
            self._spin = base + 90.0 * dt
            return
        rpm = np.where(np.isfinite(self._rpm), self._rpm, 0.0)
        base = self._spin if isinstance(self._spin, np.ndarray) \
            else np.zeros(len(rpm))
        self._spin = base + rpm * 6.0 * dt

    # ------------------------------------------------------------------
    def _render_tick(self):
        """只管画面：插值转子相位 + 刷新。不推进物理。"""
        self._collect()
        if not self._playing:
            return
        now = self._clock.nsecsElapsed()
        dt = (now - self._last_ns) / 1e9
        self._last_ns = now
        self._advance_spin(min(dt, 0.25))   # 卡一下别让转子一次跳过大半圈
        self._redraw_turbines(deform=self._m_dirty)
        self._m_dirty = False
        self._chase_camera()       # 锁定机位可能被拖走，每帧跟一次（纯算术，不贵）
        self.plotter.render()

    def _collect(self):
        """把后台控制步的发布搬到主线程（Qt 控件只能在这里碰）。"""
        fut = self._pending
        if fut is None or not fut.done():
            return
        self._pending = None
        try:
            fut.result()
        except Exception as e:                                  # noqa: BLE001
            print(f"[rviz] 控制步异常，停止推进: {e!r}", flush=True)
            self._playing = False
            self.timer.stop()
            return
        for name, data in self._buf.drain():
            self.hub.publish(name, data)
        self._m_dirty = True
        self._after_step()

    def _tick(self):
        """推进一个控制步。画面刷新不在这里 —— 由 _render_tick 独立跑。"""
        if self.sender() is self.timer and not self._playing:
            return
        if self._pending is not None:       # 上一步还没跑完，跳过这一拍
            return
        if self._pool is not None:
            self._pending = self._pool.submit(self.source.step, self._buf)
        else:
            self.source.step(self.hub)
            self._m_dirty = True
            self._after_step()

    def _after_step(self):
        if getattr(self.source, "done", False):
            self.status.setText(f"  回合结束 (step {self.source._t})  ")
            self._playing = False
            self.timer.stop()
        else:
            self.status.setText(f"  step {self.source._t}  ")

    def closeEvent(self, event):
        """关闭窗口时排空 FAST.Farm 迭代预算，避免子进程孤儿。"""
        self.timer.stop()
        self.rtimer.stop()
        if self._pool is not None:
            # 必须等在跑的控制步收尾：MPI 侧不能一边 step 一边 close
            self.status.setText("  等待当前控制步结束…  ")
            QtWidgets.QApplication.processEvents()
            self._pool.shutdown(wait=True)
            self._pending = None
        if hasattr(self.source, "close"):
            self.status.setText("  正在安全关闭 FAST.Farm…  ")
            QtWidgets.QApplication.processEvents()
            self.source.close()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    def _wire_drag(self):
        """左键点中机组→拖拽；地面拾取器求鼠标落点；松开回填 FLORIS。"""
        import vtk
        self._prop_picker = vtk.vtkPropPicker()
        self._cell_picker = vtk.vtkCellPicker()
        self._cell_picker.SetTolerance(0.005)
        self._turb_actors = {}              # actor -> turbine idx
        for i, t in enumerate(self.turbines):
            for a in turbine_actors(t):     # 真实几何下三片叶片是独立 actor
                self._turb_actors[a] = i

        iren = self.plotter.iren.interactor
        self._tag_press = iren.AddObserver("LeftButtonPressEvent",
                                           self._on_press, 10.0)
        self._tag_move = iren.AddObserver("MouseMoveEvent", self._on_move, 10.0)
        self._tag_release = iren.AddObserver("LeftButtonReleaseEvent",
                                             self._on_release, 10.0)

    @staticmethod
    def _abort(obj, tag):
        """吞掉本次事件，阻止它再传给 VTK 默认 trackball 样式。

        不吞的话，拖机组的同时相机也会跟着转——两个交互叠在一起。
        点空白处不吞，轨道/缩放/平移保持原生行为。
        """
        cmd = obj.GetCommand(tag) if obj is not None else None   # None = 脚本调用
        if cmd is not None:
            cmd.SetAbortFlag(1)

    def _mouse_xy(self):
        return self.plotter.iren.interactor.GetEventPosition()

    def _poked_renderer(self):
        """鼠标当前所在的 renderer。

        双视图下**不能**用 `plotter.renderer`（那是 active renderer，与光标位置
        无关）：在右视图里按下，会拿左视图的相机去解算射线，机组瞬移到别处。
        """
        if not self.dual:
            return self.plotter.renderer
        x, y = self._mouse_xy()
        return self.plotter.iren.interactor.FindPokedRenderer(x, y)

    def _on_press(self, obj, evt):
        x, y = self._mouse_xy()
        ren = self._poked_renderer()
        # 只允许在世界视图里拖：随机镜头是观察窗，在那儿拖会把机位甩到镜头轴上
        if self.dual and ren is not self.plotter.renderers[0]:
            return
        self._prop_picker.Pick(x, y, 0, ren)
        actor = self._prop_picker.GetActor()
        if actor in self._turb_actors:
            self._drag_idx = self._turb_actors[actor]
            self.status.setText(f"  拖拽 T{self._drag_idx+1} …  ")
            self._abort(obj, self._tag_press)

    def _on_move(self, obj, evt):
        if self._drag_idx is None:
            return
        self._abort(obj, self._tag_move)
        x, y = self._mouse_xy()
        ren = self._poked_renderer()
        if self._cell_picker.Pick(x, y, 0, ren):
            wx, wy, _ = self._cell_picker.GetPickPosition()
            i = self._drag_idx
            self.source.lx[i], self.source.ly[i] = wx, wy
            # 只移动 3D actor（轻量），松开再回填 FLORIS 重算
            t = self.turbines[i]
            move_turbine_actors(t, wx - t["x"], wy - t["y"])
            self._redraw_turbines()
            self.plotter.render()

    def _on_release(self, obj, evt):
        if self._drag_idx is None:
            return
        self._abort(obj, self._tag_release)
        i = self._drag_idx
        self._drag_idx = None
        self.status.setText(f"  T{i+1} 重定位，重算尾流…  ")
        self.source.move_turbine(i, self.source.lx[i], self.source.ly[i])
        # 布局变了 → FLORIS 的水平切面采样域也跟着变，必须连网格点一起换。
        # 只换标量的话，新的速度场会被贴到旧几何上，尾流位置整体错位。
        X, Y, Z, U = extract_wake_plane(self.source.fi, self.source.hub_h,
                                        self.source._yaw_angles)
        self.sgrid.points = np.column_stack([X.ravel(order="F"),
                                             Y.ravel(order="F"),
                                             Z.ravel(order="F")])
        self._on_wake(U)
        self.status.setText(f"  T{i+1} 已重定位  ")


# ---------------------------------------------------------------------------
def make_policy_fn(kind):
    """FLORIS 后端的策略包装（保持原行为）。"""
    if kind != "auto":
        print(f"[rviz] FLORIS 策略模式 '{kind}' 暂为 TODO（需同步双 FLORIS），回退 auto。")
    return "auto"


def make_fastfarm_policy_fn(ckpt_path):
    """加载 train_fastfarm.py 存的 MAPPO actor，返回 source->偏航增量(n,)。

    actor 的动作本身就是"偏航增量"，与 FastFarmDriver.step 的入参语义一致，直接返回。
    """
    import torch
    from wfrl.mappo_central import Actor

    # 允许只给文件名：ckpt 都在 results/checkpoints/ 下，且本模块启动时已 chdir 到
    # 仓库根，裸相对路径会相对根解析而非用户的 cwd —— 兜底查一次 paths.CKPT。
    if not os.path.exists(ckpt_path):
        cand = os.path.join(paths.CKPT, os.path.basename(ckpt_path))
        if os.path.exists(cand):
            ckpt_path = cand
        else:
            raise FileNotFoundError(
                f"找不到 ckpt: {ckpt_path}\n"
                f"也不在 {paths.CKPT} 下。可用的有:\n  "
                + "\n  ".join(sorted(f for f in os.listdir(paths.CKPT)
                                     if f.endswith(".pt"))))

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    obs_keys = ckpt.get("obs_keys", ["yaw", "wind_speed", "wind_direction"])
    actor = Actor(ckpt["obs_dim"], ckpt["act_dim"])
    actor.load_state_dict(ckpt["actor"])
    actor.eval()
    mean, var = ckpt["obs_mean"], ckpt["obs_var"]

    def fn(source):
        m = source._m
        obs = np.stack([np.asarray(m[k], dtype=np.float32) for k in obs_keys],
                       axis=1)                          # (n, obs_dim)
        obs_n = ((obs - mean) / np.sqrt(var + 1e-8)).astype(np.float32)
        with torch.no_grad():
            mu = actor.mu(torch.as_tensor(obs_n))       # 确定性动作=增量
        return mu.cpu().numpy().ravel()

    print(f"[rviz] 已加载 FAST.Farm MAPPO 策略: {ckpt_path}", flush=True)
    return fn


def _run_headless(args):
    """不开 Qt 窗口，直接跑数据源并打印遥测。

    FAST.Farm 那一路要 mpiexec 起，而 GUI 在 CI/远端不一定有显示；自检用这条路
    能验证「pitch/torque 指令进得去、转速反解出得来」而不依赖窗口。
    """
    env_id = args.env or ("Dec_Turb3_Row1_Fastfarm" if args.backend == "fastfarm"
                          else "Ablaincourt_Floris")
    # 经 mpiexec 的管道是块缓冲，启动那 ~25s 不主动 flush 就一个字都不出，
    # 看着和「起不来」一模一样 —— 每条状态都显式 flush。
    if args.backend == "fastfarm":
        wake = "真实 DisXY 切面" if args.wake_vtk else "FLORIS proxy"
        infl = f"湍流 {args.turb}" if args.turb else (
            f"均匀 {args.wind_speed} m/s" if args.wind_speed else "均匀(随机抽样)")
        print(f"[rviz] (1/2) 启动 FAST.Farm（尾流={wake}，来流={infl}，约 10-25 秒）… "
              f"env={env_id} controls={args.controls}", flush=True)
        src = FastFarmSource(env_id=env_id, policy="auto",
                             max_steps=max(args.max_steps, args.headless + 2),
                             controls=tuple(args.controls.split(",")),
                             wake_vtk=args.wake_vtk, wake_vtk_z=args.wake_z,
                             wind_time_series=args.turb,
                             wind_speed=args.wind_speed,
                             pitch_demo=args.pitch_demo)
        print("[rviz] (2/2) 数据源就绪，开始步进；Ctrl-C 可随时终止。", flush=True)
    else:
        src = FarmSource(env_id=env_id, policy="auto",
                         wind_speed=args.wind_speed,
                         wind_direction=getattr(args, "wind_dir", None))
    hub = ChannelHub()
    box = {}
    for t in ("yaw", "pitch", "pitch_meas", "torque", "rotor_speed",
              "power", "m_flap"):
        hub.subscribe(t, lambda d, k=t: box.__setitem__(k, np.asarray(d, float)))
    # 尾流单独订阅：真实路径发的是 DisXYFrame（不是 ndarray），且**可能整步没有新帧**
    # —— 这正是要在无窗口下核对的东西，所以记帧号与最深亏损。
    wk = {}
    hub.subscribe("wake", lambda fr: wk.update(
        {} if isinstance(fr, np.ndarray)
        else {"t": fr.t, "min": float(np.nanmin(fr.u)),
              "shape": fr.u.shape, "n": wk.get("n", 0) + 1}))
    # 净空要真实叶片几何才能算；解析不出来（比如没装模板）就只是少一列
    try:
        from wfrl.viz.geometry import load_turbine_geometry
        _geo = load_turbine_geometry()
        _bl = _geo.blade_surface()
        _bp, _xi = _bl.points.copy(), np.asarray(_bl.point_data["xi"])
    except Exception as e:                                      # noqa: BLE001
        print(f"[rviz] 几何解析失败，净空列关闭: {e!r}", flush=True)
        _geo = None
    print("step |   yaw(deg)   | 桨距指令(deg) | 桨距实测(deg) | torque(kNm)"
          " |  rpm  |  P(MW)"
          " | 转子测速(m/s) | T1 三片叶尖挠度(m) | T1 扫塔净空(m)", flush=True)
    # 循环体里任何异常都必须走到 src.close()：FAST.Farm 在 t=0 拿到固定迭代预算，
    # 不排空就会卡在 MPI_RECV 变孤儿，而孤儿会让**下一次** spawn 无声挂死（见
    # memory: wfcrl-fastfarm-bringup）。实测一次 UnboundLocalError 就留下一个。
    try:
        _headless_loop(args, src, hub, box, wk, _geo, _bp, _xi)
    finally:
        if hasattr(src, "close"):
            src.close()
    print("HEADLESS_OK", flush=True)
    return 0


def _report_inflow(src, ws):
    """收尾报一次来流：自由来流、盒子 TI、以及转子处测速的**时间**波动。

    转子测速的时间标准差是判断湍流有没有真的进来的判据：均匀来流（WindType=1）下
    它只随尾流建立缓慢漂移，湍流盒子（WindType=3）下每步都在抖。注意这个 std 不是
    盒子的 TI —— 采样只有每 3 s 一点、且已被转子平均过，必然比 TI 小。

    FLORIS 源没有 `u_inf`/`last_wind_speed`，整段都会是 nan：那不是"来流为 nan"，
    是这条后端不提供这两个量。直接说明并跳过，别打印 nan 也别让 nanstd 对空切片
    发 RuntimeWarning（一屏警告会盖住真正的输出）。
    """
    bts = getattr(getattr(src, "drv", None), "bts", None)
    u0 = getattr(src, "u_inf", float("nan"))
    if not np.isfinite(u0) and not np.isfinite(np.asarray(ws)).any():
        print("[rviz] 该后端不报自由来流/转子测速（FLORIS 源无此通道），跳过来流统计",
              flush=True)
        return
    line = f"[rviz] 自由来流 u_inf={u0:.2f} m/s"
    if bts is not None:
        line += (f"（.bts 轮毂层，TI={bts.ti:.1f}%，盒子 {bts.t_span:.0f}s，"
                 f"取 z={bts.z_used:.0f}m 层算 TI）")
    else:
        line += "（InflowWind HWindSpeed，均匀来流）"
    print(line, flush=True)
    if ws.ndim == 2 and len(ws) >= 2:
        sd = np.nanstd(ws, axis=0)
        print(f"[rviz] 转子处测速时间波动 std={np.array2string(sd, precision=3)} "
              f"m/s（均值 {np.array2string(np.nanmean(ws, axis=0), precision=2)}）",
              flush=True)


def _headless_loop(args, src, hub, box, wk, _geo, _bp, _xi):
    """遥测主循环（拆出来是为了让调用方用 try/finally 兜住 close）。"""
    ws = []
    for k in range(args.headless):
        src.step(hub)
        f = lambda a, p=2: np.array2string(box[a], precision=p,
                                           suppress_small=True)
        # 桨距两列必须分开看：`pitch` 是外部累加的指令（PitchRef 是**下界**），
        # `pitch_meas` 是 avrSWAP(4) 回读。只印指令的话 5 m/s 和 14 m/s 会打印
        # 出完全相同的一列 —— 因为 mdp 把被控量排除出 measures，指令根本不会
        # 被测量覆盖，那一列与风速无关。
        pm = " | " + f("pitch_meas") if "pitch_meas" in box else " | (无实测)"
        # 挠度 = 标定的仿射式作用在 T1 的三片叶根挥舞弯矩上（geometry.CALIB），
        # 和渲染器用的是同一个系数，所以这一列就是屏幕上叶片实际弯的量。
        defl = ""
        if "m_flap" in box and box["m_flap"].size:
            a, b = CALIB["flap"]
            mf3 = np.ravel(box["m_flap"])[:3]
            tip = a * mf3 + b
            defl = " | " + np.array2string(tip, precision=2)
            # 净空按 1x 真值算（渲染放大不参与），负值 = 已扫塔
            if _geo is not None:
                clr = [_geo.tower_clearance(_bp, _xi, m_flap=float(m))
                       if np.isfinite(m) else np.nan for m in mf3]
                defl += " | " + np.array2string(np.asarray(clr), precision=2)
        wake = (f" | vtk t={wk['t']} min_u={wk['min']:.2f}"
                if "t" in wk else (" | vtk 等待首帧" if args.wake_vtk else ""))
        # 转子处测速：湍流来流下它应逐步波动，均匀来流下基本是条直线 ——
        # 这是"湍流真的进来了"最直接的判据，所以逐步记下来。
        get_ws = getattr(src, "last_wind_speed", None)   # FLORIS 源没有这个方法
        ws.append(np.asarray(get_ws(), dtype=float) if get_ws
                  else np.array([np.nan]))
        print(f" {k+1:3d} | {f('yaw',1)} | {f('pitch')}{pm} | "
              f"{np.array2string(box['torque']/1e3, precision=1)} | "
              f"{f('rotor_speed')} | {f('power')} | "
              f"{np.array2string(ws[-1], precision=2, suppress_small=True)}"
              f"{defl}{wake}", flush=True)
        if getattr(src, "done", False):
            break
    _report_inflow(src, np.asarray(ws))
    if args.wake_vtk:
        if "t" in wk:
            u0 = float(getattr(src, "u_inf", np.nan) or np.nan)
            rel = (f"，末帧最深亏损 {100*(1-wk['min']/u0):.0f}%"
                   f"（相对自由来流 {u0:.2f} m/s）") if np.isfinite(u0) else ""
            print(f"[rviz] 真实切面共交付 {wk['n']} 帧，网格 {wk['shape']}{rel}",
                  flush=True)
        else:
            print("[rviz] 真实切面一帧都没到 —— 检查案例 vtk_ff/ 是否在写盘",
                  flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="floris", choices=["floris", "fastfarm"],
                    help="floris=稳态可拖拽；fastfarm=真实动态(需 mpiexec)")
    ap.add_argument("--env", default=None,
                    help="环境 id；默认按 backend 选 Ablaincourt_Floris / Dec_Turb3_Row1_Fastfarm")
    ap.add_argument("--model", default="auto",
                    help="auto 或 ckpt 路径(fastfarm) / ippo|central(floris)")
    ap.add_argument("--max-steps", type=int, default=200,
                    help="fastfarm 回合长度（FAST.Farm 迭代预算）")
    ap.add_argument("--controls", default="yaw",
                    help="逗号分隔的控制量：yaw[,pitch][,torque]（仅 fastfarm）。"
                         "torque 会完全替换基线变速控制器，默认边界 ±2e4 N·m "
                         "低于额定 ~4.3e4，谨慎开。")
    ap.add_argument("--pitch-demo", default="hold",
                    choices=("hold", "sweep", "off"),
                    help="外部桨距指令的形态（仅 --controls 含 pitch 时有意义）。"
                         "hold=阶跃保持，只在上游机组抬 3° 并保持一段，其余归零，"
                         "贴近真机（额定以下基线贴 0°、调节不频繁，运行区间 0~25°，"
                         "90° 是停机）；sweep=连续正弦，不真实但自检直观；"
                         "off=完全不给外部指令，画面上的桨距全部来自基线控制器。")
    ap.add_argument("--headless", type=int, default=0, metavar="N",
                    help="不开窗口，只跑 N 步并打印遥测（自检用）")
    ap.add_argument("--flex-scale", type=float, default=5.0, metavar="K",
                    help="叶片变形的**显示**放大倍数（不影响物理与遥测读数）。"
                         "1=真实挠度，0=关掉柔性回到刚体。仅 fastfarm 有弯矩通道。"
                         "会被自动压低到不穿塔筒为止（净空列给的仍是 1x 真值）。")
    ap.add_argument("--wake-vtk", action="store_true",
                    help="尾流平面用 FAST.Farm 真实 DisXY 切面（默认 FLORIS proxy）；"
                         "仅 fastfarm 后端，每控制步落一张盘 ~1 MB"
                         "（3T 布局实测，200 步回合 ≈ 200 MB）")
    ap.add_argument("--wake-z", type=float, default=90.0, metavar="M",
                    help="真实切面的高度 (m)，默认 90 = 轮毂高度")
    ap.add_argument("--turb", nargs="?", const="90m_08mps.bts", default=None,
                    metavar="BTS",
                    help="湍流来流（WindType=3，TurbSim .bts）。不给值用模板自带的 "
                         "90m_08mps.bts（u_hub 8.00 m/s, TI 9%%, 仅 200 s）。"
                         "开了它来流由盒子决定，--wind-speed 失效")
    ap.add_argument("--wind-speed", type=float, default=None, metavar="MPS",
                    help="钉死均匀来流 (m/s)；不给则每次 reset 重抽 Weibull，"
                         "而 P∝u³ ⇒ 前后对照会变成比两次抽签")
    ap.add_argument("--wind", default=None, metavar="NAME",
                    help="内置风况预设，一键切换小风/大风（覆盖 --wind-speed/--turb）。"
                         "可选见 --list-wind")
    ap.add_argument("--list-wind", action="store_true",
                    help="列出内置风况预设后退出")
    ap.add_argument("--render-ms", type=int, default=33, metavar="MS",
                    help="画面刷新周期（默认 33≈30fps）。物理步进已与渲染解耦，"
                         "转子按真实流逝时间以 1x 实时匀速转。")
    ap.add_argument("--terrain", choices=("mountains", "gobi", "flat"),
                    default=None,
                    help="背景地形（山地/戈壁/平地）。**纯装饰，不参与流场计算** —— "
                         "本算例 FAST.Farm 地面是平的、轮毂高度是绝对高度；"
                         "地形已压平塔基并在切面覆盖区限高，不会暗示地形效应")
    ap.add_argument("--dual-view", action="store_true",
                    help="Gazebo 式双视图：左「世界」自由轨道，右「随机镜头」贴着"
                         "一台机组看叶片。两个视图共享 actor，不额外建几何")
    ap.add_argument("--focus", type=int, default=0, metavar="I",
                    help="随机镜头锁定第几台（0 起，默认 T1）")
    args = ap.parse_args()

    from wfrl import windcond
    if args.list_wind:
        print("内置风况预设（--wind NAME）：\n" + windcond.describe())
        print("\n各档看点：")
        for k in windcond.ORDER:
            print(f"  {k}: {windcond.PRESETS[k]['note']}")
        return 0
    if args.wind:
        ws, wd, turb = windcond.resolve(args.wind, backend=args.backend)
        args.wind_speed, args.wind_dir, args.turb = ws, wd, turb
        p = windcond.PRESETS[args.wind]
        print(f"[风况] {args.wind} — {p['label']}\n        {p['note']}",
              flush=True)
    else:
        args.wind_dir = None

    if args.headless:
        return _run_headless(args)

    app = QtWidgets.QApplication(sys.argv)

    if args.backend == "fastfarm":
        env_id = args.env or "Dec_Turb3_Row1_Fastfarm"
        wake = "真实 DisXY 切面" if args.wake_vtk else "FLORIS proxy"
        print(f"[rviz] (1/4) 正在启动 FAST.Farm（尾流={wake}，约 10 秒）…",
              flush=True)
        source = FastFarmSource(env_id=env_id, policy="auto",
                                max_steps=args.max_steps,
                                controls=tuple(args.controls.split(",")),
                                wake_vtk=args.wake_vtk,
                                wake_vtk_z=args.wake_z,
                                wind_time_series=args.turb,
                                wind_speed=args.wind_speed,
                                pitch_demo=args.pitch_demo)
        print("[rviz] (2/4) 数据源就绪，正在建 3D 窗口…", flush=True)
        policy_fn = (None if args.model == "auto"
                     else make_fastfarm_policy_fn(args.model))
        # FAST.Farm 一个 3 s 控制步要 2~3 s 墙钟，扔到后台线程去跑（async_step），
        # 主线程只负责按 render_ms 插值转子相位；布局固定 ⇒ 禁用拖拽。
        # tick_ms 只是"多久看一眼能不能开下一步"，实际节奏由 FAST.Farm 决定。
        win = MainWindow(source, policy_fn=policy_fn,
                         allow_drag=False, tick_ms=100,
                         flex_scale=args.flex_scale,
                         render_ms=args.render_ms, async_step=True,
                         terrain_kind=args.terrain,
                         dual_view=args.dual_view, focus=args.focus)
        print("[rviz] (3/4) 窗口已创建，弹出中。若没看到，请看任务栏 / Alt+Tab。",
              flush=True)
    else:
        env_id = args.env or "Ablaincourt_Floris"
        source = FarmSource(env_id=env_id, policy="auto",
                            wind_speed=args.wind_speed,
                            wind_direction=args.wind_dir)
        policy_fn = None if args.model == "auto" else make_policy_fn(args.model)
        # FLORIS 后端没有弯矩通道 ⇒ 柔性用不上，叶片保持刚体
        # FLORIS 后端没有弯矩通道 ⇒ 柔性用不上，叶片保持刚体。稳态求解够快，
        # 同步步进即可（拖拽也要在主线程改 fi，异步会打架）。
        win = MainWindow(source, policy_fn=policy_fn, allow_drag=True,
                         tick_ms=120, flex_scale=0.0,
                         render_ms=args.render_ms,
                         terrain_kind=args.terrain,
                         dual_view=args.dual_view, focus=args.focus)

    win.show()
    win.raise_()
    win.activateWindow()
    if args.backend == "fastfarm":
        print("[rviz] (4/4) 已进入事件循环，窗口应已在前台。关闭窗口即安全退出。",
              flush=True)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
