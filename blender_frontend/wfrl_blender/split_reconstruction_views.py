"""Split comparison viewport roles, navigation, presentation and layout."""
from __future__ import annotations


def _views(screen):
    return sorted((area for area in screen.areas if area.type == 'VIEW_3D' and
                   area.width > 0 and area.height > 0), key=lambda area: (area.x, -area.y))


def _pair(scene, screen):
    areas = _core._views(screen)
    if len(areas) != 2:
        return None
    right = [area for area in areas if _core.is_reconstruction_view(scene, area.spaces.active)]
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
    region = _core._region(area)
    if region is None:
        return 0., 0., 1., 1.
    return available_rectangle(area, region)


def _signature(areas):
    return tuple((area.as_pointer(), area.width, area.height,
                  area.spaces.active.show_region_ui,
                  area.spaces.active.show_region_toolbar, tuple(_core._rectangle(area)))
                 for area in areas)


def _navigation(space):
    rv = space.region_3d
    return dict(view_perspective=rv.view_perspective,
                view_rotation=rv.view_rotation.copy(), view_location=rv.view_location.copy(),
                view_distance=rv.view_distance, view_camera_zoom=rv.view_camera_zoom,
                view_camera_offset=tuple(rv.view_camera_offset), lens=space.lens)


def _restore_navigation(space, values, *, free=False):
    for key, value in values.items():
        setattr(space if key == 'lens' else space.region_3d, key, value)
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
        with bpy.context.temp_override(window=window, area=area, region=_core._region(area)):
            bpy.ops.view3d.localview(frame_selected=False)


def _isolate(window, area, scene):
    """Use native local view while preserving the user's selection."""
    import bpy
    space = area.spaces.active
    texture = _core.texture_adapter(scene)
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
            with bpy.context.temp_override(window=window, area=area, region=_core._region(area)):
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
    _core.ensure_geometry_structure(scene)
    left_area, right_area = areas
    left, right = (area.spaces.active for area in areas)
    _core._leave_local(window, left_area)
    camera = _core._source_camera(scene, [left_area])
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
        _core._restore_navigation(left, navigation, free=True)
    elif reset_view:
        left.region_3d.view_perspective = 'PERSP'
    _core._sidebar_category(left_area)
    _core._isolate(window, right_area, scene)
    right.camera = _core.reconstruction_camera(scene)
    if right.camera is not None:
        from .cameras import clear_camera_mask
        clear_camera_mask(right.camera)
    right.use_local_camera = True
    right.lock_camera = False
    right.lock_object = None
    right.lock_cursor = False
    right.show_region_ui = False
    right.show_region_toolbar = False
    right.show_region_tool_header = False
    right.show_gizmo = False
    right.overlay.show_overlays = False
    right.shading.type = 'MATERIAL' if _core.texture_adapter(scene).active(scene) else 'SOLID'
    right.shading.use_scene_world = False
    right.shading.use_scene_lights = False
    right.shading.color_type = 'MATERIAL'
    if reset_view:
        right.region_3d.view_perspective = 'CAMERA'
    for area in areas:
        area.tag_redraw()


def _sidebar_category(area):
    from .cameras import select_sidebar_category
    return select_sidebar_category(area, 'MAPPO')


def _layout_factor(areas):
    """Desired native split ratio, accounting for the left MAPPO sidebar."""
    import bpy
    left = areas[0]
    scale = bpy.context.preferences.system.ui_scale
    paired = (len(areas) == 2 and abs(areas[0].y - areas[1].y) <= 8 and
              abs(areas[0].height - areas[1].height) <= 8)
    total = sum(a.width for a in areas) if paired else max(a.width for a in areas)
    left_chrome = max(0., left.width - _core._rectangle(left)[2])
    if not left.spaces.active.show_region_ui:
        sidebar = next((r.width for r in left.regions if r.type == 'UI' and r.width > 16), 300 * scale)
        left_chrome += sidebar
    right_chrome = max(0., areas[1].width - _core._rectangle(areas[1])[2]) if paired else 0.
    factor = (total + left_chrome - right_chrome) / (2 * max(1, total))
    balanced = paired and abs((left.width - left_chrome) -
                              (areas[1].width - right_chrome)) <= 24 * scale
    return max(.3, min(.75, factor)), balanced


def _timeline_all(window):
    import bpy
    for area in window.screen.areas:
        if area.type == 'DOPESHEET_EDITOR' and area.spaces.active.mode == 'TIMELINE':
            with bpy.context.temp_override(window=window, area=area, region=_core._region(area)):
                bpy.ops.action.view_all()


# The facade imports these functions, so bind its shared namespace last.
from . import split_reconstruction as _core
