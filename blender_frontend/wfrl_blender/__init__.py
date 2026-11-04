"""WFRL Blender: recorded MAPPO demonstration and backend tools."""
from __future__ import annotations

from .cameras import build_cameras
from .scene_builder import build_scene, validate_scene_build
from .scene_model import SceneDTO
from .workspace import ensure_workspace
from . import atmosphere, cameras, wake
from .turbine_geometry import MODELS, DEFAULT_MODEL


_BLADE_MODEL_KEYS = tuple(MODELS)
_BLADE_MODEL_ITEMS = tuple((key, label, '选择回放叶片外形', index)
                          for index, (key, (_, label)) in enumerate(MODELS.items()))


def _cancel_playback(scene=None, *, all_scenes=False):
    """Cancel the native player only when the target scene actually owns it."""
    import bpy
    if bpy.app.background:
        return
    from . import playback
    if scene is None and not all_scenes:
        scene = bpy.context.scene
    if not playback.any_playing() or (not all_scenes and not playback.is_playing(scene)):
        return
    # Blender has one global native playback timer. A single cancel pauses all
    # views of its owner scene; repeating it per window cannot identify ownership.
    window = playback.owner_window()
    if window is None:
        window = next(iter(bpy.context.window_manager.windows), None)
    if window is not None:
        with bpy.context.temp_override(window=window, screen=window.screen):
            if playback.any_playing() and (all_scenes or playback.is_playing(scene)):
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
        if visible is not None:
            obj.hide_render = not visible
            for layer in scene.view_layers:
                if obj.name in layer.objects:
                    obj.hide_set(not visible, view_layer=layer)
    wake.set_disxy_validity(scene, scene.get('wfrl_disxy_validity', 'waiting'))


def _update_disxy(scene, context=None):
    wake.set_disxy_validity(scene, scene.get('wfrl_disxy_validity', 'waiting'),
                            visible=scene.wfrl_show_disxy)


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


def _get_blade_model(scene):
    model = scene.get('wfrl_blade_model', DEFAULT_MODEL)
    return _BLADE_MODEL_KEYS.index(model) if model in MODELS else 0


def _set_blade_model(scene, index):
    from . import custom_camera_capture, native_camera_views, split_reconstruction
    from .panels import custom_cameras, stacked_camera_rig
    model = _BLADE_MODEL_KEYS[index]
    reason = ''
    if model != DEFAULT_MODEL and scene.get('wfrl_nrel_defect_enabled'):
        reason = '请先关闭 NREL 缺陷编辑器，再切换叶片外形'
    elif model != DEFAULT_MODEL and split_reconstruction.enabled(scene):
        reason = '请先退出 NREL 重建对照，再切换叶片外形'
    elif custom_camera_capture.active():
        reason = '请先完成或取消相机采集，再切换叶片外形'
    else:
        for session in (custom_cameras._ACTIVE, stacked_camera_rig._ACTIVE, native_camera_views._ACTIVE):
            if session is not None and getattr(session, 'scene', None) == scene:
                reason = '请先结束相机编辑或观察会话，再切换叶片外形'
                break
    scene['wfrl_blade_model_error'] = reason
    if not reason:
        scene['wfrl_blade_model'] = model


def _update_blade_model(scene, context=None):
    """Re-loft recorded motion while preserving the observation state."""
    from . import farm_flex, clearance_replay
    from .blade_root_display import RootFittingBindings
    chosen = scene.wfrl_blade_model
    active = farm_flex.active_for(scene)
    if active is None or active.model == chosen:
        return
    frame, subframe = scene.frame_current, scene.frame_subframe
    turbine = scene.get('wfrl_clearance_turbine', 'T1')
    visible = set(active.visible_turbines)
    render = scene.render
    clock = (render.fps, render.fps_base, scene.frame_start, scene.frame_end,
             scene.sync_mode, scene.use_preview_range)
    previous_model = active.model
    try:
        path = farm_flex.saved_package(scene)
        package = farm_flex._load_package(path)
        fittings = RootFittingBindings(scene, chosen)
    except Exception as exc:
        scene['wfrl_blade_model'] = previous_model
        scene['wfrl_blade_model_error'] = '叶片外形切换失败：' + str(exc)
        return
    try:
        fittings.apply()
        replacement = farm_flex.attach(scene, path, _package=package)
    except Exception as exc:
        fittings.rollback()
        scene['wfrl_blade_model'] = previous_model
        scene['wfrl_blade_model_error'] = '叶片外形切换失败：' + str(exc)
        try:
            replacement = farm_flex.attach(scene, path, _package=package)
        except Exception as restore_error:
            scene['wfrl_blade_model_error'] += '；原模型恢复失败：' + str(restore_error)
            (render.fps, render.fps_base, scene.frame_start, scene.frame_end,
             scene.sync_mode, scene.use_preview_range) = clock
            scene.frame_set(frame, subframe=subframe)
            return
    replacement.visible_turbines = visible
    if turbine in replacement.readers:
        scene['wfrl_clearance_turbine'] = turbine
        clearance_replay._READERS[scene.as_pointer()] = replacement.readers[turbine]
    (render.fps, render.fps_base, scene.frame_start, scene.frame_end,
     scene.sync_mode, scene.use_preview_range) = clock
    scene.frame_set(frame, subframe=subframe)
    fittings.commit()


