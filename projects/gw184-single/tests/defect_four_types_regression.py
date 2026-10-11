"""Blender background operator/geometry regression for six defect types.

Run only after static review, with an empty output directory::

    Blender --background --factory-startup --python-exit-code 1 \
      --python projects/gw184-single/tests/defect_four_types_regression.py \
      -- /absolute/new/output-directory

This script creates JSON/G evidence only. It never invokes image generation,
appearance export, or cropping. The output-module render guard is scoped, not
a process-wide interception of all Blender render operators.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

import bpy
import bmesh
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT/'projects/gw184-single'
sys.path[:0] = [str(Path(__file__).resolve().parent), str(PROJECT), str(ROOT/'blender_frontend')]
from defect_render_guard import GUARD_SCOPE, forbid_output_render
from gw184 import defect_editor, defect_geometry, defect_schema, reference_surface
from gw184.defect_editor import EditorSession, register
from gw184.defect_schema import PRESETS, new_document
from gw184.defect_shapes import PATCH_KINDS
from gw184.defect_outputs import write_json
from gw184.scene import at_time


def mesh_digest(mesh):
    """Geometry/topology identity deliberately excludes material assignments."""
    data = (tuple(tuple(v.co) for v in mesh.vertices), tuple(tuple(p.vertices) for p in mesh.polygons))
    return hashlib.sha256(repr(data).encode()).hexdigest()


def run(output, report, renderer):
    checks = report['checks']

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
        print('PASS: '+name, flush=True)

    register()
    editor = EditorSession(all_presets=True)
    props = editor.scene.gw184_defects
    camera_signature = editor.fixed_camera_signature()
    initial = editor.store.document
    check('all-presets creates each of the six morphologies once',
          Counter(d['morphology'] for d in initial['defects']) == Counter(PRESETS.keys()))
    check('six default event IDs are unique', len({d['id'] for d in initial['defects']}) == 6)
    enum_types = {item.identifier for item in props.bl_rna.properties['kind'].enum_items}
    check('native type selector exposes six supported morphologies', enum_types == set(PRESETS))
    healthy = {blade: mesh_digest(mesh) for blade, mesh in editor.geometry._healthy.items()}

    def operation(action):
        result = bpy.ops.gw184.defect_action('EXEC_DEFAULT', action=action)
        check('operator '+action+' finished', result == {'FINISHED'})
        editor.verify_cameras()

    # Existing cracks retain their fields in the same real operator UI path.
    for kind in ('fine_crack', 'open_crack'):
        props.selected = next(i for i, d in enumerate(editor.store.document['defects']) if d['morphology'] == kind)
        item = editor.store.document['defects'][props.selected]
        operation('EDIT')
        check(kind+': operator loads the correct kind and seed', props.kind == kind and props.seed == item['shape']['seed'])
        operation('CANCEL')

    # Each added type is changed through RNA properties and actual operators.
    # Undo/redo must restore the entire document, including composite events.
    for kind in PATCH_KINDS:
        props.selected = next(i for i, d in enumerate(editor.store.document['defects']) if d['morphology'] == kind)
        before = editor.store.document
        before_payload = editor.payload
        item = before['defects'][props.selected]
        frozen_time = float(editor.scene['defect_time_s'])
        operation('EDIT')
        check(kind+': operator reads morphology and patch parameters',
              props.kind == kind and abs(props.edge_roughness-item['shape']['edge_roughness']) < 1e-7)
        props.span -= .05
        props.width *= 1.025
        props.edge_roughness = min(.49, props.edge_roughness+.01)
        if kind == 'lightning':
            props.crater_fraction = .42
        if kind != 'coating_loss':
            props.depth *= 1.05
        operation('PREVIEW')
        check(kind+': preview preserves committed document and payload',
              editor.store.document == before and editor.payload is before_payload and editor.preview is not None)
        check(kind+': preview is the active temporary geometry', editor.geometry.active is editor.preview.resource)
        editor.scene.frame_set(1)
        check(kind+': native scrub freezes the edit time', abs(float(editor.scene['defect_time_s'])-frozen_time) < 1e-9)
        operation('CANCEL')
        check(kind+': cancel restores committed geometry and document',
              editor.store.document == before and editor.geometry.active is before_payload
              and editor.payload is before_payload and editor.draft is None and editor.preview is None)

        operation('EDIT')
        props.span -= .05
        props.width *= 1.025
        props.edge_roughness = min(.49, props.edge_roughness+.01)
        if kind == 'lightning':
            props.crater_fraction = .42
        if kind == 'coating_loss':
            # Hidden stale depth must not create accidental geometry.
            props.depth = .02
        else:
            props.depth *= 1.05
        expected = dict(span=props.span, width=props.width, edge_roughness=props.edge_roughness,
                        depth=0. if kind == 'coating_loss' else props.depth,
                        crater_fraction=props.crater_fraction)
        operation('CONFIRM')
        confirmed = editor.store.document
        edited = confirmed['defects'][props.selected]
        check(kind+': confirm retains ID and increments revision',
              edited['id'] == item['id'] and edited['revision'] == item['revision']+1)
        check(kind+': confirmed fields come from actual UI properties',
              edited['anchor']['s_m'] == expected['span'] and edited['shape']['max_width_m'] == expected['width']
              and edited['shape']['edge_roughness'] == expected['edge_roughness']
              and edited['shape']['depth_m'] == expected['depth'])
        if kind == 'lightning':
            check('lightning: crater fraction roundtrips through UI', edited['shape']['crater_fraction'] == expected['crater_fraction'])
        operation('UNDO')
        check(kind+': undo restores whole configuration', editor.store.document == before)
        operation('REDO')
        check(kind+': redo restores whole configuration', editor.store.document == confirmed)

    supports = editor.payload['supports']
    enabled_ids = {d['id'] for d in editor.store.document['defects'] if d['enabled']}
    check('every enabled event has exactly one primary support',
          Counter(s['defect_id'] for s in supports if s['primary']) == Counter({key: 1 for key in enabled_ids}))
    lightning = next(d for d in editor.store.document['defects'] if d['morphology'] == 'lightning')
    lightning_supports = [s for s in supports if s['defect_id'] == lightning['id']]
    check('lightning keeps event, scorch, opening and interior under one ID/revision',
          {s['support_kind'] for s in lightning_supports} ==
          {'event_footprint', 'material_mask', 'opening_footprint', 'damaged_surface'}
          and all(s['revision'] == lightning['revision'] for s in lightning_supports)
          and [s['support_kind'] for s in lightning_supports if s['primary']] == ['event_footprint'])
    check('support IDs are unique across composite events', len({s['support_id'] for s in supports}) == len(supports))

    # Composite-event list operations must affect all of its support regions.
    props.selected = next(i for i, d in enumerate(editor.store.document['defects']) if d['id'] == lightning['id'])
    before_toggle = editor.store.document
    other_ids = enabled_ids-{lightning['id']}
    operation('TOGGLE')
    check('disabling lightning removes all its regions and preserves other events',
          {s['defect_id'] for s in editor.payload['supports']} == other_ids)
    operation('UNDO')
    check('undo restores whole lightning event', editor.store.document == before_toggle)
    operation('COPY')
    copied = editor.store.document['defects'][-1]
    check('copy is a disabled new lightning event with a unique ID',
          copied['id'] not in enabled_ids and copied['morphology'] == 'lightning'
          and not copied['enabled'] and copied['revision'] == 1 and copied['shape'] == lightning['shape'])
    check('disabled lightning copy contributes no support', copied['id'] not in {s['defect_id'] for s in editor.payload['supports']})
    props.selected = len(editor.store.document['defects'])-1
    operation('DELETE')
    check('delete removes only the copied composite event', editor.store.document == before_toggle)

    # Save/load uses the user-facing operators and exact persisted identity.
    props.config_path = str(output/'saved-defects.json')
    operation('SAVE')
    saved = editor.store.document
    check('saved JSON preserves all six types and exact fields', json.loads((output/'saved-defects.json').read_text()) == saved)
    props.selected = 0
    operation('TOGGLE')
    operation('LOAD')
    check('load restores all saved IDs revisions seeds and shapes', editor.store.document == saved)

    active = editor.payload
    operation('HEALTHY')
    check('healthy comparison restores original blade topology', mesh_digest(editor.rig['blades'][0].data) == healthy[1])
    check('healthy comparison removes all selected-blade support regions',
          not any(s['blade_id'] == 1 for s in editor.geometry.active['supports']))
    check('healthy comparison leaves saved document untouched', editor.store.document == saved)
    operation('HEALTHY')
    check('return from healthy comparison restores exact committed payload',
          editor.geometry.active is active and editor.payload is active and editor.healthy_payload is None)

    # Isolated payloads establish which morphology owns actual mesh removal.
    geometry_evidence = []
    for kind in PATCH_KINDS:
        item = deepcopy(next(d for d in saved['defects'] if d['morphology'] == kind))
        single = new_document(editor.surface); single['defects'] = [item]
        isolated = editor.geometry.prepare(single)
        try:
            mesh = isolated['meshes'][item['blade_id']]
            check(kind+': isolated preparation leaves committed payload active', editor.geometry.active is active)
            if kind == 'coating_loss':
                check('coating_loss: mesh coordinates and topology remain healthy', mesh_digest(mesh) == healthy[item['blade_id']])
                check('coating_loss: material mask is the sole primary support',
                      len(isolated['supports']) == 1 and isolated['supports'][0]['support_kind'] == 'material_mask'
                      and isolated['supports'][0]['primary'])
                geometry_evidence.append(dict(morphology=kind, topology='healthy', depth_m=0.))
                continue
            check(kind+': genuine geometry differs from healthy mesh', mesh_digest(mesh) != healthy[item['blade_id']])
            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                check(kind+': every resulting mesh edge is manifold', bool(bm.edges) and all(edge.is_manifold for edge in bm.edges))
            finally:
                bm.free()
            frame = editor.surface.frame(item['anchor'])
            point, normal = Vector(frame['point']), Vector(frame['normal'])
            hit, _, _, distance = isolated['bvh_by_blade'][item['blade_id']].ray_cast(point+normal*.02, -normal, .2)
            check(kind+': independent normal ray hits a genuine inward floor', hit is not None and distance-.02 > .001)
            inward = float(distance)-.02
            check(kind+': independently observed central depth matches nominal depth', abs(inward-item['shape']['depth_m']) <= .001)
            measurements = [m for m in isolated['measurements'] if m['defect_id'] == item['id']]
            check(kind+': independent depth and mouth-width measurements are within tolerance',
                  bool(measurements) and all(m['max_depth_error_m'] <= .001 and
                                            m['max_mouth_width_error_m'] <= .001 for m in measurements))
            geometry_evidence.append(dict(morphology=kind, independent_center_depth_m=inward,
                                          measurements=measurements, manifold=True))
        finally:
            editor.geometry.dispose(isolated)
    report['geometry'] = geometry_evidence

    # Pose changes must reuse the built local geometry and preserve the cameras.
    mesh_before = [mesh_digest(blade.data) for blade in editor.rig['blades']]
    boolean_calls = editor.geometry.boolean_calls
    for timestamp in (0., 100., 319.9666666667, 55., 100.):
        at_time(editor.rig, timestamp)
    check('pose updates do not rebuild or alter local geometry',
          editor.geometry.boolean_calls == boolean_calls
          and [mesh_digest(blade.data) for blade in editor.rig['blades']] == mesh_before)
    editor.set_time(100.)
    props.output_path = str(output/'g-run')
    operation('EXPORT_G')
    records = json.loads((output/'g-run/visibility.json').read_text())
    expected_records = {(s['support_id'], camera.name) for s in editor.payload['supports'] for camera in editor.rig['cameras']}
    check('G export records every support for all three fixed cameras',
          len(records) == len(expected_records)
          and {(r['support_id'], r['camera_id']) for r in records} == expected_records
          and {r['camera_id'] for r in records} == {'C1', 'C2', 'C3'})
    check('G records contain no INVALID status', all(r['status'] != 'INVALID' for r in records))
    check('G records retain unreviewed appearance boundary',
          all(r['appearance_review']['status'] == 'NOT_REVIEWED' for r in records))
    check('fixed camera transforms and intrinsics survive editing geometry and G export',
          editor.fixed_camera_signature() == camera_signature)
    check('no scene image or video artifact was generated',
          not [p for p in output.rglob('*') if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.exr', '.mp4')])
    check('output renderer was never dispatched', renderer.call_count == 0)
    report.update(event_count=len(saved['defects']), support_count=len(editor.payload['supports']),
                  g_record_count=len(records), g_status_counts=dict(Counter(r['status'] for r in records)),
                  boolean_calls=editor.geometry.boolean_calls, render_calls=renderer.call_count,
                  result='PASS')


def main():
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    if len(argv) != 1:
        raise ValueError('Provide one empty output directory after --')
    output = Path(argv[0]).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Choose an empty new output directory; prior evidence is never overwritten')
    output.mkdir(parents=True, exist_ok=True)
    source_modules = (defect_editor, defect_geometry, defect_schema, reference_surface)
    report = dict(result='RUNNING', checks=[], rendered=False, blender_version=bpy.app.version_string,
        test_file=str(Path(__file__).resolve()), output=str(output),
        source_paths={m.__name__: str(Path(m.__file__).resolve()) for m in source_modules},
        source_sha256={m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in source_modules},
        render_guard_scope=GUARD_SCOPE,
        limitations=['Workspace source and background native operators; no installed extension or visible-window claim.',
                     'Synthetic prescribed morphology; no measured specimen, strike physics, or field validation.',
                     'G geometry evidence does not establish image discernibility; no image/appearance review.'])
    started = time.perf_counter()
    try:
        with forbid_output_render() as renderer:
            run(output, report, renderer)
    except BaseException as exc:
        report.update(result='FAIL', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        report['seconds'] = time.perf_counter()-started
        write_json(output/'result.json', report)
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
