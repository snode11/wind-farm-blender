"""Scene-load failures preserve the owning scene's deformation and radar."""
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
sys.path.append(str(Path(__file__).resolve().parent))
import _native
_native.check_addon_root(addon)

OUT = _native.output_directory(new=False)
addon.register()
addon.load_demo_scene(camera_rig=False)
scene_a = bpy.context.scene
scene_a.name = 'A_MAPPO'
scene_a.use_fake_user = True
scene_a.frame_set(121)
from unittest.mock import patch
source=farm_flex.default_package()
def signature():
    active=farm_flex.active_for(scene_a)
    return (active,clearance_replay.reader_for(scene_a),scene_a.frame_current,scene_a.camera,
        scene_a.get('wfrl_clearance_time_s'),[(obj.as_pointer(),obj.data.as_pointer()) for obj,*_ in active.blades])
original=signature()
for fault in ('missing','hash','comparison'):
    candidate=OUT/fault
    candidate.mkdir()
    for entry in source.iterdir():
        if entry.is_file() and not (fault=='missing' and entry.name=='geometry.npz'):
            (candidate/entry.name).symlink_to(entry)
    if fault=='hash':
        (candidate/'geometry.npz').unlink()
        (candidate/'geometry.npz').write_bytes(b'invalid candidate')
    if fault=='comparison':
        manifest=json.loads((candidate/'manifest.json').read_text())
        (candidate/'manifest.json').unlink()
        (candidate/'deflection-t1.json').unlink()
        data=json.loads((source/'deflection-t1.json').read_text());data['unit']='wrong'
        (candidate/'deflection-t1.json').write_text(json.dumps(data))
        manifest['files']['deflection-t1.json']=__import__('hashlib').sha256((candidate/'deflection-t1.json').read_bytes()).hexdigest()
        (candidate/'manifest.json').write_text(json.dumps(manifest))
    try: addon.load_demo_scene(candidate,camera_rig=False)
    except (ValueError,OSError): pass
    else: raise AssertionError('Bad candidate accepted')
    assert signature()==original
# Wrong dual method and ordinary package selected through the dual entry preserve A.
with patch.object(farm_flex,'default_dual_package',farm_flex.default_dual_tls_package):
    try: result=bpy.ops.wfrl.load_dual_beam(builtin_method='AXIS')
    except RuntimeError: result={'CANCELLED'}
    assert result=={'CANCELLED'} and signature()==original
try: result=bpy.ops.wfrl.load_dual_beam(filepath=str(source/'manifest.json'))
except RuntimeError: result={'CANCELLED'}
assert result=={'CANCELLED'} and signature()==original
scene_a.pop('wfrl_home_load_error',None)
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
linked.use_fake_user = True
clearance_visual = importlib.import_module(ADDON_MODULE + '.clearance_visual')
clearance_visual.update_beams(scene_a, 'T1', (True, False, True))
def beam_state(scene):
    return [(obj.as_pointer(), obj.data.as_pointer(), tuple(m.as_pointer() for m in obj.data.materials),
             obj.data.bevel_depth, obj['wfrl_beam_active']) for obj in scene.objects
            if '.ClearanceRadar.Beam' in obj.name]
beam_before = beam_state(scene_a)
try:
    farm_flex.attach(linked, farm_flex.saved_package(scene_a))
except ValueError as exc:
    assert 'already driven by scene' in str(exc), str(exc)
else:
    raise AssertionError('Linked scene incorrectly acquired shared FarmFlex objects')
assert farm_flex.is_active(scene_a) and runtime_a.enabled
assert clearance_replay.reader_for(linked) is None
assert not linked['wfrl_flex_active']
assert beam_state(scene_a) == beam_before
assert clearance_replay.reader_for(scene_a) is runtime_a.readers['T1']
shared_curve_scene = bpy.data.scenes.new('D_SHARED_CURVE')
shared_curve_scene.use_fake_user = True
shared_beam = scene_a.objects['WFRL.Turbine.T1.ClearanceRadar.Beam1'].copy()
shared_curve_scene.collection.objects.link(shared_beam)
clearance_replay.clear(shared_curve_scene)
assert beam_state(scene_a) == beam_before
saved_linked = OUT / 'linked-copy.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(saved_linked))
bpy.ops.wm.open_mainfile(filepath=str(saved_linked))
scene_a, linked = bpy.data.scenes['A_MAPPO'], bpy.data.scenes['C_LINKED_COPY']
scene_b = bpy.data.scenes['B_FULL_COPY']
assert farm_flex.is_active(scene_a) and not farm_flex.is_active(linked)
assert clearance_replay.reader_for(scene_a) is not None and clearance_replay.reader_for(linked) is None
clearance_visual.update_beams(scene_a, 'T1', (True, False, True))
beam_before = beam_state(scene_a)
clearance_replay.clear(linked)
assert beam_state(scene_a) == beam_before

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
