"""场景 → 3D 场景图。

这是 D1 验收的渲染那一半：机位、机型、传感器全部由场景文件决定，改 YAML 里的
坐标，画面和仿真一起变 —— 而不是像 `viz/rviz_app.py` 那样从 `env_id` 反查一张
预注册的常量表。

SceneView 只管**画**，不碰 Qt 事件循环、不碰 MPI：

    view = SceneView(plotter, scene)
    view.attach_sensors(runtime)          # 传感器自己的 3D 表现（lidar 点云等）
    view.update(frame, measure, dt=0.033) # 每帧调一次

通道开关走 `set_channel(topic, on)`：它同时管**可见性**和**采样**。只藏 actor
不停采样的话，关掉一路重的通道（lidar 视线插值、camera 离屏渲染）省不下任何
计算，"订阅"就退化成了一个显示复选框。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

import numpy as np
import pyvista as pv

from wfrl.viz.animate import build_turbine, turbine_actors, update_turbine

# 与 rviz_app.TURB_SCALE 同源：整场跨数千米、机组只有百米量级，1:1 画出来是
# 几根针。凡是要和机组尺寸对齐的东西（取景包围盒、相机机位）都必须乘上它。
TURB_SCALE = 5.0


def floris_proxy(scene):
    """同布局的 FLORIS 稳态求解器，仅用于渲染尾流平面。

    FAST.Farm 的真实扰动风切面要等它落盘（见 viz/wakevtk.py），起步阶段画面上
    什么都没有；proxy 让场景一建好就有尾流可看。物理上它是**稳态**解 —— 没有
    输运时延也没有蜿蜒，所以调用方必须在界面上标出来。

    走 `Scene` 自己的 floris 分支而不是 `env_id.replace("Fastfarm","Floris")`：
    后者只有预注册布局才有对应项，自定义场景根本查不到。
    """
    import copy
    from wfrl.viz.field import get_floris
    sc = copy.deepcopy(scene)
    sc.backend = "floris"
    sc.turbulence = None            # 湍流盒只有 fastfarm 支持，校验会拦
    sc.controls = ["yaw"]           # FLORIS 只实现了 yaw（interface.py:576-581）
    # 传感器必须清空：proxy 只画一张尾流平面，不出任何数据。留着的话
    # bladeload（requires=("aeroelastic",)）会在 floris 后端上校验失败，
    # 整个尾流平面被静默跳过 —— 画面上少一张图，却不知道为什么。
    sc.sensors = []
    env = sc.make_env(max_iter=10_000, log=False)
    env.reset()
    fi = get_floris(env)
    fi.reinitialize(wind_speeds=[float(scene.wind_speed)],
                    wind_directions=[270.0])
    return fi


class SceneView:
    """按场景建 3D 场景图，并逐帧更新。"""

    def __init__(self, plotter, scene, turb_scale: float = TURB_SCALE,
                 flex_scale: float = 5.0, wake: bool = True):
        self.plotter = plotter
        self.scene = scene
        self.turb_scale = float(turb_scale)
        self.flex_scale = float(flex_scale)
        self.fi = None
        self.sgrid = None
        self._sensor_actors: Dict[str, list] = {}
        self._sensor_of: Dict[str, Any] = {}
        self.sensors: list = []          # attach_sensors 填；TurbineWindow 读它列相机
        self._spin = np.zeros(scene.n)
        self._rpm = np.zeros(scene.n)
        self._pitch = np.zeros(scene.n)
        self._yaw = np.zeros(scene.n)
        self._m_flap = None
        self._m_edge = None
        # 手动姿态覆盖（纯几何，不进物理）：演示时直接摆某台机组的偏航/桨距/转速，
        # 用来看几何、相机视野、挂机方案。被锁定的量 on_frame 不再用仿真快照盖掉
        # —— 否则手动摆好的角度会在下一个控制步被仿真回读顶回去。key 形如
        # ("yaw", 2)，值是手动设定的量；训练与手动可并存（各锁各的机组/通道）。
        self._manual: Dict[tuple, float] = {}
        # 后台求解线程。主线程实测有两个百毫秒级的卡点，都在这儿卸掉：
        #   FLORIS 稳态解     0.24~0.36 s / 控制步（`_tick` 峰值 362 ms）
        #   叶片柔性变形求解  中位 192 ms、峰值 326 ms（`_redraw` 那一支）
        # 同期 GIL 交还正常（sleep 超时 < 100 ms），卡的是主线程自己在算，不是
        # 被工作线程攥住 —— 所以搬走计算就够，不用动锁的粒度。
        # 线程只产出纯 numpy，主线程只做赋值（`drain_*`，毫秒级）。
        self._solve_lock = threading.Lock()
        self._wake_req: Optional[tuple] = None       # (yaw, u_inf) 待算
        self._wake_out: Optional[np.ndarray] = None  # 算好的 U，等主线程取走
        self._deform_req: Optional[tuple] = None     # (m_flap, m_edge, pitch)
        self._deform_out: Optional[list] = None      # 逐机组的顶点，等主线程贴
        self._solve_evt = threading.Event()
        self._solve_stop = threading.Event()
        self._solve_thread: Optional[threading.Thread] = None
        self._ctx: Dict[str, Any] = {"u_inf": scene.wind_speed, "t": 0.0,
                                     "_turb_scale": self.turb_scale}

        plotter.set_background("white")
        self.wake_actor = None
        self._heatmap_on = False
        if wake:
            self._build_wake()
            self._heatmap_on = self.wake_actor is not None
        self.turbines = [build_turbine(plotter, t.x, t.y, scene.hub_height,
                                       scale=self.turb_scale)
                         for t in scene.turbines]
        # 尾流圆环（转子平面往下游扩散的包络，动画版）：默认「预备」开，
        # 但只有叶片真的在转（rpm>阈值）时才显示 —— 停转即隐。菜单可整体禁用。
        self._rings_armed = True                 # 用户/菜单意愿（是否允许出现）
        self._rings_on = False                   # 当前实际可见（受 rpm 门控）
        self._rings = []
        self._ring_phase = np.zeros(scene.n)     # 各台的下游滚动相位，按 rpm 累进
        # 地形（渲染装饰，从场景 YAML 的 terrain 字段读；菜单可临时改）
        self._terrain_actor = None
        self._terrain_label = None
        if scene.terrain:
            self.set_terrain(scene.terrain)
        b = self.bounds()
        # z 向量程只有百米、xy 有数千米，默认刻度数会让 z 标签叠成一团
        plotter.show_grid(color="gray", n_xlabels=4, n_ylabels=4,
                          n_zlabels=2, bounds=b)
        plotter.add_axes(line_width=3)
        plotter.camera_position = "yz"
        plotter.camera.azimuth = -60
        plotter.camera.elevation = 25
        plotter.reset_camera(bounds=b)
        self._redraw()

    # ---- 几何 ------------------------------------------------------------
    def draw_dims(self):
        """画面上机组的 (轮毂高度, 转子半径)，已乘 turb_scale 的**显示**尺寸。

        取景必须用这个而不是物理尺寸：机组按 5 倍画，拿 90/63 去摆镜头会把相机
        放进塔筒内部。遥测里给的仍是物理真值。
        """
        from wfrl.viz.geometry import load_turbine_geometry
        try:
            r = float(load_turbine_geometry().tip_rad)      # 63 m，ElastoDyn 真值
        except Exception:                                   # noqa: BLE001
            r = 0.5 * self.scene.rotor_diameter
        return self.scene.hub_height * self.turb_scale, r * self.turb_scale

    def bounds(self):
        """风场本身的包围盒，用来钉住网格与相机（与地形无关）。"""
        hub_z, r = self.draw_dims()
        x = np.asarray(self.scene.xcoords, float)
        y = np.asarray(self.scene.ycoords, float)
        return (float(x.min()) - 2.0 * r, float(x.max()) + 2.0 * r,
                float(y.min()) - 2.0 * r, float(y.max()) + 2.0 * r,
                0.0, hub_z + r)

    def _build_wake(self):
        from wfrl.viz.field import add_wake_plane
        try:
            self.fi = floris_proxy(self.scene)
        except Exception as e:                              # noqa: BLE001
            print(f"[view] FLORIS proxy 建不起来，尾流平面关闭: {e!r}", flush=True)
            return
        self.sgrid, self.wake_actor = add_wake_plane(
            self.plotter, self.fi, self.scene.hub_height, return_actor=True)
        # 切面按物理轮毂高度 90 m 解，但机组按 turb_scale 画、轮毂在 450 m ——
        # 不抬的话尾流会铺在塔基上，看着像地面上的一层雾。只抬**渲染**的 z，
        # 求解用的高度没变，色带和速度值都还是 90 m 层的真值。
        p = self.sgrid.points.copy()
        p[:, 2] = self.scene.hub_height * self.turb_scale
        self.sgrid.points = p

    # ---- 传感器 ----------------------------------------------------------
    def ensure_sensors(self):
        """没训练也把传感器建起来（相机/lidar 是几何，不需要 runtime/MPI）。

        Studio 只在训练起来后才 attach_sensors。但相机视锥、单风机窗口选相机
        这些不该等训练 —— 场景一加载就该能用。这里按场景 build 一份并 attach，
        幂等（已经有就不重复）。
        """
        if self.sensors:
            return
        from wfrl.channels import registry
        try:
            sensors = registry.build(self.scene)
        except Exception as e:                              # noqa: BLE001
            print(f"[view] 传感器 build 失败: {e!r}", flush=True)
            return
        self.attach_sensors(sensors)

    def attach_sensors(self, runtime, ctx: Optional[dict] = None):
        """给每个有 3D 表现的传感器建 actor。没有表现的（功率等）自然跳过。

        `runtime` 可以是 SceneRuntime，也可以直接给一串 Sensor —— 离线验收不该
        为了画一朵点云去起 MPI。
        """
        sensors = getattr(runtime, "sensors", runtime)
        ctx = dict(ctx or getattr(runtime, "last_ctx", None) or
                   {"u_inf": self.scene.wind_speed, "t": 0.0})
        ctx["_turb_scale"] = self.turb_scale
        self._ctx = ctx
        self.sensors = list(sensors)         # 存一份，TurbineWindow 要列相机
        for s in sensors:
            for topic in s.topics:           # 重复 attach（先 ensure 后训练）先清旧 actor
                for act, _mesh in self._sensor_actors.pop(topic, []):
                    try:
                        self.plotter.remove_actor(act)
                    except Exception:                        # noqa: BLE001
                        pass
            try:
                actors = s.make_actors(self.plotter, ctx)
            except Exception as e:                          # noqa: BLE001
                print(f"[view] 传感器 {s.type_name} 建 actor 失败: {e!r}", flush=True)
                continue
            if not actors:
                continue
            for topic in s.topics:
                self._sensor_actors[topic] = actors
                self._sensor_of[topic] = s

    def set_channel(self, topic: str, on: bool, runtime=None):
        """开关一路通道：同时管可见性与采样。

        `runtime` 给了就一起停采样 —— 关掉一路重的通道要省下真实计算，不然
        "订阅"只是个显示复选框。色带也要跟着收：留一条 "lidar u (m/s)" 在画面上，
        等于在截图里宣称那一路还在采。
        """
        if runtime is not None:
            runtime.set_enabled(topic, on)
        for act, _mesh in self._sensor_actors.get(topic, []):
            act.SetVisibility(bool(on))
        title = getattr(self._sensor_of.get(topic), "scalar_bar_title", None)
        # ScalarBars 是个 Mapping 子类但没有 .get，只能先查 keys()
        if title and title in self.plotter.scalar_bars.keys():
            self.plotter.scalar_bars[title].SetVisibility(bool(on))
        # 必须自己 render 一次：`screenshot()` 直接读帧缓冲，**不**触发重绘。
        # 少了这句，"关掉通道后截图"拿到的是上一帧 —— 图上点云还在，实测
        # 前后两张图逐像素完全相同。交互窗口里有渲染定时器兜着，出图没有。
        self.plotter.render()

    def has_actors(self, topic: str) -> bool:
        return bool(self._sensor_actors.get(topic))

    # ---- 手动姿态覆盖（纯几何，演示用）----------------------------------
    def set_manual(self, topic: str, i: int, value: float):
        """手动摆某台机组的一个姿态量并锁定它，立即重绘。不进物理。

        topic ∈ {"yaw","pitch","rpm"}。锁定后 on_frame 不会用仿真快照覆盖这台
        这个量 —— 训练在跑也没关系，各锁各的。转速走 _rpm，tick 会按它推进自转，
        所以设了转速叶片是真的转起来（rpm×6 = °/s，1× 实时）。
        """
        dst = {"yaw": "_yaw", "pitch": "_pitch", "rpm": "_rpm"}[topic]
        arr = np.array(getattr(self, dst), float)
        arr[int(i)] = float(value)
        setattr(self, dst, arr)
        self._manual[(topic, int(i))] = float(value)
        self._redraw()
        self.plotter.render()

    def clear_manual(self, topic: Optional[str] = None, i: Optional[int] = None):
        """解锁：给了 (topic,i) 解一个，给了 topic 解该通道全部，都不给全解。

        解锁只是让 on_frame 重新接管，不主动改回姿态 —— 下一帧仿真快照到了自然
        覆盖；没有训练在跑时就停在手动摆好的位置。
        """
        if topic is None:
            self._manual.clear()
        else:
            for k in [k for k in self._manual
                      if k[0] == topic and (i is None or k[1] == int(i))]:
                self._manual.pop(k, None)

    def is_manual(self, topic: str, i: int) -> bool:
        return (topic, int(i)) in self._manual

    def manual_locks(self):
        """当前锁定的 (topic, i) 集合，UI 拿去标记哪些量正被手动接管。"""
        return set(self._manual)

    # ---- 地形（渲染装饰，reuse wfrl/viz/terrain）------------------------
    def set_terrain(self, kind):
        """切换地形。kind ∈ {None,"mountains","gobi","flat"}。移旧建新。

        h_far 压到**放大后机组高度的一半**：机组按 turb_scale 画（塔顶数百米），
        山脊若也几百米，画面主体就从风场变成山。这是照 rviz_app 的取法。
        地形不参与流场（FAST.Farm 平地），只是背景 —— 自带"装饰"声明标签。
        """
        from wfrl.viz import terrain as terr
        if self._terrain_actor is not None:
            self.plotter.remove_actor(self._terrain_actor)
            self._terrain_actor = None
        if self._terrain_label is not None:
            self.plotter.remove_actor(self._terrain_label)
            self._terrain_label = None
        if not kind:
            self.plotter.render()
            return
        hub_z, _r = self.draw_dims()
        mesh = terr.build(self.scene.xcoords, self.scene.ycoords, kind=kind,
                          z_wake=self.scene.hub_height, h_far=0.5 * hub_z)
        self._terrain_actor = terr.add(self.plotter, mesh, kind=kind,
                                       label=False)
        self._terrain_label = terr.add_label(self.plotter)
        self.plotter.render()

    # ---- 风况热力图（现有轮毂层尾流速度面的开关）----------------------
    def set_heatmap(self, on: bool):
        """开关风况热力图 = 轮毂层速度色面 + 其色带。

        --no-wake 起的场景没建这张面，首次开时按需补建。关掉只是隐藏，不销毁
        （下次开免得重求解一次 FLORIS 稳态）。
        """
        on = bool(on)
        if on and self.wake_actor is None:
            self._build_wake()
        self._heatmap_on = on and self.wake_actor is not None
        if self.wake_actor is not None:
            self.wake_actor.SetVisibility(self._heatmap_on)
        if "U [m/s]" in self.plotter.scalar_bars.keys():
            self.plotter.scalar_bars["U [m/s]"].SetVisibility(self._heatmap_on)
        self.plotter.render()

    # ---- 尾流圆环（转子平面往下游扩散，动画合成量）------------------
    def set_wake_rings(self, on: bool):
        """菜单开关：是否**允许**尾流圆环出现。

        真正的可见性还要过 rpm 门控（叶片转才有、停转就隐）—— 见
        `_apply_ring_visibility`。这里只记录意愿并按当前转速刷一次。
        环是动画的：sceneView.tick 里按各台 rpm 累进相位，环向下游流动、扩散、
        渐隐。纯几何合成量，不进 reward/约束。
        """
        self._rings_armed = bool(on)
        rpm = np.where(np.isfinite(self._rpm), self._rpm, 0.0)
        self._apply_ring_visibility(rpm)
        self.plotter.render()

    def _apply_ring_visibility(self, rpm):
        """按转速门控圆环可见性：armed 且有一台在转(rpm>0.1)才显示。

        首次需要显示时才惰性建 actor（每台一个颜色）。停转 → 全部隐藏。
        """
        from wfrl.viz import wake_rings as wr
        spinning = bool(np.any(np.asarray(rpm, float) > 0.1))
        want = self._rings_armed and spinning
        if want and not self._rings:
            n = len(self.turbines)
            self._rings = [wr.build_wake_rings(
                self.plotter, color=wr.turbine_color(i, n))
                for i in range(n)]
        self._rings_on = want
        for h in self._rings:
            wr.set_visible(h, want)
        self._update_wake_rings()

    def _update_wake_rings(self):
        if not self._rings_on or not self._rings:
            return
        from wfrl.viz import wake_rings as wr
        _hub_z, r_tip = self.draw_dims()
        wd = float(self.scene.wind_direction)
        for i, (turb, h) in enumerate(zip(self.turbines, self._rings)):
            wr.update_wake_rings(h, turb["hub_c"], r_tip, wd, self.turb_scale,
                                 float(self._ring_phase[i]))

    # ---- 逐帧 ------------------------------------------------------------
    def on_frame(self, frame: Dict[str, Any], measure: Dict[str, Any],
                 ctx: Optional[dict] = None):
        """控制步边界：吃一帧数据。不刷新画面（那是 tick 的事）。

        机组**位姿**取 driver 原始测量而不是通道数据：把 bladeload 那一路关掉，
        叶片不该停在原地不转 —— 姿态是几何，不是订阅内容。通道数据只驱动
        传感器自己的 actor。
        """
        m = measure or {}
        for key, dst in (("yaw", "_yaw"), ("pitch_meas", "_pitch"),
                         ("rotor_speed", "_rpm")):
            v = m.get(key)
            if v is None:
                continue
            v = np.asarray(v, float).ravel()
            if v.size == self.scene.n:
                cur = getattr(self, dst)
                # 手动锁定的机组不被仿真快照覆盖；其余照常更新，nan 保持上一帧
                topic = {"_yaw": "yaw", "_pitch": "pitch", "_rpm": "rpm"}[dst]
                new = np.where(np.isfinite(v), v, cur)
                for i in range(self.scene.n):
                    if (topic, i) in self._manual:
                        new[i] = cur[i]
                setattr(self, dst, new)
        if m.get("m_flap") is not None:
            self._m_flap = np.asarray(m["m_flap"], float)
            self._m_edge = (np.asarray(m["m_edge"], float)
                            if m.get("m_edge") is not None else None)
            self._update_deform()

        # 尾流 proxy 跟着实测偏航走，否则画面上的尾流偏转与机组朝向对不上
        if self.fi is not None and self.sgrid is not None:
            self._update_wake((frame.get("_meta") or {}).get("u_inf"))

        for topic, actors in self._sensor_actors.items():
            if topic not in frame:
                continue                    # 抽稀跳过或通道已关，保持上一帧
            s = self._sensor_of[topic]
            c = dict(ctx or self._ctx)
            c["_turb_scale"] = self.turb_scale   # 缺了它点云会掉回物理尺度
            try:
                s.update_actors(actors, frame, c)
            except Exception as e:                          # noqa: BLE001
                print(f"[view] 传感器 {topic} 更新失败: {e!r}", flush=True)

    def _kick(self):
        """确保求解线程在跑。第一次排队时才起，无尾流无变形的场景就不起。"""
        self._solve_evt.set()
        if self._solve_thread is None:
            self._solve_thread = threading.Thread(
                target=self._solve_loop, name="wfrl-solve", daemon=True)
            self._solve_thread.start()

    def _update_wake(self, u_inf=None):
        """把一次尾流求解**排队**给求解线程，并取走上一次的结果。不阻塞。

        丢弃式排队：待算的请求只留最新一个。控制步 2~3 s、求解 0.3 s，正常情况
        下算得完；偶尔跟不上时丢掉中间帧比排队积压好 —— 画面要的是"当前偏航对应
        的尾流"，补一张三步前的没有意义。
        """
        if self.fi is None or self.sgrid is None:
            return
        with self._solve_lock:
            self._wake_req = (np.array(self._yaw, float),
                              None if u_inf is None else float(u_inf))
        self._kick()
        self.drain_wake()

    def _update_deform(self):
        """把一次柔性变形求解排队。同样是丢弃式：弯矩只在控制步边界更新。"""
        if self._m_flap is None:
            return
        with self._solve_lock:
            self._deform_req = (np.array(self._m_flap, float),
                                None if self._m_edge is None
                                else np.array(self._m_edge, float),
                                np.array(self._pitch, float))
        self._kick()

    def drain_wake(self) -> bool:
        """取走算好的 U 场（若有）。主线程调，只有一次数组赋值。

        渲染回调里也调一次：只在控制步边界取的话，尾流要等下一步才上屏，
        画面上就成了"偏航先动、尾流慢一拍"。
        """
        with self._solve_lock:
            out, self._wake_out = self._wake_out, None
        if out is None:
            return False
        self.sgrid["wind_speed"] = out
        return True

    def drain_deform(self) -> bool:
        """把算好的顶点贴到网格上。碰 VTK，只能主线程调。"""
        from wfrl.viz.animate import deform_apply
        with self._solve_lock:
            out, self._deform_out = self._deform_out, None
        if out is None:
            return False
        for turb, res in zip(self.turbines, out):
            deform_apply(turb, res)
        return True

    def _solve_loop(self):
        """求解线程：只碰 `self.fi` 与纯 numpy 几何，绝不碰 actor 或 mesh。"""
        from wfrl.viz.animate import deform_solve
        from wfrl.viz.field import extract_wake_plane
        while not self._solve_stop.is_set():
            self._solve_evt.wait(0.2)
            self._solve_evt.clear()
            with self._solve_lock:
                wreq, self._wake_req = self._wake_req, None
                dreq, self._deform_req = self._deform_req, None
            if self._solve_stop.is_set():
                break
            if wreq is not None:
                yaw, u_inf = wreq
                try:
                    ya = self.fi.floris.farm.yaw_angles * 0.0
                    ya[..., :] = yaw
                    # proxy 的来流要跟**自由来流**走，不能用全场测速均值：下游
                    # 机组测到的是尾流内速度，尾流一建立均值就往下掉，色带上限
                    # 跟着缩水。
                    if u_inf is not None and np.isfinite(u_inf) \
                            and abs(u_inf - self._ws0()) > 0.2:
                        self.fi.reinitialize(wind_speeds=[u_inf])
                    self.fi.calculate_wake(yaw_angles=ya)
                    _, _, _, U = extract_wake_plane(
                        self.fi, self.scene.hub_height, ya)
                    with self._solve_lock:
                        self._wake_out = U.flatten(order="F")
                except Exception as e:                      # noqa: BLE001
                    print(f"[view] 尾流求解失败（画面保持上一帧）: {e!r}",
                          flush=True)
            if dreq is not None:
                mf, me, pitch = dreq
                try:
                    res = [deform_solve(turb, mf[i],
                                        None if me is None else me[i],
                                        scale=self.flex_scale,
                                        pitch_deg=float(pitch[i]))
                           for i, turb in enumerate(self.turbines)]
                    with self._solve_lock:
                        self._deform_out = res
                except Exception as e:                      # noqa: BLE001
                    print(f"[view] 柔性变形求解失败（保持上一帧）: {e!r}",
                          flush=True)

    def close(self):
        """停掉求解线程。窗口关闭时调；不调也只是多一个 daemon 线程。"""
        self._solve_stop.set()
        self._solve_evt.set()
        t = self._solve_thread
        if t is not None and t.is_alive():
            t.join(5.0)
        self._solve_thread = None

    def _ws0(self):
        return float(np.ravel(self.fi.floris.flow_field.wind_speeds)[0])

    def tick(self, dt: float):
        """按**真实流逝时间** dt(s) 推进转子相位并重画，1× 实时：rpm × 6 = °/s。

        这样各机组转速的差别（下游落在尾流里，转得慢）在画面上直接看得见。
        物理步与渲染解耦：一个 3 s 控制步要 2~3 s 墙钟，压在渲染回调里就等于
        把帧率钉在 0.4 fps。
        """
        rpm = np.where(np.isfinite(self._rpm), self._rpm, 0.0)
        self._spin = self._spin + rpm * 6.0 * min(float(dt), 0.25)
        if self.sgrid is not None:
            self.drain_wake()
        self.drain_deform()
        self._redraw()
        # 圆环可见性跟着转速走：叶片开始转才冒出来，停转就隐（含起停切换）。
        self._apply_ring_visibility(rpm)
        if self._rings_on:
            from wfrl.viz.wake_rings import RING_SPEED
            self._ring_phase = (self._ring_phase
                                + RING_SPEED * rpm * min(float(dt), 0.25)) % 1.0
            self._update_wake_rings()

    def _redraw(self):
        """刷新各机组姿态：只改 user_matrix（几微秒）。

        叶片顶点不在这里算 —— 柔性变形要重算 8000+ 顶点再叠扫塔间隙搜索，实测
        中位 192 ms，摆在 30 fps 的回调里必然掉帧。求解在 `_solve_loop` 里做，
        这里只负责刚体姿态，顶点由 `drain_deform` 贴。
        """
        for i, turb in enumerate(self.turbines):
            update_turbine(turb, float(self._yaw[i]), float(self._spin[i]),
                           float(self._pitch[i]), flex_scale=self.flex_scale)

    def clearance(self):
        """三片叶掠塔时的最小间隙 (n,3)，**1× 真值**（不含显示放大）；没算过给 nan。"""
        out = []
        for t in self.turbines:
            c = t.get("clearance")
            out.append(np.full(3, np.nan) if c is None else np.asarray(c, float))
        return np.asarray(out)

    def actors(self):
        """场景里全部 actor（双视图共享时要逐个挂进第二个 renderer）。"""
        acts = []
        for t in self.turbines:
            acts += turbine_actors(t)
        if self.sgrid is not None:
            acts.append(self.wake_actor)
        for lst in self._sensor_actors.values():
            acts += [a for a, _ in lst]
        return acts
