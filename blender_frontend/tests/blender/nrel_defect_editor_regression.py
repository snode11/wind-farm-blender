"""Source-loaded native NREL/MAPPO defect regression; never renders images.

WFRL_TEST_OUTPUT selects a new evidence directory. Run the default save phase,
then WFRL_NREL_DEFECT_PHASE=reopen to verify the saved .blend in a fresh process.
"""
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
farm_flex = importlib.import_module(MODULE+'.farm_flex')
clearance_replay = importlib.import_module(MODULE+'.clearance_replay')
editor_module = importlib.import_module(MODULE+'.nrel_defects.editor')
schema = importlib.import_module(MODULE+'.nrel_defects.defect_schema')
out = Path(os.environ.get('WFRL_TEST_OUTPUT', ROOT/'outputs/blender-validation/nrel-defects'))
out.mkdir(parents=True, exist_ok=True)
phase = os.environ.get('WFRL_NREL_DEFECT_PHASE', 'save')
assert phase in ('save', 'reopen'), phase


def coordinates(obj):
    return np.array([tuple(vertex.co) for vertex in obj.data.vertices])


def source_signature(flex):
    """Saved result bytes and reader values must survive visual defect edits."""
    package = farm_flex.default_package()
    files = flex.manifest['files']
    return {name: hashlib.sha256((package/name).read_bytes()).hexdigest()
            for name in files}


def check_seek(session, flex, times, baseline_tips):
    before = session.geometry.boolean_calls
    snapshots = {}
    for time_s in times:
        session.set_time(float(time_s))
        for obj, rest, *_ in flex.blades:
            expected = flex.deform_points(obj, rest, float(time_s), world=False)
            np.testing.assert_allclose(coordinates(obj), expected, atol=3e-5, rtol=0)
            # Mechanical reference and cosmetic apex retain their dedicated
            # final two indices after every topology-changing defect rebuild.
            tips = flex.deform_points(obj, baseline_tips[obj.name], float(time_s), world=False)
            np.testing.assert_allclose(coordinates(obj)[-2:], tips, atol=3e-5, rtol=0)
        for support in session.payload['supports']:
            obj = session.scene.objects[f"WFRL.Turbine.{support['turbine_id']}.Blade{support['blade_id']}"]
            world = session.world_support(support, float(time_s))
            points = np.asarray(support['triangles_local']).reshape(-1, 3)
            expected = flex.deform_points(obj, points, float(time_s), world=True, high_precision=True).reshape(-1, 3, 3)
            np.testing.assert_allclose(world['triangles_world'], expected, atol=3e-5, rtol=0)
        for helper in session.helpers:
            rest = session.helper_rest[helper.name]
            obj = session.scene.objects[f"WFRL.Turbine.{helper['turbine_id']}.Blade{helper['blade_id']}"]
            expected = flex.deform_points(obj, rest, float(time_s), world=False)
            actual = coordinates(helper) if helper.type == 'MESH' else np.array([tuple(helper.location)])
            np.testing.assert_allclose(actual, expected, atol=3e-5, rtol=0)
        current = {obj.name: coordinates(obj) for obj, *_ in flex.blades}
        if float(time_s) in snapshots:
            for name, points in current.items():
                np.testing.assert_allclose(points, snapshots[float(time_s)][name], atol=3e-5, rtol=0)
        else:
            snapshots[float(time_s)] = current
    assert session.geometry.boolean_calls == before, 'Seeking must not rerun Boolean geometry'


addon.register()
if phase == 'reopen':
    saved = json.loads((out/'expected.json').read_text())
    bpy.ops.wm.open_mainfile(filepath=str(out/'nrel-defect-editor.blend'))
    scene = bpy.context.scene
    session = editor_module.ensure(scene)
    assert session.store.document == saved['document']
    flex = farm_flex._ACTIVE
    assert len(flex.blades) == 9
    bpy.ops.wfrl.farm_flex_view(turbine='all')
    baseline_tips = {name: np.asarray(points) for name, points in saved['baseline_tips'].items()}
    check_seek(session, flex, saved['seek_times'], baseline_tips)
    assert source_signature(flex) == saved['source_signature']
    result = dict(status='PASS', phase=phase, module_file=addon.__file__,
                  scope='background save/reopen and flexible geometry; no viewport or appearance acceptance')
