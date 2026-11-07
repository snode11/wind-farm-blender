"""Native helper deletion and retryable editor cleanup in an isolated process."""
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
addon_root = Path(addon.__file__).resolve().parent
expected = Path(os.environ.get('WFRL_EXPECTED_ADDON_ROOT', addon_root)).resolve()
assert addon_root == expected, (addon_root, expected)
editor = importlib.import_module(MODULE+'.nrel_defects.editor')
schema = importlib.import_module(MODULE+'.nrel_defects.defect_schema')
geometry = importlib.import_module(MODULE+'.nrel_defects.defect_geometry')
output = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
output.mkdir(parents=True, exist_ok=False)
if MODULE == 'wfrl_blender':
    addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
bpy.ops.wfrl.farm_flex_view(turbine='all')
from unittest.mock import patch
initial_checks = []
for boundary in ('constructor', 'activate', 'rebind', 'helpers', 'list'):
    flex = importlib.import_module(MODULE+'.farm_flex').active_for(scene)
    before = [(obj, obj.data) for obj, *_ in flex.blades]
    bindings = list(flex.blades)
    metadata = {k:scene[k] for k in scene.keys() if k.startswith(('defect_', 'wfrl_nrel_defect'))}
    counts = len(bpy.data.meshes), len(bpy.data.objects)
    mesh_names = set(bpy.data.meshes.keys())
    calls = [0]
    if boundary in ('constructor', 'activate'):
        method = '__init__' if boundary == 'constructor' else 'activate'
        original = getattr(geometry.DefectGeometry, method)
        def injected(*a, **kw):
            calls[0] += 1
            result = original(*a, **kw)
            if calls[0] == 2: raise RuntimeError('initial '+boundary)
            return result
        target, name = geometry.DefectGeometry, method
    elif boundary == 'rebind':
        original = flex.rebind_blade
        def injected(*a, **kw):
            calls[0] += 1
            if calls[0] == 4: raise RuntimeError('initial rebind')
            return original(*a, **kw)
        target, name = flex, 'rebind_blade'
    else:
        target, name = (editor.EditorSession, 'update_helpers') if boundary=='helpers' else (editor, 'sync_defect_list')
        original = getattr(target, name)
        def injected(*a, **kw):
            original(*a, **kw)
            raise RuntimeError('initial '+boundary)
    with patch.object(target, name, injected):
        try: editor.ensure(scene)
        except RuntimeError: pass
        else: raise AssertionError('Initial failure was accepted: '+boundary)
    assert editor.session() is None and not editor._INITIALIZING, boundary
    assert all(obj.data == mesh for obj, mesh in before), boundary
    assert all(a[1] is b[1] for a,b in zip(bindings,flex.blades)), boundary
    assert {k:scene[k] for k in scene.keys() if k.startswith(('defect_', 'wfrl_nrel_defect'))} == metadata
    assert (len(bpy.data.meshes),len(bpy.data.objects)) == counts, (boundary, counts, (len(bpy.data.meshes),len(bpy.data.objects)), set(bpy.data.meshes.keys())-mesh_names, mesh_names-set(bpy.data.meshes.keys()))
    initial_checks.append(boundary)
s = editor.ensure(scene)
document = s.store.document
document['defects'] = [schema.new_defect(s.surface, 'fine_crack')]
s.store.restore(document)
committed = deepcopy(s.store.document)
checks = {'initial_transactions': initial_checks}

helper = s.helpers[0]
removed_mesh = helper.data
removed_name = removed_mesh.name
bpy.data.objects.remove(helper, do_unlink=True)
s.set_time(float(s.flex.times[0])+1)
scene.wfrl_nrel_defects.show_helpers = False
scene.wfrl_nrel_defects.show_helpers = True
assert s.helpers == [] and s.store.document == committed
checks['deleted_helper_seek_visibility'] = True
s.healthy(0)
s.healthy(0)
assert len(s.helpers) == 1
assert bpy.data.meshes.get(removed_name) != removed_mesh
checks['healthy_toggle_recreates_from_committed_document'] = True
rejected = []
real_activate = s.geometry.activate
def failing_activate(payload, *args, **kwargs):
    if payload is not s.payload:
        rejected.append(payload)
        raise RuntimeError('injected healthy activation failure')
    return real_activate(payload, *args, **kwargs)
s.geometry.activate = failing_activate
try:
    try:
        s.healthy(0)
    except RuntimeError as exc:
        assert 'injected healthy activation failure' in str(exc), exc
    else:
        raise AssertionError('healthy activation failure was not reported')
finally:
    del s.geometry.activate
assert len(rejected) == 1 and s.healthy_payload is None and s.geometry.active is s.payload
assert all(item is not rejected[0] for item in s.geometry.pending)
checks['healthy_activation_failure_keeps_committed_state'] = True

