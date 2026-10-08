"""Native lifecycle and framing for saved MAPPO/reconstruction comparisons.

The reconstruction's samples and source cameras are never edited here.  A small
timer observes screen/region changes; camera ownership is enforced by the view
operators, not by polling and repairing an overwritten camera.
"""
from __future__ import annotations

import math
import time


_SESSIONS = {}
_JOBS = {}
_BOUNDS = {}
_REGISTERED = False
_INTERVAL = .25
_DEBOUNCE = .35
_METADATA = {
    'split_reconstruction_start_s': 117.,
    'split_reconstruction_timeline_fps': 60.,
    'split_reconstruction_stride': 6,
    'split_reconstruction_samples': 601,
    'split_reconstruction_frame_start': 1,
}


def _legacy_available(scene):
    return bool(scene and scene.get('split_reconstruction_review') and
                scene.objects.get('SplitRecon.Camera') is not None and
                all(scene.objects.get(f'SplitRecon.B{b}') is not None for b in (1, 2, 3)))


def available(scene):
    """Offer surface comparison in a saved example or loaded MAPPO scene."""
    return bool(_legacy_available(scene) or (scene and scene.get('wfrl_farm_flex_path') and
                scene.objects.get('WFRL.Turbine.T1.Rotor') is not None))


def reconstruction_camera(scene):
    return texture_adapter(scene).camera(scene) or scene.objects.get('SplitRecon.Camera')


def texture_adapter(scene):
    """Keep same-source playback and independent synthetic inspection distinct."""
    from . import split_mappo_texture, split_surface_texture
    return split_mappo_texture if split_mappo_texture.active(scene) else split_surface_texture


def enabled(scene):
    return available(scene) and bool(scene.get('split_review_enabled', _legacy_available(scene)))


def is_reconstruction_view(scene, space):
    """Identify the saved right view even while it is manually orbited."""
    return bool(enabled(scene) and getattr(space, 'type', None) == 'VIEW_3D' and
                getattr(space, 'camera', None) == reconstruction_camera(scene))


def _views(screen):
    return sorted((area for area in screen.areas if area.type == 'VIEW_3D' and
                   area.width > 0 and area.height > 0), key=lambda area: (area.x, -area.y))


def _pair(scene, screen):
    areas = _views(screen)
    if len(areas) != 2:
        return None
    right = [area for area in areas if is_reconstruction_view(scene, area.spaces.active)]
    if len(right) != 1:
        return None
    left = next(area for area in areas if area != right[0])
    if (left.x >= right[0].x or abs(left.y - right[0].y) > 8 or
            abs(left.height - right[0].height) > 8):
        return None
    return left, right[0]


def _key(window, scene=None):
    return window.as_pointer(), window.screen.as_pointer(), (scene or window.scene).as_pointer()


def _region(area):
    return next((r for r in area.regions if r.type == 'WINDOW'), None)


def _rectangle(area):
    from .custom_camera_preview import available_rectangle
    region = _region(area)
    if region is None:
        return 0., 0., 1., 1.
    return available_rectangle(area, region)


def _signature(areas):
    return tuple((area.as_pointer(), area.width, area.height,
                  area.spaces.active.show_region_ui,
                  area.spaces.active.show_region_toolbar, tuple(_rectangle(area)))
                 for area in areas)


def _navigation(space):
    rv = space.region_3d
    return dict(view_perspective=rv.view_perspective,
                view_rotation=rv.view_rotation.copy(), view_location=rv.view_location.copy(),
                view_distance=rv.view_distance, view_camera_zoom=rv.view_camera_zoom,
                view_camera_offset=tuple(rv.view_camera_offset))


def _restore_navigation(space, values, *, free=False):
    for key, value in values.items():
        setattr(space.region_3d, key, value)
    if free:
        space.region_3d.view_perspective = 'PERSP'


