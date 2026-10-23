"""Owned rigid wind-farm scenes, separate from recorded and backend scenes."""
from functools import partial
import json
import math
import uuid

_VIEWS = {}
_RESUME = {}
MANUAL_BLADE_MODEL = 'rootkink'


def is_manual(scene):
    return scene is not None and scene.get('wfrl_scene_kind') == 'manual'


def entry_error(context):
    from . import runtime
    if context.window is None:
        return '需要 Blender 窗口才能切换场景'
    if context.mode != 'OBJECT':
        return '请先退出对象编辑模式'
    state = runtime.get_state()
    client = runtime._client
    if (state.connection not in {'LOCAL DEMO', 'OFFLINE RESULTS', 'DISCONNECTED'}
            or (client is not None and client.status != 'DISCONNECTED')
            or runtime._pending_command or not state.allows('configure')):
        return '请先停止并断开后端，再进入手动控制'
    return ''


def can_enter(context):
    return not entry_error(context)


def _remember_view(context):
    saved = []
    for area in context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        space = area.spaces.active
        region = space.region_3d
        saved.append((area, space, dict(
            location=region.view_location.copy(), rotation=region.view_rotation.copy(),
            distance=region.view_distance, perspective=region.view_perspective,
            camera_zoom=region.view_camera_zoom, camera_offset=tuple(region.view_camera_offset),
            camera=space.camera, local_camera=space.use_local_camera,
            clip_start=space.clip_start, clip_end=space.clip_end,
            shading=space.shading.type, color_type=space.shading.color_type,
            scene_world=space.shading.use_scene_world,
            scene_lights=space.shading.use_scene_lights,
            overlays=space.overlay.show_overlays, sidebar=space.show_region_ui)))
    _VIEWS[context.window.as_pointer()] = saved


def _save_return_view(context, scene, fallback_scene=None):
    records = []
    areas = list(context.screen.areas)
    for area, space, saved in _VIEWS.get(context.window.as_pointer(), ()):
        record = dict(saved)
        record['location'] = list(saved['location'])
        record['rotation'] = list(saved['rotation'])
        record['camera'] = saved['camera'].name if saved['camera'] else None
        record['area_index'] = areas.index(area)
        records.append(record)
    if records:
        scene['wfrl_manual_return_views'] = json.dumps(records)
    elif fallback_scene is not None:
        saved = fallback_scene.get('wfrl_manual_return_views')
        if saved:
            scene['wfrl_manual_return_views'] = saved


def _restore_view(context, saved_scene=None):
    snapshots = _VIEWS.pop(context.window.as_pointer(), ())
    if not snapshots and saved_scene is not None:
        snapshots = []
        try:
            records = json.loads(saved_scene.get('wfrl_manual_return_views', '[]'))
            areas = list(context.screen.areas)
            for saved in records:
                index = saved['area_index']
                if index < 0 or index >= len(areas) or areas[index].type != 'VIEW_3D':
                    continue
                area = areas[index]
                saved['camera'] = context.scene.objects.get(saved['camera']) if saved['camera'] else None
                snapshots.append((area, area.spaces.active, saved))
        except (ValueError, TypeError, KeyError):
            snapshots = []
    for area, space, saved in snapshots:
        try:
            if area not in list(context.screen.areas) or space != area.spaces.active:
                continue
            region = space.region_3d
            region.view_location = saved['location']
            region.view_rotation = saved['rotation']
            region.view_distance = saved['distance']
            region.view_perspective = saved['perspective']
            if 'camera_zoom' in saved:
                region.view_camera_zoom = saved['camera_zoom']
            if 'camera_offset' in saved:
                region.view_camera_offset = saved['camera_offset']
            space.camera = saved['camera']
            space.use_local_camera = saved['local_camera']
            space.clip_start, space.clip_end = saved['clip_start'], saved['clip_end']
            space.shading.type = saved['shading']
            space.shading.color_type = saved['color_type']
            if 'scene_world' in saved:
                space.shading.use_scene_world = saved['scene_world']
            if 'scene_lights' in saved:
                space.shading.use_scene_lights = saved['scene_lights']
            space.overlay.show_overlays = saved['overlays']
            space.show_region_ui = saved['sidebar']
            area.tag_redraw()
        except (ReferenceError, RuntimeError):
            continue