s.begin(0)
s.draft['defects'][0]['shape']['length_m'] *= 1.1
s.draft['defects'][0]['revision'] += 1
s.preview_draft()
preview_document = deepcopy(s.preview.document)
bpy.data.objects.remove(s.helpers[0], do_unlink=True)
s.suspend_for_save()
assert json.loads(scene[editor.DOCUMENT_KEY]) == committed
s.resume_after_save(saved=True)
assert s.preview.document == preview_document and s.store.document == committed
s.finish(False)
assert s.draft is None and s.store.document == committed
checks['deleted_preview_helper_save_suspend_resume_cancel'] = True

owned = s.helpers[0].data
shared = bpy.data.objects.new('ResourceRegression.SharedHelper', owned)
scene.collection.objects.link(shared)
foreign = bpy.data.meshes.new('ResourceRegression.ForeignReplacement')
s.helpers[0].data = foreign
editor.detach(scene)
assert shared.data == owned and owned.users == 1
assert foreign.name in bpy.data.meshes and foreign.users == 0
assert all(manager._closed for manager in s.geometry.managers.values())
assert editor.session() is None
editor.detach(scene)
checks['close_shared_and_replaced_mesh_protection'] = True
s = editor.ensure(scene)
assert s.store.document == committed and len(s.helpers) == 1
s.set_time(float(s.flex.times[0])+2)
checks['close_reopen_seek'] = True

for state in ('preview', 'healthy'):
    if state == 'preview':
        s.begin(0)
        s.draft['defects'][0]['shape']['length_m'] *= 1.1
        s.draft['defects'][0]['revision'] += 1
        s.preview_draft()
    else:
        s.healthy(0)
    bpy.data.objects.remove(s.helpers[0], do_unlink=True)
    editor.detach(scene)
    assert editor.session() is None
    assert all(manager._closed for manager in s.geometry.managers.values())
    for obj, rest, *_ in s.flex.blades:
        np.testing.assert_array_equal(rest, s.baseline[obj.name])
        coordinates = np.array([tuple(vertex.co) for vertex in obj.data.vertices])
        expected_pose = s.flex.deform_points(obj, rest, float(scene['wfrl_clearance_time_s']))
        np.testing.assert_allclose(coordinates, expected_pose, atol=3e-5, rtol=0)
    assert json.loads(scene[editor.DOCUMENT_KEY]) == committed
    s = editor.ensure(scene)
    assert s.store.document == committed
    checks[state+'_close_restores_nine_blades_and_reopens_committed_document'] = True

manager = s.geometry.managers['T1']
original_bpy = geometry.bpy
victim = manager._active['meshes'][1]
failures = []
def remove_mesh(mesh):
    if mesh == victim:
        failures.append(mesh)
        raise RuntimeError('injected mesh release failure')
    return original_bpy.data.meshes.remove(mesh)
geometry.bpy = SimpleNamespace(data=SimpleNamespace(
    meshes=SimpleNamespace(remove=remove_mesh),
    materials=original_bpy.data.materials, node_groups=original_bpy.data.node_groups))
try:
    try:
        editor.detach(scene)
    except RuntimeError as exc:
        assert 'injected mesh release failure' in str(exc), exc
    else:
        raise AssertionError('Cleanup failure reported success')
finally:
    geometry.bpy = original_bpy
assert failures and editor.session() is s and s.closing
assert [m._closed for m in s.geometry.managers.values()] == [False, True, True]
s.set_time(float(s.flex.times[0])+3)
editor.detach(scene)
assert all(manager._closed for manager in s.geometry.managers.values())
assert editor.session() is None
checks['release_failure_keeps_session_and_retries_after_other_managers_close'] = True

s = editor.ensure(scene)
key = scene.as_pointer()
bpy.data.objects.remove(s.helpers[0], do_unlink=True)
survivor = bpy.data.scenes.new('ResourceRegression.Survivor')
bpy.context.window.scene = survivor
bpy.data.scenes.remove(scene)
editor.detach(restore=False)
assert key not in editor._SESSIONS
assert all(manager._closed for manager in s.geometry.managers.values())
checks['deleted_scene_and_helper_detach_without_restore'] = True

result = dict(status='PASS', checks=checks, module_file=str(addon.__file__),
              blender=bpy.app.version_string,
              source_sha256={str(p.relative_to(addon_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (addon_root/'nrel_defects/editor.py', addon_root/'nrel_defects/defect_geometry.py')},
              scope='Independent background process; native scripted deletion and injected release failure; no window, GPU or science acceptance')
(output/'checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print('NREL_EDITOR_RESOURCE_PASS', json.dumps(result, ensure_ascii=False), flush=True)