def _presentation(space):
    return dict(shading={name: getattr(space.shading, name) for name in
                         ('type', 'use_scene_world', 'use_scene_lights')},
                chrome={name: getattr(space, name) for name in
                        ('show_region_ui', 'show_region_toolbar', 'show_region_tool_header', 'show_gizmo')},
                overlays=space.overlay.show_overlays)


def _restore_presentation(space, values):
    for name, value in values['shading'].items():
        setattr(space.shading, name, value)
    for name, value in values['chrome'].items():
        setattr(space, name, value)
    space.overlay.show_overlays = values['overlays']


def _source_camera(scene, areas):
    for area in areas:
        camera = area.spaces.active.camera
        if camera and not camera.name.startswith(('SplitRecon.', 'SplitTexture.', 'SplitMappoTexture.')):
            return camera
    camera = scene.camera
    if camera and not camera.name.startswith(('SplitRecon.', 'SplitTexture.', 'SplitMappoTexture.')):
        return camera
    return scene.objects.get('WFRL.Camera.T1.FrontQuarter')


def _leave_local(window, area):
    import bpy
    if area.spaces.active.local_view:
        with bpy.context.temp_override(window=window, area=area, region=_region(area)):
            bpy.ops.view3d.localview(frame_selected=False)


def _isolate(window, area, scene):
    """Use native local view while preserving the user's selection."""
    import bpy
    space = area.spaces.active
    texture = texture_adapter(scene)
    owned = (texture.owned(scene) if texture.active(scene) else
             [obj for obj in scene.objects if obj.name.startswith('SplitRecon.')])
    if not space.local_view:
        selected = list(bpy.context.selected_objects)
        active = bpy.context.view_layer.objects.active
        seed = next((obj for obj in owned if obj.type == 'MESH' and not obj.hide_get()), None)
        if seed is None:
            raise ValueError('重建网格尚未就绪')
        old_select = seed.hide_select
        try:
            for obj in selected:
                obj.select_set(False)
            seed.hide_select = False
            seed.select_set(True)
            bpy.context.view_layer.objects.active = seed
            with bpy.context.temp_override(window=window, area=area, region=_region(area)):
                bpy.ops.view3d.localview(frame_selected=False)
        finally:
            seed.select_set(False)
            seed.hide_select = old_select
            for obj in selected:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = active
    owned_ids = {obj.as_pointer() for obj in owned}
    for obj in scene.objects:
        obj.local_view_set(space, obj.as_pointer() in owned_ids)


def _configure(window, areas, *, navigation=None, reset_view=False):
    scene = window.scene
    left_area, right_area = areas
    left, right = (area.spaces.active for area in areas)
    _leave_local(window, left_area)
    camera = _source_camera(scene, [left_area])
    if camera is None:
        raise ValueError('MAPPO 场景相机尚未就绪')
    scene.camera = camera
    left.camera = camera
    left.use_local_camera = False
    left.lock_camera = False
    left.lock_object = None
    left.lock_cursor = False
    left.show_region_ui = True
    left.show_region_toolbar = True
    left.show_region_tool_header = True
    left.show_gizmo = True
    left.shading.type = 'MATERIAL'
    left.shading.use_scene_world = True
    left.shading.use_scene_lights = True
    if navigation:
        _restore_navigation(left, navigation, free=True)
    elif reset_view:
        left.region_3d.view_perspective = 'PERSP'
    _sidebar_category(left_area)
    _isolate(window, right_area, scene)
    right.camera = reconstruction_camera(scene)
    right.use_local_camera = True
    right.lock_camera = False
    right.show_region_ui = False
    right.show_region_toolbar = False
    right.show_region_tool_header = False
    right.show_gizmo = False
    right.overlay.show_overlays = False
    right.shading.type = 'MATERIAL' if texture_adapter(scene).active(scene) else 'SOLID'
    right.shading.use_scene_world = False
    right.shading.use_scene_lights = False
    right.shading.color_type = 'MATERIAL'
    if reset_view:
        right.region_3d.view_perspective = 'CAMERA'
    for area in areas:
        area.tag_redraw()