def _on_load(_unused):
    import bpy
    from .manual_scene_links import restore
    restore()
    from .scene_builder import refresh_saved_surface_style
    from . import runtime, repair_marks
    from .blade_root_display import refresh as refresh_root_fittings
    for scene in bpy.data.scenes:
        cameras.clear_viewport_masks(scene)
        scene.pop('wfrl_home_load_error', None)
        if scene.objects:
            refresh_saved_surface_style(scene)
            if scene.get('wfrl_scene_kind') in {'clearance_replay', 'demo'}:
                try:
                    refresh_root_fittings(scene, scene.get('wfrl_blade_model', DEFAULT_MODEL))
                except (ValueError, RuntimeError, ReferenceError) as exc:
                    scene['wfrl_blade_model_error'] = str(exc)
            repair_marks.ensure_on_load(scene)
    if (runtime.has_backend_session()
            or any(scene.get('wfrl_scene_kind') == 'live' for scene in bpy.data.scenes)):
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
    from .panels import scene, channels, run, safety, presentation, training, gimbal, clearance, farm_replay, video_output, custom_cameras, blade_recon, home
    from .nrel_defects.editor import CLASSES as DEFECT_CLASSES
    from .panels.manual_control import CLASSES as MANUAL_CLASSES
    from .panels.radar_x100 import CLASSES as X100_CLASSES
    return (PREFERENCE_CLASSES + CLASSES + CONNECTION_CLASSES + RUN_CLASSES + STATUS_CLASSES + TELEMETRY_CLASSES
            + WORKFLOW_CLASSES + HISTORY_EXPORT_CLASSES + scene.CLASSES + channels.CLASSES + run.CLASSES
            + safety.CLASSES + presentation.CLASSES + training.CLASSES + gimbal.CLASSES + clearance.CLASSES + farm_replay.CLASSES + video_output.CLASSES + custom_cameras.CLASSES + blade_recon.CLASSES + DEFECT_CLASSES + MANUAL_CLASSES + home.CLASSES + X100_CLASSES)


def _cancel_managed_playback():
    from . import playback, farm_flex, clearance_replay
    owner = playback.owner_scene()
    if owner is not None and (farm_flex.active_for(owner) is not None
            or clearance_replay.reader_for(owner) is not None
            or owner.get('wfrl_scene_kind') == 'manual'):
        _cancel_playback(owner)


def register():
    import bpy
    previous = getattr(bpy, '_wfrl_registered_cleanup', None)
    if previous:
        previous()
    resources = []
    def cleanup():
        errors, pending = [], []
        try:
            _cancel_managed_playback()
        except Exception as exc:
            errors.append(exc)
        for callback in reversed(resources):
            try:
                callback()
            except Exception as exc:
                pending.append(callback)
                errors.append(exc)
        resources[:] = reversed(pending)
        if errors:
            raise RuntimeError('WFRL cleanup failed: ' + '; '.join(map(str, errors)))
    def acquire(release, action):
        resources.append(release)
        action()
    bpy._wfrl_registered_cleanup = cleanup
    try:
        _register_resources(acquire)
    except Exception as exc:
        try:
            cleanup()
        except Exception as cleanup_error:
            raise RuntimeError(str(exc) + '; ' + str(cleanup_error)) from exc
        raise


