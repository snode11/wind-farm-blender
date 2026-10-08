"""Missing native slots preserve camera identity and reject incomplete analysis."""
import importlib
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
if os.environ.get('WFRL_EXPECTED_ADDON_ROOT'):
    assert Path(addon.__file__).resolve().parent == Path(os.environ['WFRL_EXPECTED_ADDON_ROOT']).resolve()
core = importlib.import_module(MODULE+'.custom_cameras')
editor = importlib.import_module(MODULE+'.nrel_defects.editor')
addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
session = editor.ensure(scene)
c1, c2, c3 = [core.get_camera(scene, slot) for slot in (1, 2, 3)]
assert all((c1, c2, c3))
collections = tuple(c2.users_collection)
for collection in collections:
    collection.objects.unlink(c2)
try:
    assert core.get_camera(scene, 2) is None
    scene.camera = c1
    try:
        result = bpy.ops.wfrl.nrel_defect_action(action='CAMERA2')
    except RuntimeError as exc:
        assert 'C2' in str(exc) and '缺失' in str(exc), str(exc)
    else:
        assert result == {'CANCELLED'}, result
    assert scene.camera is c1, 'Missing C2 must not select C3'
    assert bpy.ops.wfrl.nrel_defect_action(action='CAMERA3') == {'FINISHED'}
    assert scene.camera is c3
    try:
        session.verify_cameras()
    except ValueError as exc:
        assert 'C2' in str(exc) and '缺失' in str(exc)
    else:
        raise AssertionError('Incomplete three-camera analysis accepted')
finally:
    for collection in collections:
        collection.objects.link(c2)
session.verify_cameras()
assert len(session.camera_signature) == 3
assert bpy.ops.wfrl.nrel_defect_action(action='CAMERA2') == {'FINISHED'}
assert scene.camera is c2
report = dict(status='PASS', module_file=addon.__file__,
    missing_slot_preserves_camera=True, c3_identity_preserved=True,
    incomplete_analysis_rejected=True, restored_c2_selected=True)
if os.environ.get('WFRL_TEST_OUTPUT'):
    output = Path(os.environ['WFRL_TEST_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    (output/'camera-slots.json').write_text(json.dumps(report, indent=2))
addon.unregister()
print('NREL_CAMERA_SLOTS_PASS', json.dumps(report), flush=True)