def _sidebar_category(area):
    """A newly loaded UI region can expose its category as read-only briefly."""
    for region in area.regions:
        if region.type != 'UI':
            continue
        try:
            if region.active_panel_category != 'MAPPO':
                region.active_panel_category = 'MAPPO'
            return region.active_panel_category == 'MAPPO'
        except (AttributeError, RuntimeError):
            # Keep the existing tab and visible sidebar; retry after redraw.
            area.tag_redraw()
            return False
    return False


def _corners(low, high):
    return [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1])
            for z in (low[2], high[2])]


def _saved_corners(obj):
    """Cache bounds of all saved poses; resize callbacks only project 8 corners."""
    import numpy as np
    texture_bounds = obj.get('split_texture_bounds') if hasattr(obj, 'get') else None
    if texture_bounds is not None and len(texture_bounds) == 6:
        return _corners(texture_bounds[:3], texture_bounds[3:])
    keys = obj.data.shape_keys
    if not keys:
        return tuple(tuple(v) for v in obj.bound_box)
    key = (obj.data.as_pointer(), keys.as_pointer(), len(keys.key_blocks), len(obj.data.vertices))
    if key not in _BOUNDS:
        low, high = np.full(3, np.inf), np.full(3, -np.inf)
        coordinates = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
        for pose in keys.key_blocks:
            pose.data.foreach_get('co', coordinates)
            xyz = coordinates.reshape(-1, 3)
            low = np.minimum(low, xyz.min(axis=0))
            high = np.maximum(high, xyz.max(axis=0))
        _BOUNDS[key] = _corners(low, high)
    return _BOUNDS[key]


def _points(scene, area, *, reconstruction):
    import bpy
    from mathutils import Vector
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        if reconstruction:
            texture = texture_adapter(scene)
            prefix = texture.PREFIX if texture.active(scene) else 'SplitRecon.'
            if not obj.name.startswith(prefix):
                continue
            corners = _saved_corners(obj)
        else:
            if not obj.name.startswith('WFRL.Turbine.T1.') or not obj.visible_get(viewport=area.spaces.active):
                continue
            corners = obj.evaluated_get(depsgraph).bound_box
        matrix = obj.matrix_world
        points.extend(matrix @ Vector(corner) for corner in corners)
    if not reconstruction and points:
        # Frame the reconstructed T1, retaining the other turbines as background.
        # A hub-centred envelope covers rotor rotation without seeking the source
        # timeline. The margin is presentation padding, not a geometry change.
        rotor = scene.objects.get('WFRL.Turbine.T1.Rotor')
        root = scene.objects.get('WFRL.Turbine.T1')
        if rotor is not None and root is not None:
            hub = rotor.matrix_world.translation
            radius = float(root.get('rotor_diameter_m', 0.)) / 2
            for obj in scene.objects:
                if obj.name.startswith('WFRL.Turbine.T1.Blade') and obj.type == 'MESH':
                    radius = max(radius, max((obj.matrix_world @ Vector(corner) - hub).length
                                            for corner in obj.evaluated_get(depsgraph).bound_box))
            if radius > 0:
                radius = radius * 1.15 + 2.
                points.extend(hub + Vector(corner) for corner in
                              _corners((-radius,) * 3, (radius,) * 3))
    return points


def _project(area, points):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    region, rv = _region(area), area.spaces.active.region_3d
    rv.update()
    projected = [location_3d_to_region_2d(region, rv, point) for point in points]
    projected = [point for point in projected if point is not None]
    if not projected:
        return None
    xs, ys = [point.x for point in projected], [point.y for point in projected]
    return min(xs), min(ys), max(xs), max(ys)


