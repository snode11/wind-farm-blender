"""Split comparison entry, exit and geometry/texture mode transitions."""
from __future__ import annotations


def _display_origin(scene):
    """Both comparisons use nacelle coordinates, at different display offsets."""
    from . import split_mappo_texture
    from mathutils import Vector
    obj = (split_mappo_texture.blades(scene)[0] if split_mappo_texture.active(scene)
           else scene.objects.get('SplitRecon.B1'))
    return obj.matrix_world.translation.copy() if obj is not None else Vector((0., 0., 0.))


def _transfer_display_camera(source, target, offset):
    """Carry framing between private display cameras, never source calibration."""
    target.matrix_world = source.matrix_world.copy()
    target.location += offset
    for name in ('type', 'lens', 'ortho_scale', 'sensor_width', 'sensor_height',
                 'sensor_fit', 'shift_x', 'shift_y', 'clip_start', 'clip_end'):
        setattr(target.data, name, getattr(source.data, name))


def switch_mode(context):
    """Switch the current pair in place, preserving its clock and navigation."""
    import bpy
    from . import cameras, split_mappo_texture, split_texture_settings as settings
    scene, window = context.scene, context.window
    if not _core.enabled(scene) or not _core._legacy_available(scene):
        raise ValueError('请先打开几何重建示例')
    if window is None or cameras._LAYOUT_JOB is not None or _core._key(window) in _core._JOBS:
        raise ValueError('请等待当前布局调整完成')
    areas = _core._pair(scene, window.screen)
    if areas is None:
        raise ValueError('当前重建分屏尚未就绪')
    previous = _core.texture_adapter(scene)
    source_camera = _core.reconstruction_camera(scene)
    origin = _core._display_origin(scene)
    navigation = _core._navigation(areas[1].spaces.active)
    left_navigation = _core._navigation(areas[0].spaces.active)
    old_right_navigation = _core._navigation(areas[1].spaces.active)
    membership = [(obj, obj.local_view_get(areas[1].spaces.active)) for obj in scene.objects
                  if hasattr(obj, 'local_view_get')]
    target_camera = target_state = None
    try:
        with settings.rollback_on_error(scene):
            if previous.active(scene):
                source = scene.get(settings.CURRENT)
                if source in {'MAPPO', 'SYNTHETIC'}:
                    settings._store(scene, settings._key(source), settings._encode(settings.capture(scene)))
                scene[settings.CURRENT] = 'GEOMETRY'
                scene[split_mappo_texture.DETAIL] = False
            else:
                split_mappo_texture.ensure(scene)
                scene[split_mappo_texture.DETAIL] = False
            offset = _core._display_origin(scene) - origin
            navigation['view_location'] += offset
            target_camera = _core.reconstruction_camera(scene)
            target_state = (target_camera.matrix_world.copy(),
                            {name: getattr(target_camera.data, name) for name in _core._DISPLAY_OPTICS})
            _core._transfer_display_camera(source_camera, target_camera, offset)
            with bpy.context.temp_override(window=window, area=areas[0]):
                _core._configure(window, areas)
            _core._restore_navigation(areas[0].spaces.active, left_navigation)
            _core._restore_navigation(areas[1].spaces.active, navigation)
            _core._remember(window, areas)
            scene['split_review_status'] = 'READY'
            _core._redraw_scene(scene)
    except Exception:
        if target_state is not None:
            target_camera.matrix_world = target_state[0]
            for name, value in target_state[1].items():
                setattr(target_camera.data, name, value)
        areas[1].spaces.active.camera = source_camera
        _core._restore_navigation(areas[0].spaces.active, left_navigation)
        _core._restore_navigation(areas[1].spaces.active, old_right_navigation)
        for obj, included in membership:
            obj.local_view_set(areas[1].spaces.active, included)
        for obj in scene.objects:
            if hasattr(obj, 'local_view_set') and all(obj != old for old, _ in membership):
                obj.local_view_set(areas[1].spaces.active, False)
        raise
    return True


def _saved_return_navigation(scene):
    raw = scene.get('split_review_return_navigation')
    if not raw:
        return None
    try:
        return _core.json.loads(raw)
    except (ValueError, TypeError):
        return None


