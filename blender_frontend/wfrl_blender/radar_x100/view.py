"""One GPU point batch per scene; physical housing remains in raw RGB."""
import math
import numpy as np
from .core import schema

_HANDLER = None


def remove_orphans(owner):
    import bpy
    for obj in list(bpy.data.objects):
        if obj.get('wfrl_x100_owner') == owner and not obj.users_scene and not obj.use_fake_user:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if isinstance(data, bpy.types.Mesh) and data.users == 0 and not data.use_fake_user:
                bpy.data.meshes.remove(data)
    for collection in list(bpy.data.collections):
        if collection.get('wfrl_x100_owner') == owner and not collection.users and not collection.use_fake_user:
            bpy.data.collections.remove(collection)


def remove_objects(scene):
    import bpy
    owner = scene.get('wfrl_x100_owner')
    if not owner:
        return
    for collection in list(scene.collection.children):
        if collection.get('wfrl_x100_owner') == owner and collection.users > 1:
            scene.collection.children.unlink(collection)
    for obj in list(scene.objects):
        if obj.get('wfrl_x100_owner') != owner:
            continue
        # A linked object in another scene belongs to that saved scene as well.
        if len(obj.users_scene) > 1:
            continue
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0 and not data.use_fake_user:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    for collection in list(scene.collection.children):
        if collection.get('wfrl_x100_owner') == owner and not collection.objects and collection.users == 1:
            bpy.data.collections.remove(collection)


def install(scene, owner, sensor_matrix, *, show_fov=True):
    import bpy
    from mathutils import Matrix
    remove_objects(scene)
    collection = bpy.data.collections.new('WFRL.X100')
    collection['wfrl_x100_owner'] = owner
    scene.collection.children.link(collection)
    mount = bpy.data.objects.new('WFRL.X100.Mount', None)
    mount['wfrl_x100_owner'] = owner
    collection.objects.link(mount)
    mount.matrix_world = Matrix(sensor_matrix.tolist())
    mesh = bpy.data.meshes.new('WFRL.X100.Housing')
    mesh.from_pydata([(x * .020, y * .0475, z * .060) for x, y, z in
                     ((-1,-1,-1),(-1,-1,1),(-1,1,-1),(-1,1,1),(1,-1,-1),(1,-1,1),(1,1,-1),(1,1,1))],
                    [], [(0,4,6,2),(1,3,7,5),(0,1,5,4),(2,6,7,3),(0,2,3,1),(4,5,7,6)])
    housing = bpy.data.objects.new('WFRL.X100.Housing', mesh)
    collection.objects.link(housing)
    housing.parent = mount
    housing['wfrl_x100_owner'] = owner
    housing['wfrl_x100_role'] = 'physical'
    housing['geometry_source'] = 'X100 nominal 120x95x40 mm envelope; approximate axes, no manufacturer CAD'
    material = bpy.data.materials.get('WFRL.X100.Housing') or bpy.data.materials.new('WFRL.X100.Housing')
    material.diffuse_color = (.045, .06, .075, 1)
    mesh.materials.append(material)
    points, edges = [(0., 0., 0.)], []
    for elevation in (-22, 22):
        row = []
        for azimuth in np.linspace(-60, 60, 25):
            az, el = math.radians(azimuth), math.radians(elevation)
            row.append(len(points))
            points.append((60 * math.cos(el) * math.cos(az), 60 * math.cos(el) * math.sin(az), 60 * math.sin(el)))
        edges.extend(zip(row, row[1:]))
        edges.extend([(0, row[0]), (0, row[-1])])
    edges.extend([(1, 26), (25, 50)])
    mesh = bpy.data.meshes.new('WFRL.X100.FOV')
    mesh.from_pydata(points, edges, [])
    fov = bpy.data.objects.new('WFRL.X100.FOV', mesh)
    collection.objects.link(fov)
    fov.parent, fov.hide_render = mount, True
    fov['wfrl_x100_owner'], fov['wfrl_x100_role'] = owner, 'annotation'
    fov.color = (.05, .7, 1, 1)
    for layer in scene.view_layers:
        fov.hide_set(not show_fov, view_layer=layer)
    return mount, fov


def batch(frame, color_by):
    import gpu
    from gpu_extras.batch import batch_for_shader
    xyz = frame.xyz[frame.finite_mask]
    transform = frame.spatial.get('world_from_sensor')
    if transform is not None and frame.spatial.get('alignment') in {'SIMULATION', 'STATIC_CALIBRATION', 'VERIFIED'}:
        matrix = np.asarray(transform)
        xyz = xyz @ matrix[:3, :3].T + matrix[:3, 3]
    if color_by == 'RANGE':
        values = np.linalg.norm(frame.xyz[frame.finite_mask], axis=1)
    elif color_by == 'HEIGHT':
        values = xyz[:, 2]
    elif color_by == 'VELOCITY':
        values = frame.radial_velocity_m_s[frame.finite_mask] if frame.radial_velocity_m_s is not None else np.full(len(xyz), np.nan)
    else:
        values = frame.points[color_by.lower()][frame.finite_mask]
    finite = np.isfinite(values)
    colors = np.tile([.5, .5, .5, 1.], (len(xyz), 1)).astype(np.float32)
    if finite.any():
        lo, hi = float(values[finite].min()), float(values[finite].max())
        scaled = (values[finite] - lo) / max(hi - lo, 1e-9)
        colors[finite, :3] = np.column_stack((scaled, 1 - np.abs(2 * scaled - 1), 1 - scaled))
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
        if session.fov is not None and bpy.context.scene.wfrl_x100_fov and session.fov.visible_get(view_layer=bpy.context.view_layer):
            if session.fov_gpu_batch is None:
                session.fov_gpu_batch = fov_batch(session.fov)
            shader, field_batch = session.fov_gpu_batch
            with gpu.matrix.push_pop():
                gpu.matrix.multiply_matrix(session.fov.matrix_world)
                shader.bind()
                shader.uniform_float('color', (.05, .7, 1., .85))
                field_batch.draw(shader)
        if session.gpu_batch is not None and session.display_valid:
            shader, point_batch = session.gpu_batch
            shader.bind()
            shader.uniform_float('size', 4.)
            point_batch.draw(shader)
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