def _fit_camera(area, points):
    """Fit via viewport pan/zoom; only the private display camera may widen."""
    import bpy
    rv = area.spaces.active.region_3d
    if rv.view_perspective != 'CAMERA' or not points:
        return
    scale = bpy.context.preferences.system.ui_scale
    x, y, width, height = _rectangle(area)
    margin, title = 20 * scale, 64 * scale
    target = (x + margin, y + margin, max(1, width - 2 * margin), max(1, height - title - 2 * margin))
    rv.view_camera_zoom = 0
    rv.view_camera_offset = (0, 0)
    bounds = _project(area, points)
    if bounds is None:
        return
    factor = min(target[2] / max(1, bounds[2] - bounds[0]),
                 target[3] / max(1, bounds[3] - bounds[1]))
    minimum_factor = (math.sqrt(2) - .6) ** 2 / 2
    camera = area.spaces.active.camera
    if (factor < minimum_factor and camera and
            (camera.name == 'SplitRecon.Camera' or camera.get('split_texture_owned'))
            and camera.data.type == 'ORTHO'):
        # Blender clamps viewport zoom at -30. Only this presentation camera
        # may widen to fit extreme/narrow panes; source/calibration stays intact.
        camera.data.ortho_scale *= minimum_factor / max(factor, 1e-6) * 1.02
        bpy.context.view_layer.update()
        bounds = _project(area, points)
        if bounds is None:
            return
        factor = min(target[2] / max(1, bounds[2] - bounds[0]),
                     target[3] / max(1, bounds[3] - bounds[1]))
    rv.view_camera_zoom = max(-30, min(600, (math.sqrt(2 * factor) - math.sqrt(2)) * 50))
    bounds = _project(area, points)
    if bounds is None:
        return
    center = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    rv.view_camera_offset = (.01, .01)
    shifted = _project(area, points)
    if shifted is None:
        rv.view_camera_offset = (0, 0)
        return
    shift = ((shifted[0] + shifted[2]) / 2 - center[0],
             (shifted[1] + shifted[3]) / 2 - center[1])
    target_center = target[0] + target[2] / 2, target[1] + target[3] / 2
    rv.view_camera_offset = tuple(.01 * (target_center[i] - center[i]) / shift[i]
                                  if abs(shift[i]) > 1e-6 else 0. for i in (0, 1))
    rv.update()
    area.tag_redraw()


def _perspective_distance(points, scale_x, scale_y, bounds, near=.1):
    """Solve all four perspective-frustum inequalities without screen feedback.

    ``points`` are relative to the geometry centre in the current view's axes;
    their +Z points toward the viewer. The result is an orbit distance and the
    lateral orbit-centre offset needed to centre the unobscured canvas.
    """
    left, bottom, right, top = bounds
    cx, cy = (left + right) / 2, (bottom + top) / 2
    if min(scale_x, scale_y, right - left, top - bottom) <= 0:
        raise ValueError('可用三维视口尺寸无效')
    distance = max(1., max(point[2] + max(.1, near * 2) for point in points))
    for x, y, z in points:
        distance = max(distance,
            (scale_x * x + right * z) / (right - cx),
            (-scale_x * x - left * z) / (cx - left),
            (scale_y * y + top * z) / (top - cy),
            (-scale_y * y - bottom * z) / (cy - bottom))
    distance *= 1.06
    return distance, (-cx * distance / scale_x, -cy * distance / scale_y, 0.)


