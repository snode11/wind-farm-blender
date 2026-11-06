"""RViz 三维场景、窗口交互与渲染定时器。"""
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor
from qtpy import QtCore, QtWidgets

from wfrl.viz.field import (add_wake_plane, extract_wake_plane,
                            add_vtk_wake_plane, vtk_plane_scalars)
from wfrl.viz.animate import (build_turbine, update_turbine, turbine_actors,
                              move_turbine_actors)
from wfrl.viz.geometry import load_turbine_geometry
from wfrl.viz.terrain import label_font as _cjk_font
from wfrl.viz.rviz_channels import ChannelHub, BufferHub
from wfrl.viz.rviz_panels import TelemetryPanel, ChannelPanel


# 机组显示放大倍数。整场跨数千米、机组只有百米量级，1:1 画出来是几根针。
# 凡是要和机组尺寸对齐的东西（取景包围盒、随机镜头的机位）都必须乘上它，
# 否则相机会按真实尺寸取景、却对着一台放大 5 倍的机器 —— 镜头埋进塔筒里。
TURB_SCALE = 5.0


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