def focus(context, target=None):
    """Frame once; subsequent viewport navigation remains user controlled."""
    from mathutils import Vector
    scene = context.scene
    if not is_manual(scene):
        return
    _ensure_environment(scene)
    target = target or scene.wfrl_manual_target
    entries = list(scene.wfrl_manual_turbines)
    chosen = [item for item in entries if target == 'ALL' or item.turbine_id == target]
    if not chosen:
        return
    positions = [item.yaw_object.parent.location for item in chosen if item.yaw_object]
    if not positions:
        return
    center = Vector((sum(p.x for p in positions) / len(positions),
                     sum(p.y for p in positions) / len(positions), 82.0))
    offset = Vector((-240.0, -330.0, 120.0))
    for area in context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        space = area.spaces.active
        space.use_local_camera = False
        space.camera = scene.camera
        space.clip_start, space.clip_end = .1, 10000.0
        space.shading.type = 'MATERIAL'
        space.shading.use_scene_world = True
        space.shading.use_scene_lights = True
        space.overlay.show_overlays = False
        space.show_region_ui = True
        region = space.region_3d
        region.view_location = center
        region.view_rotation = (-offset).to_track_quat('-Z', 'Y')
        region.view_distance = 800.0 if len(chosen) > 1 else 310.0
        region.view_perspective = 'PERSP'
        area.tag_redraw()


def down_view(context):
    """Observe the manual T1 geometry from its own fixed nacelle camera."""
    from . import cameras, manual_control
    scene = context.scene
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    entry = next(item for item in scene.wfrl_manual_turbines if item.turbine_id == 'T1')
    manual_control._validate_native_controls((entry.yaw_object,))
    owner = scene['wfrl_manual_owner']
    camera = cameras.ensure_gimbal(scene, 'T1', yaw_object=entry.yaw_object,
                                   name=owner + '.Camera.T1.Down', owner=owner)
    manual_control._validate_native_controls((camera,))
    cameras.down_gimbal(camera)
    scene.camera = camera
    for area in context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        space = area.spaces.active
        space.use_local_camera = False
        space.camera = camera
        space.clip_start, space.clip_end = .05, 30000.0
        space.shading.type = 'MATERIAL'
        space.shading.use_scene_world = space.shading.use_scene_lights = True
        space.overlay.show_overlays = False
        space.show_region_ui = True
        space.region_3d.view_perspective = 'CAMERA'
        cameras.fill_camera_view(area, scene)
        area.tag_redraw()
    return camera


