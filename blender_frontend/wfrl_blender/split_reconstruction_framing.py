"""Saved-pose bounds and camera/free-view framing for split comparisons."""
from __future__ import annotations


def _corners(low, high):
    return [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1])
            for z in (low[2], high[2])]


def _saved_corners(obj):
    """Cache bounds of all saved poses; resize callbacks only project 8 corners."""
    import numpy as np
    texture_bounds = obj.get('split_texture_bounds') if hasattr(obj, 'get') else None
    if texture_bounds is not None and len(texture_bounds) == 6:
        return _core._corners(texture_bounds[:3], texture_bounds[3:])
    keys = obj.data.shape_keys
    if not keys:
        return tuple(tuple(v) for v in obj.bound_box)
    key = (obj.data.as_pointer(), keys.as_pointer(), len(keys.key_blocks), len(obj.data.vertices))
    if key not in _core._BOUNDS:
        low, high = np.full(3, np.inf), np.full(3, -np.inf)
        coordinates = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
        for pose in keys.key_blocks:
            pose.data.foreach_get('co', coordinates)
            xyz = coordinates.reshape(-1, 3)
            low = np.minimum(low, xyz.min(axis=0))
            high = np.maximum(high, xyz.max(axis=0))
        _core._BOUNDS[key] = _core._corners(low, high)
    return _core._BOUNDS[key]


def _points(scene, area, *, reconstruction):
    import bpy
    from mathutils import Vector
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        if reconstruction:
            texture = _core.texture_adapter(scene)
            prefix = texture.PREFIX if texture.active(scene) else 'SplitRecon.'
            if not obj.name.startswith(prefix):
                continue
            corners = _core._saved_corners(obj)
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
                              _core._corners((-radius,) * 3, (radius,) * 3))
    return points


def _project(area, points):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    region, rv = _core._region(area), area.spaces.active.region_3d
    rv.update()
    projected = [location_3d_to_region_2d(region, rv, point) for point in points]
    projected = [point for point in projected if point is not None]
    if not projected:
        return None
    xs, ys = [point.x for point in projected], [point.y for point in projected]
    return min(xs), min(ys), max(xs), max(ys)


def _fit_camera(area, points, *, orbit_center):
    """Fit via viewport pan/zoom; only the private display camera may widen."""
    import bpy
    rv = area.spaces.active.region_3d
    if rv.view_perspective != 'CAMERA' or not points:
        return
    camera = area.spaces.active.camera
    if camera and (camera.name == 'SplitRecon.Camera' or camera.get('split_mappo_texture_owned')):
        from mathutils import Vector
        target = Vector(orbit_center)
        # Native camera exit restores this free-view pivot. Keep it on the
        # displayed rotor while fitting the complete turbine with all points.
        rv.view_perspective = 'PERSP'
        rv.view_location = target
        rv.view_rotation = camera.matrix_world.to_quaternion()
        rv.view_distance = max(1., (camera.matrix_world.translation - target).length)
        # The native camera operator also saves the free-view orientation.
        # Assigning the visible CAMERA rotation alone leaves Blender's cached
        # exit orientation pointing at the old left-side scene.
        # A delayed camera transition can restore its cached zoom *after* the
        # framing below has fitted the full turbine, especially after reopening
        # a zoomed ORTHO inspection. Complete this programmatic transition now;
        # keep the user's smooth navigation preference for subsequent input.
        view_preferences = bpy.context.preferences.view
        smooth_view = view_preferences.smooth_view
        try:
            view_preferences.smooth_view = 0
            with bpy.context.temp_override(area=area, region=_core._region(area)):
                bpy.ops.view3d.view_camera()
        finally:
            view_preferences.smooth_view = smooth_view
    scale = bpy.context.preferences.system.ui_scale
    x, y, width, height = _core._rectangle(area)
    margin, title = 20 * scale, 64 * scale
    target = (x + margin, y + margin, max(1, width - 2 * margin), max(1, height - title - 2 * margin))
    rv.view_camera_zoom = 0
    rv.view_camera_offset = (0, 0)
    bounds = _core._project(area, points)
    if bounds is None:
        return
    factor = min(target[2] / max(1, bounds[2] - bounds[0]),
                 target[3] / max(1, bounds[3] - bounds[1]))
    minimum_factor = (_core.math.sqrt(2) - .6) ** 2 / 2
    camera = area.spaces.active.camera
    if (factor < minimum_factor and camera and
            (camera.name == 'SplitRecon.Camera' or camera.get('split_texture_owned'))
            and camera.data.type == 'ORTHO'):
        # Blender clamps viewport zoom at -30. Only this presentation camera
        # may widen to fit extreme/narrow panes; source/calibration stays intact.
        camera.data.ortho_scale *= minimum_factor / max(factor, 1e-6) * 1.02
        bpy.context.view_layer.update()
        bounds = _core._project(area, points)
        if bounds is None:
            return
        factor = min(target[2] / max(1, bounds[2] - bounds[0]),
                     target[3] / max(1, bounds[3] - bounds[1]))
    rv.view_camera_zoom = max(-30, min(600, (_core.math.sqrt(2 * factor) - _core.math.sqrt(2)) * 50))
    bounds = _core._project(area, points)
    if bounds is None:
        return
    center = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    rv.view_camera_offset = (.01, .01)
    shifted = _core._project(area, points)
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
    x, y, width, height = _core._rectangle(area)
    margin = 24 * bpy.context.preferences.system.ui_scale
    region = _core._region(area)
    bounds = (2 * (x + margin) / region.width - 1 + projection[0][2],
              2 * (y + margin) / region.height - 1 + projection[1][2],
              2 * (x + width - margin) / region.width - 1 + projection[0][2],
              2 * (y + height - margin) / region.height - 1 + projection[1][2])
    distance, offset = _core._perspective_distance(local, projection[0][0], projection[1][1],
                                            bounds, area.spaces.active.clip_start)
    rv.view_distance = distance
    rv.view_location = center + rotation @ Vector(offset)
    rv.update()
    area.tag_redraw()


def _fit_pair(window, areas, *, explicit=False):
    import bpy
    from . import split_turbine_context
    with bpy.context.temp_override(window=window, area=areas[0], region=_core._region(areas[0])):
        if explicit:
            _core._fit_free(areas[0], _core._points(window.scene, areas[0], reconstruction=False))
        # Automatic resize fitting never changes a manually orbited right view.
        _core._fit_camera(areas[1], _core._points(window.scene, areas[1], reconstruction=True),
                    orbit_center=split_turbine_context.rotor_center(
                        window.scene, offset=_core._display_origin(window.scene)))


# The facade imports these functions, so bind its shared namespace last.
from . import split_reconstruction as _core
