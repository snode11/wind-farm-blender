"""读取主场景姿态的单风机观察窗口。"""
from __future__ import annotations

import logging

import numpy as np
from pyvistaqt import QtInteractor
from qtpy import QtCore, QtWidgets

from wfrl.scene.schema import Scene
from wfrl.studio import channels as _channels


_LOG = logging.getLogger("wfrl.studio.app")


class TurbineWindow(QtWidgets.QMainWindow):
    """单风机弹出窗口：只画选中的那台，贴身镜头看叶片扭角/变桨/变形。

    PyVista 的 actor 绑在创建它的 render window 上，不能跨窗口共享 —— 所以这里
    **自己建一份**该机组的几何，每帧从主窗口的共享姿态（_yaw/_pitch/_spin）读值
    重绘。它不持有任何物理，纯画面。
    """

    def __init__(self, parent, scene: Scene, view, idx: int):
        super().__init__(parent)
        self.scene = scene
        self.src_view = view          # 主窗口的 SceneView，姿态的唯一事实来源
        self.idx = int(idx)
        self.turb = None
        self._ready = False           # GL 上下文可用（窗口已 exposed）才置真
        self.setWindowTitle(f"单风机视图 — {scene.turbines[self.idx].id}")
        self.resize(720, 720)

        self.plotter = QtInteractor(self)
        self.setCentralWidget(self.plotter.interactor)

        pick = QtWidgets.QComboBox()
        pick.addItems([tt.id for tt in scene.turbines])
        pick.setCurrentIndex(self.idx)
        pick.currentIndexChanged.connect(self._switch)
        tb = self.addToolBar("t")
        tb.addWidget(QtWidgets.QLabel(" 机组 "))
        tb.addWidget(pick)
        self._pick = pick

        # 相机下拉：自由观察 + 场景里每一路 camera 传感器。选传感器相机就把视口
        # 切到它的安装位姿+FOV —— 看到的是这台相机拍到的实时叶片。相机项存
        # (sensor, 本窗口机组在该 sensor.indices 里的下标)。
        self._cams = self._collect_cameras(scene, view)
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" 相机 "))
        self._cam_pick = QtWidgets.QComboBox()
        self._cam_pick.addItem("自由观察")
        for label, _s, _si in self._cams:
            self._cam_pick.addItem(label)
        self._cam_pick.currentIndexChanged.connect(self._on_cam_changed)
        tb.addWidget(self._cam_pick)

        # FOV / 俯仰：自由观察时是观察镜头参数；选了传感器相机时微调该相机的
        # fov/pitch（短焦↔长焦、对准叶尖一眼可比）。
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" FOV "))
        self._fov = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._fov.setRange(5, 90)           # 度；5°≈长焦看局部，90°≈广角看整叶
        self._fov.setValue(30)
        self._fov.setFixedWidth(130)
        self._fov.valueChanged.connect(self._set_fov)
        tb.addWidget(self._fov)
        self._fov_lbl = QtWidgets.QLabel(" 30° ")
        tb.addWidget(self._fov_lbl)
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" 俯仰 "))
        self._pitch = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._pitch.setRange(-90, 30)       # 度；负=向下俯视叶片
        self._pitch.setValue(-30)
        self._pitch.setFixedWidth(130)
        self._pitch.valueChanged.connect(self._set_pitch)
        tb.addWidget(self._pitch)
        self._pitch_lbl = QtWidgets.QLabel(" -30° ")
        tb.addWidget(self._pitch_lbl)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._refresh)

    def showEvent(self, ev):
        """几何/镜头/定时器全部推迟到窗口真正显示之后再建。

        Windows 上在 .show() 之前往 QtInteractor 的 OpenGL 上下文里画东西，
        会 `wglMakeCurrent failed ... 句柄无效` —— 那时 GL 句柄还没生效。
        exposed 之后 GL 才可用，所以第一次 showEvent 里才建几何、起定时器。
        """
        super().showEvent(ev)
        if self._ready:
            return
        try:
            self.plotter.clear()
            self.turb = None
            self.plotter.set_background("white")
            self._build(self.idx)
            self.plotter.add_axes(line_width=3)
        except Exception:                                    # noqa: BLE001
            _LOG.exception("单风机窗口初始化渲染失败")
            return
        self._ready = True
        self.timer.start(33)

    def _build(self, idx):
        """为第 idx 台建一份**真实尺度**几何并摆好镜头。actor 不跨窗口共享。

        真尺度（scale=1.0）而不是主视图的 TURB_SCALE=5：机舱 90 m、叶片 63 m、
        叶尖 0.5 m 都是真值 —— 这样"相机能不能拍到 0.5 m 叶尖"才是可信的验证。
        """
        from wfrl.viz.animate import build_turbine
        self.idx = int(idx)
        t = self.scene.turbines[self.idx]
        self.turb = build_turbine(self.plotter, t.x, t.y, self.scene.hub_height,
                                  scale=1.0)
        self._apply_view()
        self.setWindowTitle(f"单风机视图 — {t.id}")

    _collect_cameras = _channels._collect_cameras

    def _current_cam(self):
        """当前选中的相机 (sensor, si)，自由观察时返回 (None, None)。"""
        i = self._cam_pick.currentIndex() - 1     # 0 是"自由观察"
        if 0 <= i < len(self._cams):
            _label, s, si = self._cams[i]
            return s, si
        return None, None

    def _apply_view(self):
        """按当前相机选择摆镜头：传感器相机 → 切到它的位姿+FOV；否则自由观察。"""
        s, si = self._current_cam()
        if s is not None:
            self._apply_camera_pose(s, si)
        else:
            self._place_free_camera()

    def _apply_camera_pose(self, sensor, si):
        """把视口切到传感器相机的安装位姿+FOV（真实尺度）。看到实时叶片。"""
        from wfrl.viz.animate import update_turbine
        ti = sensor.indices[si]
        ctx = {"yaw": np.array([float(self.src_view._yaw[ti])])}
        # 滑块微调覆盖 sensor 的 fov/pitch，实时可比
        sensor.params["fov"] = float(self._fov.value())
        sensor.params["pitch"] = float(self._pitch.value())
        pos, focal, up, fov = sensor.camera_pose(ctx, si, scale=1.0)
        cam = self.plotter.camera
        cam.SetParallelProjection(False)
        cam.view_angle = float(fov)
        cam.position = tuple(pos)
        cam.focal_point = tuple(focal)
        cam.up = tuple(up)
        self.plotter.reset_camera_clipping_range()
        update_turbine(self.turb, float(self.src_view._yaw[ti]), 0.0, 0.0,
                       flex_scale=self.src_view.flex_scale)

    def _place_free_camera(self):
        """自由观察贴身镜头（真实尺度）：看点取轮毂，机位偏南抬高，FOV 按滑块。"""
        from wfrl.viz.animate import update_turbine
        hub_z = self.scene.hub_height
        r = 63.0                                  # 真实转子半径
        t = self.scene.turbines[self.idx]
        fx, fy = float(t.x), float(t.y)
        cam = self.plotter.camera
        cam.SetParallelProjection(False)
        cam.view_angle = float(self._fov.value())
        dirv = np.array([-2.2, -3.4, 1.1])
        dirv = dirv / np.linalg.norm(dirv)
        d = 4.0                                   # 4R 距离，看清整机
        cam.focal_point = (fx, fy, hub_z)
        cam.position = (fx + dirv[0] * d * r, fy + dirv[1] * d * r,
                        hub_z + dirv[2] * d * r)
        cam.up = (0.0, 0.0, 1.0)
        self.plotter.reset_camera_clipping_range()
        update_turbine(self.turb, 0.0, 0.0, 0.0,
                       flex_scale=self.src_view.flex_scale)

    def _place_camera(self):
        """兼容旧名：等价 _apply_view。"""
        self._apply_view()

    def _on_cam_changed(self, _i):
        s, _si = self._current_cam()
        # 切到传感器相机时把滑块同步成它的当前 fov/pitch，便于从真值起调
        if s is not None:
            self._fov.blockSignals(True)
            self._pitch.blockSignals(True)
            self._fov.setValue(int(s.params.get("fov", 40)))
            self._pitch.setValue(int(s.params.get("pitch", -30)))
            self._fov.blockSignals(False)
            self._pitch.blockSignals(False)
            self._fov_lbl.setText(f" {self._fov.value()}° ")
            self._pitch_lbl.setText(f" {self._pitch.value()}° ")
        if self._ready and self.turb is not None:
            try:
                self._apply_view()
                self.plotter.interactor.repaint()
            except Exception:                        # noqa: BLE001
                _LOG.exception("单风机窗口切换相机失败")

    def _set_fov(self, deg):
        """FOV → view_angle，顺带显示等效焦距（35mm 全画幅示意）。"""
        f = 18.0 / max(np.tan(np.deg2rad(deg) / 2.0), 1e-3)   # 示意焦距 mm
        self._fov_lbl.setText(f" {deg}° ≈{f:.0f}mm ")
        if self._ready and self.turb is not None:
            try:
                self._apply_view()
                self.plotter.interactor.repaint()
            except Exception:                        # noqa: BLE001
                _LOG.exception("单风机窗口设置相机 FOV 失败")

    def _set_pitch(self, deg):
        self._pitch_lbl.setText(f" {deg}° ")
        if self._ready and self.turb is not None:
            try:
                self._apply_view()
                self.plotter.interactor.repaint()
            except Exception:                        # noqa: BLE001
                _LOG.exception("单风机窗口设置相机俯仰失败")

    def _switch(self, i):
        """换一台：重建几何（换 x/y），重摆镜头。窗口没就绪时只记下选择。"""
        if not self._ready or self.turb is None:
            self.idx = int(i)
            return
        from wfrl.viz.animate import turbine_actors
        try:
            for a in turbine_actors(self.turb):
                self.plotter.remove_actor(a)
            self._build(int(i))
        except Exception:                                    # noqa: BLE001
            _LOG.exception("单风机窗口切换机组失败")

    def _refresh(self):
        """从主窗口共享姿态重绘这台。姿态是主窗口那份的唯一事实。

        渲染异常记录类型、信息和堆栈，保持事件循环可继续关闭窗口。
        连续相同异常只记录一次；异常变化或刷新恢复后再次失败时重新记录。
        """
        if not self._ready or self.turb is None:
            return
        from wfrl.viz.animate import update_turbine
        v = self.src_view
        i = self.idx
        try:
            update_turbine(self.turb, float(v._yaw[i]), float(v._spin[i]),
                           float(v._pitch[i]), flex_scale=v.flex_scale)
            # 传感器相机随机组偏航转 —— 机舱转了，装在上面的相机跟着转。
            s, si = self._current_cam()
            if s is not None:
                ctx = {"yaw": np.array([float(v._yaw[i])])}
                pos, focal, up, fov = s.camera_pose(ctx, si, scale=1.0)
                cam = self.plotter.camera
                cam.position = tuple(pos)
                cam.focal_point = tuple(focal)
                cam.up = tuple(up)
                self.plotter.reset_camera_clipping_range()
            # QVTKRenderWindowInteractor 基类是 QWidget，VTK 绘制到原生子窗口。
            # repaint() 同步触发 paintEvent，让 VTK 在本窗口完成重绘。
            self.plotter.render()            # 更新场景/相机状态
            self.plotter.interactor.repaint()   # 强制把它画到本窗口
            if not getattr(self, "_diag", False):
                self._diag = True
                it = self.plotter.interactor
                print(f"[turbine-win] interactor={type(it).__name__} "
                      f"visible={it.isVisible()} size={it.width()}x{it.height()}",
                      flush=True)
        except Exception as e:                               # noqa: BLE001
            error = (type(e), str(e))
            if getattr(self, "_refresh_error", None) != error:
                self._refresh_error = error
                _LOG.exception("单风机窗口刷新失败")
        else:
            self._refresh_error = None

    def closeEvent(self, ev):
        # 先停定时器再做别的：即便下面出错，也不能留一个还在 render 的定时器，
        # 否则窗口关不掉（正是坏上下文反复抛异常时的表现）。
        self._ready = False
        self.timer.stop()
        try:
            self.plotter.close()
        except Exception:                                    # noqa: BLE001
            _LOG.exception("单风机窗口关闭渲染器失败")
        p = self.parent()
        if p is not None and hasattr(p, "_on_turbine_window_closed"):
            p._on_turbine_window_closed()
        super().closeEvent(ev)
