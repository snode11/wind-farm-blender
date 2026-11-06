"""Studio 的相机传感器收集与通道订阅回调。"""
from __future__ import annotations

import logging


_LOG = logging.getLogger("wfrl.studio.app")


def _collect_cameras(self, scene, view):
    """场景里所有 camera 传感器 × 挂载机组 → 下拉项列表。

    直接从场景 build 相机传感器（相机是纯几何、不需要 runtime/训练），不依赖
    view.sensors 是否已 attach。
    返回 [(label, sensor, si)]，si 是机组在 sensor.indices 里的下标。
    """
    from wfrl.channels import registry
    out = []
    try:
        sensors = [s for s in registry.build(scene)
                   if getattr(s, "type_name", None) == "camera"]
    except (KeyError, TypeError, ValueError):
        _LOG.warning("单风机窗口相机传感器配置无效", exc_info=True)
        sensors = []
    for s in sensors:
        for si, ti in enumerate(s.indices):
            tid = scene.turbines[ti].id
            mount = s.params.get("mount", "nacelle_down")
            mname = {"nacelle_down": "机舱下俯视",
                     "nacelle_front": "机舱前视",
                     "hub": "轮毂",
                     "custom": "自定义点位"}.get(mount, mount)
            out.append((f"{tid}·{mname}", s, si))
    return out


def _toggle_camera_master(self, on):
    """相机总开关：视锥框（camera 通道可见性）+ 右分屏视口 + 相机控件一起开关。"""
    on = bool(on)
    # 1. 视锥框：camera 通道的可见性（走现有 set_channel）
    try:
        self.view.set_channel("camera", on, runtime=self.trainer.runtime)
    except Exception:                                    # noqa: BLE001
        _LOG.exception("Studio 切换相机通道失败")
    # 2. 右分屏视口 + 控件
    self._toggle_camera_viewport(on)


def _collect_cams(self):
    """场景里所有 camera 传感器 × 挂载机组 → [(label, sensor, si)]。"""
    from wfrl.channels import registry
    out = []
    try:
        sensors = [s for s in registry.build(self.scene)
                   if getattr(s, "type_name", None) == "camera"]
    except (KeyError, TypeError, ValueError):
        _LOG.warning("Studio 相机传感器配置无效", exc_info=True)
        sensors = []
    for s in sensors:
        for si, ti in enumerate(s.indices):
            tid = self.scene.turbines[ti].id
            mount = s.params.get("mount", "nacelle_down")
            mname = {"nacelle_down": "机舱下俯视",
                     "nacelle_front": "机舱前视", "hub": "轮毂",
                     "custom": "自定义点位"}.get(
                         mount, mount)
            out.append((f"{tid}·{mname}", s, si))
    self._cams = out


def _toggle_channel(self, topic, on):
    rt = self.trainer.runtime
    # rt 为 None（demo 脚本模式无 driver）时也能切：view.set_channel 在
    # runtime=None 下只跳过“停止采样”，actor 可见性照常切 —— 关相机/雷达即生效。
    self.view.set_channel(topic, on, runtime=rt)
    if rt is None:
        self._demo_enabled[topic] = on     # 记住状态，稍后有 runtime 再补采样