def _fit_free(area, points):
    """One analytic fit; never iterate on matrices awaiting Blender redraw."""
    import bpy
    from mathutils import Vector
    if not points:
        return
    rv = area.spaces.active.region_3d
    low = [min(point[i] for point in points) for i in range(3)]
    high = [max(point[i] for point in points) for i in range(3)]
    center = Vector([(a + b) / 2 for a, b in zip(low, high)])
    rv.view_perspective = 'PERSP'
    projection = rv.window_matrix.copy()
    if abs(projection[3][3]) > 1e-6:
        # CAMERA/ORTHO -> PERSP needs one redraw. The staged second fit handles
        # this transition; do not derive a perspective distance from old optics.
        area.tag_redraw()
        return
    rotation = rv.view_rotation.copy()
    inverse = rotation.inverted()
    local = [inverse @ (point - center) for point in points]
    x, y, width, height = _rectangle(area)
    margin = 24 * bpy.context.preferences.system.ui_scale
    region = _region(area)
    bounds = (2 * (x + margin) / region.width - 1 + projection[0][2],
              2 * (y + margin) / region.height - 1 + projection[1][2],
              2 * (x + width - margin) / region.width - 1 + projection[0][2],
              2 * (y + height - margin) / region.height - 1 + projection[1][2])
    distance, offset = _perspective_distance(local, projection[0][0], projection[1][1],
                                            bounds, area.spaces.active.clip_start)
    rv.view_distance = distance
    rv.view_location = center + rotation @ Vector(offset)
    rv.update()
    area.tag_redraw()


def _fit_pair(window, areas, *, explicit=False):
    import bpy
    with bpy.context.temp_override(window=window, area=areas[0], region=_region(areas[0])):
        if explicit:
            _fit_free(areas[0], _points(window.scene, areas[0], reconstruction=False))
        # Automatic resize fitting never changes a manually orbited right view.
        _fit_camera(areas[1], _points(window.scene, areas[1], reconstruction=True))


def _layout_factor(areas):
    """Desired native split ratio, accounting for the left MAPPO sidebar."""
    import bpy
    left = areas[0]
    scale = bpy.context.preferences.system.ui_scale
    paired = (len(areas) == 2 and abs(areas[0].y - areas[1].y) <= 8 and
              abs(areas[0].height - areas[1].height) <= 8)
    total = sum(a.width for a in areas) if paired else max(a.width for a in areas)
    left_chrome = max(0., left.width - _rectangle(left)[2])
    if not left.spaces.active.show_region_ui:
        sidebar = next((r.width for r in left.regions if r.type == 'UI' and r.width > 16), 300 * scale)
        left_chrome += sidebar
    right_chrome = max(0., areas[1].width - _rectangle(areas[1])[2]) if paired else 0.
    factor = (total + left_chrome - right_chrome) / (2 * max(1, total))
    balanced = paired and abs((left.width - left_chrome) -
                              (areas[1].width - right_chrome)) <= 24 * scale
    return max(.3, min(.75, factor)), balanced


def _timeline_all(window):
    import bpy
    for area in window.screen.areas:
        if area.type == 'DOPESHEET_EDITOR' and area.spaces.active.mode == 'TIMELINE':
            with bpy.context.temp_override(window=window, area=area, region=_region(area)):
                bpy.ops.action.view_all()


def _remember(window, areas):
    _SESSIONS[_key(window)] = dict(signature=_signature(areas), changed=None,
                                  areas=tuple(a.as_pointer() for a in areas))


def _set_error(scene, exc):
    scene['split_review_status'] = 'ERROR: ' + str(exc)
    _redraw_scene(scene)
    print('WFRL reconstruction split:', exc)


def _redraw_scene(scene):
    """Publish lifecycle status changes without waiting for user interaction."""
    import bpy
    manager = getattr(getattr(bpy, 'context', None), 'window_manager', None)
    for window in getattr(manager, 'windows', ()):
        if window.scene == scene:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


