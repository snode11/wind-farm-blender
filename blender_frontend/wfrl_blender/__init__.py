"""WFRL Blender: recorded MAPPO demonstration and backend tools."""
from __future__ import annotations

from .cameras import build_cameras
from .scene_builder import build_scene
from .scene_model import SceneDTO
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
    from . import cinematic
    cinematic.set_visibility(scene)
    wake.update_proxy_objects(scene, phase=float(scene.get("wfrl_proxy_phase", 0.0)) if scene.get("wfrl_scene_kind") == "live" else float(scene.get('wfrl_clearance_time_s', 0.0)) * .9)
    for obj in scene.objects:
        visible = None
        if obj.name.startswith("WFRL.WakeProxy."):
            visible = scene.wfrl_show_wake and scene.wfrl_wake_display == "SCIENTIFIC" and not obj.name.endswith(".Volume")
            if visible:
                from .materials import get_material
                if hasattr(obj.data, "materials"):
                    obj.data.materials.clear()
                    obj.data.materials.append(get_material("wake_pulse" if ".Pulse" in obj.name else "wake_line"))
                if hasattr(obj.data, "bevel_depth"):
                    obj.data.bevel_depth = .48 if ".Pulse" in obj.name else .11
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


# Blender retains pointers to dynamic enum strings; retain the item list.
_turbine_items_cache = {}


def _turbine_items(scene, context):
    ids = tuple(sorted(str(obj["wfrl_turbine_id"]) for obj in scene.objects
                       if "wfrl_turbine_id" in obj)) if scene else ()
    if not ids:
        ids = ("T1", "T2", "T3")
    if ids not in _turbine_items_cache:
        _turbine_items_cache[ids] = [(tid, tid, "Select turbine " + tid) for tid in ids]
    return _turbine_items_cache[ids]


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
    # Selection callbacks refresh charts without introducing a second clock.
    from . import farm_flex
    if farm_flex.is_active(scene):
        farm_flex._ACTIVE.record_telemetry(scene)


def _on_load(_unused):
    import bpy
    from .scene_builder import refresh_saved_surface_style
    from . import runtime
    for scene in bpy.data.scenes:
        if scene.objects:
            refresh_saved_surface_style(scene)
    if any(scene.get('wfrl_scene_kind') == 'live' for scene in bpy.data.scenes):
        runtime.disconnect(force=True)
    else:
        runtime.enter_local_demo()


def _classes():
    from .panels.demo import CLASSES
    from .panels.status import CLASSES as STATUS_CLASSES
    from .preferences import CLASSES as PREFERENCE_CLASSES
    from .operators.connection import CLASSES as CONNECTION_CLASSES
    from .operators.run import CLASSES as RUN_CLASSES
    from .operators.history_export import CLASSES as HISTORY_EXPORT_CLASSES
    from .panels.telemetry import CLASSES as TELEMETRY_CLASSES
    from .operators.workflow import CLASSES as WORKFLOW_CLASSES
    from .panels import scene, channels, run, safety, presentation, training, gimbal, clearance, farm_replay, video_output, custom_cameras
    return (PREFERENCE_CLASSES + CLASSES + CONNECTION_CLASSES + RUN_CLASSES + STATUS_CLASSES + TELEMETRY_CLASSES
            + WORKFLOW_CLASSES + HISTORY_EXPORT_CLASSES + scene.CLASSES + channels.CLASSES + run.CLASSES
            + safety.CLASSES + presentation.CLASSES + training.CLASSES + gimbal.CLASSES + clearance.CLASSES + farm_replay.CLASSES + video_output.CLASSES + custom_cameras.CLASSES)


