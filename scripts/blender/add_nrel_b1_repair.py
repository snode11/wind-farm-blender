"""Create a saved T1/B1 cosmetic repaint example using the installed frontend.

The small overlay samples fixed B1 triangle vertices with barycentric weights.
Native Geometry Nodes preserves attachment during replay and file reopening;
no runtime Python handler, extension update, or solver run is required.
Run: blender --background --python this_file.py -- --output NEW_DIRECTORY
"""
import argparse
import importlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

MODULE = 'bl_ext.user_default.wfrl_blender'
NAME = 'WFRL.Turbine.T1.Blade1.RepairPaint'


def attribute(mesh, name, kind, values):
    attr = mesh.attributes.new(name, kind, 'POINT')
    attr.data.foreach_set('value', values)


def make_attachment(blade):
    tree = bpy.data.node_groups.new('T1.B1.Repair.SurfaceAttachment', 'GeometryNodeTree')
    tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = tree.nodes, tree.links
    incoming = nodes.new('NodeGroupInput')
    outgoing = nodes.new('NodeGroupOutput')
    target = nodes.new('GeometryNodeObjectInfo')
    target.transform_space = 'RELATIVE'
    target.inputs['Object'].default_value = blade
    target.inputs['As Instance'].default_value = False
    position = nodes.new('GeometryNodeInputPosition')
    samples, products = [], []
    for i in range(3):
        index = nodes.new('GeometryNodeInputNamedAttribute')
        index.data_type = 'INT'
        index.inputs['Name'].default_value = f'repair_i{i}'
        weight = nodes.new('GeometryNodeInputNamedAttribute')
        weight.data_type = 'FLOAT'
        weight.inputs['Name'].default_value = f'repair_w{i}'
        sample = nodes.new('GeometryNodeSampleIndex')
        sample.data_type = 'FLOAT_VECTOR'
        sample.domain = 'POINT'
        links.new(target.outputs['Geometry'], sample.inputs['Geometry'])
        links.new(position.outputs['Position'], sample.inputs['Value'])
        links.new(index.outputs['Attribute'], sample.inputs['Index'])
        product = nodes.new('ShaderNodeVectorMath')
        product.operation = 'SCALE'
        links.new(sample.outputs['Value'], product.inputs[0])
        links.new(weight.outputs['Attribute'], product.inputs['Scale'])
        samples.append(sample.outputs['Value'])
        products.append(product.outputs['Vector'])

    def vector_math(op, a, b=None):
        node = nodes.new('ShaderNodeVectorMath')
        node.operation = op
        links.new(a, node.inputs[0])
        if b is not None:
            links.new(b, node.inputs[1])
        return node.outputs['Vector']

    center = vector_math('ADD', vector_math('ADD', products[0], products[1]), products[2])
    normal = vector_math('NORMALIZE', vector_math('CROSS_PRODUCT',
        vector_math('SUBTRACT', samples[1], samples[0]),
        vector_math('SUBTRACT', samples[2], samples[0])))
    offset = nodes.new('ShaderNodeVectorMath')
    offset.operation = 'SCALE'
    offset.inputs['Scale'].default_value = .001
    links.new(normal, offset.inputs[0])
    final = vector_math('ADD', center, offset.outputs['Vector'])
    set_position = nodes.new('GeometryNodeSetPosition')
    links.new(incoming.outputs['Geometry'], set_position.inputs['Geometry'])
    links.new(final, set_position.inputs['Position'])
    links.new(set_position.outputs['Geometry'], outgoing.inputs['Geometry'])
    return tree


def make_material():
    mat = bpy.data.materials.new('T1.B1.RepairedPaint')
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
    mat['description'] = 'Synthetic local repaint: colour and roughness variation, no structural repair simulation.'
    return mat