def enter(context, reset_layout=True, use_texture=None, use_same_source=False):
    """Restore the current comparison, or explicitly enter synthetic texture.

    A saved geometry example keeps its original right-hand data when returning
    or repairing its layout. Only the texture entry explicitly upgrades it.
    """
    import bpy
    from . import cameras, split_surface_texture, split_mappo_texture, split_texture_settings
    scene, window = context.scene, context.window
    if not available(scene) or window is None or bpy.app.background:
        raise ValueError('请先载入 MAPPO 回放或打开 MAPPO 重建示例')
    areas = _views(window.screen)
    if not areas:
        raise ValueError('请先打开三维视口')
    if cameras._LAYOUT_JOB is not None or _key(window) in _JOBS:
        raise ValueError('请等待当前布局调整完成')
    source_area = next((a for a in areas if not is_reconstruction_view(scene, a.spaces.active)), areas[0])
    source = _source_camera(scene, [source_area])
    if source is None:
        raise ValueError('MAPPO 场景相机尚未就绪')
    previous_session = _SESSIONS.get(_key(window))
    try:
        with split_texture_settings.rollback_on_error(scene):
            if use_same_source:
                split_mappo_texture.ensure(scene)
                # The explicit synchronization entry starts with the full rotor;
                # CURRENT restores the user's chosen full/detail view instead.
                scene[split_mappo_texture.DETAIL] = False
            elif use_texture is True:
                split_surface_texture.ensure(scene)
            else:
                texture = texture_adapter(scene)
                if texture.active(scene) or not _legacy_available(scene):
                    texture.ensure(scene)
            texture = texture_adapter(scene)
            scene['split_review_enabled'] = True
            for key, value in _METADATA.items():
                if key not in scene:
                    scene[key] = value
            job = dict(window=window, scene=scene, screen=window.screen, stage='layout',
                       navigation=_navigation(source_area.spaces.active), deadline=time.monotonic() + 15,
                       frame=scene.frame_current, subframe=scene.frame_subframe,
                       explicit=True, detail=bool(texture.active(scene) and
                                                 scene.get(texture.DETAIL, texture is split_surface_texture)))
            scene['split_review_status'] = 'PREPARING'
            _SESSIONS.pop(_key(window), None)
            _JOBS[_key(window)] = job
            factor, balanced = _layout_factor(areas)
            rebuild = bool(reset_layout and len(areas) > 1 and not balanced)
            if _pair(scene, window.screen) is None or rebuild:
                with bpy.context.temp_override(window=window, area=source_area):
                    cameras.set_view_layout(context, 'DUAL',
                        camera_names=(source.name, reconstruction_camera(scene).name),
                        split_factor=factor, rebuild=rebuild)
            _ensure_timer()
    except Exception:
        _JOBS.pop(_key(window), None)
        if previous_session is not None:
            _SESSIONS[_key(window)] = previous_session
        raise
    return True


def fit(context, reset_layout=False):
    from . import cameras
    if cameras._LAYOUT_JOB is not None or _key(context.window) in _JOBS:
        raise ValueError('请等待当前布局调整完成')
    texture = texture_adapter(context.scene)
    if texture.active(context.scene):
        context.scene[texture.DETAIL] = False
    if reset_layout or _pair(context.scene, context.window.screen) is None:
        return enter(context, reset_layout=True)
    if not enabled(context.scene):
        return enter(context, reset_layout=reset_layout)
    areas = _pair(context.scene, context.window.screen)
    areas[0].spaces.active.region_3d.view_perspective = 'PERSP'
    areas[1].spaces.active.region_3d.view_perspective = 'CAMERA'
    now = time.monotonic()
    _JOBS[_key(context.window)] = dict(window=context.window, scene=context.scene,
        screen=context.window.screen, stage='fit', settle=now + _DEBOUNCE, deadline=now + 15)
    context.scene['split_review_status'] = 'PREPARING'
    _ensure_timer()
    return True