def enter(context, reset_layout=True, use_texture=None, use_same_source=False):
    """Restore the current comparison, or explicitly select its source.

    A saved geometry example keeps its original right-hand data when returning
    or repairing its layout. False explicitly selects that geometry; True
    and use_same_source select the matching MAPPO texture result.
    """
    import bpy
    from . import cameras, split_mappo_texture, split_texture_settings
    scene, window = context.scene, context.window
    if scene.get('wfrl_blade_model', 'nrel5mw') != 'nrel5mw':
        raise ValueError('重建对照需要 NREL 叶片外形，请先切回 NREL 5MW')
    if not _core.available(scene) or window is None or bpy.app.background:
        raise ValueError('请先载入 MAPPO 回放或打开 MAPPO 重建示例')
    areas = _core._views(window.screen)
    if not areas:
        raise ValueError('请先打开三维视口')
    if cameras._LAYOUT_JOB is not None or _core._key(window) in _core._JOBS:
        raise ValueError('请等待当前布局调整完成')
    source_area = next((a for a in areas if not _core.is_reconstruction_view(scene, a.spaces.active)), areas[0])
    source = _core._source_camera(scene, [source_area])
    if source is None:
        raise ValueError('MAPPO 场景相机尚未就绪')
    previous_session = _core._SESSIONS.get(_core._key(window))
    try:
        with split_texture_settings.rollback_on_error(scene):
            if use_same_source or use_texture is True:
                split_mappo_texture.ensure(scene)
                # The explicit synchronization entry starts with the full rotor;
                # CURRENT restores the user's chosen full/detail view instead.
                scene[split_mappo_texture.DETAIL] = False
            elif use_texture is False:
                if not _core._legacy_available(scene):
                    raise ValueError('请先打开几何重建示例')
                outgoing_source = split_texture_settings._source(scene)
                if outgoing_source is not None:
                    split_texture_settings._store(scene, split_texture_settings._key(outgoing_source),
                        split_texture_settings._encode(split_texture_settings.capture(scene)))
                scene[split_texture_settings.CURRENT] = 'GEOMETRY'
                scene[split_mappo_texture.DETAIL] = False
            else:
                texture = _core.texture_adapter(scene)
                if texture.active(scene) or not _core._legacy_available(scene):
                    texture.ensure(scene)
                else:
                    scene[split_texture_settings.CURRENT] = 'GEOMETRY'
            texture = _core.texture_adapter(scene)
            current_pair = _core._pair(scene, window.screen)
            right_navigation = None
            if use_texture is None and not use_same_source and current_pair:
                if texture.active(scene) and hasattr(texture, 'track'):
                    texture.track(scene, current_pair[1])
                right_navigation = _core._navigation(current_pair[1].spaces.active)
            elif use_texture is None and not use_same_source:
                right_navigation = _core._saved_return_navigation(scene)
            scene['split_review_enabled'] = True
            for key, value in _core._METADATA.items():
                if key not in scene:
                    scene[key] = value
            job = dict(window=window, scene=scene, screen=window.screen, stage='layout',
                       navigation=_core._navigation(source_area.spaces.active), deadline=_core.time.monotonic() + 15,
                       frame=scene.frame_current, subframe=scene.frame_subframe,
                       right_navigation=right_navigation,
                       explicit=True, detail=bool(texture.active(scene) and
                                                 scene.get(texture.DETAIL, False)))
            scene['split_review_status'] = 'PREPARING'
            _core._SESSIONS.pop(_core._key(window), None)
            _core._JOBS[_core._key(window)] = job
            factor, balanced = _core._layout_factor(areas)
            rebuild = bool(reset_layout and len(areas) > 1 and not balanced)
            if _core._pair(scene, window.screen) is None or rebuild:
                with bpy.context.temp_override(window=window, area=source_area):
                    cameras.set_view_layout(context, 'DUAL',
                        camera_names=(source.name, _core.reconstruction_camera(scene).name),
                        split_factor=factor, rebuild=rebuild)
            _core._ensure_timer()
    except Exception:
        _core._JOBS.pop(_core._key(window), None)
        if previous_session is not None:
            _core._SESSIONS[_core._key(window)] = previous_session
        raise
    return True