def create_patch(scene, blade):
    blade.data.calc_loop_triangles()
    coords = [v.co.copy() for v in blade.data.vertices]
    triangles = [tuple(t.vertices) for t in blade.data.loop_triangles]
    bvh = BVHTree.FromPolygons(coords, triangles, all_triangles=True)
    zcenter = 35.5  # approximately 34 m from the blade root (HubRad=1.5 m)
    section = [p.y for p in coords if abs(p.z-zcenter) < 1.0]
    if not section:
        raise RuntimeError('Missing B1 midspan section')
    ycenter = (min(section)+max(section))/2
    vertices, uv_values, anchors, faces = [], [], [], []
    angle = math.radians(8)
    outline = []
    for i in range(96):
        t = i*2*math.pi/96
        cy, sz = math.cos(t), math.sin(t)
        y = .35*math.copysign(abs(cy)**.5, cy)
        z = .70*math.copysign(abs(sz)**.5, sz)
        outline.append((ycenter+y*math.cos(angle)-z*math.sin(angle),
                        zcenter+y*math.sin(angle)+z*math.cos(angle)))

    def area(points):
        return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points, points[1:]+points[:1])))/2

    def clip(points, a, b):
        result = []
        def signed(p):
            return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
        for p,q in zip(points, points[1:]+points[:1]):
            dp,dq = signed(p),signed(q)
            if dp >= -1e-10:
                result.append(p)
            if (dp >= -1e-10) != (dq >= -1e-10):
                f = dp/(dp-dq)
                result.append((p[0]+f*(q[0]-p[0]), p[1]+f*(q[1]-p[1])))
        return result

    # Clip the footprint to original triangle boundaries. Every overlay face
    # stays parallel to its target facet, avoiding interior surface crossings.
    covered = 0.
    for ids in triangles:
        a,b,c = (coords[i] for i in ids)
        normal = (b-a).cross(c-a).normalized()
        if normal.x > -.15 or max(a.z,b.z,c.z) < zcenter-.8 or min(a.z,b.z,c.z) > zcenter+.8:
            continue
        points = [(p.y,p.z) for p in (a,b,c)]
        for edge_a,edge_b in zip(outline, outline[1:]+outline[:1]):
            if not points:
                break
            points = clip(points, edge_a, edge_b)
        if len(points) < 3 or area(points) < 1e-10:
            continue
        covered += area(points)
        first = len(vertices)
        for y,z in points:
            weights = barycentric_transform(Vector((0,y,z)),
                *(Vector((0,p.y,p.z)) for p in (a,b,c)),
                Vector((1,0,0)), Vector((0,1,0)), Vector((0,0,1)))
            point = a*weights[0]+b*weights[1]+c*weights[2]
            # Ensure this is the first upstream-facing shell at this footprint.
            hit = bvh.ray_cast(Vector((-20,y,z)), Vector((1,0,0)), 40)[0]
            if hit is None or (hit-point).length > .00005:
                raise RuntimeError('Repair footprint intersects another blade shell')
            vertices.append(tuple(point+normal*.001))
            dy,dz = y-ycenter,z-zcenter
            uv_values.append((.5+(dy*math.cos(angle)+dz*math.sin(angle))/.7,
                              .5+(-dy*math.sin(angle)+dz*math.cos(angle))/1.4))
            anchors.append((ids, tuple(weights)))
        faces.append(tuple(range(first,len(vertices))))
    assert abs(covered-area(outline)) < 1e-6, (covered,area(outline))
    mesh = bpy.data.meshes.new(NAME+'.Mesh')
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(make_material())
    uv = mesh.uv_layers.new(name='RepairUV')
    for poly in mesh.polygons:
        poly.use_smooth = True
        for loop in poly.loop_indices:
            uv.data[loop].uv = uv_values[mesh.loops[loop].vertex_index]
    for i in range(3):
        attribute(mesh, f'repair_i{i}', 'INT', [row[0][i] for row in anchors])
        attribute(mesh, f'repair_w{i}', 'FLOAT', [row[1][i] for row in anchors])
    patch = bpy.data.objects.new(NAME, mesh)
    scene.collection.objects.link(patch)
    patch.parent = blade
    patch.matrix_parent_inverse = Matrix.Identity(4)
    patch.matrix_basis = Matrix.Identity(4)
    patch.color = mesh.materials[0].diffuse_color
    modifier = patch.modifiers.new('Follow B1 surface during flexible replay', 'NODES')
    modifier.node_group = make_attachment(blade)
    patch['repair_example'] = True
    patch['turbine_id'] = 'T1'
    patch['blade_id'] = 1
    patch['kind'] = 'synthetic_repaint'
    patch['nominal_length_m'] = 1.4
    patch['nominal_width_m'] = .7
    patch['approx_root_span_m'] = 34.
    patch['display_offset_m'] = .001
    patch['physics_coupled'] = False
    patch['target_vertices'] = len(coords)
    patch['target_polygons'] = len(blade.data.polygons)
    patch['attachment'] = 'saved triangle vertex indices and barycentric weights; native Geometry Nodes'
    return patch


