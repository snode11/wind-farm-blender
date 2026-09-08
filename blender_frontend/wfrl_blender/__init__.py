"""WFRL Blender: deterministic offline presentation, explicitly SYNTH."""
from __future__ import annotations

from .cameras import build_cameras
from .scene_builder import build_scene
from .scene_model import SceneDTO
from .state import END_FRAME, apply_demo_state, demo_keyframes, sample_demo, time_for_frame
from .workspace import ensure_workspace
from . import atmosphere, cameras, wake


def _cancel_playback():
    import bpy
    if bpy.app.background:
        return
    for window in bpy.context.window_manager.windows:
        if window.screen.is_animation_playing:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.screen.animation_cancel(restore_frame=False)


def _update_layers(scene, context=None):
    for obj in scene.objects:
        visible = None
        if obj.name.startswith("WFRL.WakeProxy."):
            visible = scene.wfrl_show_wake and not obj.name.endswith(".Volume")
        elif obj.name.startswith("WFRL.Fixture.T1.Lidar") or obj.name == "WFRL.Fixture.T1.SensorFrustum":
            visible = scene.wfrl_show_lidar
        elif obj.name == "WFRL.WakeDisXY":
            visible = scene.wfrl_show_disxy
        if visible is not None:
            obj.hide_render = not visible
            obj.hide_set(not visible)


def _update_atmosphere(scene, context=None):
    atmosphere.apply_preset(scene, scene.wfrl_atmosphere_preset,
                             enabled=scene.wfrl_show_atmosphere,
                             quality=scene.wfrl_wake_quality)
    atmosphere.set_feature_visibility(scene, scene.wfrl_show_atmosphere)


def _update_camera_view(scene, context=None):
    name = scene.wfrl_camera_view
    if name and scene.objects.get(name) is not None:
        cameras.select_camera(scene, name)


def _update_selection(scene, context):
    import bpy
    obj = scene.objects.get(f"WFRL.Turbine.{scene.wfrl_selected_turbine}.Nacelle")
    if obj is not None and context and context.view_layer:
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = None
    _update_demo_status(scene)


def _update_manual(scene, context=None):
    _update_demo_status(scene)


def _update_demo_status(scene, depsgraph=None):
    """Frame-aligned telemetry and pose; lifecycle stays paused during scrubbing."""
    from . import runtime
    if runtime.get_state().connection != "LOCAL DEMO" or scene.get("wfrl_scene_kind") != "demo":
        return
    if not scene.objects.get("WFRL.Turbine.T1.Rotor"):
        return
    sample = sample_demo(time_for_frame(scene.frame_current))
    status = scene.get("wfrl_run_status", "READY")
    if status in {"STARTING", "RUNNING"}:
        scene["wfrl_run_status"] = str(sample.status)
        if scene.frame_current >= END_FRAME:
            import bpy
            if not bpy.app.background and not bpy.app.timers.is_registered(_cancel_playback):
                bpy.app.timers.register(_cancel_playback, first_interval=0.0)
    yaw, pitch, rpm = list(sample.yaw_deg), list(sample.pitch_deg), list(sample.rpm)
    manual = getattr(scene, "wfrl_manual_enabled", False) and scene.get("wfrl_run_status") == "PAUSED"
    if manual:
        index = ("T1", "T2", "T3").index(scene.wfrl_selected_turbine)
        yaw[index], pitch[index], rpm[index] = scene.wfrl_manual_yaw, scene.wfrl_manual_pitch, 0.0
    apply_demo_state(yaw, pitch, rpm, sample.rotor_rad)
    wake.update_proxy_objects(scene, phase=sample.time_s * 0.9)
    scene["wfrl_demo_time_s"] = sample.time_s
    scene["wfrl_demo_phase"] = "Manual pose (paused)" if manual else sample.label
    if getattr(scene, "wfrl_channel_telemetry", True):
        scene["wfrl_yaw_deg"] = yaw
        scene["wfrl_pitch_deg"] = pitch
        scene["wfrl_rpm"] = rpm
        # Manual pose is not a power model: power is unavailable in that mode.
        scene["wfrl_power_mw"] = list(sample.power_mw)
        scene["wfrl_power_available"] = not manual
        scene["wfrl_telemetry_time_s"] = sample.time_s
    from . import charts
    charts.record_demo(sample, manual=manual)
    scene["wfrl_fidelity"] = "SYNTH"
    scene["wfrl_backend"] = "demo"
    scene["wfrl_wind_speed_mps"] = 8.0