def _register_resources(acquire):
    import bpy
    from bpy.app.handlers import persistent
    from . import runtime
    from .deflection import refresh as refresh_deflection
    for cls in _classes():
        if not cls.is_registered:
            acquire(lambda cls=cls: bpy.utils.unregister_class(cls) if cls.is_registered else None,
                    lambda cls=cls: bpy.utils.register_class(cls))
    from .panels import home
    acquire(home.unregister_properties, lambda: home.register_properties())
    definitions = {
        "wfrl_diagnostics_sampling": bpy.props.BoolProperty(name="视口播放帧率", default=False),
        "wfrl_farm_panel_page": bpy.props.EnumProperty(
            name="回放面板", items=(("DEFLECTION", "挠度", "T1 叶尖位置与挠度对照", 0),
                                    ("RADAR", "净空", "当前机组的雷达与净空读数", 1),
                                    ("IMBALANCE", "不平衡", "质量、气动指标与三叶离线响应对比", 4),
                                    ("VIDEO", "视频输出", "导出离线 MP4 或启动 RTSP 视频流", 2),
                                    ("TOOLS", "工具", "视角、遥测、截图录制与数据", 3)),
            default="DEFLECTION"),
        "wfrl_imbalance_turbine": bpy.props.EnumProperty(
            name="分析机组", items=tuple((tid, tid, '离线响应分析') for tid in ('T1', 'T2', 'T3')),
            default='T1'),
        "wfrl_imbalance_metric": bpy.props.EnumProperty(
            name="响应分量", items=tuple((key, label, '同方位角的三叶响应对比') for key, label in (
                ('tip_x', '叶尖根系 x'), ('tip_y', '叶尖根系 y'), ('tip_z', '叶尖根系 z'),
                ('root_Mx', '叶根弯矩 Mx'), ('root_My', '叶根弯矩 My'), ('root_Mz', '叶根弯矩 Mz'))),
            default='tip_x'),
        "wfrl_imbalance_curve": bpy.props.BoolProperty(name="三叶均值曲线", default=False),
        "wfrl_deflection_visible": bpy.props.BoolProperty(default=True, update=refresh_deflection),
        "wfrl_deflection_blade": bpy.props.EnumProperty(items=(("1", "叶片 1", "T1 第一片"), ("2", "叶片 2", "T1 第二片"), ("3", "叶片 3", "T1 第三片")), default="1", update=refresh_deflection),
        "wfrl_selected_turbine": bpy.props.EnumProperty(items=_turbine_items, update=_update_selection),
        "wfrl_blade_model": bpy.props.EnumProperty(
            name="叶片外形", items=_BLADE_MODEL_ITEMS,
            get=_get_blade_model, set=_set_blade_model, update=_update_blade_model),
        "wfrl_show_wake": bpy.props.BoolProperty(default=True, update=_update_layers),
        "wfrl_wake_display": bpy.props.EnumProperty(items=(("SCIENTIFIC", "Scientific", "Green diagnostic tracers"), ("CINEMATIC", "Cinematic", "Animated incoming filaments and yaw-deflected wake (SYNTH)")), default="SCIENTIFIC", update=_update_layers),
        "wfrl_cinematic_wind_mode": bpy.props.EnumProperty(name="Wind mode", items=(("FRONT", "Front / 迎风", "Shared wind follows the reference turbine's heading plus a manual offset"), ("RANDOM", "360° random / 随机", "Shared, repeatable wind headings with smooth transitions")), default="FRONT", update=_update_layers),
        "wfrl_cinematic_reference": bpy.props.StringProperty(name="Reference turbine / 基准风机", default="T1", update=_update_layers),
        "wfrl_cinematic_offset": bpy.props.FloatProperty(name="Offset / 来风偏角 (°)", default=0.0, min=-10.0, max=10.0, update=_update_layers),
        "wfrl_cinematic_seed": bpy.props.IntProperty(name="Seed / 随机种子", default=42, min=0, max=1000000, update=_update_layers),
        "wfrl_cinematic_interval": bpy.props.FloatProperty(name="Change every / 换向间隔 (s)", default=8.0, min=2.0, max=60.0, update=_update_layers),
        "wfrl_show_lidar": bpy.props.BoolProperty(default=False, update=_update_layers),
        "wfrl_show_disxy": bpy.props.BoolProperty(default=True, update=_update_disxy),
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
            acquire(lambda name=name: delattr(bpy.types.Scene, name) if hasattr(bpy.types.Scene, name) else None,
                    lambda name=name, prop=prop: setattr(bpy.types.Scene, name, prop))
    for handlers, function in ((bpy.app.handlers.load_post, _on_load),):
        persistent(function)
        if function not in handlers:
            acquire(lambda handlers=handlers, function=function: handlers.remove(function) if function in handlers else None,
                    lambda: handlers.append(function))
    # Blender installs extensions under _RestrictData; scene setup belongs to Load Demo.
    if hasattr(bpy.data, "workspaces"):
        ensure_workspace()
    if hasattr(bpy.data, "scenes"):
        for scene in bpy.data.scenes:
            cameras.clear_viewport_masks(scene)
    from .presentation import register_overlay, unregister_overlay
    acquire(unregister_overlay, register_overlay)
    from . import charts
    from .operators import workflow
    from .panels import presentation as capture_panel
    acquire(workflow.unregister_properties, lambda: workflow.register_properties())
    acquire(capture_panel.unregister_properties, lambda: capture_panel.register_properties())
    from . import animation_render_ui
    acquire(animation_render_ui.unregister, lambda: animation_render_ui.register())
    from .panels import video_output
    acquire(video_output.unregister, lambda: video_output.register())
    from .panels import gimbal
    acquire(gimbal.unregister_properties, lambda: gimbal.register_properties())
    from .panels import custom_cameras
    acquire(custom_cameras.unregister_properties, lambda: custom_cameras.register_properties())
    from .panels import clearance
    from . import clearance_replay
    from . import playback
    acquire(playback.unregister, lambda: playback.register())
    acquire(clearance.unregister_properties, lambda: clearance.register_properties())
    acquire(clearance_replay.unregister, lambda: clearance_replay.register())
    acquire(charts.unregister, lambda: charts.register())
    from .operators.history_export import register_timer as history_export_register, unregister_timer as history_export_unregister
    acquire(history_export_unregister, history_export_register)
    acquire(runtime.shutdown, lambda: runtime.register())
    from . import blade_recon_review
    acquire(blade_recon_review.unregister, lambda: blade_recon_review.register())
    from . import split_reconstruction, split_reconstruction_ui
    acquire(split_reconstruction.unregister, lambda: split_reconstruction.register())
    acquire(split_reconstruction_ui.unregister, lambda: split_reconstruction_ui.register())
    from .nrel_defects import editor as defect_editor
    acquire(defect_editor.unregister_properties, lambda: defect_editor.register_properties())
    from . import frontend_diagnostics
    acquire(frontend_diagnostics.unregister, lambda: frontend_diagnostics.register())
    from . import manual_control, manual_down
    acquire(manual_control.unregister, lambda: manual_control.register())
    acquire(manual_down.unregister, lambda: manual_down.register())
    from .radar_x100 import runtime as x100_runtime
    acquire(x100_runtime.unregister, lambda: x100_runtime.register())

def unregister():
    """Release everything register() acquired; a failed release stays retryable."""
    import bpy
    from .cameras import cancel_view_layout
    cancel_view_layout()
    cleanup = getattr(bpy, '_wfrl_registered_cleanup', None)
    if cleanup:
        cleanup()
        del bpy._wfrl_registered_cleanup


def build_demo_geometry():
    """Shared three-turbine geometry, without animation or fabricated telemetry."""
    import bpy
    if bpy.context.scene.get("wfrl_scene_kind") == "manual":
        raise ValueError("请先返回原场景，再加载 MAPPO")
    dto = SceneDTO.from_mapping({"name": "mappo_60s", "backend": "demo", "turbine": "nrel5mw", "dt": 1,
        "layout": [{"id": "T1", "x": 0.0, "y": 0.0}, {"id": "T2", "x": 504.0, "y": 0.0}, {"id": "T3", "x": 1008.0, "y": 0.0}],
        "inflow": {"speed": 8.0, "direction": 270.0}})
    collection = build_scene(dto)
    build_cameras(dto)
    scene = bpy.context.scene
    scene['wfrl_scene_kind'] = 'clearance_replay'
    scene['wfrl_run_status'] = 'READY'
    from .blade_root_display import refresh as refresh_root_fittings
    refresh_root_fittings(scene, scene.get('wfrl_blade_model', DEFAULT_MODEL))
    atmosphere.apply_preset(scene, 'clear', enabled=True, quality='realtime')
    scene.wfrl_show_wake = False
    _update_layers(scene)
    from .workspace import configure_presentation
    configure_presentation()
    return collection


def load_demo_scene(path=None, *, camera_rig=True, _package=None):
    """Load recorded results and retain a visible error until a successful load."""
    import bpy
    scene = bpy.context.scene
    try:
        collection = _load_demo_scene(path, camera_rig=camera_rig, _package=_package)
    except Exception as exc:
        scene['wfrl_home_load_error'] = str(exc)
        raise
    scene.pop('wfrl_home_load_error', None)
    return collection


def _load_demo_scene(path=None, *, camera_rig=True, _package=None):
    import bpy
    if bpy.context.scene.get("wfrl_scene_kind") == "manual":
        raise ValueError("请先返回原场景，再加载 MAPPO")
    from . import runtime, farm_flex
    if not runtime.configuration_editable():
        raise ValueError('Stop the active backend before loading MAPPO')
    from .home_state import can_start
    allowed, reason = can_start('replace_scene', bpy.context.scene)
    if not allowed:
        raise ValueError(reason)
    validate_scene_build()
    path = path or farm_flex.default_package()
    package = farm_flex.preflight(path) if _package is None else _package
    farm_flex.validate_candidate(path, package)
    _cancel_playback(bpy.context.scene)
    runtime.enter_result_replay()
    collection = build_demo_geometry()
    scene = bpy.context.scene
    try:
        farm_flex.attach(scene, path, _package=package)
        from .panels.farm_replay import build_review_cameras
        build_review_cameras(scene)
        scene.wfrl_flex_show_tip_trails = False
        bpy.ops.wfrl.farm_flex_view(turbine='T1')
        scene.frame_set(1)
        if camera_rig:
            from . import stacked_camera_rig
            stacked_camera_rig.build_default(scene)
            scene.wfrl_deflection_visible = False
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
