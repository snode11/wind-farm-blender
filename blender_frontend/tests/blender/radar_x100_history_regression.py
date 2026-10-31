"""Native X100 and FarmFlex rebinding across unrelated undo and redo."""
from pathlib import Path
import importlib
import json
import os
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
farm = importlib.import_module(MODULE + '.farm_flex')
radar = importlib.import_module(MODULE + '.radar_x100.runtime')
output = Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=False)
if MODULE == 'wfrl_blender':
    addon.register()
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()

checks = []
try:
    bpy.context.preferences.edit.use_global_undo = True
    addon.load_demo_scene(farm.default_package())
    scene = bpy.context.scene
    scene.wfrl_x100_source = 'SIMULATION'
    scene.wfrl_x100_candidates = 64
    session = radar.start(scene)
    source_hash = scene['wfrl_farm_manifest_sha256']
    calibration = session.sampler.calibration_id
    session.start_recording(output / 'before-history')
    recorder = session.fanout.recorder
    bpy.ops.ed.undo_push(message='X100 source and installation')
    scene['history_probe'] = 1
    bpy.ops.ed.undo_push(message='Unrelated scene property')
    for operation in ('undo', 'redo'):
        before_pointer = bpy.context.scene.as_pointer()
        assert getattr(bpy.ops.ed, operation)() == {'FINISHED'}
        scene = bpy.context.scene
        radar.tick()
        radar.tick()
        current = radar.session_for(scene)
        checks.append(dict(operation=operation, scene_before=before_pointer,
                           scene_after=scene.as_pointer(), enabled=scene.wfrl_x100_enabled,
                           frame_available=current is not None and current.frame is not None,
                           bound_to_current_farm=current is not None and current.sampler.flex is farm.active_for(scene),
                           error=scene.get('wfrl_x100_error', ''),
                           source=scene.get('wfrl_farm_manifest_sha256')))
        (output / 'result.json').write_text(json.dumps(dict(status='RUNNING', checks=checks), indent=2))
        assert scene.wfrl_x100_enabled
        assert current is not None and current.display_valid, checks[-1]
        assert current.sampler.flex is farm.active_for(scene), checks[-1]
        assert scene['wfrl_farm_manifest_sha256'] == source_hash
        assert current.sampler.calibration_id == calibration
        assert current.fanout is None, 'History must not resume a recorder on a replaced source'
        assert sum(obj.get('wfrl_x100_owner') == current.owner for obj in scene.objects) == 3
    assert recorder.state == 'COMPLETE'
    assert not session.fanout
    current.fov.hide_set(True, view_layer=scene.view_layers[0])
    bpy.ops.ed.undo_push(message='User radar visibility')
    scene['history_visibility_probe'] = 1
    bpy.ops.ed.undo_push(message='Unrelated property with hidden radar field')
    for operation in ('undo', 'redo'):
        assert getattr(bpy.ops.ed, operation)() == {'FINISHED'}
        scene = bpy.context.scene
        radar.tick()
        current = radar.session_for(scene)
        assert current is not None and current.display_valid
        assert current.fov.hide_get(view_layer=scene.view_layers[0]), 'History must retain user visibility'
    current.mount.location.x += .5
    edited = tuple(tuple(row) for row in current.mount.matrix_basis)
    radar.tick()
    assert not current.display_valid
    bpy.ops.ed.undo_push(message='Native radar installation edit')
    scene['history_installation_probe'] = 1
    bpy.ops.ed.undo_push(message='Unrelated property with edited installation')
    for operation in ('undo', 'redo'):
        assert getattr(bpy.ops.ed, operation)() == {'FINISHED'}
        scene = bpy.context.scene
        radar.tick()
        radar.tick()
        mount = next(obj for obj in scene.objects
                     if obj.get('wfrl_x100_owner') == scene['wfrl_x100_owner'] and obj.type == 'EMPTY')
        assert tuple(tuple(row) for row in mount.matrix_basis) == edited, 'History must preserve native edits'
        assert radar.session_for(scene) is None or not radar.session_for(scene).display_valid
        assert scene.get('wfrl_x100_error')
    (output / 'result.json').write_text(json.dumps(dict(status='PASS', checks=checks,
        addon_file=addon.__file__, recording_state=recorder.state,
        user_visibility_preserved=True, native_installation_edit_preserved=True,
        scope='Native background undo/redo and radar rebinding; no GPU drawing'), indent=2))
    print('X100_HISTORY_PASS', flush=True)
finally:
    addon.unregister()
