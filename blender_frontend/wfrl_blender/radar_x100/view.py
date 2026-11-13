"""One GPU point batch per scene; physical housing remains in raw RGB."""
import math
import time
import numpy as np
from .core import schema

_HANDLER = None


def _remove_unused_materials(materials):
    import bpy
    for material in set(materials):
        if (material is not None and material.get('wfrl_x100_owner')
                and material.users == 0 and not material.use_fake_user):
            bpy.data.materials.remove(material)


def remove_mesh(mesh):
    """Release an unused radar-created mesh and its unused radar-created materials."""
    import bpy
    if (isinstance(mesh, bpy.types.Mesh) and mesh.get('wfrl_x100_owner')
            and mesh.users == 0 and not mesh.use_fake_user):
        materials = tuple(mesh.materials)
        bpy.data.meshes.remove(mesh)
        _remove_unused_materials(materials)


def _remove_data_orphans(owner):
    import bpy
    for mesh in list(bpy.data.meshes):
        if mesh.get('wfrl_x100_owner') == owner:
            remove_mesh(mesh)
    _remove_unused_materials(material for material in bpy.data.materials
                             if material.get('wfrl_x100_owner') == owner)


def remove_orphans(owner):
    import bpy
    for obj in list(bpy.data.objects):
        if (obj.get('wfrl_x100_owner') == owner and not obj.users_scene and not obj.use_fake_user
                and not any(collection.users or collection.use_fake_user for collection in obj.users_collection)):
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            remove_mesh(data)
    for collection in list(bpy.data.collections):
        if (collection.get('wfrl_x100_owner') == owner and not collection.users
                and not collection.use_fake_user and not collection.objects and not collection.children):
            bpy.data.collections.remove(collection)
    _remove_data_orphans(owner)


def remove_objects(scene, *, owner=None):
    import bpy
    if owner is None:
        owner = scene.get('wfrl_x100_owner')
    if not owner:
        return
    for collection in list(scene.collection.children):
        if collection.get('wfrl_x100_owner') == owner and collection.users > 1:
            scene.collection.children.unlink(collection)
    for obj in list(scene.objects):
        if obj.get('wfrl_x100_owner') != owner:
            continue
        if len(obj.users_scene) > 1 or obj.use_fake_user:
            local = (scene.collection, *scene.collection.children_recursive)
            others = [other for other in obj.users_scene if other != scene]
            for collection in list(obj.users_collection):
                shared = any(collection == other.collection or collection in tuple(other.collection.children_recursive)
                             for other in others)
                if collection in local and not shared:
                    collection.objects.unlink(obj)
            continue
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        remove_mesh(data)
    for collection in list(scene.collection.children):
        if (collection.get('wfrl_x100_owner') == owner and not collection.objects
                and not collection.children and collection.users == 1 and not collection.use_fake_user):
            bpy.data.collections.remove(collection)
    _remove_data_orphans(owner)


def existing_installation(scene, owner):
    objects = [obj for obj in scene.objects if obj.get('wfrl_x100_owner') == owner]
    mounts = [obj for obj in objects if obj.type == 'EMPTY']
    fields = [obj for obj in objects if obj.get('wfrl_x100_role') == 'annotation']
    housings = [obj for obj in objects if obj.get('wfrl_x100_role') == 'physical']
    if (len(objects) != 3 or len(mounts) != 1 or len(fields) != 1 or len(housings) != 1
            or fields[0].parent != mounts[0] or housings[0].parent != mounts[0]):
        raise ValueError('保存的 X100 安装缺失或归属不明确；请检查后重新应用配置')
    return mounts[0], fields[0]


def field_of_view(value=None):
    """Applied rectangular half angles (azimuth, elevation) in degrees; None is the datasheet FOV."""
    if value is None:
        return float(schema.PROFILE['azimuth_deg']), float(schema.PROFILE['elevation_deg'])
    return tuple(float(v) for v in value)


def saved_field_of_view(fov):
    """Read the installed annotation settings, including fixed angular legacy meshes."""
    return (field_of_view(fov.data.get('wfrl_x100_field_of_view_deg')),
            fov.data.get('wfrl_x100_field_of_view_shape', 'ANGULAR'))


