"""Studio 主场景与右视口的渲染、相机和显示控制。"""
from __future__ import annotations

import numpy as np
from qtpy import QtCore, QtWidgets


def _set_camera(self, kind):
    b = self.view.bounds()
    fp = ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2)
    cam = self.plotter.camera
    cam.focal_point = fp
    cam.up = (0.0, 0.0, 1.0)
    span = max(b[1] - b[0], b[3] - b[2], b[5] - b[4])
    if kind == "top":
        cam.position = (fp[0], fp[1], fp[2] + 2.0 * span)
        cam.up = (0.0, 1.0, 0.0)
    elif kind == "side":
        cam.position = (fp[0], fp[1] - 2.0 * span, fp[2])
    else:                                    # iso
        cam.position = (fp[0] - span, fp[1] - span, fp[2] + span)
    self.plotter.reset_camera_clipping_range()
    self.plotter.render()


def _toggle_vortex(self, on):
    self.view.set_wake_rings(bool(on))


def _toggle_heatmap(self, on):
    self.view.set_heatmap(bool(on))


def _set_terrain(self, kind):
    self.view.set_terrain(kind)


def _set_right_viewport(self, frac):
    """右视口占屏宽度比例 frac∈[0,1]。0=收零（只显示左）。"""
    f = float(np.clip(frac, 0.0, 1.0))
    self.plotter.renderers[0].SetViewport(0.0, 0.0, 1.0 - f, 1.0)
    self.plotter.renderers[1].SetViewport(1.0 - f, 0.0, 1.0, 1.0)
    # 只重算左（主）视口的裁剪范围。右视口相机贴着转子平面，自动重算会把机组
    # 裁没（见 _apply_cam_pose）—— 它的裁剪范围在 _apply_cam_pose 里钉死，别在这
    # 里覆盖掉。相机开着时随后会走 _apply_cam_pose 把范围重新钉好。
    self.plotter.renderers[0].ResetCameraClippingRange()
    if not getattr(self, "_cam_on", False):
        self.plotter.renderers[1].ResetCameraClippingRange()


def _toggle_camera_viewport(self, on):
    self._cam_on = bool(on)
    if self._cam_on:
        self._collect_cams()
        if not self._cams:
            self.statusBar().showMessage(
                "场景里没有 camera 传感器 —— 在 YAML 的 sensors 加一路 "
                "{type: camera, ...}")
            self.act_camera.setChecked(False)
            self._cam_on = False
            return
        self._build_camera_toolbar()
        self._build_right_turbine()
        self._set_right_viewport(0.42)
        self.cam_toolbar.setVisible(True)
    else:
        self._set_right_viewport(0.0)
        if getattr(self, "cam_toolbar", None) is not None:
            self.cam_toolbar.setVisible(False)
    self.plotter.interactor.repaint()


def _build_camera_toolbar(self):
    if getattr(self, "cam_toolbar", None) is not None:
        return
    tb = self.addToolBar("相机")
    self.cam_toolbar = tb
    tb.addWidget(QtWidgets.QLabel(" 相机 "))
    self._cam_pick = QtWidgets.QComboBox()
    for label, _s, _si in self._cams:
        self._cam_pick.addItem(label)
    self._cam_pick.currentIndexChanged.connect(self._on_cam_pick)
    tb.addWidget(self._cam_pick)
    tb.addSeparator()
    tb.addWidget(QtWidgets.QLabel(" FOV "))
    self._cam_fov = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self._cam_fov.setRange(5, 90)
    self._cam_fov.setFixedWidth(130)
    self._cam_fov.valueChanged.connect(self._on_cam_slider)
    tb.addWidget(self._cam_fov)
    self._cam_fov_lbl = QtWidgets.QLabel("  ")
    tb.addWidget(self._cam_fov_lbl)
    tb.addSeparator()
    tb.addWidget(QtWidgets.QLabel(" 俯仰 "))
    self._cam_pitch = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self._cam_pitch.setRange(-90, 30)
    self._cam_pitch.setFixedWidth(130)
    self._cam_pitch.valueChanged.connect(self._on_cam_slider)
    tb.addWidget(self._cam_pitch)
    self._cam_pitch_lbl = QtWidgets.QLabel("  ")
    tb.addWidget(self._cam_pitch_lbl)
    self._on_cam_pick(0)          # 同步滑块到首个相机的 fov/pitch