def _on_load(_unused):
    import bpy
    from . import runtime
    if any(scene.get("wfrl_scene_kind") == "live" for scene in bpy.data.scenes):
        runtime.disconnect(force=True)
    else:
        runtime.enter_local_demo()
    for scene in bpy.data.scenes:
        if (scene.get("wfrl_scene_kind") in {None, "demo"}
                and scene.objects.get("WFRL.Turbine.T1.Rotor")):
            scene["wfrl_scene_kind"] = "demo"
            scene["wfrl_run_status"] = "STOPPED" if scene.frame_current >= END_FRAME else ("READY" if scene.frame_current == 1 else "PAUSED")
            _update_layers(scene)
            _update_demo_status(scene)


def _classes():
    from .panels.demo import CLASSES
    from .panels.status import CLASSES as STATUS_CLASSES
    from .preferences import CLASSES as PREFERENCE_CLASSES
    from .operators.connection import CLASSES as CONNECTION_CLASSES
    from .operators.run import CLASSES as RUN_CLASSES
    from .operators.history_export import CLASSES as HISTORY_EXPORT_CLASSES
    from .panels.telemetry import CLASSES as TELEMETRY_CLASSES
    from .operators.workflow import CLASSES as WORKFLOW_CLASSES
    from .panels import scene, channels, run, safety, presentation, training
    return (PREFERENCE_CLASSES + CLASSES + CONNECTION_CLASSES + RUN_CLASSES + STATUS_CLASSES + TELEMETRY_CLASSES
            + WORKFLOW_CLASSES + HISTORY_EXPORT_CLASSES + scene.CLASSES + channels.CLASSES + run.CLASSES
            + safety.CLASSES + presentation.CLASSES + training.CLASSES)


def register():
    import bpy
    from bpy.app.handlers import persistent
    from . import runtime
    # Preserve cleanup callbacks outside reloadable module globals.
    previous = getattr(bpy, '_wfrl_registered_cleanup', None)
    if previous:
        previous()
    for cls in _classes():
        if not cls.is_registered:
            bpy.utils.register_class(cls)
    definitions = {
        "wfrl_selected_turbine": bpy.props.EnumProperty(items=(("T1", "T1", "Upstream turbine"), ("T2", "T2", "Middle turbine"), ("T3", "T3", "Downstream turbine")), default="T1", update=_update_selection),
        "wfrl_fixture_state": bpy.props.EnumProperty(items=(("NOMINAL", "Nominal", ""), ("WAITING", "Waiting", ""), ("CHANNEL_OFF", "Channel Off", ""), ("STALE", "Stale Data", ""), ("INCOMPATIBLE", "Bad Checkpoint", ""), ("FAILED", "Failed", "")), default="NOMINAL"),
        "wfrl_show_wake": bpy.props.BoolProperty(default=True, update=_update_layers),
        "wfrl_show_lidar": bpy.props.BoolProperty(default=False, update=_update_layers),
        "wfrl_show_disxy": bpy.props.BoolProperty(default=True, update=_update_layers),
        "wfrl_show_atmosphere": bpy.props.BoolProperty(default=True, update=_update_atmosphere),
        "wfrl_atmosphere_preset": bpy.props.EnumProperty(
            items=(("clear", "Clear", "Light blue daylight"),
                   ("overcast", "Overcast", "Muted grey daylight"),
                   ("dusk", "Dusk", "Warm low-angle light")),
            default="clear", update=_update_atmosphere),
        "wfrl_wake_quality": bpy.props.EnumProperty(
            items=(("realtime", "Realtime", "Lightweight viewport settings"),
                   ("render", "Render", "Higher-quality still/animation settings")),
            default="realtime", update=_update_atmosphere),
        "wfrl_camera_view": bpy.props.EnumProperty(
            items=tuple((name, name.rsplit(".", 1)[-1], "WFRL camera") for name in cameras.camera_view_names()),
            default="WFRL.Camera.World", update=_update_camera_view),
        "wfrl_channel_telemetry": bpy.props.BoolProperty(default=True, update=_update_manual),
        "wfrl_manual_enabled": bpy.props.BoolProperty(default=False, update=_update_manual),
        "wfrl_manual_yaw": bpy.props.FloatProperty(default=0, min=-30, max=30, update=_update_manual),
        "wfrl_manual_pitch": bpy.props.FloatProperty(default=2, min=0, max=90, update=_update_manual),
    }
    for name, prop in definitions.items():
        if not hasattr(bpy.types.Scene, name):
            setattr(bpy.types.Scene, name, prop)
    for handlers, function in ((bpy.app.handlers.frame_change_post, _update_demo_status), (bpy.app.handlers.load_post, _on_load)):
        persistent(function)
        if function not in handlers:
            handlers.append(function)
    # Blender installs extensions under _RestrictData; scene setup belongs to Load Demo.
    if hasattr(bpy.data, "workspaces"):
        ensure_workspace()
    from .presentation import register_overlay
    register_overlay()
    from . import charts
    from .operators import workflow
    from .panels import presentation as capture_panel
    workflow.register_properties()
    capture_panel.register_properties()
    charts.register()
    runtime.register()
    registered_classes = _classes()
    registered_handlers = ((bpy.app.handlers.frame_change_post, _update_demo_status),
                           (bpy.app.handlers.load_post, _on_load))
    registered_properties = tuple(definitions)
    from .presentation import unregister_overlay
    from .operators.history_export import unregister_timer as history_export_cleanup
    timer_cancel = _cancel_playback
    runtime_cleanup = runtime.shutdown
    workflow_cleanup = workflow.unregister_properties
    capture_cleanup = capture_panel.unregister_properties
    charts_cleanup = charts.unregister
    def cleanup():
        runtime_cleanup()
        history_export_cleanup()
        capture_cleanup()
        charts_cleanup()
        workflow_cleanup()
        unregister_overlay()
        if bpy.app.timers.is_registered(timer_cancel):
            bpy.app.timers.unregister(timer_cancel)
        for handlers, function in registered_handlers:
            if function in handlers:
                handlers.remove(function)
        for name in registered_properties:
            if hasattr(bpy.types.Scene, name):
                delattr(bpy.types.Scene, name)
        for cls in reversed(registered_classes):
            if cls.is_registered:
                bpy.utils.unregister_class(cls)
    bpy._wfrl_registered_cleanup = cleanup


