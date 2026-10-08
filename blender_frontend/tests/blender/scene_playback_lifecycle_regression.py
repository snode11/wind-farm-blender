"""Save/reopen FULL_COPY failure must preserve the ready scene's deformation."""
import json
import importlib
import os
from pathlib import Path
import sys
import numpy as np
import bpy

ROOT = Path(__file__).resolve().parents[3]
ADDON_MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if ADDON_MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(ADDON_MODULE)
farm_flex = importlib.import_module(ADDON_MODULE + '.farm_flex')
clearance_replay = importlib.import_module(ADDON_MODULE + '.clearance_replay')
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(addon.__file__).resolve().parent == Path(expected_root).resolve()

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
addon.register()
addon.load_demo_scene(camera_rig=False)
scene_a = bpy.context.scene
scene_a.name = 'A_MAPPO'
scene_a.use_fake_user = True
scene_a.frame_set(121)
bpy.ops.scene.new(type='FULL_COPY')
scene_b = bpy.context.scene
scene_b.name = 'B_FULL_COPY'
scene_b.use_fake_user = True
scene_b.frame_set(241)
bpy.context.window.scene = scene_a
saved = OUT / 'full-copy.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(saved))
bpy.ops.wm.open_mainfile(filepath=str(saved))
scene_a, scene_b = bpy.data.scenes['A_MAPPO'], bpy.data.scenes['B_FULL_COPY']
assert farm_flex.is_active(scene_a)
runtime_a = farm_flex.active_for(scene_a)
assert runtime_a.enabled and scene_a['wfrl_flex_active']
assert clearance_replay.reader_for(scene_a) is runtime_a.readers['T1']
assert scene_a.frame_current == 121
assert not farm_flex.is_active(scene_b)
assert clearance_replay.reader_for(scene_b) is None
assert not scene_b['wfrl_flex_active']
assert '未就绪' in scene_b['wfrl_clearance_status']
assert farm_flex.update in bpy.app.handlers.frame_change_post
obj = scene_a.objects['WFRL.Turbine.T1.Blade1']
before = np.asarray([vertex.co[:] for vertex in obj.data.vertices])
scene_a.frame_set(181)
after = np.asarray([vertex.co[:] for vertex in obj.data.vertices])
mesh_delta = float(np.max(np.linalg.norm(after-before, axis=1)))
assert mesh_delta > .01, mesh_delta
assert clearance_replay.sample(scene_a)['time_s'] == float(runtime_a.times[0]) + 3.
assert scene_a['wfrl_clearance_time_s'] == clearance_replay.sample(scene_a)['time_s']
clock_b = scene_b.get('wfrl_clearance_time_s')
scene_b.frame_set(301)
assert scene_b.get('wfrl_clearance_time_s') == clock_b
assert farm_flex.active_for(scene_a) is runtime_a
np.testing.assert_array_equal(after, [vertex.co[:] for vertex in obj.data.vertices])

# A linked copy owns another timeline but shares transforms and meshes. Refuse
# that second writer explicitly instead of claiming two independent replays.
linked = scene_a.copy()
linked.name = 'C_LINKED_COPY'
try:
    farm_flex.attach(linked, farm_flex.saved_package(scene_a))
except ValueError as exc:
    assert 'already driven by scene' in str(exc), str(exc)
else:
    raise AssertionError('Linked scene incorrectly acquired shared FarmFlex objects')
assert farm_flex.is_active(scene_a) and runtime_a.enabled
assert clearance_replay.reader_for(linked) is None
assert not linked['wfrl_flex_active']

result = dict(status='PASS', scope='isolated background Blender', addon_module=ADDON_MODULE,
    addon_file=addon.__file__, ready_scene=scene_a.name, failed_scene=scene_b.name,
    failed_status=scene_b['wfrl_clearance_status'], mesh_change_max_m=mesh_delta,
    frame=scene_a.frame_current, time_s=scene_a['wfrl_clearance_time_s'],
    linked_scene_rejected=True)
addon.unregister()
assert farm_flex._ACTIVE is None and not farm_flex._ACTIVES
assert farm_flex.update not in bpy.app.handlers.frame_change_post
assert farm_flex.on_load_pre not in bpy.app.handlers.load_pre
(OUT / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print('SCENE_PLAYBACK_LIFECYCLE_PASS ' + json.dumps(result), flush=True)