def _fov_mesh(limits, shape='ANGULAR'):
    import bpy
    azimuth_limit, elevation_limit = field_of_view(limits)
    if shape == 'PROJECTIVE':
        from .display import fov_corners
        # Pinhole rectangle: straight edges at the far end, as the camera image frames it.
        corners = fov_corners((azimuth_limit, elevation_limit), shape)
        points = [(0., 0., 0.), *map(tuple, corners[[0, 1, 3, 2]])]
        edges = [(0, 1), (0, 2), (0, 3), (0, 4), (1, 2), (2, 3), (3, 4), (4, 1)]
    else:
        points, edges, rows = [(0., 0., 0.)], [], []
        length = schema.DISPLAY_DEFAULTS['fov_length_m']
        for elevation in (-elevation_limit, elevation_limit):
            row = []
            for azimuth in np.linspace(-azimuth_limit, azimuth_limit, 25):
                az, el = math.radians(azimuth), math.radians(elevation)
                row.append(len(points))
                points.append((length * math.cos(el) * math.cos(az), length * math.cos(el) * math.sin(az), length * math.sin(el)))
            edges.extend(zip(row, row[1:]))
            edges.extend([(0, row[0]), (0, row[-1])])
            rows.append(row)
        edges.extend([(rows[0][0], rows[1][0]), (rows[0][-1], rows[1][-1])])
    mesh = bpy.data.meshes.new('WFRL.X100.FOV')
    try:
        mesh.from_pydata(points, edges, [])
        mesh['wfrl_x100_field_of_view_shape'] = shape
        mesh['wfrl_x100_field_of_view_deg'] = [azimuth_limit, elevation_limit]
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    return mesh


def set_field_of_view(fov, limits, shape='ANGULAR'):
    """Replace the annotation mesh; returns the previous mesh for rollback."""
    import bpy
    previous = fov.data
    mesh = _fov_mesh(limits, shape)
    try:
        mesh['wfrl_x100_owner'] = fov['wfrl_x100_owner']
        fov.data = mesh
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    return previous