def exit_mode(context):
    import bpy
    from . import cameras
    scene, window = context.scene, context.window
    areas = _views(window.screen)
    if not areas:
        raise ValueError('请先打开三维视口')
    if cameras._LAYOUT_JOB is not None:
        raise ValueError('请等待当前布局调整完成')
    pair = _pair(scene, window.screen)
    source_area = pair[0] if pair else areas[0]
    navigation = _navigation(source_area.spaces.active)
    presentation = _presentation(source_area.spaces.active)
    source = _source_camera(scene, [source_area])
    scene['split_review_enabled'] = False
    for key in list(_JOBS):
        if key[2] == scene.as_pointer():
            del _JOBS[key]
    for key in list(_SESSIONS):
        if key[2] == scene.as_pointer():
            del _SESSIONS[key]
    # Enabled state belongs to the scene. Clean every visible right pane for
    # that scene before disabling it, including comparisons in other windows.
    recon_camera = reconstruction_camera(scene)
    for other_window in bpy.context.window_manager.windows:
        if other_window == window or other_window.scene != scene:
            continue
        other_areas = _views(other_window.screen)
        demo_area = next((a for a in other_areas if a.spaces.active.camera != recon_camera), None)
        demo_navigation = _navigation(demo_area.spaces.active) if demo_area else navigation
        demo_presentation = _presentation(demo_area.spaces.active) if demo_area else presentation
        demo_camera = _source_camera(scene, [demo_area]) if demo_area else source
        for other_area in other_areas:
            space = other_area.spaces.active
            if space.camera != recon_camera:
                continue
            _leave_local(other_window, other_area)
            space.camera = demo_camera
            space.use_local_camera = False
            _restore_presentation(space, demo_presentation)
            _restore_navigation(space, demo_navigation)
            other_area.tag_redraw()
    for area in areas:
        _leave_local(window, area)
        area.spaces.active.camera = source
        area.spaces.active.use_local_camera = False
        _restore_navigation(area.spaces.active, navigation)
        _restore_presentation(area.spaces.active, presentation)
    if source:
        scene.camera = source
    _JOBS[_key(window)] = dict(window=window, scene=scene, screen=window.screen, stage='exit',
        navigation=navigation, presentation=presentation, source=source, deadline=time.monotonic() + 15)
    with bpy.context.temp_override(window=window, area=source_area):
        cameras.set_view_layout(context, 'SINGLE', camera_names=(source.name,) if source else None)
    scene['split_review_status'] = 'EXITING'
    _ensure_timer()
    return True


def _advance_jobs(now):
    import bpy
    from . import cameras
    for key, job in list(_JOBS.items()):
        scene = job['scene']
        try:
            window = job['window']
            if window.screen != job['screen'] or window.scene != scene:
                del _JOBS[key]
                continue
            if now > job['deadline']:
                raise ValueError('分屏布局超时，请再次点击恢复布局')
            if cameras._LAYOUT_JOB is not None:
                continue
            if scene.get('wfrl_view_layout_error'):
                raise ValueError(scene['wfrl_view_layout_error'])
            areas = _views(window.screen)
            if job['stage'] == 'exit':
                if len(areas) != 1:
                    continue
                space = areas[0].spaces.active
                space.camera = job['source']
                space.use_local_camera = False
                _restore_navigation(space, job['navigation'])
                _restore_presentation(space, job['presentation'])
                scene['split_review_status'] = 'OFF'
                _redraw_scene(scene)
                del _JOBS[key]
                continue
            if len(areas) != 2:
                continue
            if job['stage'] == 'layout':
                with bpy.context.temp_override(window=window, area=areas[0]):
                    _configure(window, areas, navigation=job['navigation'], reset_view=True)
                job['stage'], job['settle'] = 'balance', now + _DEBOUNCE
                continue
            if now < job['settle']:
                continue
            if job['stage'] == 'balance':
                _sidebar_category(areas[0])
                job['stage'], job['settle'] = 'fit', now + _DEBOUNCE
                continue
            if job['stage'] in {'fit', 'refit'}:
                _sidebar_category(areas[0])
                _fit_pair(window, areas, explicit=job.get('explicit', True))
                if job.get('explicit', True):
                    _timeline_all(window)
                job['signature'] = _signature(areas)
                # A viewport camera update needs a redraw before its matrices
                # reflect the new pane/optics. READY is only published afterwards.
                job['stage'] = 'refit' if job['stage'] == 'fit' else 'ready'
                job['settle'] = now + _DEBOUNCE
                continue
            if job.get('signature') != _signature(areas):
                # A panel may still be animating or the user may be dragging a
                # divider. Keep PREPARING until a complete fit survives redraw.
                job['stage'], job['settle'] = 'fit', now + _DEBOUNCE
                continue
            if job.get('detail'):
                with bpy.context.temp_override(window=window, area=areas[1], region=_region(areas[1])):
                    texture_adapter(scene).focus(bpy.context, areas[1])
            _remember(window, areas)
            scene['split_review_status'] = 'READY'
            _redraw_scene(scene)
            del _JOBS[key]
        except (ReferenceError, RuntimeError, ValueError, TypeError, AttributeError, StopIteration) as exc:
            try:
                _set_error(scene, exc)
            except ReferenceError:
                pass
            _JOBS.pop(key, None)
            _SESSIONS[key] = {'failed': True}