def fit(context, reset_layout=False):
    from . import cameras
    if cameras._LAYOUT_JOB is not None or _core._key(context.window) in _core._JOBS:
        raise ValueError('请等待当前布局调整完成')
    texture = _core.texture_adapter(context.scene)
    if texture.active(context.scene):
        context.scene[texture.DETAIL] = False
    if reset_layout or _core._pair(context.scene, context.window.screen) is None:
        return _core.enter(context, reset_layout=True)
    if not _core.enabled(context.scene):
        return _core.enter(context, reset_layout=reset_layout)
    areas = _core._pair(context.scene, context.window.screen)
    areas[0].spaces.active.region_3d.view_perspective = 'PERSP'
    areas[1].spaces.active.region_3d.view_perspective = 'CAMERA'
    now = _core.time.monotonic()
    _core._JOBS[_core._key(context.window)] = dict(window=context.window, scene=context.scene,
        screen=context.window.screen, stage='fit', settle=now + _core._DEBOUNCE, deadline=now + 15)
    context.scene['split_review_status'] = 'PREPARING'
    _core._ensure_timer()
    return True


def exit_mode(context):
    import bpy
    from . import cameras
    scene, window = context.scene, context.window
    areas = _core._views(window.screen)
    if not areas:
        raise ValueError('请先打开三维视口')
    if cameras._LAYOUT_JOB is not None:
        raise ValueError('请等待当前布局调整完成')
    pair = _core._pair(scene, window.screen)
    if pair:
        values = _core._navigation(pair[1].spaces.active)
        scene['split_review_return_navigation'] = _core.json.dumps({
            name: value if isinstance(value, (str, float, int)) else list(value)
            for name, value in values.items()})
    source_area = pair[0] if pair else areas[0]
    navigation = _core._navigation(source_area.spaces.active)
    presentation = _core._presentation(source_area.spaces.active)
    source = _core._source_camera(scene, [source_area])
    scene['split_review_enabled'] = False
    for key in list(_core._JOBS):
        if key[2] == scene.as_pointer():
            del _core._JOBS[key]
    for key in list(_core._SESSIONS):
        if key[2] == scene.as_pointer():
            del _core._SESSIONS[key]
    # Enabled state belongs to the scene. Clean every visible right pane for
    # that scene before disabling it, including comparisons in other windows.
    recon_camera = _core.reconstruction_camera(scene)
    for other_window in bpy.context.window_manager.windows:
        if other_window == window or other_window.scene != scene:
            continue
        other_areas = _core._views(other_window.screen)
        demo_area = next((a for a in other_areas if a.spaces.active.camera != recon_camera), None)
        demo_navigation = _core._navigation(demo_area.spaces.active) if demo_area else navigation
        demo_presentation = _core._presentation(demo_area.spaces.active) if demo_area else presentation
        demo_camera = _core._source_camera(scene, [demo_area]) if demo_area else source
        for other_area in other_areas:
            space = other_area.spaces.active
            if space.camera != recon_camera:
                continue
            _core._leave_local(other_window, other_area)
            space.camera = demo_camera
            space.use_local_camera = False
            _core._restore_presentation(space, demo_presentation)
            _core._restore_navigation(space, demo_navigation)
            other_area.tag_redraw()
    for area in areas:
        _core._leave_local(window, area)
        area.spaces.active.camera = source
        area.spaces.active.use_local_camera = False
        _core._restore_navigation(area.spaces.active, navigation)
        _core._restore_presentation(area.spaces.active, presentation)
    if source:
        scene.camera = source
    _core._JOBS[_core._key(window)] = dict(window=window, scene=scene, screen=window.screen, stage='exit',
        navigation=navigation, presentation=presentation, source=source, deadline=_core.time.monotonic() + 15)
    with bpy.context.temp_override(window=window, area=source_area):
        cameras.set_view_layout(context, 'SINGLE', camera_names=(source.name,) if source else None)
    scene['split_review_status'] = 'EXITING'
    _core._ensure_timer()
    return True


# The facade imports these functions, so bind its shared namespace last.
from . import split_reconstruction as _core
