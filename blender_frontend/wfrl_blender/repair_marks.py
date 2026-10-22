"""Default cosmetic repaint on the original NREL T1/B1 reference surface.

The saved Geometry Nodes attachment follows rigid motion and flexible mesh
writes without a Python frame handler. This is an illustrative surface mark;
it does not modify the solver geometry, stiffness, or saved measurements.
"""
from __future__ import annotations

import hashlib
import math
import struct

TARGET_NAME = 'WFRL.Turbine.T1.Blade1'
NAME = TARGET_NAME + '.RepairPaint'
# Turbine visibility operators reveal every WFRL.Turbine.<id> descendant by
# name. Keep the implementation-only sampling shell outside that namespace.
SOURCE_NAME = 'WFRL.RepairReference.T1.B1'
VERSION = 2
_OWNER = 'wfrl_default_b1_repair'
_OFFSET = .001


def topology_signature(mesh):
    """Hash connectivity, excluding coordinates changed during replay."""
    digest = hashlib.sha256()
    digest.update(struct.pack('<QQ', len(mesh.vertices), len(mesh.polygons)))
    for polygon in mesh.polygons:
        ids = tuple(polygon.vertices)
        digest.update(struct.pack('<I', len(ids)))
        digest.update(struct.pack('<' + 'I' * len(ids), *ids))
    return digest.hexdigest()


def _attribute(mesh, name, kind, values):
    attr = mesh.attributes.new(name, kind, 'POINT')
    attr.data.foreach_set('value', values)


def _attachment(source):
    import bpy
    tree = bpy.data.node_groups.new(NAME + '.SurfaceAttachment', 'GeometryNodeTree')
    tree[_OWNER] = True
    tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = tree.nodes, tree.links
    incoming = nodes.new('NodeGroupInput')
    outgoing = nodes.new('NodeGroupOutput')
    target = nodes.new('GeometryNodeObjectInfo')
    target.transform_space = 'RELATIVE'
    target.inputs['Object'].default_value = source
    target.inputs['As Instance'].default_value = False
    position = nodes.new('GeometryNodeInputPosition')
    samples, products = [], []
    for index in range(3):
        vertex = nodes.new('GeometryNodeInputNamedAttribute')
        vertex.data_type = 'INT'
        vertex.inputs['Name'].default_value = f'repair_i{index}'
        weight = nodes.new('GeometryNodeInputNamedAttribute')
        weight.data_type = 'FLOAT'
        weight.inputs['Name'].default_value = f'repair_w{index}'
        sample = nodes.new('GeometryNodeSampleIndex')
        sample.data_type = 'FLOAT_VECTOR'
        sample.domain = 'POINT'
        links.new(target.outputs['Geometry'], sample.inputs['Geometry'])
        links.new(position.outputs['Position'], sample.inputs['Value'])
        links.new(vertex.outputs['Attribute'], sample.inputs['Index'])
        product = nodes.new('ShaderNodeVectorMath')
        product.operation = 'SCALE'
        links.new(sample.outputs['Value'], product.inputs[0])
        links.new(weight.outputs['Attribute'], product.inputs['Scale'])
        samples.append(sample.outputs['Value'])
        products.append(product.outputs['Vector'])

    def vector_math(operation, first, second=None):
        node = nodes.new('ShaderNodeVectorMath')
        node.operation = operation
        links.new(first, node.inputs[0])
        if second is not None:
            links.new(second, node.inputs[1])
        return node.outputs['Vector']

    center = vector_math('ADD', vector_math('ADD', products[0], products[1]), products[2])
    normal = vector_math('NORMALIZE', vector_math('CROSS_PRODUCT',
        vector_math('SUBTRACT', samples[1], samples[0]),
        vector_math('SUBTRACT', samples[2], samples[0])))
    offset = nodes.new('ShaderNodeVectorMath')
    offset.operation = 'SCALE'
    offset.inputs['Scale'].default_value = _OFFSET
    links.new(normal, offset.inputs[0])
    final = vector_math('ADD', center, offset.outputs['Vector'])
    set_position = nodes.new('GeometryNodeSetPosition')
    links.new(incoming.outputs['Geometry'], set_position.inputs['Geometry'])
    links.new(final, set_position.inputs['Position'])
    links.new(set_position.outputs['Geometry'], outgoing.inputs['Geometry'])
    return tree