else:
    addon.load_demo_scene()
    scene = bpy.context.scene
    flex = farm_flex._ACTIVE
    assert len(flex.blades) == 9
    bpy.ops.wfrl.farm_flex_view(turbine='all')
    baseline_tips = {obj.name: rest[-2:].copy() for obj, rest, *_ in flex.blades}
    signature = source_signature(flex)
    reader = clearance_replay.reader_for(scene)
    reader_before = deepcopy(reader.at(float(flex.times[0])))
    session = editor_module.ensure(scene)
    doc = schema.new_document(session.surface)
    doc['defects'] = [schema.new_defect(session.surface, kind,
        turbine_id='T'+str(index % 3 + 1), blade_id=index//3 + 1,
        anchor=dict(surface_side='suction' if index % 2 == 0 else 'pressure'))
        for index, kind in enumerate(schema.PRESETS)]
    session.store.restore(doc)
    committed = session.store.document
    scene.wfrl_nrel_defects.selected = 2
    assert bpy.ops.wfrl.nrel_defect_action(action='EDIT') == {'FINISHED'}
    assert session.draft['defects'][session.draft_index]['morphology'] == 'pit'
    assert bpy.ops.wfrl.nrel_defect_action(action='CANCEL') == {'FINISHED'}
    assert session.draft is None and session.store.document == committed
    session.begin(0)
    frozen_time = float(scene['defect_time_s'])
    frozen_frame = scene.frame_current + scene.frame_subframe
    scene.frame_set(min(scene.frame_end, scene.frame_current + 11))
    assert abs(float(scene['defect_time_s'])-frozen_time) < 1e-8
    assert abs(scene.frame_current + scene.frame_subframe-frozen_frame) < 1e-7
    session.draft['defects'][0]['shape']['length_m'] *= 1.1
    session.draft['defects'][0]['revision'] += 1
    session.preview_draft()
    assert session.store.document == committed
    session.finish(False)
    assert session.store.document == committed
    session.begin(0)
    session.draft['defects'][0]['shape']['length_m'] *= 1.1
    session.draft['defects'][0]['revision'] += 1
    session.finish(True)
    modified = session.store.document
    assert modified != committed
    session.store.undo(); assert session.store.document == committed
    session.store.redo(); assert session.store.document == modified
    session.store.undo()
    session.mutate('TOGGLE', 0)
    assert not session.store.document['defects'][0]['enabled']
    session.store.undo(); assert session.store.document == committed
    session.mutate('COPY', 0)
    copied = session.store.document['defects'][-1]
    assert not copied['enabled'] and copied['id'] != committed['defects'][0]['id']
    session.mutate('DELETE', len(session.store.document['defects'])-1)
    assert session.store.document == committed
    session.save(out/'defects.json')
    bad = deepcopy(committed); bad['reference_geometry_id'] = 'gw184-reference'
    (out/'invalid.json').write_text(json.dumps(bad))
    try:
        session.load(out/'invalid.json')
    except ValueError:
        pass
    else:
        raise AssertionError('A GW184 reference must not be accepted as NREL geometry')
    assert session.store.document == committed
    session.healthy(0)
    assert session.store.document == committed
    session.healthy(0)
    assert session.store.document == committed
    session.load(out/'defects.json')
    assert session.store.document == committed
    previous_session = session
    editor_module.detach(scene)
    assert editor_module.session() is None
    session = editor_module.ensure(scene)
    assert session is not previous_session
    assert session.store.document == committed
    start, end = float(flex.times[0]), float(flex.times[-1])
    times = [start, start+.137, end, start+.137, start]
    check_seek(session, flex, times, baseline_tips)
    assert source_signature(flex) == signature
    assert clearance_replay.reader_for(scene) is reader
    assert reader.at(start) == reader_before
    expected = dict(document=committed, source_signature=signature, seek_times=times,
                    baseline_tips={name: points.tolist() for name, points in baseline_tips.items()})
    (out/'expected.json').write_text(json.dumps(expected, indent=2, allow_nan=False))
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'nrel-defect-editor.blend'))
    result = dict(status='PASS', phase=phase, module_file=addon.__file__, types=6,
                  turbines=3, blades=9, seeking_boolean_calls=0,
                  draft_clock_frozen=True, close_and_restore=True,
                  scope='background edit transactions and flexible geometry; no viewport or appearance acceptance')
(out/(phase+'-checks.json')).write_text(json.dumps(result, indent=2))
print('NREL_DEFECT_EDITOR_'+phase.upper()+'_PASS', json.dumps(result), flush=True)