def unregister():
    import bpy
    previous = getattr(bpy, '_wfrl_registered_cleanup', None)
    if previous:
        previous()
        del bpy._wfrl_registered_cleanup
    from .presentation import unregister_overlay
    unregister_overlay()
    _cancel_playback()
    if bpy.app.timers.is_registered(_cancel_playback):
        bpy.app.timers.unregister(_cancel_playback)
    for handlers, function in ((bpy.app.handlers.frame_change_post, _update_demo_status), (bpy.app.handlers.load_post, _on_load)):
        if function in handlers:
            handlers.remove(function)
    for cls in reversed(_classes()):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    for name in ("wfrl_selected_turbine", "wfrl_fixture_state", "wfrl_show_wake", "wfrl_show_lidar", "wfrl_show_disxy", "wfrl_show_atmosphere", "wfrl_atmosphere_preset", "wfrl_wake_quality", "wfrl_camera_view", "wfrl_channel_telemetry", "wfrl_manual_enabled", "wfrl_manual_yaw", "wfrl_manual_pitch"):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)


def load_demo_scene():
    import bpy
    from . import runtime
    if not runtime.configuration_editable():
        raise ValueError("Stop the active session before rebuilding the Demo")
    runtime.select_mode('demo')
    _cancel_playback()
    dto = SceneDTO.from_mapping({"name": "turb3_demo", "backend": "demo", "turbine": "nrel5mw", "dt": 1,
        "layout": [{"id": "T1", "x": 0.0, "y": 0.0}, {"id": "T2", "x": 504.0, "y": 0.0}, {"id": "T3", "x": 1008.0, "y": 0.0}],
        "inflow": {"speed": 8.0, "direction": 270.0}})
    collection = build_scene(dto)
    build_cameras(dto)
    scene = bpy.context.scene
    scene["wfrl_scene_kind"] = "demo"
    scene["wfrl_run_status"] = "READY"
    atmosphere.apply_preset(scene, "clear", enabled=True, quality="realtime")
    scene.wfrl_manual_enabled = False
    demo_keyframes(collection)
    _update_layers(scene)
    _update_demo_status(scene)
    from .workspace import configure_presentation
    configure_presentation()
    return collection


if __name__ == "__main__":
    register()
    load_demo_scene()
