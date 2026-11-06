"""Studio 主窗口布局、控件接线与 Qt 关闭事件。"""
from __future__ import annotations

from typing import Optional

from pyvistaqt import QtInteractor
from qtpy import QtCore, QtGui, QtWidgets

from wfrl.scene.schema import Scene
from wfrl.studio import channels as _channels
from wfrl.studio import lifecycle as _lifecycle
from wfrl.studio import rendering as _rendering
from wfrl.studio.panels import ChannelPanel, ControlPanel, SafetyPanel, ScenePanel, TrainPanel
from wfrl.studio.trainer import Trainer
from wfrl.studio.view import SceneView


class StudioWindow(QtWidgets.QMainWindow):
    def __init__(self, scene: Scene, train_kw: Optional[dict] = None,
                 tick_ms: int = 120, render_ms: int = 33, wake: bool = True):
        super().__init__()
        self.scene = scene
        self._stopping = False
        self._stop_error = None
        self._close_pending = False
        self._stop_timer = QtCore.QTimer(self)
        self._stop_timer.setInterval(50)
        self._stop_timer.timeout.connect(self._poll_stop)
        self.setWindowTitle(
            f"WFRL Studio — {scene.name}  [{scene.backend} · "
            f"{scene.n} 台 · {'湍流盒' if scene.turbulence else '均匀来流'}]")
        self.resize(1560, 940)

        # shape=(1,2)：左视口是主场景，右视口留给相机视图。构造时就得定（QtInteractor
        # 建好后不能改 shape）。默认把右视口收成零宽 —— 只看到左边整场，和单视口一样；
        # 开「相机视口」时再把它拉出来。同一 render window ⇒ 不存在多窗口 GL 上下文
        # 那个刷不出的坑（独立弹窗在本机 VTK 版本下 repaint 都救不活）。
        self.plotter = QtInteractor(self, shape=(1, 2))
        self.setCentralWidget(self.plotter.interactor)
        for r in self.plotter.renderers:
            r.SetBackground(1.0, 1.0, 1.0)
        self.plotter.subplot(0, 0)          # SceneView 全建在左视口
        self.view = SceneView(self.plotter, scene, wake=wake)
        self.view.ensure_sensors()
        # 右视口是「相机传感器所见」——只由 FOV/俯仰滑块驱动，鼠标不该动它。
        # 关掉它的交互性：否则光标划过右视口时 trackball 会抓住 renderer[1] 拖动
        # 相机，而每帧 _apply_cam_pose 又把相机拽回传感器位姿，两边打架 ⇒ 拖动时
        # 在「拖出来的焦距」和「传感器焦距」之间反复闪。
        self.plotter.renderers[1].InteractiveOff()
        self._cam_on = False                # 右视口（相机视图）是否开
        self._cam_turb = None               # 右视口里那台真尺度机组的 actor 句柄
        self._cam_idx = 0
        self._cams = []                     # [(label, sensor, si)]
        self._set_right_viewport(0.0)       # 右视口收零，只显示左

        self.trainer = Trainer(scene, **(train_kw or {}))
        self.limiter = self.trainer.limiter
        self._sensors_attached = False
        self._pending = {}                 # topic -> 期望的订阅状态（训练起来前）
        self._demo_enabled = {}            # demo（无 runtime）下各通道的订阅状态

        self.scene_panel = ScenePanel(scene)
        self.channel_panel = ChannelPanel(self._toggle_channel)
        # 通道表不依赖 driver/MPI —— ensure_sensors() 已建好 view.sensors，
        # 先按它铺一份，demo 模式下也能立即勾选/取消相机、雷达等通道。
        if getattr(self.view, "sensors", None):
            self.channel_panel.populate_from_sensors(self.view.sensors)
        self.train_panel = TrainPanel(self._start, self._pause, self._stop)
        self.safety_panel = SafetyPanel()
        self.control_panel = ControlPanel(scene, self._manual_set,
                                          self._manual_clear)
        self.safety_panel.set_notes(
            self.limiter.notes(n_steps=self.trainer.episode_steps
                               or self.trainer.n_steps))
        self._dock("场景", self.scene_panel, QtCore.Qt.LeftDockWidgetArea)
        self._dock("数据通道", self.channel_panel, QtCore.Qt.LeftDockWidgetArea)
        self._dock("手动摆位", self.control_panel, QtCore.Qt.LeftDockWidgetArea)
        self._dock("训练", self.train_panel, QtCore.Qt.RightDockWidgetArea)
        self._dock("物理与安全约束", self.safety_panel,
                   QtCore.Qt.RightDockWidgetArea)

        self._build_menu()

        self.statusBar().showMessage("就绪 —— 点「开始训练」启动 FAST.Farm")
        self._paused = False
        self._last_step = -1
        self._clock = QtCore.QElapsedTimer()
        self._clock.start()
        self._last_ns = self._clock.nsecsElapsed()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(tick_ms)
        self.rtimer = QtCore.QTimer(self)
        self.rtimer.timeout.connect(self._render)
        self.rtimer.start(render_ms)

    def _dock(self, title, widget, area):
        d = QtWidgets.QDockWidget(title, self)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _build_menu(self):
        mb = self.menuBar()
        vm = mb.addMenu("视图")
        self.act_vortex = QtGui.QAction("尾流圆环（扩散包络）", self,
                                        checkable=True)
        self.act_vortex.setChecked(self.view._rings_armed)  # 默认预备开，转起来才现
        self.act_vortex.toggled.connect(self._toggle_vortex)
        vm.addAction(self.act_vortex)
        self.act_heatmap = QtGui.QAction("风况热力图", self, checkable=True)
        self.act_heatmap.setChecked(self.view._heatmap_on)
        self.act_heatmap.toggled.connect(self._toggle_heatmap)
        vm.addAction(self.act_heatmap)
        # 地形子菜单：无/山地/戈壁（单选），初始选中场景 YAML 的 terrain
        tm = vm.addMenu("地形")
        self._terr_group = QtGui.QActionGroup(self)
        self._terr_group.setExclusive(True)
        for label, kind in (("无", None), ("山地", "mountains"),
                            ("戈壁", "gobi")):
            a = QtGui.QAction(label, self, checkable=True)
            a.setChecked(self.scene.terrain == kind)
            a.triggered.connect(lambda _=False, k=kind: self._set_terrain(k))
            self._terr_group.addAction(a)
            tm.addAction(a)
        vm.addSeparator()
        for name, cam in (("俯视", "top"), ("侧视", "side"),
                          ("斜视", "iso")):
            a = QtGui.QAction(f"主相机：{name}", self)
            a.triggered.connect(lambda _=False, c=cam: self._set_camera(c))
            vm.addAction(a)

        # 相机传感器功能全部收到一个开关后面：打开 = 视锥框 + 右分屏视口 + 相机控件；
        # 关掉 = 全部收起，不干扰主流程。右视口在本机 VTK 下存在画面刷新限制，
        # 视锥框与控件可用。
        self.act_camera = QtGui.QAction("相机传感器", self, checkable=True)
        self.act_camera.toggled.connect(self._toggle_camera_master)
        vm.addSeparator()
        vm.addAction(self.act_camera)

        cm = mb.addMenu("控制")
        a = QtGui.QAction("全部释放手动摆位", self)
        a.triggered.connect(lambda: self._manual_clear(None, None))
        cm.addAction(a)

    _toggle_camera_master = _channels._toggle_camera_master

    _set_camera = _rendering._set_camera

    def _manual_set(self, topic, i, value):
        self.view.set_manual(topic, i, value)

    def _manual_clear(self, topic, i):
        self.view.clear_manual(topic, i)

    _toggle_vortex = _rendering._toggle_vortex
    _toggle_heatmap = _rendering._toggle_heatmap
    _set_terrain = _rendering._set_terrain
    _set_right_viewport = _rendering._set_right_viewport
    _toggle_camera_viewport = _rendering._toggle_camera_viewport
    _collect_cams = _channels._collect_cams
    _build_camera_toolbar = _rendering._build_camera_toolbar
    _build_right_turbine = _rendering._build_right_turbine
    _on_cam_pick = _rendering._on_cam_pick
    _on_cam_slider = _rendering._on_cam_slider
    _apply_cam_pose = _rendering._apply_cam_pose
    _update_right_turbine = _rendering._update_right_turbine
    _mark_right_dirty = _rendering._mark_right_dirty

    _start = _lifecycle._start
    _pause = _lifecycle._pause
    _stop = _lifecycle._stop
    _poll_stop = _lifecycle._poll_stop
    _toggle_channel = _channels._toggle_channel
    _tick = _lifecycle._tick
    _render = _lifecycle._render

    def closeEvent(self, ev):
        self._close_pending = True
        if self.trainer.running or self._stopping:
            self._stop()
            ev.ignore()
            return
        if self.trainer.runtime is not None:
            self._stop_error = self.trainer.error or '后端资源仍由 Trainer 持有'
            self.statusBar().showMessage('停止未确认：' + str(self._stop_error))
            ev.ignore()
            return
        if not self.view.close(timeout=0):
            self._stop_timer.start()
            self.statusBar().showMessage("停止中：等待显示求解线程退出…")
            ev.ignore()
            return
        self._stop_timer.stop()
        self.timer.stop()
        self.rtimer.stop()
        self.trainer.stop(timeout=0)
        super().closeEvent(ev)