def _material():
    import bpy
    mat = bpy.data.materials.new(NAME + '.RepairedPaint')
    mat[_OWNER] = True
    mat.diffuse_color = (.49, .52, .48, 1)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    shader = nodes.get('Principled BSDF')
    shader.inputs['Roughness'].default_value = .65
    uv = nodes.new('ShaderNodeTexCoord')
    noise = nodes.new('ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 28
    noise.inputs['Detail'].default_value = 2
    links.new(uv.outputs['UV'], noise.inputs['Vector'])
    paint = nodes.new('ShaderNodeValToRGB')
    paint.color_ramp.elements[0].color = (.51, .53, .48, 1)
    paint.color_ramp.elements[1].color = (.56, .58, .53, 1)
    links.new(noise.outputs['Fac'], paint.inputs['Fac'])
    links.new(paint.outputs['Color'], shader.inputs['Base Color'])
    bump = nodes.new('ShaderNodeBump')
    bump.inputs['Strength'].default_value = .03
    bump.inputs['Distance'].default_value = .00005
    links.new(noise.outputs['Fac'], bump.inputs['Height'])
    links.new(bump.outputs['Normal'], shader.inputs['Normal'])
    mat['description'] = 'Synthetic local repaint; no structural repair simulation.'
    return mat


def _footprint(blade):
    """Clip the 1.4 by 0.7 m outline to each original upstream facet."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from mathutils.geometry import barycentric_transform
    blade.data.calc_loop_triangles()
    coords = [vertex.co.copy() for vertex in blade.data.vertices]
    triangles = [tuple(triangle.vertices) for triangle in blade.data.loop_triangles]
    bvh = BVHTree.FromPolygons(coords, triangles, all_triangles=True)
    zcenter = 35.5  # HubRad 1.5 m + approximately 34 m from the root.
    section = [point.y for point in coords if abs(point.z - zcenter) < 1.0]
    if not section:
        raise ValueError('Missing NREL T1/B1 repair reference section')
    ycenter = (min(section) + max(section)) / 2
    angle = math.radians(8)
    outline = []
    for index in range(96):
        phase = index * 2 * math.pi / 96
        cy, sz = math.cos(phase), math.sin(phase)
        y = .35 * math.copysign(abs(cy)**.5, cy)
        z = .70 * math.copysign(abs(sz)**.5, sz)
        outline.append((ycenter + y*math.cos(angle) - z*math.sin(angle),
                        zcenter + y*math.sin(angle) + z*math.cos(angle)))

    def area(points):
        return abs(sum(a[0]*b[1] - b[0]*a[1]
                       for a, b in zip(points, points[1:] + points[:1]))) / 2

    def clip(points, a, b):
        result = []

        def signed(point):
            return (b[0]-a[0])*(point[1]-a[1]) - (b[1]-a[1])*(point[0]-a[0])

        for p, q in zip(points, points[1:] + points[:1]):
            dp, dq = signed(p), signed(q)
            if dp >= -1e-10:
                result.append(p)
            if (dp >= -1e-10) != (dq >= -1e-10):
                weight = dp / (dp - dq)
                result.append((p[0] + weight*(q[0]-p[0]), p[1] + weight*(q[1]-p[1])))
        return result

    vertices, uv_values, anchors, faces = [], [], [], []
    covered = 0.
    for ids in triangles:
        a, b, c = (coords[index] for index in ids)
        normal = (b-a).cross(c-a).normalized()
        if (normal.x > -.15 or max(a.z, b.z, c.z) < zcenter-.8
                or min(a.z, b.z, c.z) > zcenter+.8):
            continue
        points = [(point.y, point.z) for point in (a, b, c)]
        for edge_a, edge_b in zip(outline, outline[1:] + outline[:1]):
            if not points:
                break
            points = clip(points, edge_a, edge_b)
        if len(points) < 3 or area(points) < 1e-10:
            continue
        covered += area(points)
        first = len(vertices)
        for y, z in points:
            weights = barycentric_transform(Vector((0, y, z)),
                *(Vector((0, point.y, point.z)) for point in (a, b, c)),
                Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
            point = a*weights[0] + b*weights[1] + c*weights[2]
            hit = bvh.ray_cast(Vector((-20, y, z)), Vector((1, 0, 0)), 40)[0]
            if hit is None or (hit-point).length > .00005:
                raise ValueError('NREL repair footprint intersects another blade shell')
            vertices.append(tuple(point + normal*_OFFSET))
            dy, dz = y-ycenter, z-zcenter
            uv_values.append((.5 + (dy*math.cos(angle) + dz*math.sin(angle))/.7,
                              .5 + (-dy*math.sin(angle) + dz*math.cos(angle))/1.4))
            anchors.append((ids, tuple(weights)))
        faces.append(tuple(range(first, len(vertices))))
    if abs(covered - area(outline)) >= 1e-6:
        raise ValueError('Incomplete NREL repair footprint on reference surface')
    return vertices, faces, uv_values, anchors


def _owned(obj, blade):
    # Also migrate the already delivered standalone repaint when encountered.
    return bool(obj.get(_OWNER) or (obj.name == NAME and obj.parent == blade
                and obj.get('repair_example') and obj.get('kind') == 'synthetic_repaint'))


def _remove(objects):
    """Remove only managed patch objects and their unreferenced resources."""
    import bpy
    resources = []
    for obj in objects:
        legacy = bool(obj.get('repair_example'))
        if obj.data is not None and (obj.data.get(_OWNER) or legacy):
            resources.append(obj.data)
            resources.extend(material for material in obj.data.materials
                             if material is not None and (material.get(_OWNER) or legacy))
        resources.extend(modifier.node_group for modifier in obj.modifiers
                         if modifier.type == 'NODES' and modifier.node_group is not None
                         and (modifier.node_group.get(_OWNER) or legacy))
        bpy.data.objects.remove(obj, do_unlink=True)
    # Mesh removal releases its material references, so inspect materials last.
    unique = {resource.as_pointer(): resource for resource in resources}
    for resource in unique.values():
        if resource.users == 0:
            bpy.data.batch_remove(ids=(resource,))


def ensure(scene, *, force=False):
    """Idempotently ensure one mark; call before flexible coordinate updates.

    FarmFlex must force a rebind immediately after replacing the reference
    loft. Load callbacks defer flexible scenes to that path so an absent mark
    is never positioned using a saved, deformed frame.
    """
    import bpy
    blade = scene.objects.get(TARGET_NAME)
    if blade is None or blade.type != 'MESH':
        return None
    # This exact original blade is built by the NREL frontend. Do not match
    # reconstructed meshes, GW184 blades, or other turbine identifiers.
    if not str(blade.get('geometry_source', '')).startswith('Packaged OpenFAST AeroDyn'):
        return None
    signature = topology_signature(blade.data)
    patch = scene.objects.get(NAME)
    source = scene.objects.get(SOURCE_NAME)
    if (not force and patch is not None and _owned(patch, blade)
            and patch.get('repair_generation_version') == VERSION
            and patch.get('target_topology_sha256') == signature
            and patch.parent == blade and source is not None
            and source.get(_OWNER) and source.parent == blade
            and source.data == blade.data):
        return patch
    if patch is not None and not _owned(patch, blade):
        raise ValueError('NREL repair object name is occupied by an unmanaged object')
    # Compute first, preserving the existing mark if the reference is invalid.
    vertices, faces, uv_values, anchors = _footprint(blade)
    from mathutils import Matrix
    _remove([obj for obj in list(scene.objects) if _owned(obj, blade)])
    collection = next((collection for collection in blade.users_collection
                       if collection.name in scene.collection.children), None)
    if collection is None:
        collection = blade.users_collection[0] if blade.users_collection else scene.collection
    source = bpy.data.objects.new(SOURCE_NAME, blade.data)
    collection.objects.link(source)
    source[_OWNER] = True
    source['source_target_name'] = TARGET_NAME
    source.parent = blade
    source.matrix_parent_inverse = Matrix.Identity(4)
    source.matrix_basis = Matrix.Identity(4)
    # Sampling this modifier-free shared mesh keeps original vertex indices
    # stable even when the rigid blade retains its presentation bevel.
    source.hide_render = True
    for view_layer in scene.view_layers:
        source.hide_set(True, view_layer=view_layer)
    source.hide_select = True
    mesh = bpy.data.meshes.new(NAME + '.Mesh')
    mesh[_OWNER] = True
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(_material())
    uv = mesh.uv_layers.new(name='RepairUV')
    for polygon in mesh.polygons:
        polygon.use_smooth = True
        for loop in polygon.loop_indices:
            uv.data[loop].uv = uv_values[mesh.loops[loop].vertex_index]
    for index in range(3):
        _attribute(mesh, f'repair_i{index}', 'INT', [row[0][index] for row in anchors])
        _attribute(mesh, f'repair_w{index}', 'FLOAT', [row[1][index] for row in anchors])
    mesh.update()
    patch = bpy.data.objects.new(NAME, mesh)
    collection.objects.link(patch)
    patch.parent = blade
    patch.matrix_parent_inverse = Matrix.Identity(4)
    patch.matrix_basis = Matrix.Identity(4)
    patch.color = mesh.materials[0].diffuse_color
    modifier = patch.modifiers.new('Follow B1 reference surface', 'NODES')
    modifier.node_group = _attachment(source)
    patch[_OWNER] = True
    patch['repair_generation_version'] = VERSION
    patch['target_topology_sha256'] = signature
    patch['source_target_name'] = TARGET_NAME
    patch['turbine_id'] = 'T1'
    patch['blade_id'] = 1
    patch['kind'] = 'synthetic_repaint'
    patch['nominal_length_m'] = 1.4
    patch['nominal_width_m'] = .7
    patch['approx_root_span_m'] = 34.
    patch['display_offset_m'] = _OFFSET
    patch['physics_coupled'] = False
    patch['target_vertices'] = len(blade.data.vertices)
    patch['target_polygons'] = len(blade.data.polygons)
    patch['attachment'] = 'reference triangle indices and barycentric weights; native Geometry Nodes'
    return patch


def remove(scene):
    """Release managed attachments before the original scene is rebuilt."""
    blade = scene.objects.get(TARGET_NAME)
    if blade is not None:
        _remove([obj for obj in list(scene.objects) if _owned(obj, blade)])


def ensure_on_load(scene):
    """Rigid saved demos bind immediately; flexible loaders rebuild at rest."""
    if not scene.get('wfrl_farm_flex_path'):
        return ensure(scene)
    return None
