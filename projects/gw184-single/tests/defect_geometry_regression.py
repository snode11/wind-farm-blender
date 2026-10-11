"""Background geometry/material transaction regression without scene rendering.

The guard covers render dispatch through gw184.defect_outputs only; it does not
intercept arbitrary Blender operators elsewhere in the process.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
import bmesh
from mathutils import Vector
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(ROOT/'projects/gw184-single'), str(ROOT/'blender_frontend')]
from defect_render_guard import GUARD_SCOPE, forbid_output_render
from gw184.model import read_config
from gw184.scene import build, at_time
from gw184.reference_surface import ReferenceSurface
from gw184.defect_geometry import DefectGeometry


def row(identifier, morphology, blade=1, side='pressure', span=45., width=.08):
    return dict(id=identifier, revision=1, enabled=True, turbine_id='T1', blade_id=blade,
        morphology=morphology, representation='geometry' if morphology=='open_crack' else 'appearance',
        anchor=dict(s_m=span,u=.4,surface_side=side,theta_deg=90.,region_hint='surface'),
        shape=dict(length_m=.6,max_width_m=width,depth_m=.015 if morphology=='open_crack' else 0.,
                   seed=17,generator_version='open-crack-v1' if morphology=='open_crack' else 'fine-crack-v1'))


def digest(mesh):
    return (tuple(tuple(v.co) for v in mesh.vertices),tuple(tuple(p.vertices) for p in mesh.polygons))


def check(renderer):
    g,m,c = read_config()
    rig = build(g,m,c,width=3840,apply_legacy_defects=False)
    surface = ReferenceSurface(g)
    generator = DefectGeometry(rig,surface)
    document = dict(schema='wfrl.blade_defects.v1', **surface.metadata(), defects=[])
    original_mesh_names = [blade.data.name for blade in rig['blades']]
    baseline = generator.prepare(document)
    generator.dispose(generator.activate(baseline))
    assert all(name not in bpy.data.meshes for name in original_mesh_names), 'initial builder orphan meshes leaked'
    healthy = digest(rig['blades'][0].data)
    initial_objects = len(bpy.data.objects)
    doc = deepcopy(document)
    doc['defects'] = [row('FINE','fine_crack',width=.002),row('OPEN','open_crack',span=46.)]
    before = rig['blades'][0].data
    payload = generator.prepare(doc)
    assert rig['blades'][0].data is before, 'prepare changed live geometry'
    assert len(bpy.data.objects) == initial_objects, 'temporary staging/cutter objects leaked'
    primary = [s for s in payload['supports'] if s['primary']]
    assert len(primary) == 2
    assert {s['support_kind'] for s in payload['supports']} == {'material_mask','opening_footprint','damaged_surface'}
    for support in primary:
        assert support['triangles_local']
        for tri in support['triangles_local']:
            centre = sum((Vector(p) for p in tri),Vector())/3
            found = surface.locate(centre, doc['defects'][0]['anchor']['surface_side'])
            assert (Vector(surface.point(found))-centre).length <= 1e-5
    assert any(mat.get('gw184_defect_mask') for mat in payload['materials'])
    assert all(v['max_depth_error_m'] <= .001 and v['max_mouth_width_error_m'] <= .001 for v in payload['measurements'] if v['defect_id']=='OPEN')
    generator.dispose(generator.activate(payload))
    combined = digest(rig['blades'][0].data)
    assert combined != healthy
    bm = bmesh.new(); bm.from_mesh(rig['blades'][0].data)
    assert all(edge.is_manifold for edge in bm.edges), 'groove introduced nonmanifold edges'
    bm.free()
    calls = generator.boolean_calls
    local = deepcopy(primary[0]['centreline_local'])
    camera_matrices = [cam.matrix_world.copy() for cam in rig['cameras']]
    for t in (0.,100.,55.,319.9666666667,55.,0.):
        at_time(rig,t)
        assert digest(rig['blades'][0].data) == combined
        assert primary[0]['centreline_local'] == local
        assert all(cam.matrix_world == old for cam,old in zip(rig['cameras'],camera_matrices))
    assert generator.boolean_calls == calls, 'pose update performed Boolean work'
    # Same configuration rebuilds the same local topology deterministically.
    resources_before_repeat = (len(bpy.data.objects),len(bpy.data.meshes),len(bpy.data.materials))
    repeat = generator.prepare(doc)
    assert digest(repeat['meshes'][1]) == combined
    generator.dispose(repeat)
    assert (len(bpy.data.objects),len(bpy.data.meshes),len(bpy.data.materials)) == resources_before_repeat
    # A material-only state leaves healthy topology untouched; no decal objects.
    fine_doc = deepcopy(doc); fine_doc['defects'][1]['enabled'] = False
    fine = generator.prepare(fine_doc)
    assert digest(fine['meshes'][1]) == healthy
    assert [s['defect_id'] for s in fine['supports']] == ['FINE']
    generator.dispose(generator.activate(fine))
    # Failed rebuild leaves mesh, document and cache identity at last valid state.
    active = generator.active
    resources_before_failure = (len(bpy.data.objects),len(bpy.data.meshes),len(bpy.data.materials))
    with patch.object(generator, '_cut', side_effect=RuntimeError('injected failure')):
        try: generator.prepare(doc)
        except RuntimeError as exc: assert str(exc) == 'injected failure'
        else: raise AssertionError('injected build should fail')
    assert generator.active is active and rig['blades'][0].data is active['meshes'][1]
    assert len(bpy.data.objects) == initial_objects
    assert (len(bpy.data.objects),len(bpy.data.meshes),len(bpy.data.materials)) == resources_before_failure
    with patch.object(generator, '_measure_depth', side_effect=RuntimeError('post-Boolean failure')):
        try: generator.prepare(doc)
        except RuntimeError as exc: assert str(exc) == 'post-Boolean failure'
        else: raise AssertionError('post-Boolean failure should roll back')
    assert generator.active is active
    assert (len(bpy.data.objects),len(bpy.data.meshes),len(bpy.data.materials)) == resources_before_failure
    # Both registered sides have real independent groove depth, not visual masks.
    other_doc = deepcopy(document)
    other_doc['defects'] = [row('SUCTION','open_crack',blade=2,side='suction')]
    other_doc['defects'][0]['anchor']['theta_deg'] = 0.
    other = generator.prepare(other_doc)
    assert other['measurements'][0]['max_depth_error_m'] <= .001
    generator.dispose(generator.activate(other))
    reset = generator.prepare(document)
    generator.dispose(generator.activate(reset))
    assert digest(rig['blades'][0].data) == healthy
    assert not generator.active['supports']
    assert len(bpy.data.objects) == initial_objects
    assert renderer.call_count == 0, 'No output render may be attempted'
    print(json.dumps(dict(result='PASS', rendered=False,
        render_calls=renderer.call_count, render_guard_scope=GUARD_SCOPE,
        checks=['exact healthy-facet material mask','true groove and independent inward depth',
            'both reference sides and directions','watertight Boolean mesh','independent mouth width','deterministic rebuild','prepare/activate isolation','failure rollback and resource cleanup','initial builder orphan cleanup',
            'disable one preserves other','healthy restore','rigid pose no rebuild','fixed cameras'],
        boolean_calls=generator.boolean_calls),ensure_ascii=False))

with forbid_output_render() as renderer:
    check(renderer)
