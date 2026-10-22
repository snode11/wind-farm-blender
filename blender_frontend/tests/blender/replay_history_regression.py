"""Native undo/redo, replay clock recovery and deleted-scene cleanup."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
addon_root = Path(addon.__file__).resolve().parent
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert addon_root == Path(expected).resolve()
if MODULE == 'wfrl_blender':
    addon.register()
else:
    assert MODULE in bpy.context.preferences.addons
farm = importlib.import_module(MODULE + '.farm_flex')
replay = importlib.import_module(MODULE + '.clearance_replay')
output = Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=False)
result = dict(scope='Independent Blender process; native operators invoked by script',
              blender=bpy.app.version_string, addon_file=addon.__file__, checks=[])


def resources():
    return dict(meshes=len(bpy.data.meshes), curves=len(bpy.data.curves),
                comparison_orphans=[data.name for data in (*bpy.data.meshes, *bpy.data.curves)
                                    if data.users == 0 and data.name.startswith('WFRL.Deflection.')])


def check_motion(label):
    scene = bpy.context.scene
    assert farm.is_active(scene), scene.get('wfrl_clearance_status')
    active = farm.active_for(scene)
    visibility = set(active.visible_turbines)
    active.visible_turbines = {0, 1, 2}
    active.cache.clear()
    active.update(scene)
    assert bpy.ops.wfrl.farm_transport.poll()
    before = {obj.name: np.asarray([v.co[:] for v in obj.data.vertices])
              for obj, *_ in active.blades}
    frame, subframe = scene.frame_current, scene.frame_subframe
    scene.frame_set(frame + 120, subframe=subframe)
    deltas = {obj.name: float(np.max(np.linalg.norm(
        np.asarray([v.co[:] for v in obj.data.vertices]) - before[obj.name], axis=1)))
        for obj, *_ in active.blades}
    assert len(deltas) == 9 and all(value > .01 for value in deltas.values()), deltas
    assert scene['wfrl_clearance_time_s'] == replay.sample(scene)['time_s']
    scene.frame_set(frame, subframe=subframe)
    active.visible_turbines = visibility
    result['checks'].append(dict(label=label, deltas=deltas, time_s=scene['wfrl_clearance_time_s'],
                                resources=resources()))


addon.load_demo_scene(camera_rig=False)
result['baseline_resources'] = resources()
scene = bpy.context.scene
scene.frame_set(181, subframe=.25)
scene['wfrl_clearance_turbine'] = 'T2'
replay._READERS[scene.as_pointer()] = farm.active_for(scene).readers['T2']
scene.render.fps, scene.render.fps_base = 30, 1.001
scene.use_preview_range = True
scene.frame_preview_start, scene.frame_preview_end = 20, 300
camera = scene.camera
bpy.ops.object.select_all(action='DESELECT')
camera.select_set(True)
bpy.context.view_layer.objects.active = camera
original_location = tuple(camera.location)
camera_name = camera.name
bpy.context.preferences.edit.use_global_undo = True
bpy.ops.ed.undo_push(message='Replay camera baseline')
assert bpy.ops.transform.translate('EXEC_DEFAULT', True, value=(1, 0, 0)) == {'FINISHED'}
translated_location = tuple(bpy.context.scene.camera.location)
assert bpy.ops.ed.undo() == {'FINISHED'}
scene = bpy.context.scene
assert tuple(scene.camera.location) == original_location
assert scene.camera.name == camera_name
assert (scene.frame_current, scene.frame_subframe) == (181, .25)
assert (scene.render.fps, round(scene.render.fps_base, 3)) == (30, 1.001)
assert scene.use_preview_range and (scene.frame_preview_start, scene.frame_preview_end) == (20, 300)
assert scene['wfrl_clearance_turbine'] == 'T2'
check_motion('camera_undo')
assert resources() == result['baseline_resources'], resources()
assert bpy.ops.ed.redo() == {'FINISHED'}
scene = bpy.context.scene
assert tuple(scene.camera.location) == translated_location
assert scene['wfrl_clearance_turbine'] == 'T2'
check_motion('camera_redo')
assert resources() == result['baseline_resources'], resources()

# Failed saved-package validation remains explicitly unavailable.
original_load = farm._load_package
def missing_package(_path):
    raise OSError('Injected unavailable saved package')
replay.on_history_pre(None)
farm._load_package = missing_package
try:
    replay.on_history_post(None)
finally:
    farm._load_package = original_load
scene = bpy.context.scene
assert not farm.is_active(scene) and replay.reader_for(scene) is None
assert 'Injected unavailable saved package' in scene['wfrl_clearance_status']
result['checks'].append(dict(label='saved_package_failure_remains_unavailable', passed=True))
replay.on_history_post(None)
assert farm.is_active(scene)

# A missing farm runtime never exposes a standalone rigid replay reader.
active = farm.active_for(scene)
farm._ACTIVES.pop(scene.as_pointer())
assert replay.reader_for(scene) is None and replay.sample(scene) is None
previous_time = scene['wfrl_clearance_time_s']
scene.frame_set(421)
assert scene['wfrl_clearance_time_s'] == previous_time
farm._ACTIVES[scene.as_pointer()] = active
result['checks'].append(dict(label='missing_runtime_no_rigid_fallback', passed=True))

other = bpy.data.scenes.new('Independent empty scene')
bpy.context.window.scene = other
bpy.data.scenes.remove(scene)
addon.load_demo_scene(camera_rig=False)
check_motion('reload_after_scene_delete')
assert len(farm._ACTIVES) == len(replay._READERS) == 1
scene = bpy.context.scene
other = bpy.data.scenes.new('Second independent empty scene')
bpy.context.window.scene = other
bpy.data.scenes.remove(scene)
farm.detach()
assert not farm._ACTIVES and not replay._READERS
addon.unregister()
assert not farm._ACTIVES and not replay._READERS
for name, callback in replay._HANDLERS:
    assert callback not in getattr(bpy.app.handlers, name)
result['checks'].append(dict(label='unregister_after_reload', passed=True))
result['sha256'] = {name: hashlib.sha256((addon_root / name).read_bytes()).hexdigest()
                    for name in ('farm_flex.py', 'clearance_replay.py', 'split_turbine_context.py',
                                 'deflection.py')}
result['status'] = 'PASS'
(output / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print('REPLAY_HISTORY_PASS ' + json.dumps(result), flush=True)
if not bpy.app.background:
    bpy.ops.wm.quit_blender()
