"""Transactional visual defects bound to the healthy NREL display surface.

All reconstruction happens in prepare(), never during pose/frame updates.  The
healthy meshes are private immutable copies.  Analysis footprints are plain
coordinates, not hidden objects that could accidentally occlude an opening.
"""
from copy import deepcopy
import math
import time

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from .defect_shapes import patch_depth_factor, lightning_crater

BINDING_TOLERANCE_M = 1e-5
SIZE_TOLERANCE_M = .001
OCCLUSION_TOLERANCE_M = .0001
GENERATOR_VERSION = 'nrel-visual-defect-geometry-v1'

STYLES = {
    'fine_crack': ((.085, .062, .043), .8),
    'open_crack': ((.095, .072, .052), .9),
    'pit': ((.28, .21, .13), .85),
    'erosion': ((.42, .33, .20), .95),
    'coating_loss': ((.58, .43, .24), .85),
    'lightning': ((.045, .025, .016), .95),
}
REFERENCE_UV = 'NREL.ReferenceSU'


def _parameter_triangles(shape):
    sign = 1. if shape['anchors'][0]['surface_side'] == 'suction' else -1.
    def uv(anchor): return (anchor['s_m'], sign*anchor['u'], 0.)
    result = []
    for row in range(len(shape['left_anchors'])-1):
        quad = [uv(shape['left_anchors'][row]), uv(shape['right_anchors'][row]),
                uv(shape['right_anchors'][row+1]), uv(shape['left_anchors'][row+1])]
        result.extend(([quad[0],quad[1],quad[2]], [quad[0],quad[2],quad[3]]))
    return result


def _tree(mesh):
    mesh.calc_loop_triangles()
    return BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices],
        [tuple(t.vertices) for t in mesh.loop_triangles], all_triangles=True)


def _xyz(value):
    return tuple(float(x) for x in value)


def _normal(triangle):
    # Keep clipped reference slivers in double precision. Converting their
    # nearby endpoints to mathutils float32 before subtraction can tilt normals.
    a, b, c = triangle
    u, v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
    n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
    length = math.sqrt(sum(x*x for x in n))
    if length <= 1e-12:
        raise ValueError('Degenerate defect support triangle')
    return tuple(x/length for x in n)


def _normals(triangles, outward=None):
    result = []
    for triangle in triangles:
        n = Vector(_normal(triangle))
        if outward is not None and n.dot(Vector(outward)) < 0:
            n.negate()
        result.append(_xyz(n))
    return result


def _support(defect, kind, triangles, shape, *, primary, definition=None):
    triangles = [[_xyz(p) for p in tri] for tri in triangles]
    normal = shape['normals'][len(shape['normals']) // 2]
    return dict(support_id=f"{defect['id']}:r{defect['revision']}:{kind}",
        defect_id=defect['id'], revision=defect['revision'], turbine_id=defect['turbine_id'], blade_id=defect['blade_id'],
        support_kind=kind, primary=primary,
        support_definition=dict(version='nrel-defect-support-v1',
            generator_version=GENERATOR_VERSION, region=kind,
            coordinate_system='healthy blade-local metres',
            primary=primary, **(definition or {})),
        triangles_local=triangles,
        normals_local=_normals(triangles, normal if kind != 'damaged_surface' else None),
        centreline_local=[_xyz(p) for p in shape['centerline']] if primary else [],
        width_sections_local=[[_xyz(a), _xyz(b)] for a, b in zip(shape['left'], shape['right'])] if primary else [],
        anchors=deepcopy(shape.get('anchors', [])) if primary else [])


def _new_material(name, color, roughness):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*color, 1)
    node = mat.node_tree.nodes.get('Principled BSDF')
    node.inputs['Base Color'].default_value = (*color, 1)
    node.inputs['Roughness'].default_value = roughness
    return mat