def register():
    import bpy
    from bpy.app.handlers import persistent
    from . import runtime
    from .deflection import refresh as refresh_deflection
    # Preserve cleanup callbacks outside reloadable module globals.
    previous = getattr(bpy, '_wfrl_registered_cleanup', None)
    if previous:
        previous()
    for cls in _classes():
        if not cls.is_registered:
            bpy.utils.register_class(cls)
    definitions = {
        "wfrl_farm_panel_page": bpy.props.EnumProperty(
            name="回放面板", items=(("DEFLECTION", "挠度", "T1 叶尖位置与挠度对照"),
                                    ("RADAR", "净空", "当前机组的雷达与净空读数"),
                                    ("VIDEO", "视频输出", "导出离线 MP4 或启动 RTSP 视频流"),
                                    ("TOOLS", "工具", "视角、遥测、截图录制与数据")),
            default="DEFLECTION"),
        "wfrl_deflection_visible": bpy.props.BoolProperty(default=True, update=refresh_deflection),
        "wfrl_deflection_blade": bpy.props.EnumProperty(items=(("1", "叶片 1", "T1 第一片"), ("2", "叶片 2", "T1 第二片"), ("3", "叶片 3", "T1 第三片")), default="1", update=refresh_deflection),
        "wfrl_selected_turbine": bpy.props.EnumProperty(items=_turbine_items, update=_update_selection),
        "wfrl_show_wake": bpy.props.BoolProperty(default=True, update=_update_layers),
        "wfrl_wake_display": bpy.props.EnumProperty(items=(("SCIENTIFIC", "Scientific", "Green diagnostic tracers"), ("CINEMATIC", "Cinematic", "Animated incoming filaments and yaw-deflected wake (SYNTH)")), default="SCIENTIFIC", update=_update_layers),
        "wfrl_cinematic_wind_mode": bpy.props.EnumProperty(name="Wind mode", items=(("FRONT", "Front / 迎风", "Shared wind follows the reference turbine's heading plus a manual offset"), ("RANDOM", "360° random / 随机", "Shared, repeatable wind headings with smooth transitions")), default="FRONT", update=_update_layers),
        "wfrl_cinematic_reference": bpy.props.StringProperty(name="Reference turbine / 基准风机", default="T1", update=_update_layers),
        "wfrl_cinematic_offset": bpy.props.FloatProperty(name="Offset / 来风偏角 (°)", default=0.0, min=-10.0, max=10.0, update=_update_layers),
        "wfrl_cinematic_seed": bpy.props.IntProperty(name="Seed / 随机种子", default=42, min=0, max=1000000, update=_update_layers),
        "wfrl_cinematic_interval": bpy.props.FloatProperty(name="Change every / 换向间隔 (s)", default=8.0, min=2.0, max=60.0, update=_update_layers),
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
    }
    for name, prop in definitions.items():
        if not hasattr(bpy.types.Scene, name):
            setattr(bpy.types.Scene, name, prop)
    for handlers, function in ((bpy.app.handlers.load_post, _on_load),):
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
    from .panels import video_output
    video_output.register()
    from .panels import gimbal
    gimbal.register_properties()
    from .panels import custom_cameras
    custom_cameras.register_properties()
    from .panels import clearance
    from . import clearance_replay
    clearance.register_properties()
    clearance_replay.register()
    charts.register()
    runtime.register()
    registered_classes = _classes()
    registered_handlers = ((bpy.app.handlers.load_post, _on_load),)
    registered_properties = tuple(definitions)
    from .presentation import unregister_overlay
    from .operators.history_export import unregister_timer as history_export_cleanup
    timer_cancel = _cancel_playback
    runtime_cleanup = runtime.shutdown
    workflow_cleanup = workflow.unregister_properties
    capture_cleanup = capture_panel.unregister_properties
    charts_cleanup = charts.unregister
    gimbal_cleanup = gimbal.unregister_properties
    custom_cameras_cleanup = custom_cameras.unregister_properties
    def cleanup():
        custom_cameras_cleanup()
        runtime_cleanup()
        video_output.unregister()
        history_export_cleanup()
        gimbal_cleanup()
        clearance.unregister_properties()
        clearance_replay.unregister()
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
    for handlers, function in ((bpy.app.handlers.load_post, _on_load),):
        if function in handlers:
            handlers.remove(function)
    for cls in reversed(_classes()):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    for name in ("wfrl_selected_turbine", "wfrl_fixture_state", "wfrl_show_wake", "wfrl_wake_display", "wfrl_show_lidar", "wfrl_show_disxy", "wfrl_show_atmosphere", "wfrl_atmosphere_preset", "wfrl_wake_quality", "wfrl_camera_view", "wfrl_channel_telemetry", "wfrl_manual_enabled", "wfrl_manual_yaw", "wfrl_manual_pitch"):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)


def build_demo_geometry():
    """Shared three-turbine geometry, without animation or fabricated telemetry."""
    import bpy
    dto = SceneDTO.from_mapping({"name": "mappo_60s", "backend": "demo", "turbine": "nrel5mw", "dt": 1,
        "layout": [{"id": "T1", "x": 0.0, "y": 0.0}, {"id": "T2", "x": 504.0, "y": 0.0}, {"id": "T3", "x": 1008.0, "y": 0.0}],
        "inflow": {"speed": 8.0, "direction": 270.0}})
    collection = build_scene(dto)
    build_cameras(dto)
    scene = bpy.context.scene
    scene['wfrl_scene_kind'] = 'clearance_replay'
    scene['wfrl_run_status'] = 'READY'
    atmosphere.apply_preset(scene, 'clear', enabled=True, quality='realtime')
    scene.wfrl_show_wake = False
    _update_layers(scene)
    from .workspace import configure_presentation
    configure_presentation()
    return collection


def load_demo_scene(path=None, *, camera_rig=True):
    import bpy
    from . import runtime, farm_flex
    if not runtime.configuration_editable():
        raise ValueError('Stop the active backend before loading MAPPO')
    _cancel_playback()
    runtime.enter_result_replay()
    collection = build_demo_geometry()
    scene = bpy.context.scene
    try:
        farm_flex.attach(scene, path or farm_flex.default_package())
        from .panels.farm_replay import build_review_cameras
        build_review_cameras(scene)
        scene.wfrl_flex_show_tip_trails = True
        bpy.ops.wfrl.farm_flex_view(turbine='T1')
        scene.frame_set(1)
        if camera_rig:
            from . import stacked_camera_rig
            stacked_camera_rig.build(scene)
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces.active.show_region_ui = True
                    area.tag_redraw()
    except Exception as exc:
        from . import clearance_replay
        clearance_replay.clear(scene, 'MAPPO 数据未就绪：' + str(exc))
        raise
    return collection


if __name__ == "__main__":
    register()
    load_demo_scene()