def _environment(scene, collection, prefix):
    import bpy
    from mathutils import Vector
    from .scene_builder import _mesh_object, _configure_render
    from .atmosphere import apply_preset
    from .landscape import height, terrain_shader

    def load_image(path):
        image = bpy.data.images.load(str(path), check_existing=False)
        image.name = prefix + '.' + image.name
        image.pack()
        return image

    terrain = bpy.data.materials.new(prefix + '.terrain')
    terrain.diffuse_color = (.10, .30, .12, 1.0)
    terrain.use_nodes = True
    terrain_shader(terrain, image_loader=load_image)
    nx, ny = 80, 64
    rows = sorted({-3500 + j * 8500 / ny for j in range(ny + 1)} | {-24., 0., 24.})
    vertices = [(float(-3500 + i * 8500 / nx), float(y))
                for y in rows for i in range(nx + 1)]
    vertices = [(x, y, height(x, y)) for x, y in vertices]
    faces = [(j * (nx + 1) + i, j * (nx + 1) + i + 1,
              (j + 1) * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i)
             for j in range(len(rows) - 1) for i in range(nx)]
    ground = scene.objects.get(prefix + '.Ground')
    if ground is None:
        ground = _mesh_object(collection, prefix + '.Ground', vertices, faces, terrain)
    else:
        old_mesh = ground.data
        mesh = bpy.data.meshes.new(prefix + '.Ground.Data')
        mesh.from_pydata(vertices, [], faces)
        mesh.materials.append(terrain)
        ground.data = mesh
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
    for face in ground.data.polygons:
        face.use_smooth = True
    ground['fidelity'] = 'SYNTH'
    if scene.objects.get(prefix + '.FarGround') is None:
        _mesh_object(collection, prefix + '.FarGround',
                     [(-40000, -40000, -12), (40000, -40000, -12),
                      (40000, 40000, -12), (-40000, 40000, -12)], [(0, 1, 2, 3)], terrain)
    world = bpy.data.worlds.new(prefix + '.World')
    scene.world = world
    world.use_nodes = True
    apply_preset(scene, 'clear', quality='realtime', image_loader=load_image)
    data = bpy.data.lights.new(prefix + '.Sun', 'SUN')
    data.energy = 1.4
    data.color = (.98, .97, .95)
    data.angle = math.radians(2)
    light = scene.objects.get(prefix + '.Sun')
    if light is None:
        light = bpy.data.objects.new(prefix + '.Sun', data)
        collection.objects.link(light)
    else:
        light.data = data
    light.rotation_euler = (math.radians(25), math.radians(-30), math.radians(-25))
    data = bpy.data.cameras.new(prefix + '.Camera')
    camera = scene.objects.get(prefix + '.Camera')
    if camera is None:
        camera = bpy.data.objects.new(prefix + '.Camera', data)
        collection.objects.link(camera)
    else:
        camera.data = data
    camera.location = (-420, -850, 330)
    camera.rotation_euler = (Vector((210, 0, 80)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
    data.lens = 45
    data.clip_end = 10000
    scene.camera = camera
    _configure_render()
    scene['wfrl_manual_environment_revision'] = 1


def _ensure_environment(scene):
    if scene.get('wfrl_manual_environment_revision', 0) >= 1:
        return
    prefix = scene['wfrl_manual_owner']
    collection = scene.wfrl_manual_turbines[0].yaw_object.users_collection[0]
    _environment(scene, collection, prefix)


def _ensure_blade_model(scene):
    """Use owned rootkink meshes while retaining poses, materials and topology."""
    from .turbine_geometry import blade_mesh
    blades = [getattr(entry, f'blade{index}_object')
              for entry in scene.wfrl_manual_turbines for index in (1, 2, 3)]
    if (scene.get('wfrl_manual_blade_model') == MANUAL_BLADE_MODEL
            and all(blade.data.get('wfrl_manual_blade_model') == MANUAL_BLADE_MODEL for blade in blades)):
        return
    vertices, faces = blade_mesh(subdiv=8, ring_points=96, model=MANUAL_BLADE_MODEL)
    sources = {blade.data.as_pointer(): blade.data for blade in blades}
    for mesh in sources.values():
        if (len(mesh.vertices) != len(vertices)
                or tuple(tuple(face.vertices) for face in mesh.polygons) != tuple(faces)):
            raise ValueError('手动叶片拓扑与 rootkink 不匹配，请创建独立手动场景')
    replacements = {}
    for key, mesh in sources.items():
        owned = mesh.copy()
        owned.name = scene['wfrl_manual_owner'] + '.RootkinkBlade'
        owned.vertices.foreach_set('co', [value for vertex in vertices for value in vertex])
        owned['wfrl_manual_blade_model'] = MANUAL_BLADE_MODEL
        owned.update()
        replacements[key] = owned
    for blade in blades:
        blade.data = replacements[blade.data.as_pointer()]
    scene['wfrl_manual_blade_model'] = MANUAL_BLADE_MODEL


def _discard(scene, collection, prefix):
    """Remove only this failed construction's owned data."""
    import bpy
    if collection is not None:
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)
    bpy.data.scenes.remove(scene)
    for blocks in (bpy.data.meshes, bpy.data.curves, bpy.data.lights,
                   bpy.data.cameras, bpy.data.materials, bpy.data.worlds, bpy.data.images):
        for block in list(blocks):
            if block.name.startswith(prefix) and block.users == 0:
                blocks.remove(block)


def create(context):
    import bpy
    from . import manual_control
    from .materials import get_material
    from .scene_builder import _make_turbine, _cone, _finish
    from .scene_model import TurbineDTO
    reason = entry_error(context)
    if reason:
        raise ValueError(reason)
    source = context.scene
    if is_manual(source):
        return source
    prefix = 'WFRLManual.' + uuid.uuid4().hex[:12]
    scene = bpy.data.scenes.new('风机手动控制')
    scene['wfrl_scene_kind'] = 'manual'
    scene['wfrl_manual_owner'] = prefix
    scene['wfrl_fidelity'] = 'SYNTH'
    scene.use_fake_user = True
    scene.wfrl_manual_return_scene = source
    collection = None
    material = partial(get_material, namespace=prefix)
    try:
        context.window.scene = scene
        collection = bpy.data.collections.new(prefix + '.Scene')
        scene.collection.children.link(collection)
        for index in range(3):
            tid = f'T{index + 1}'
            turbine = TurbineDTO(tid, 210.0 * index, 0.0)
            name = prefix + '.Turbine.' + tid
            _make_turbine(collection, turbine, object_prefix=name,
                          blade_mesh_name=prefix + '.ReferenceBlade',
                          material_factory=material, include_radar=False)
            foundation = _cone(collection, name + '.Foundation', 7.5, 7.5, 1.15, material('foundation'))
            foundation.location = (turbine.x_m, turbine.y_m, .225)
            _finish(foundation, bevel=.08, smooth=True)
            entry = scene.wfrl_manual_turbines.add()
            entry.turbine_id = tid
            entry.yaw_object = collection.objects[name + '.YawRoot']
            entry.rotor_object = collection.objects[name + '.Rotor']
            for blade in (1, 2, 3):
                setattr(entry, f'blade{blade}_object', collection.objects[name + f'.Blade{blade}'])
        for obj in collection.objects:
            obj['wfrl_manual_owner'] = prefix
        _ensure_blade_model(scene)
        _environment(scene, collection, prefix)
        source.wfrl_manual_scene = scene
        if not manual_control.apply_scene(scene):
            raise ValueError(manual_control.validate_scene(scene)[1])
        manual_control.sync_batch(scene)
        context.view_layer.objects.active = None
        for obj in scene.objects:
            obj.select_set(False)
        context.view_layer.update()
        return scene
    except Exception:
        context.window.scene = source
        _discard(scene, collection, prefix)
        raise


def enter(context):
    from . import manual_control, playback, _cancel_playback
    reason = entry_error(context)
    if reason:
        raise ValueError(reason)
    source = context.scene
    if is_manual(source):
        if manual_control.validate_scene(source)[0]:
            manual_control.pause_scene(source)
            _ensure_blade_model(source)
            _ensure_environment(source)
            return source
        original = source.wfrl_manual_return_scene
        if original is None or original == source:
            raise ValueError('原场景已不存在；请切换到其他场景后创建')
        context.window.scene = original
        try:
            scene = create(context)
            manual_control.pause_scene(scene)
            _save_return_view(context, scene, fallback_scene=source)
            focus(context)
            return scene
        except Exception:
            context.window.scene = source
            raise
    _remember_view(context)
    playing = playback.is_playing(source)
    if playing:
        _cancel_playback(source)
    try:
        scene = source.wfrl_manual_scene
        if scene is None or not is_manual(scene):
            scene = create(context)
        else:
            valid, reason = manual_control.validate_scene(scene)
            if not valid:
                scene = create(context)
            else:
                scene.wfrl_manual_return_scene = source
                context.window.scene = scene
                _ensure_environment(scene)
        manual_control.pause_scene(scene)
        _ensure_blade_model(scene)
        _RESUME[context.window.as_pointer()] = (source, playing)
        _save_return_view(context, scene)
        focus(context)
        return scene
    except Exception:
        context.window.scene = source
        _restore_view(context)
        if playing and not playback.any_playing():
            import bpy
            bpy.ops.screen.animation_play()
        raise


def leave(context):
    import bpy
    from . import manual_control, playback
    scene = context.scene
    if not is_manual(scene):
        return scene
    manual_control.pause_scene(scene)
    source = scene.wfrl_manual_return_scene
    if source is None or source == scene:
        raise ValueError('原场景已不存在；请使用 Blender 场景选择器切换')
    context.window.scene = source
    _restore_view(context, scene)
    previous, playing = _RESUME.pop(context.window.as_pointer(), (None, False))
    if previous == source and playing and not playback.any_playing():
        bpy.ops.screen.animation_play()
    return source


def clear_views():
    _VIEWS.clear()
    _RESUME.clear()