def _build_right_turbine(self):
    """在右视口建一台**真实尺度**的选中机组（相机拍它，叶尖 0.5 m 真值）。"""
    from wfrl.viz.animate import build_turbine, turbine_actors
    self.plotter.subplot(0, 1)
    try:
        if self._cam_turb is not None:
            for a in turbine_actors(self._cam_turb):
                self.plotter.renderers[1].RemoveActor(a)
        _label, sensor, si = self._cams[self._cam_pick.currentIndex()] \
            if self._cams else (None, None, None)
        ti = sensor.indices[si] if sensor is not None else 0
        self._cam_idx = ti
        t = self.scene.turbines[ti]
        self._cam_turb = build_turbine(self.plotter, t.x, t.y,
                                     self.scene.hub_height, scale=1.0)
    finally:
        self.plotter.subplot(0, 0)    # 交互焦点还给左视口
    self._apply_cam_pose()


def _on_cam_pick(self, _i):
    if not self._cams:
        return
    _label, s, _si = self._cams[self._cam_pick.currentIndex()]
    self._cam_fov.blockSignals(True); self._cam_pitch.blockSignals(True)
    self._cam_fov.setValue(int(s.params.get("fov", 40)))
    self._cam_pitch.setValue(int(s.params.get("pitch", -30)))
    self._cam_fov.blockSignals(False); self._cam_pitch.blockSignals(False)
    self._cam_fov_lbl.setText(f" {self._cam_fov.value()}° ")
    self._cam_pitch_lbl.setText(f" {self._cam_pitch.value()}° ")
    if self._cam_on:
        self._build_right_turbine()
        self.plotter.interactor.repaint()


def _on_cam_slider(self, _v):
    self._cam_fov_lbl.setText(f" {self._cam_fov.value()}° ")
    self._cam_pitch_lbl.setText(f" {self._cam_pitch.value()}° ")
    self._apply_cam_pose()
    self.plotter.interactor.repaint()


def _apply_cam_pose(self):
    """把右视口相机设成选中传感器相机的位姿+FOV（真实尺度）。"""
    if not self._cams or self._cam_turb is None:
        return
    _label, s, si = self._cams[self._cam_pick.currentIndex()]
    ti = s.indices[si]
    s.params["fov"] = float(self._cam_fov.value())
    s.params["pitch"] = float(self._cam_pitch.value())
    ctx = {"yaw": np.array([float(self.view._yaw[ti])])}
    pos, focal, up, fov = s.camera_pose(ctx, si, scale=1.0)
    cam = self.plotter.renderers[1].camera
    cam.SetParallelProjection(False)
    cam.view_angle = float(fov)
    cam.position = tuple(pos)
    cam.focal_point = tuple(focal)
    cam.up = tuple(up)
    # 关键：**不要**用 ResetCameraClippingRange。相机贴着转子平面（离叶片
    # 才 1~3 m）却盯着 57 m 外的焦点，机组包围盒把相机整个裹在里面 —— VTK 自动
    # 算近/远面时会把近面推到叶片前/后，随俯仰角不同就把整台机组裁没了（拖动
    # 闪现、只有某些角度才稳）。固定一个够宽的裁剪范围：近面 0.5 m 收住最近
    # 的叶尖，远面覆盖焦点外一段 + 塔筒到地面。TIP_R≈63，HUB_H≈90。
    tip_r = float(getattr(s, "TIP_R", 63.0))
    hub_h = float(getattr(s, "HUB_H", 90.0))
    far = 0.9 * tip_r + 3.0 * tip_r + hub_h
    cam.SetClippingRange(0.5, far)


def _update_right_turbine(self):
    """渲染定时器每帧调：右视口机组姿态 + 相机随实时数据更新。"""
    if not self._cam_on or self._cam_turb is None:
        return
    from wfrl.viz.animate import update_turbine
    v = self.view
    ti = self._cam_idx
    update_turbine(self._cam_turb, float(v._yaw[ti]), float(v._spin[ti]),
                   float(v._pitch[ti]), flex_scale=v.flex_scale)
    self._apply_cam_pose()


def _mark_right_dirty(self):
    """强制右 renderer 下次重绘。改了 user_matrix 后 VTK 不一定认为 renderer
    变脏（尤其相机没动时）—— 显式 Modified() 让 paintEvent 一定重画右视口。"""
    r = self.plotter.renderers[1]
    r.Modified()
    for a in r.GetActors():
        a.Modified()