def _mask_group(support, owned_groups):
    """Reuse an immutable mask group while a live transaction references it.

    Independent groups bound node-graph construction cost; unchanged masks are
    shared across prepare/activate/undo. The final material user owns lifetime.
    """
    import hashlib
    import json
    parameter = support['support_definition'].get('parameter_mask')
    signature = hashlib.sha256(json.dumps(dict(version='mask-group-v1',
        parameter=parameter, triangles=None if parameter else support['triangles_local']),
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    group = next((g for g in bpy.data.node_groups if g.get('nrel_mask_signature') == signature), None)
    if group is not None:
        if group not in owned_groups: owned_groups.append(group)
        return group
    group = bpy.data.node_groups.new('NREL.Editor.AnalyticMask', 'ShaderNodeTree')
    owned_groups.append(group)
    group['nrel_mask_signature'] = signature
    group.interface.new_socket(name='Mask', in_out='OUTPUT', socket_type='NodeSocketFloat')
    nodes, links = group.nodes, group.links
    geometry = nodes.new('ShaderNodeNewGeometry')
    local = nodes.new('ShaderNodeVectorTransform')
    local.vector_type = 'POINT'
    local.convert_from, local.convert_to = 'WORLD', 'OBJECT'
    links.new(geometry.outputs['Position'], local.inputs['Vector'])
    uv = nodes.new('ShaderNodeUVMap'); uv.uv_map = REFERENCE_UV

    def vector(op, a, b):
        node = nodes.new('ShaderNodeVectorMath'); node.operation = op
        for index, value in enumerate((a, b)):
            if hasattr(value, 'node'): links.new(value, node.inputs[index])
            else: node.inputs[index].default_value = value
        return node.outputs['Value' if op == 'DOT_PRODUCT' else 'Vector']

    def scalar(op, a, b):
        node = nodes.new('ShaderNodeMath'); node.operation = op
        for index, value in enumerate((a, b)):
            if hasattr(value, 'node'): links.new(value, node.inputs[index])
            else: node.inputs[index].default_value = value
        return node.outputs[0]

    def triangle_union(triangles, coordinates, slab_required):
        union = None
        for triangle in triangles:
            a, b, c = map(Vector, triangle)
            n = (b-a).cross(c-a).normalized()
            mask = None
            for start, end in ((a,b), (b,c), (c,a)):
                signed = vector('DOT_PRODUCT', vector('SUBTRACT', coordinates, start),
                                n.cross(end-start).normalized())
                inside = scalar('GREATER_THAN', signed, 0.)
                mask = inside if mask is None else scalar('MULTIPLY', mask, inside)
            if slab_required:
                distance = vector('DOT_PRODUCT', vector('SUBTRACT', coordinates, a), n)
                slab = scalar('LESS_THAN', scalar('ABSOLUTE', distance, 0.), BINDING_TOLERANCE_M)
                mask = scalar('MULTIPLY', mask, slab)
            union = mask if union is None else scalar('MAXIMUM', union, mask)
        if union is None: raise ValueError('Material defect has an empty mask')
        return union

    if parameter:
        union = triangle_union(parameter['outer'], uv.outputs['UV'], False)
        if parameter['excluded']:
            excluded = triangle_union(parameter['excluded'], uv.outputs['UV'], False)
            union = scalar('MULTIPLY', union, scalar('SUBTRACT', 1., excluded))
    else:
        union = triangle_union(support['triangles_local'], local.outputs['Vector'], True)
    output = nodes.new('NodeGroupOutput')
    links.new(union, output.inputs['Mask'])
    return group


def _mask_material(base, supports, name, owned_materials, owned_groups):
    """Layer independently colored analytic masks on healthy-surface material."""
    mat = base.copy(); owned_materials.append(mat)
    mat.name = name; mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    shader = nodes.get('Principled BSDF')
    if shader is None: raise ValueError('Healthy blade material has no Principled BSDF')
    for support in supports:
        mask = nodes.new('ShaderNodeGroup')
        mask.node_tree = _mask_group(support, owned_groups)
        mask.label = support['support_id']
        style = support['support_definition'].get('appearance', {})
        color = style.get('color', STYLES['fine_crack'][0])
        roughness = style.get('roughness', STYLES['fine_crack'][1])
        mix = nodes.new('ShaderNodeMixRGB'); mix.blend_type = 'MIX'
        if shader.inputs['Base Color'].is_linked:
            links.new(shader.inputs['Base Color'].links[0].from_socket, mix.inputs[1])
        else: mix.inputs[1].default_value = tuple(shader.inputs['Base Color'].default_value)
        mix.inputs[2].default_value = (*color, 1.)
        links.new(mask.outputs['Mask'], mix.inputs[0])
        links.new(mix.outputs[0], shader.inputs['Base Color'])
        rough = nodes.new('ShaderNodeMixRGB'); rough.blend_type = 'MIX'
        if shader.inputs['Roughness'].is_linked:
            links.new(shader.inputs['Roughness'].links[0].from_socket, rough.inputs[1])
        else:
            value = shader.inputs['Roughness'].default_value
            rough.inputs[1].default_value = (value, value, value, 1.)
        rough.inputs[2].default_value = (roughness, roughness, roughness, 1.)
        links.new(mask.outputs['Mask'], rough.inputs[0])
        links.new(rough.outputs[0], shader.inputs['Roughness'])
    mat['nrel_defect_mask'] = 'analytic immutable reference UV union; semantic threshold >= 0.5'
    mat['nrel_mask_coordinate_system'] = REFERENCE_UV
    return mat


class DefectGeometry:
    """prepare / activate / dispose callbacks for DefectStore.

    prepare() does not mutate the visible blade data. activate() swaps all blade
    meshes and analysis metadata together; its return value can be disposed only
    after the store has accepted the transaction.
    """
    def __init__(self, rig, surface):
        self.rig, self.surface = rig, surface
        self.turbine_id = rig.get('turbine_id', surface.turbine_ids[0])
        if self.turbine_id not in surface.turbine_ids or len(rig['blades']) != 3:
            raise ValueError('A NREL defect geometry manager needs one registered three-blade turbine')
        self._closed = False
        self._baseline = {}
        referenced = {index for face in surface.faces for index in face}
        if len(surface.vertices)-1 not in referenced:
            raise ValueError('NREL cosmetic apex must be the referenced terminal vertex')
        self._apex = Vector(surface.vertices[-1])
        self._mechanical_tip = (Vector(surface.vertices[-2])
            if len(surface.vertices) % surface.n == 2 and len(surface.vertices)-2 not in referenced else None)
        self._healthy = {}
        for i, blade in enumerate(rig['blades'], 1):
            # The visible source mesh may already be deformed. Keep its material
            # slots/face assignment, but restoration must use the healthy rest
            # coordinates supplied by the frontend.
            if len(blade.data.polygons) != len(surface.faces):
                raise ValueError('Visible NREL blade topology differs from its healthy reference')
            baseline = bpy.data.meshes.new(f'NREL.Editor.{self.turbine_id}.Baseline.Blade{i}')
            baseline.from_pydata(surface.vertices, [], surface.faces)
            for material in blade.data.materials:
                baseline.materials.append(material)
            for polygon, original in zip(baseline.polygons, blade.data.polygons):
                polygon.material_index = original.material_index
                polygon.use_smooth = original.use_smooth
            for key in ('wfrl_tip_marking_revision', 'wfrl_tip_marking_note'):
                if key in blade.data:
                    baseline[key] = blade.data[key]
            baseline.update()
            self._baseline[i] = baseline
            mesh = bpy.data.meshes.new('NREL.ImmutableHealthyReference')
            mesh.from_pydata(surface.vertices, [], surface.triangles)
            mesh.update()
            uv = mesh.uv_layers.new(name=REFERENCE_UV)
            for polygon in mesh.polygons:
                coordinates = [divmod(v, surface.n) for v in polygon.vertices]
                cap = (any(row >= len(surface._spans) for row,j in coordinates) or
                       len({row for row,j in coordinates}) == 1)
                # Corner UVs intentionally split the shared trailing-edge vertex.
                sign = -1. if any(j > surface.n//2 for row,j in coordinates) else 1.
                for index in polygon.loop_indices:
                    row, j = divmod(mesh.loops[index].vertex_index, surface.n)
                    uv.data[index].uv = ((-1., 0.) if cap else
                                        (surface._spans[row], sign*surface._us[j]))
            for material in blade.data.materials:
                mesh.materials.append(material)
            for polygon, source_index in zip(mesh.polygons, surface.triangle_face_indices):
                original = blade.data.polygons[source_index]
                polygon.material_index = original.material_index
                polygon.use_smooth = original.use_smooth
            for key in ('wfrl_tip_marking_revision', 'wfrl_tip_marking_note'):
                if key in blade.data:
                    mesh[key] = blade.data[key]
            self._healthy[i] = mesh
        self._active = dict(document=None, supports=[],
            meshes={i: blade.data for i, blade in enumerate(rig['blades'], 1)},
            materials=[], bvh_by_blade={i: _tree(blade.data) for i, blade in enumerate(rig['blades'], 1)},
            measurements=[], version=0, owned_meshes=True)
        self.boolean_calls = 0

    @property
    def active(self):
        return self._active

    def _preserve_tip_vertices(self, mesh):
        """Rebuild post-Boolean ordering while retaining loop UVs and markings.

        Trails read the cosmetic apex at [-1]. ComparisonView reads the loose
        mechanical tip at [-2] when present. Exact Boolean can reorder/drop
        these vertices, so preserve this explicit presentation contract.
        """
        referenced = {int(v) for polygon in mesh.polygons for v in polygon.vertices}
        apex = min(referenced, key=lambda index: (mesh.vertices[index].co-self._apex).length_squared)
        if (mesh.vertices[apex].co-self._apex).length > BINDING_TOLERANCE_M:
            raise ValueError('NREL Boolean did not preserve the cosmetic apex')
        loose = []
        if self._mechanical_tip is not None:
            loose = [v.index for v in mesh.vertices if v.index not in referenced and
                     (v.co-self._mechanical_tip).length <= BINDING_TOLERANCE_M]
        order = [v.index for v in mesh.vertices if v.index != apex and v.index not in loose]
        coordinates = [tuple(mesh.vertices[index].co) for index in order]
        mapping = {index: position for position, index in enumerate(order)}
        if self._mechanical_tip is not None:
            coordinates.append(tuple(self._mechanical_tip))
        mapping[apex] = len(coordinates)
        coordinates.append(tuple(mesh.vertices[apex].co))
        result = bpy.data.meshes.new(mesh.name+'.StableTips')
        result.from_pydata(coordinates, [], [tuple(mapping[index] for index in polygon.vertices)
                                            for polygon in mesh.polygons])
        for material in mesh.materials:
            result.materials.append(material)
        for target, source in zip(result.polygons, mesh.polygons):
            target.material_index = source.material_index
            target.use_smooth = source.use_smooth
        for layer in mesh.uv_layers:
            target = result.uv_layers.new(name=layer.name)
            for index, value in enumerate(layer.data):
                target.data[index].uv = value.uv
        for key in ('wfrl_tip_marking_revision', 'wfrl_tip_marking_note'):
            if key in mesh:
                result[key] = mesh[key]
        result.update()
        return result

    def _footprint(self, shape, exclusions=()):
        """Clip the strip in (s,u) to every crossed healthy mesh triangle.

        Interpolating only strip corner positions would bridge curved mesh
        facets. Clipping first makes every support sample an actual healthy
        surface point and keeps the shader slab and analysis region identical.
        """
        surface = self.surface
        side = shape['anchors'][0]['surface_side']
        js = range(surface.n//2) if side == 'suction' else range(surface.n//2, surface.n-1)
        def cross(a, b): return a[0]*b[1]-a[1]*b[0]
        def sub(a, b): return (a[0]-b[0], a[1]-b[1])
        def uv(a): return (a['s_m'], a['u'])
        def halfplane(poly, a, b, orientation):
            if not poly: return []
            edge = sub(b, a)
            def signed(p): return orientation*cross(edge,sub(p,a))
            result = []
            previous, dp = poly[-1], signed(poly[-1])
            for current in poly:
                dc = signed(current)
                if (dc >= 0.) != (dp >= 0.):
                    t = dp/(dp-dc)
                    result.append(tuple(x+t*(y-x) for x,y in zip(previous,current)))
                if dc >= 0.: result.append(current)
                previous, dp = current, dc
            return result
        def clip(poly, triangle):
            orientation = 1 if cross(sub(triangle[1],triangle[0]), sub(triangle[2],triangle[0])) > 0 else -1
            for a, b in zip(triangle, triangle[1:]+triangle[:1]):
                poly = halfplane(poly, a, b, orientation)
            return poly
        def bounds(poly):
            return (min(p[0] for p in poly), max(p[0] for p in poly),
                    min(p[1] for p in poly), max(p[1] for p in poly))
        holes = []
        for excluded in exclusions:
            for row in range(len(excluded['left_anchors'])-1):
                quad = [uv(excluded['left_anchors'][row]), uv(excluded['right_anchors'][row]),
                        uv(excluded['right_anchors'][row+1]), uv(excluded['left_anchors'][row+1])]
                for tri in ([quad[0],quad[1],quad[2]], [quad[0],quad[2],quad[3]]):
                    holes.append((tri, bounds(tri)))
        def subtract_holes(poly):
            pieces = [poly] if len(poly) >= 3 else []
            for hole, hb in holes:
                remaining = []
                orientation = 1 if cross(sub(hole[1],hole[0]), sub(hole[2],hole[0])) > 0 else -1
                for piece in pieces:
                    pb = bounds(piece)
                    if pb[1] <= hb[0] or hb[1] <= pb[0] or pb[3] <= hb[2] or hb[3] <= pb[2]:
                        remaining.append(piece); continue
                    inside = piece
                    for a,b in zip(hole, hole[1:]+hole[:1]):
                        outside = halfplane(inside, a, b, -orientation)
                        if len(outside) >= 3: remaining.append(outside)
                        inside = halfplane(inside, a, b, orientation)
                        if len(inside) < 3: break
                pieces = remaining
            return pieces
        triangles = []
        for row in range(len(shape['left_anchors'])-1):
            quad = [uv(shape['left_anchors'][row]),uv(shape['right_anchors'][row]),
                    uv(shape['right_anchors'][row+1]),uv(shape['left_anchors'][row+1])]
            smin,smax = min(p[0] for p in quad),max(p[0] for p in quad)
            umin,umax = min(p[1] for p in quad),max(p[1] for p in quad)
            for k in range(len(surface._spans)-1):
                s0,s1 = surface._spans[k:k+2]
                if s1 < smin or s0 > smax: continue
                for j in js:
                    u0,u1 = surface._us[j:j+2]
                    if max(u0,u1) < umin or min(u0,u1) > umax: continue
                    corners = [(s0,u0),(s1,u0),(s1,u1),(s0,u1)]
                    ids = [k*surface.n+j,(k+1)*surface.n+j,(k+1)*surface.n+j+1,k*surface.n+j+1]
                    for inds in ((0,1,2),(0,2,3)):
                        parameter = [corners[i] for i in inds]
                        positions = [surface.vertices[ids[i]] for i in inds]
                        ab,ac = sub(parameter[1],parameter[0]),sub(parameter[2],parameter[0])
                        determinant = cross(ab,ac)
                        def position(p):
                            ap = sub(p,parameter[0])
                            v,w = cross(ap,ac)/determinant,cross(ab,ap)/determinant
                            return tuple(positions[0][axis]+(positions[1][axis]-positions[0][axis])*v+
                                         (positions[2][axis]-positions[0][axis])*w for axis in range(3))
                        for strip in ([quad[0],quad[1],quad[2]],[quad[0],quad[2],quad[3]]):
                            for clipped in subtract_holes(clip(strip,parameter)):
                                for index in range(1,len(clipped)-1):
                                    tri = [position(clipped[i]) for i in (0,index,index+1)]
                                    try: _normal(tri)
                                    except ValueError: continue
                                    triangles.append(tri)
        if not triangles:
            raise ValueError('Empty healthy-surface defect support')
        return triangles

    def _cut(self, blade, defect, shape, material):
        depth = defect['shape']['depth_m']
        overhang = max(.02, depth)
        vertices, faces = [], []
        for left, right, normal in zip(shape['left'], shape['right'], shape['normals']):
            left, right, normal = Vector(left), Vector(right), Vector(normal)
            vertices.extend((left + normal*overhang, right + normal*overhang,
                             right - normal*depth, left - normal*depth))
        for row in range(len(shape['left'])-1):
            for side in range(4):
                a, b = row*4+side, row*4+(side+1)%4
                faces.extend(((a,b,b+4), (a,b+4,a+4)))
        faces.extend(((3,2,1), (3,1,0)))
        end = len(vertices)-4
        faces.extend(((end,end+1,end+2), (end,end+2,end+3)))
        return self._apply_cutter(blade, defect, vertices, faces, material)

    def _patch_point(self, shape, row, z):
        anchor = shape['anchors'][row]
        boundary = shape['left_anchors' if z < 0 else 'right_anchors'][row]
        return self.surface.frame(dict(anchor,
            s_m=anchor['s_m']+abs(z)*(boundary['s_m']-anchor['s_m']),
            u=anchor['u']+abs(z)*(boundary['u']-anchor['u'])))

    def _cut_patch(self, blade, defect, shape, material):
        """Closed sampled bowl, with an independently prescribed inward floor."""
        rows, columns = len(shape['anchors']), 9
        depth = defect['shape']['depth_m']
        vertices, faces = [], []
        # Use the actual healthy surface at each grid point, not a planar decal.
        for layer in (0, 1):
            for row in range(rows):
                q = 2*row/(rows-1)-1
                for col in range(columns):
                    z = 2*col/(columns-1)-1
                    frame = self._patch_point(shape, row, z)
                    inward = depth*patch_depth_factor(defect['morphology'], q, z, defect['shape'])
                    offset = max(.02, depth) if layer == 0 else -inward
                    vertices.append(Vector(frame['point'])+Vector(frame['normal'])*offset)
        count = rows*columns
        for layer in (0, 1):
            for row in range(rows-1):
                for col in range(columns-1):
                    a = layer*count+row*columns+col
                    faces.extend(((a,a+1,a+columns+1),(a,a+columns+1,a+columns)))
        border = (list(range(columns)) + [r*columns+columns-1 for r in range(1,rows)] +
                  [(rows-1)*columns+c for c in range(columns-2,-1,-1)] +
                  [r*columns for r in range(rows-2,0,-1)])
        for a,b in zip(border, border[1:]+border[:1]):
            faces.extend(((a,b,b+count),(a,b+count,a+count)))
        return self._apply_cutter(blade, defect, vertices, faces, material)

    def _apply_cutter(self, blade, defect, vertices, faces, material):
        mesh = blade.data
        slot = len(mesh.materials)
        mesh.materials.append(material)
        cutmesh = bpy.data.meshes.new('NREL.Editor.Cutter')
        cutmesh.from_pydata(vertices, [], faces)
        bm = bmesh.new()
        try:
            bm.from_mesh(cutmesh)
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            bm.to_mesh(cutmesh)
        finally:
            bm.free()
        cutter = bpy.data.objects.new('NREL.Editor.Cutter', cutmesh)
        self.rig['collection'].objects.link(cutter)
        cutter.parent = blade.parent
        cutter.matrix_basis = blade.matrix_basis.copy()
        cutter.matrix_parent_inverse = blade.matrix_parent_inverse.copy()
        cutter['nrel_defect_role'] = 'cutter'
        cutter.hide_render = True
        for mat in mesh.materials:
            cutmesh.materials.append(mat)
        for polygon in cutmesh.polygons:
            polygon.material_index = slot
        modifier = None
        try:
            bpy.context.view_layer.update()
            modifier = blade.modifiers.new(f"Defect {defect['id']}", 'BOOLEAN')
            modifier.operation, modifier.solver, modifier.object = 'DIFFERENCE', 'EXACT', cutter
            self.boolean_calls += 1
            with bpy.context.temp_override(object=blade, active_object=blade):
                bpy.ops.object.modifier_apply(modifier=modifier.name)
            modifier = None
        finally:
            if modifier is not None:
                blade.modifiers.remove(modifier)
            bpy.data.objects.remove(cutter, do_unlink=True)
            if cutmesh.users == 0:
                bpy.data.meshes.remove(cutmesh)
        if not any(poly.material_index == slot for poly in blade.data.polygons):
            raise ValueError(f"{defect['id']}: Boolean did not produce a genuine opening/interior")
        return slot

    def _measure_depth(self, defect, shape, tree):
        depth = defect['shape']['depth_m']
        values, expected = [], []
        for index in range(2, len(shape['centerline'])-2):
            p, normal = Vector(shape['centerline'][index]), Vector(shape['normals'][index])
            hit, _, _, distance = tree.ray_cast(p + normal*.02, -normal, depth+.04)
            if hit is None:
                raise ValueError(f"{defect['id']}: no independently verified groove floor (possible through-cut)")
            measured = float(distance)-.02
            target = depth
            if defect['morphology'] != 'open_crack':
                target *= patch_depth_factor(defect['morphology'],
                    2*index/(len(shape['centerline'])-1)-1, 0., defect['shape'])
            if measured <= BINDING_TOLERANCE_M or abs(measured-target) > SIZE_TOLERANCE_M:
                raise ValueError(f"{defect['id']}: actual inward depth {measured:.6g} m differs from {target:.6g} m")
            values.append(measured)
            expected.append(target)
        return dict(method='healthy-centreline inward ray to actual post-Boolean mesh',
            depth_range_m=[min(values), max(values)], depth_samples=len(values),
            expected_depth_samples_m=expected,
            depth_coverage='interior centreline nodes including nominal maximum; not a continuous floor bound',
            max_depth_error_m=max(abs(v-d) for v,d in zip(values,expected)))

    def _measure_opening_width(self, defect, shape, tree):
        """Locate both actual mouth edges with independent inward ray probes."""
        values, errors = [], []
        centre_index = len(shape['anchors'])//2
        indices = sorted(set((centre_index-2, centre_index-1, centre_index, centre_index+1, centre_index+2)))
        for index in indices:
            centre = shape['anchors'][index]
            actual_sides = []
            for key in ('left_anchors', 'right_anchors'):
                boundary = shape[key][index]
                def anchor_at(factor):
                    return dict(centre, s_m=centre['s_m']+factor*(boundary['s_m']-centre['s_m']),
                        u=centre['u']+factor*(boundary['u']-centre['u']))
                def removed(factor):
                    frame = self.surface.frame(anchor_at(factor))
                    p,n = Vector(frame['point']),Vector(frame['normal'])
                    hit,_,_,distance = tree.ray_cast(p+n*.02,-n,defect['shape']['depth_m']+.04)
                    return hit is None or distance-.02 > BINDING_TOLERANCE_M
                if not removed(0.) or removed(1.5):
                    raise ValueError(f"{defect['id']}: actual opening edge could not be bracketed")
                low,high = 0.,1.5
                for _ in range(20):
                    middle = (low+high)/2
                    if removed(middle): low = middle
                    else: high = middle
                factor = (low+high)/2
                points = [Vector(self.surface.point(anchor_at(factor*j/8))) for j in range(9)]
                actual_sides.append(sum((b-a).length for a,b in zip(points,points[1:])))
            width = sum(actual_sides)
            values.append(width); errors.append(abs(width-shape['widths_m'][index]))
        if max(errors) > SIZE_TOLERANCE_M:
            raise ValueError(f"{defect['id']}: actual mouth width exceeds frozen dimension tolerance")
        return dict(actual_mouth_widths_m=values, actual_mouth_width_indices=indices,
            actual_max_width_m=max(values), max_mouth_width_error_m=max(errors),
            mouth_width_method='independent inward-ray mouth transitions; transverse healthy-surface arc',
            mouth_width_coverage='five central sections including nominal maximum; other sections unmeasured')

    def prepare(self, document):
        if self._closed:
            raise ValueError('NREL defect geometry manager is closed')
        started = time.perf_counter()
        payload = dict(document=deepcopy(document), supports=[], meshes={}, materials=[], node_groups=[],
            bvh_by_blade={}, measurements=[], owned_meshes=True)
        staging = []
        shapes, interiors, geometric_defects = {}, {}, {}
        try:
            for i, original in enumerate(self.rig['blades'], 1):
                mesh = self._healthy[i].copy()
                mesh.name = f'NREL.Editor.Blade{i}'
                payload['meshes'][i] = mesh
                obj = bpy.data.objects.new(f'NREL.Editor.Staging{i}', mesh)
                self.rig['collection'].objects.link(obj)
                obj.parent = original.parent
                obj.matrix_basis = original.matrix_basis.copy()
                obj.matrix_parent_inverse = original.matrix_parent_inverse.copy()
                obj['nrel_defect_role'] = 'staging'
                obj.hide_render = True
                staging.append(obj)
            enabled = sorted((row for row in document['defects'] if row.get('enabled', True)
                              and row['turbine_id'] == self.turbine_id), key=lambda d:d['id'])
            for defect in enabled:
                shape = self.surface.sample_shape(defect)
                triangles = self._footprint(shape)
                for field, actual in (('length_m', shape['measured_length_m']), ('max_width_m', shape['measured_max_width_m'])):
                    if abs(defect['shape'][field]-actual) > SIZE_TOLERANCE_M:
                        raise ValueError(f"{defect['id']}: measured {field} is outside frozen size tolerance")
                morphology = defect['morphology']
                appearance_only = morphology in ('fine_crack', 'coating_loss')
                hybrid = morphology == 'lightning'
                kind = 'event_footprint' if hybrid else 'material_mask' if appearance_only else 'opening_footprint'
                color, roughness = STYLES[morphology]
                definition = dict(virtual_region=not appearance_only, morphology=morphology,
                    occlusion_rule='actual geometry strictly before healthy footprint' if not appearance_only else 'actual surface',
                    healthy_mesh_facet_clipped=True, sampling=deepcopy(shape.get('support_definition', {})))
                if kind == 'material_mask':
                    definition.update(mask_version='reference-su-triangle-union-v1', semantic_threshold=.5,
                        appearance=dict(color=color, roughness=roughness),
                        texture_resolution=None, mask_precision='analytic nodes; subject to renderer floating point and image sampling',
                        parameter_mask=dict(outer=_parameter_triangles(shape), excluded=[]),
                        uv_layer=REFERENCE_UV)
                if hybrid:
                    definition.update(event_id=defect['id'],
                        components=['material_mask', 'opening_footprint', 'damaged_surface'],
                        aggregation='one primary union of scorch and pit mouth; components reported separately',
                        cause_basis='user-defined synthetic lightning preset, not inferred from appearance')
                payload['supports'].append(_support(defect, kind, triangles, shape, primary=True, definition=definition))
                measure = dict(defect_id=defect['id'], revision=defect['revision'], turbine_id=defect['turbine_id'], blade_id=defect['blade_id'],
                    nominal=deepcopy(defect['shape']), measured_length_m=shape['measured_length_m'],
                    measured_max_width_m=shape['measured_max_width_m'], width_samples_m=list(shape['widths_m']),
                    binding_tolerance_m=BINDING_TOLERANCE_M, size_tolerance_m=SIZE_TOLERANCE_M,
                    depth_m=None, depth_reason='material-only; no geometric opening')
                payload['measurements'].append(measure)
                if not appearance_only:
                    geometric = lightning_crater(defect) if hybrid else defect
                    cutshape = self.surface.sample_shape(geometric) if hybrid else shape
                    shapes[defect['id']] = cutshape
                    geometric_defects[defect['id']] = geometric
                    if hybrid:
                        measure['crater_nominal'] = deepcopy(geometric['shape'])
                        measure['dimension_scope'] = 'length/width: whole event; depth/mouth width: nested crater'
                        ring = self._footprint(shape, exclusions=[cutshape])
                        payload['supports'].append(_support(defect, 'material_mask', ring, shape, primary=False,
                            definition=dict(virtual_region=False, event_id=defect['id'],
                                morphology=morphology, appearance=dict(color=color, roughness=roughness),
                                mask_version='reference-su-triangle-union-v1', semantic_threshold=.5,
                                parameter_mask=dict(outer=_parameter_triangles(shape), excluded=_parameter_triangles(cutshape)),
                                uv_layer=REFERENCE_UV,
                                healthy_mesh_facet_clipped=True, excluded_region='nested pit mouth',
                                occlusion_rule='actual surface', aggregation='separate component; do not add to primary event')))
                        payload['supports'].append(_support(defect, 'opening_footprint', self._footprint(cutshape),
                            cutshape, primary=False, definition=dict(virtual_region=True, event_id=defect['id'],
                                occlusion_rule='actual geometry strictly before healthy opening',
                                aggregation='separate component; do not add to primary event')))
                    material = _new_material(f"NREL.Editor.Interior.{defect['id']}", color, roughness)
                    material['nrel_defect_id'] = defect['id']
                    payload['materials'].append(material)
                    cutter = self._cut if morphology == 'open_crack' else self._cut_patch
                    interiors[defect['id']] = cutter(staging[defect['blade_id']-1], geometric, cutshape, material)
            for i, obj in enumerate(staging, 1):
                # modifier_apply may replace the mesh datablock.
                generated = obj.data
                obj.data = self._preserve_tip_vertices(generated)
                old = payload['meshes'][i]
                payload['meshes'][i] = obj.data
                if old is not obj.data and old.users == 0:
                    bpy.data.meshes.remove(old)
                if generated is not old and generated.users == 0:
                    bpy.data.meshes.remove(generated)
                fine = [s for s in payload['supports'] if s['blade_id'] == i and s['support_kind'] == 'material_mask']
                if fine:
                    # Markings occupy additional healthy material slots. Apply
                    # the same immutable UV mask to every healthy surface slot;
                    # interior materials retain their own geometric appearance.
                    for slot in range(len(self._healthy[i].materials)):
                        mat = _mask_material(obj.data.materials[slot], fine,
                            f'NREL.Editor.{self.turbine_id}.Mask.Blade{i}.Slot{slot}',
                            payload['materials'], payload['node_groups'])
                        obj.data.materials[slot] = mat
                payload['bvh_by_blade'][i] = _tree(obj.data)
            for defect, measure in zip(enabled, payload['measurements']):
                if defect['id'] not in geometric_defects:
                    continue
                i, shape = defect['blade_id'], shapes[defect['id']]
                geometric = geometric_defects[defect['id']]
                measure.update(self._measure_depth(geometric, shape, payload['bvh_by_blade'][i]))
                measure.update(self._measure_opening_width(geometric, shape, payload['bvh_by_blade'][i]))
                measure['depth_m'], measure['depth_reason'] = measure['depth_range_m'][1], None
                mesh = payload['meshes'][i]
                mesh.calc_loop_triangles()
                triangles = [[_xyz(mesh.vertices[v].co) for v in tri.vertices] for tri in mesh.loop_triangles
                    if mesh.polygons[tri.polygon_index].material_index == interiors[defect['id']]]
                payload['supports'].append(_support(defect, 'damaged_surface', triangles, shape, primary=False,
                    definition=dict(virtual_region=False, aggregation='separate from opening footprint',
                        occlusion_rule='actual surface', extent='surviving defect walls and floor')))
            payload['prepare_seconds'] = time.perf_counter()-started
            return payload
        except Exception:
            for i, obj in enumerate(staging, 1):
                old = payload['meshes'][i]
                payload['meshes'][i] = obj.data
                if old is not obj.data and old.users == 0:
                    bpy.data.meshes.remove(old)
                bpy.data.objects.remove(obj, do_unlink=True)
            staging.clear()
            self.dispose(payload)
            raise
        finally:
            for obj in staging:
                bpy.data.objects.remove(obj, do_unlink=True)

    def activate(self, payload, document=None, version=None):
        if self._closed:
            raise ValueError('NREL defect geometry manager is closed')
        if set(payload['meshes']) != set(self._healthy):
            raise ValueError('A geometry transaction must contain every blade')
        previous = self._active
        try:
            for i, blade in enumerate(self.rig['blades'], 1):
                blade.data = payload['meshes'][i]
            self.rig['defect_supports'] = payload['supports']
            self.rig['defect_measurements'] = payload['measurements']
            self.rig['defect_document'] = payload['document']
            if version is not None:
                payload['version'] = version
            self._active = payload
            bpy.context.view_layer.update()
        except Exception:
            for i, blade in enumerate(self.rig['blades'], 1):
                blade.data = previous['meshes'][i]
            self.rig['defect_supports'] = previous['supports']
            self.rig['defect_measurements'] = previous['measurements']
            self.rig['defect_document'] = previous['document']
            self._active = previous
            raise
        if previous.get('document') is None:
            # Initial scene-builder data belongs to this fresh rig. The store's
            # constructor has no superseded resource to dispose, so release only
            # these now-unused datablocks here. Shared data (users > 0) survives.
            self.dispose(previous)
        return previous

    def close(self, restore=True):
        """Release editor resources, optionally restoring the healthy rest loft.

        The caller owns replay rebind/update after this operation. This callback
        never changes a deformation cache or applies a loaded replay pose.
        """
        if self._closed:
            return self._active
        previous = self._active
        if restore:
            for i, blade in enumerate(self.rig['blades'], 1):
                blade.data = self._baseline[i]
            restored = dict(document=None, supports=[], measurements=[], meshes=dict(self._baseline),
                            materials=[], node_groups=[], bvh_by_blade={}, version=0, owned_meshes=False)
            self.rig['defect_supports'] = []
            self.rig['defect_measurements'] = []
            self.rig['defect_document'] = None
            self._active = restored
        else:
            self._active = None
        self._closed = True
        self.dispose(previous)
        for mesh in list(self._healthy.values())+list(self._baseline.values()):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        self._healthy.clear()
        self._baseline.clear()
        return self._active

    def dispose(self, payload):
        if payload is None or payload is self._active or payload.get('_disposed', False):
            return
        payload['_disposed'] = True
        if payload.get('owned_meshes', True):
            for mesh in payload.get('meshes', {}).values():
                if mesh.users == 0 and mesh not in self._healthy.values():
                    bpy.data.meshes.remove(mesh)
        for mat in payload.get('materials', []):
            if mat.users == 0:
                bpy.data.materials.remove(mat)
        for group in payload.get('node_groups', []):
            if group.users == 0:
                bpy.data.node_groups.remove(group)