def _tick():
    import bpy
    if not _REGISTERED:
        return None
    now = time.monotonic()
    _advance_jobs(now)
    for window in bpy.context.window_manager.windows:
        try:
            scene, key = window.scene, _key(window)
            if not enabled(scene) or key in _JOBS:
                continue
            texture = texture_adapter(scene)
            if texture.active(scene):
                texture.ensure(scene)
            areas = _pair(scene, window.screen)
            if areas is None:
                # An unrelated workspace remains untouched. Enter is the explicit
                # recovery action when that workspace does not contain a pair.
                continue
            state = _SESSIONS.get(key)
            if state is not None and state.get('failed'):
                continue
            if state is None:
                for name, value in _METADATA.items():
                    if name not in scene:
                        scene[name] = value
                with bpy.context.temp_override(window=window, area=areas[0]):
                    _configure(window, areas)
                _timeline_all(window)
                _remember(window, areas)
                _SESSIONS[key]['changed'] = now
                _SESSIONS[key]['initial'] = True
                scene['split_review_status'] = 'PREPARING'
                continue
            signature = _signature(areas)
            if signature != state['signature']:
                state['signature'], state['changed'] = signature, now
                scene['split_review_status'] = 'PREPARING'
            elif state['changed'] is not None and now - state['changed'] >= _DEBOUNCE:
                _JOBS[key] = dict(window=window, scene=scene, screen=window.screen,
                    stage='fit', settle=now, deadline=now + 15, explicit=False)
                state['changed'] = None
                scene['split_review_status'] = 'PREPARING'
        except (ReferenceError, RuntimeError, ValueError, TypeError, AttributeError, StopIteration) as exc:
            try:
                _set_error(window.scene, exc)
            except ReferenceError:
                pass
    return _INTERVAL


def _ensure_timer():
    import bpy
    if not bpy.app.background and _REGISTERED and not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=.1, persistent=True)


def _load_pre(_unused):
    from . import cameras, split_surface_texture, split_mappo_texture
    cameras.cancel_view_layout()
    _SESSIONS.clear()
    _JOBS.clear()
    _BOUNDS.clear()
    split_surface_texture.clear_runtime()
    split_mappo_texture.clear_runtime()


def _load_post(_unused):
    _ensure_timer()


def register():
    import bpy
    from . import split_mappo_texture
    global _REGISTERED
    _REGISTERED = True
    for collection, callback in ((bpy.app.handlers.load_pre, _load_pre),
                                 (bpy.app.handlers.load_post, _load_post),
                                 (bpy.app.handlers.frame_change_post, split_mappo_texture.frame_changed)):
        bpy.app.handlers.persistent(callback)
        if callback not in collection:
            collection.append(callback)
    _ensure_timer()


def unregister():
    import bpy
    from . import split_mappo_texture
    global _REGISTERED
    _REGISTERED = False
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for collection, callback in ((bpy.app.handlers.load_pre, _load_pre),
                                 (bpy.app.handlers.load_post, _load_post),
                                 (bpy.app.handlers.frame_change_post, split_mappo_texture.frame_changed)):
        if callback in collection:
            collection.remove(callback)
    _load_pre(None)
