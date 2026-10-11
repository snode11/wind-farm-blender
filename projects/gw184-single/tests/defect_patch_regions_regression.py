"""Background checks for reference UV seams, lightning regions and transactions.

This regression never generates images or invokes appearance export. Run with
factory startup and optionally one empty evidence directory after ``--``.
Without a directory the result is printed as JSON only. It does not depend on
any saved prechange source directory.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import time
from unittest.mock import patch

import bpy
import bmesh
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT/'projects/gw184-single'
sys.path[:0] = [str(Path(__file__).resolve().parent), str(PROJECT), str(ROOT/'blender_frontend')]
from defect_render_guard import GUARD_SCOPE, forbid_output_render
from gw184 import defect_geometry, reference_surface
from gw184.defect_geometry import BINDING_TOLERANCE_M, REFERENCE_UV
from gw184.defect_editor import EditorSession, register
from gw184.defect_schema import new_defect, new_document, validate_document
from gw184.defect_shapes import lightning_crater


def float32(value):
    return struct.unpack('f', struct.pack('f', value))[0]


def mesh_digest(mesh):
    value = [tuple(tuple(v.co) for v in mesh.vertices), tuple(tuple(p.vertices) for p in mesh.polygons)]
    return hashlib.sha256(repr(value).encode()).hexdigest()


def resource_counts():
    return dict(objects=len(bpy.data.objects), meshes=len(bpy.data.meshes), materials=len(bpy.data.materials),
                material_nodes=sum(len(mat.node_tree.nodes) for mat in bpy.data.materials if mat.node_tree),
                node_groups=len(bpy.data.node_groups), group_nodes=sum(len(group.nodes) for group in bpy.data.node_groups))


def area(triangles):
    values = []
    for a, b, c in triangles:
        ab, ac = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        cross = (ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0])
        values.append(.5*math.sqrt(sum(v*v for v in cross)))
    return math.fsum(values)


def inside_with_margin(point, polygon, chord_scale):
    """Ray crossing in (s,u), returning distance to boundary in a local metric."""
    x, y = point[0], point[1]*chord_scale
    vertices = [(s, u*chord_scale) for s, u in polygon]
    inside, distance = False, math.inf
    for a, b in zip(vertices, vertices[1:]+vertices[:1]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        length2 = dx*dx+dy*dy
        t = max(0., min(1., ((x-a[0])*dx+(y-a[1])*dy)/length2)) if length2 else 0.
        distance = min(distance, math.hypot(x-a[0]-t*dx, y-a[1]-t*dy))
        if (a[1] > y) != (b[1] > y) and x < a[0]+(y-a[1])*dx/dy:
            inside = not inside
    return inside, distance


def run(report, renderer):
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)
        print('PASS: '+name, flush=True)

    register()
    editor = EditorSession()
    generator, surface = editor.geometry, editor.surface
    active = generator.active
    active_meshes = {i: blade.data for i, blade in enumerate(editor.rig['blades'], 1)}
    camera_signature = editor.fixed_camera_signature()
    healthy_digest = {i: mesh_digest(mesh) for i, mesh in generator._healthy.items()}

    # Read actual per-loop UV data, including the shared j=0 trailing-edge
    # vertex. Vertex-only UV assignment cannot satisfy both branch contracts.
    uv_counts = Counter()
    max_uv_position_error = 0.
    for blade_id, mesh in generator._healthy.items():
        uv = mesh.uv_layers.get(REFERENCE_UV)
        check(f'blade {blade_id}: named reference UV layer exists', uv is not None)
        branches = {}
        for polygon in mesh.polygons:
            indices = list(polygon.vertices)
            rings = [v//surface.n for v in indices]
            cap = max(rings) >= len(surface._spans) or len(set(rings)) == 1
            if cap:
                branches[polygon.index] = None
                for loop in polygon.loop_indices:
                    assert tuple(uv.data[loop].uv) == (-1., 0.), 'Cap corner must be outside every positive-span mask'
                    uv_counts['cap_corners'] += 1
                continue
            side = 'pressure' if any(v % surface.n > surface.n//2 for v in indices) else 'suction'
            branches[polygon.index] = side
            sign = -1. if side == 'pressure' else 1.
            for loop in polygon.loop_indices:
                vertex = mesh.loops[loop].vertex_index
                row, j = divmod(vertex, surface.n)
                expected = (float32(surface._spans[row]), float32(sign*surface._us[j]))
                assert tuple(uv.data[loop].uv) == expected, f'{side} corner UV disagrees with its reference (s,u)'
                uv_counts[side+'_corners'] += 1
                if j == 0:
                    assert uv.data[loop].uv.y == sign, 'Trailing-edge shared vertex lost its branch sign'
                    uv_counts[side+'_trailing_edge_corners'] += 1
        # The other two blades are independently checked corner-by-corner;
        # interpolation needs one copy of the identical healthy reference.
        if blade_id != 1:
            continue
        mesh.calc_loop_triangles()
        for triangle in mesh.loop_triangles:
            side = branches[triangle.polygon_index]
            if side is None:
                continue
            s, signed_u = (math.fsum(uv.data[loop].uv[axis] for loop in triangle.loops)/3 for axis in (0, 1))
            if not (surface.domain['s_m'][0] <= s <= surface.domain['s_m'][1] and .05 <= abs(signed_u) <= .95):
                continue
            assert (signed_u < 0) == (side == 'pressure'), 'Triangle interpolation crossed the UV side seam'
            # Use the real float32 loop UVs and mesh coordinates. Double
            # arithmetic here does not manufacture higher precision inputs.
            actual = tuple(math.fsum(mesh.vertices[v].co[axis] for v in triangle.vertices)/3 for axis in range(3))
            reconstructed = surface.frame(dict(s_m=s, u=abs(signed_u), surface_side=side))['point']
            error = math.dist(actual, reconstructed)
            assert error <= BINDING_TOLERANCE_M, f'UV barycentre missed healthy facet by {error} m'
            max_uv_position_error = max(max_uv_position_error, error)
            uv_counts[side+'_barycentres'] += 1
    check('both reference sides have validated UV corners and interpolation',
          all(uv_counts[key] > 0 for key in ('suction_corners', 'pressure_corners', 'cap_corners',
              'suction_trailing_edge_corners', 'pressure_trailing_edge_corners', 'suction_barycentres', 'pressure_barycentres')))
    report['uv'] = dict(counts=dict(uv_counts), max_interpolated_position_error_m=max_uv_position_error,
                        position_tolerance_m=BINDING_TOLERANCE_M, precision='actual Blender float32 loop UVs and mesh vertices')

    coat = new_defect(surface, 'coating_loss', id='PRESSURE-COATING',
                      anchor=dict(surface_side='pressure', theta_deg=90.))
    lightning = new_defect(surface, 'lightning', id='PRESSURE-LIGHTNING',
                           anchor=dict(surface_side='pressure', theta_deg=90.))
    combined = new_document(surface); combined['defects'] = [coat, lightning]
    validate_document(combined, surface)

    def isolated_document(item):
        document = new_document(surface); document['defects'] = [deepcopy(item)]
        return document

    def assert_manifold(mesh, label):
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            check(label+': resulting mesh is manifold', bool(bm.edges) and all(edge.is_manifold for edge in bm.edges))
        finally:
            bm.free()

    counts = resource_counts()
    coating_payload = generator.prepare(isolated_document(coat))
    try:
        check('pressure coating at 90 degrees preserves healthy coordinates and topology',
              mesh_digest(coating_payload['meshes'][1]) == healthy_digest[1])
        assert_manifold(coating_payload['meshes'][1], 'pressure coating')
        support = coating_payload['supports'][0]
        check('pressure coating uses the declared reference UV parameter mask',
              support['support_definition']['uv_layer'] == REFERENCE_UV
              and all(p[1] < 0. for tri in support['support_definition']['parameter_mask']['outer'] for p in tri))
    finally:
        generator.dispose(coating_payload)
    check('coating preparation/disposal returns resource counts to baseline', resource_counts() == counts)

    lightning_payload = generator.prepare(isolated_document(lightning))
    try:
        assert_manifold(lightning_payload['meshes'][1], 'pressure lightning')
        frame = surface.frame(lightning['anchor'])
        point, normal = Vector(frame['point']), Vector(frame['normal'])
        hit, _, _, distance = lightning_payload['bvh_by_blade'][1].ray_cast(point+normal*.02, -normal, .2)
        check('pressure lightning at 90 degrees has independent genuine crater depth',
              hit is not None and distance-.02 > .001 and abs(distance-.02-lightning['shape']['depth_m']) <= .001)
        measure = lightning_payload['measurements'][0]
        check('pressure lightning passes depth and mouth-width measurements',
              measure['max_depth_error_m'] <= .001 and measure['max_mouth_width_error_m'] <= .001)
        regions = {s['support_kind']: s for s in lightning_payload['supports']}
        check('lightning support regions preserve one event and one primary',
              set(regions) == {'event_footprint', 'material_mask', 'opening_footprint', 'damaged_surface'}
              and all(s['defect_id'] == lightning['id'] for s in regions.values())
              and [s['support_kind'] for s in regions.values() if s['primary']] == ['event_footprint'])
        areas = {key: area(regions[key]['triangles_local']) for key in ('event_footprint', 'material_mask', 'opening_footprint')}
        area_error = abs(areas['event_footprint']-areas['material_mask']-areas['opening_footprint'])
        check('frozen-facet lightning area equals scorch ring plus crater mouth', area_error <= 1e-6)

        crater_shape = surface.sample_shape(lightning_crater(lightning))
        crater_polygon = [(a['s_m'], a['u']) for a in crater_shape['left_anchors']+list(reversed(crater_shape['right_anchors']))]
        chord_scale = Vector(frame['dp_du']).length
        projected_anchors = {}
        max_region_binding_error = 0.
        def bound_region_anchor(point):
            nonlocal max_region_binding_error
            key = tuple(point)
            if key not in projected_anchors:
                # These are known analytic region samples, not user clicks.
                # A float32 facet-seam point may have multiple click matches;
                # explicitly project, then retain the frozen binding test.
                anchor = surface.project(key, 'pressure')
                error = math.dist(surface.point(anchor), key)
                assert error <= BINDING_TOLERANCE_M, f'Region sample missed reference surface by {error} m'
                max_region_binding_error = max(max_region_binding_error, error)
                projected_anchors[key] = anchor
            return projected_anchors[key]
        ring_samples = 0
        for triangle in regions['material_mask']['triangles_local']:
            centre = tuple(math.fsum(p[i] for p in triangle)/3 for i in range(3))
            for p in list(triangle)+[centre]:
                anchor = bound_region_anchor(p)
                inside, margin = inside_with_margin((anchor['s_m'], anchor['u']), crater_polygon, chord_scale)
                assert not inside or margin <= BINDING_TOLERANCE_M, 'Scorch ring triangle intrudes into crater mouth'
                ring_samples += 1
        check('scorch ring triangle vertices and centres exclude the crater interior', ring_samples > 0)

        outward_dots = []
        for name in ('material_mask', 'opening_footprint'):
            support = regions[name]
            assert support['primary'] is False
            for triangle, normal_value in zip(support['triangles_local'], support['normals_local']):
                centre = tuple(math.fsum(p[i] for p in triangle)/3 for i in range(3))
                anchor = bound_region_anchor(centre)
                reference_normal = Vector(surface.frame(anchor)['normal'])
                observed = Vector(normal_value)
                assert abs(observed.length-1.) < 1e-6
                outward_dots.append(observed.dot(reference_normal))
        check('non-primary healthy footprint normals face the reference exterior',
              bool(outward_dots) and min(outward_dots) > .99)
        # Interior walls/floors retain actual geometry normals rather than
        # being forcibly flipped into a healthy-reference normal hemisphere.
        for triangle, normal_value in zip(regions['damaged_surface']['triangles_local'], regions['damaged_surface']['normals_local']):
            a, b, c = map(Vector, triangle)
            observed = Vector(normal_value)
            assert abs(observed.length-1.) < 1e-6 and observed.dot((b-a).cross(c-a).normalized()) > .99999
        report['lightning'] = dict(areas_m2=areas, area_partition_error_m2=area_error,
            area_tolerance_m2=1e-6, ring_vertex_and_centre_samples=ring_samples,
            unique_region_binding_samples=len(projected_anchors), max_region_binding_error_m=max_region_binding_error,
            region_binding_tolerance_m=BINDING_TOLERANCE_M,
            min_secondary_footprint_outward_dot=min(outward_dots),
            independent_centre_depth_m=float(distance)-.02, measurements=measure)
    finally:
        generator.dispose(lightning_payload)
    check('lightning preparation/disposal returns resource counts to baseline', resource_counts() == counts)

    signatures = []
    for repeat in range(2):
        payload = generator.prepare(combined)
        try:
            signatures.append(dict(meshes={i: mesh_digest(mesh) for i, mesh in payload['meshes'].items()},
                                   supports=deepcopy(payload['supports'])))
            check(f'combined preparation {repeat+1} preserves active geometry',
                  generator.active is active and all(editor.rig['blades'][i-1].data is mesh for i, mesh in active_meshes.items()))
        finally:
            generator.dispose(payload)
        check(f'combined preparation/disposal {repeat+1} leaks no object mesh material or node', resource_counts() == counts)
    check('two combined builds have deterministic local topology and support regions', signatures[0] == signatures[1])

    with patch.object(generator, '_cut_patch', side_effect=RuntimeError('injected patch failure')):
        try:
            generator.prepare(combined)
        except RuntimeError as exc:
            check('patch failure is propagated without silent fallback', str(exc) == 'injected patch failure')
        else:
            raise AssertionError('Injected patch failure was not raised')
    check('failed patch transaction leaves all resource counts unchanged', resource_counts() == counts)
    check('failed patch transaction preserves active payload and actual blade data',
          generator.active is active and editor.payload is active
          and all(editor.rig['blades'][i-1].data is mesh for i, mesh in active_meshes.items()))
    check('fixed camera transforms and intrinsics remain unchanged', editor.fixed_camera_signature() == camera_signature)
    check('no output render was dispatched', renderer.call_count == 0)
    report.update(result='PASS', render_calls=renderer.call_count, resource_baseline=counts,
                  resource_final=resource_counts(), boolean_calls=generator.boolean_calls)


def main():
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    if len(argv) > 1:
        raise ValueError('Pass at most one empty output directory after --')
    output = Path(argv[0]).expanduser().resolve() if argv else None
    if output is not None:
        if output.exists() and any(output.iterdir()):
            raise ValueError('Choose an empty directory; existing evidence is never overwritten')
        output.mkdir(parents=True, exist_ok=True)
    modules = (defect_geometry, reference_surface)
    report = dict(result='RUNNING', checks=[], rendered=False, blender_version=bpy.app.version_string,
        test_file=str(Path(__file__).resolve()), output=str(output) if output else None,
        source_paths={m.__name__: str(Path(m.__file__).resolve()) for m in modules},
        source_sha256={m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules},
        render_guard_scope=GUARD_SCOPE,
        limitations=['Workspace source in background Blender; no installed-extension or visible-window claim.',
                     'Synthetic geometry/UV and G-region contracts only; no appearance, image, or field evidence.'])
    started = time.perf_counter()
    try:
        with forbid_output_render() as renderer:
            run(report, renderer)
    except BaseException as exc:
        report.update(result='FAIL', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        report['seconds'] = time.perf_counter()-started
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
        if output is not None:
            (output/'result.json').write_text(text)
        print(text, flush=True)


if __name__ == '__main__':
    main()