def install(scene, owner, sensor_matrix, *, show_fov=True, field_of_view_deg=None, field_of_view_shape='ANGULAR'):
    """Create an owned installation that can coexist with an active calibration during preparation."""
    import bpy
    from mathutils import Matrix
    collection = bpy.data.collections.new('WFRL.X100')
    objects, meshes, created_materials = [], [], []

    def add_object(name, data=None, role=None):
        if data is not None:
            meshes.append(data)
            data['wfrl_x100_owner'] = owner
        obj = bpy.data.objects.new(name, data)
        objects.append(obj)
        obj['wfrl_x100_owner'] = owner
        if role is not None:
            obj['wfrl_x100_role'] = role
        collection.objects.link(obj)
        return obj

    try:
        collection['wfrl_x100_owner'] = owner
        scene.collection.children.link(collection)
        mount = add_object('WFRL.X100.Mount')
        mount.matrix_world = Matrix(sensor_matrix.tolist())
        from . import housing_model
        vertices, faces, materials = housing_model.build()
        mesh = bpy.data.meshes.new('WFRL.X100.Housing')
        housing = add_object('WFRL.X100.Housing', mesh, 'physical')
        mesh.from_pydata(vertices.tolist(), [], faces)
        for name, color, metallic, roughness in housing_model.MATERIALS:
            mesh.materials.append(_material(name, color, metallic, roughness,
                                            owner=owner, created=created_materials))
        mesh.polygons.foreach_set('material_index', materials.astype(np.int32))
        mesh.update()
        housing.parent = mount
        housing['geometry_source'] = housing_model.SOURCE
        fov = add_object('WFRL.X100.FOV', _fov_mesh(field_of_view_deg, field_of_view_shape), 'annotation')
        fov.parent, fov.hide_render = mount, True
        fov.color = (.05, .7, 1, 1)
        for layer in scene.view_layers:
            fov.hide_set(not show_fov, view_layer=layer)
    except Exception:
        for obj in reversed(objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in meshes:
            bpy.data.meshes.remove(mesh)
        for material in created_materials:
            if material.users == 0 and not material.use_fake_user:
                bpy.data.materials.remove(material)
        bpy.data.collections.remove(collection)
        raise
    return mount, fov


def housing(mount):
    """The display model of the installed unit under ``mount``, or None."""
    return next((child for child in mount.children if child.get('wfrl_x100_role') == 'physical'), None)


def sync_housing_visibility(scene, mount):
    """Hide the display housing while a scene camera's optical centre lies inside it.

    A mount that shares a camera's optical centre (the DOWN_CAMERA preset) would otherwise put
    the camera inside the housing, which then fills that camera's image. Only visibility this
    function set is ever undone: the prior hide_viewport/hide_render go into _CAMERA_HIDE and
    come back when no camera remains inside, and a user's own edits are left alone otherwise.
    Returns True while hidden for a camera."""
    model = housing(mount)
    if model is None:
        return False
    for layer in scene.view_layers[:1]:
        layer.update()
    corners = np.array([tuple(corner) for corner in model.bound_box])
    lo, hi = corners.min(axis=0), corners.max(axis=0)
    # A hidden housing is not evaluated and keeps a stale matrix_world; derive it from the mount.
    world = np.array(mount.matrix_world) @ np.array(model.matrix_parent_inverse) @ np.array(model.matrix_basis)
    inverse = np.linalg.inv(world)
    hidden = False
    for obj in scene.objects:
        if obj.type == 'CAMERA':
            local = inverse @ np.append(np.array(obj.matrix_world.translation), 1.)
            if np.all(local[:3] >= lo) and np.all(local[:3] <= hi):
                hidden = True
                break
    prior = model.get(_CAMERA_HIDE)
    if hidden and prior is None:
        model[_CAMERA_HIDE] = [bool(model.hide_viewport), bool(model.hide_render)]
        model.hide_viewport = model.hide_render = True
    elif not hidden and prior is not None:
        model.hide_viewport, model.hide_render = (bool(value) for value in prior)
        del model[_CAMERA_HIDE]
    return hidden


_CAMERA_HIDE = 'wfrl_x100_hidden_for_camera'


def _material(name, color, metallic, roughness, *, owner, created):
    import bpy
    material = bpy.data.materials.get(name)
    if material is not None:
        return material
    material = bpy.data.materials.new(name)
    created.append(material)
    material['wfrl_x100_owner'] = owner
    material.diffuse_color, material.metallic, material.roughness = color, metallic, roughness
    if material.node_tree is None and hasattr(material, 'use_nodes'):
        material.use_nodes = True
    bsdf = next((n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) if material.node_tree else None
    if bsdf is not None:
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Metallic'].default_value = metallic
        bsdf.inputs['Roughness'].default_value = roughness
    return material


def frame_unit(context, mount, *, distance=1.4):
    """Point every 3D view of the window at the installed unit from the boresight side."""
    from mathutils import Vector
    world = mount.matrix_world
    center = world.translation.copy()
    axes = world.to_3x3().normalized()
    # Eye in front of the boresight, off to the left and above in sensor axes, so radome,
    # side bracket and mounting plate all read; a down-looking unit is seen from below.
    offset = (axes @ Vector((.8, .55, .35))).normalized() * distance
    rotation = (-offset).to_track_quat('-Z', 'Y')
    framed = 0
    for area in context.screen.areas if context.screen else ():
        if area.type != 'VIEW_3D':
            continue
        space = area.spaces.active
        for region in tuple(space.region_quadviews) or (space.region_3d,):
            if region is None:
                continue
            region.view_perspective = 'PERSP'
            region.view_location = center
            region.view_rotation = rotation
            region.view_distance = distance
            framed += 1
        space.clip_start = min(space.clip_start, .01)
        area.tag_redraw()
    return framed


def batch(frame, color_by, *, color_scale='FIXED', velocity_limit=10., classification=None):
    from .display import world_xyz, point_style
    xyz = world_xyz(frame)
    if not len(xyz):
        return None
    import gpu
    from gpu_extras.batch import batch_for_shader
    colors, _, _ = point_style(frame, color_by, color_scale=color_scale, velocity_limit=velocity_limit,
                               classification=classification)
    shader = gpu.shader.from_builtin('POINT_FLAT_COLOR')
    return shader, batch_for_shader(shader, 'POINTS', {'pos': xyz.astype(np.float32), 'color': colors})


def fov_batch(obj):
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    return shader, batch_for_shader(shader, 'LINES',
        {'pos': [vertex.co[:] for vertex in obj.data.vertices]},
        indices=[edge.vertices[:] for edge in obj.data.edges])


def draw():
    import bpy
    import gpu
    from .. import custom_camera_preview
    from .runtime import session_for
    if custom_camera_preview._DRAWING:
        return
    session = session_for(bpy.context.scene)
    if session is None:
        return
    depth, blend = gpu.state.depth_test_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.blend_set('ALPHA')
        if session.frame is not None and session.frame.spatial.get('world_from_sensor') is not None and session.fov is not None and bpy.context.scene.wfrl_x100_fov and session.fov.visible_get(view_layer=bpy.context.view_layer):
            if session.fov_gpu_batch is None:
                session.fov_gpu_batch = fov_batch(session.fov)
            shader, field_batch = session.fov_gpu_batch
            with gpu.matrix.push_pop():
                from mathutils import Matrix
                gpu.matrix.multiply_matrix(Matrix(session.frame.spatial['world_from_sensor']))
                shader.bind()
                shader.uniform_float('color', (.05, .7, 1., .85))
                field_batch.draw(shader)
        if session.gpu_batch is not None and session.display_valid:
            shader, point_batch = session.gpu_batch
            shader.bind()
            shader.uniform_float('size', 4.)
            point_batch.draw(shader)
    except Exception as exc:
        if session.gpu_batch is not None:
            session.gpu_retry_at = time.monotonic() + .25
        session.gpu_batch = session.fov_gpu_batch = None
        session.render_error = str(exc)
    else:
        if session.gpu_retry_at is None:
            session.render_error = ''
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.blend_set(blend)


def register():
    global _HANDLER
    import bpy
    if not bpy.app.background and _HANDLER is None:
        _HANDLER = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_VIEW')


def unregister():
    global _HANDLER
    import bpy
    if _HANDLER is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_HANDLER, 'WINDOW')
        _HANDLER = None