def check_attachment(scene, patch):
    blade = patch.parent
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = patch.evaluated_get(graph)
    actual = evaluated.to_mesh()
    try:
        assert len(actual.vertices) == len(patch.data.vertices)
        assert len(blade.data.vertices) == patch['target_vertices']
        assert len(blade.data.polygons) == patch['target_polygons']
        worst = 0.
        for i, v in enumerate(actual.vertices):
            ids = [patch.data.attributes[f'repair_i{j}'].data[i].value for j in range(3)]
            weights = [patch.data.attributes[f'repair_w{j}'].data[i].value for j in range(3)]
            a, b, c = (blade.data.vertices[j].co for j in ids)
            normal = (b-a).cross(c-a).normalized()
            expected = a*weights[0]+b*weights[1]+c*weights[2]+normal*.001
            worst = max(worst, (v.co-expected).length)
        assert worst < .00005, worst
        return {'frame': scene.frame_current, 'maximum_attachment_error_m': worst,
                'patch_vertices': len(actual.vertices), 'target_vertices': len(blade.data.vertices)}
    finally:
        evaluated.to_mesh_clear()


def focus(scene, patch):
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    obj = patch.evaluated_get(graph)
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    center = sum(pts, Vector())/len(pts)
    normal = (pts[1]-pts[0]).cross(pts[2]-pts[0]).normalized()
    direction = normal + Vector((0, 0, .12))
    rotation = direction.to_track_quat('Z', 'Y')
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    patch.select_set(True)
    bpy.context.view_layer.objects.active = patch
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type != 'VIEW_3D':
                    continue
                space.region_3d.view_perspective = 'PERSP'
                space.region_3d.view_location = center
                space.region_3d.view_rotation = rotation
                space.region_3d.view_distance = 3.8
                space.shading.type = 'MATERIAL'
                space.shading.use_scene_world = False
                space.overlay.show_overlays = False
    return list(center)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    out = Path(args.output).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    target = out/'NREL5MW_T1_B1_修补痕迹.blend'
    if target.exists():
        raise FileExistsError(target)
    addon = importlib.import_module(MODULE)
    farm_flex = importlib.import_module(MODULE+'.farm_flex')
    if not hasattr(bpy.types.Scene, 'wfrl_selected_turbine'):
        addon.register()
    assert bpy.ops.wfrl.load_demo() == {'FINISHED'}
    scene = bpy.context.scene
    assert farm_flex.is_active(scene)
    scene.frame_set(1)
    blade = scene.objects['WFRL.Turbine.T1.Blade1']
    baseline = {obj.name: [p.material_index for p in obj.data.polygons]
        for obj in scene.objects if obj.type == 'MESH' and obj.name.endswith(('Blade1', 'Blade2', 'Blade3'))}
    # The current extension already adds this mark through the normal scene
    # lifecycle. Reuse it so an optional inspection copy cannot duplicate it.
    repair = importlib.import_module(MODULE+'.repair_marks')
    patch = repair.ensure(scene)
    assert patch is not None
    checks = []
    for frame in (1, 52, 901, 1801, 2701, 3601, 1):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        checks.append(check_attachment(scene, patch))
    assert baseline == {name: [p.material_index for p in scene.objects[name].data.polygons] for name in baseline}
    center = focus(scene, patch)
    scene['b1_repair_note'] = 'T1 B1: one synthetic repaint mark, approximately 1.4 x 0.7 m; existing recorded motion, no physics coupling.'
    bpy.ops.wm.save_as_mainfile(filepath=str(target))
    bpy.ops.wm.open_mainfile(filepath=str(target))
    scene = bpy.context.scene
    assert farm_flex.is_active(scene), 'Recorded flexible replay failed to restore'
    patch = scene.objects[NAME]
    centers = []
    for frame in (1, 901, 3601, 1):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        row = check_attachment(scene, patch)
        row['reopened'] = True
        checks.append(row)
        obj = patch.evaluated_get(bpy.context.evaluated_depsgraph_get())
        centers.append(sum((obj.matrix_world @ v.co for v in obj.data.vertices), Vector())/len(obj.data.vertices))
    assert (centers[1]-centers[0]).length > 1, 'B1 replay is frozen'
    report = {'status': 'PASS', 'addon_path': addon.__file__, 'blend': str(target),
              'patch': NAME, 'inspection_center_world_m': center, 'checks': checks,
              'scope': 'cosmetic synthetic repaint; no solver, no extension update, no rendering'}
    (out/'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print('NREL_B1_REPAIR_PASS '+json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
