"""Ordinary MAPPO load uses bundled supported optics; edits persist until reload."""
import copy
from dataclasses import replace
import importlib
import json
import math
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')), str(ROOT)]
import bpy
from mathutils import Matrix, Vector
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
core = importlib.import_module(MODULE+'.custom_cameras')
rig = importlib.import_module(MODULE+'.stacked_camera_rig')
history = importlib.import_module(MODULE+'.custom_camera_history')
default_path = Path(addon.__file__).parent/'assets/cameras/t1-three-camera-default.json'
expected = json.loads(default_path.read_text())
addon.register()
assert core.SLOTS == (1, 2, 3)


def assert_default(scene):
    actual = core.layout_dict(scene)
    assert core.config_equal(actual['cameras'], expected['cameras']), 'default optics/locations/labels differ from bundled JSON'
    assert actual['model_signature'] == expected['model_signature']
    assert actual['rig_pose']['surface_mount'] == expected['rig_pose']['surface_mount']
    assert actual['rig_pose']['surface_mount']['type'] == 'SUPPORT'
    assert actual['rig_pose']['surface_mount']['aim_deg'] == -30.
    assert max(abs(a-b) for a,b in zip(actual['rig_pose']['center'], expected['rig_pose']['center'])) < 2e-6
    a,b = rig.pose_rotation(actual['rig_pose']), rig.pose_rotation(expected['rig_pose'])
    assert max(abs(a[i][j]-b[i][j]) for i in range(3) for j in range(3)) < 2e-6
    assert not scene.wfrl_flex_show_tip_trails
    assert not scene.wfrl_deflection_visible
    assert not history.HISTORY.entries, 'initial preset must not become user undo history'
    return actual


# Explicit geometry-only callers retain control over their own camera build.
addon.load_demo_scene(camera_rig=False)
assert not core.enabled_cameras(bpy.context.scene)
assert all(core.get_camera(bpy.context.scene, slot) is None for slot in core.SLOTS)
assert bpy.context.scene.objects.get(rig.PREFIX) is None
counts=[]
for attempt in range(2):
    addon.load_demo_scene()
    scene=bpy.context.scene
    cams=core.enabled_cameras(scene)
    assert [c['wfrl_custom_slot'] for c in cams] == [1,2,3]
    assert scene.objects.get(rig.PREFIX)
    assert_default(scene)
    counts.append(sum(o.name.startswith(rig.PREFIX) for o in scene.objects))
    assert all(o.users_collection == cams[0].users_collection for o in scene.objects if o.get('stacked_rig_physical'))
assert counts[0] == counts[1], counts
layout=core.layout_dict(scene); before=core.layout_hash(layout)
bad=copy.deepcopy(layout);four=copy.deepcopy(bad['cameras'][-1]);four['slot_id']=4;bad['cameras'].append(four)
try:core.import_layout(scene,bad,overwrite=True)
except ValueError:pass
else:raise AssertionError('obsolete C4 layout was accepted')
assert core.layout_hash(core.layout_dict(scene)) == before
for op in (lambda:core.begin_draft(scene,4),lambda:core.camera_name(4)):
    try:op()
    except ValueError:pass
    else:raise AssertionError('fourth slot available')
original=core.layout_dict(scene)
old_pose=rig.pose(scene)
old_matrices=[cam.matrix_basis.copy() for cam in core.enabled_cameras(scene)]
center=list(rig.pose(scene)['center']);center[1]-=.15
rig.move_box(scene,center,rig.pose(scene)['housing_yaw_deg']+5)
assert abs(rig.pose(scene)['center'][1]-center[1]) < 1e-5
delta=Matrix.Rotation(math.radians(5),3,'Z')
for cam,before in zip(core.enabled_cameras(scene),old_matrices):
    wanted=Vector(center)+delta@(before.translation-Vector(old_pose['center']))
    assert (cam.matrix_basis.translation-wanted).length < 2e-5
    wanted_rotation=delta@before.to_3x3()
    assert max(abs(cam.matrix_basis[i][j]-wanted_rotation[i][j]) for i in range(3) for j in range(3)) < 2e-5
assert 'surface_mount' not in rig.pose(scene), 'free-coordinate move should release support constraint'
history.undo(scene)
assert core.config_equal(core.layout_dict(scene),original)
# Pitch change moves no optical centre and affects no other camera.
before=[core.parameters(c) for c in core.enabled_cameras(scene)]
draft=core.begin_draft(scene,2)
edited_pitch=before[1].pitch+1.
core.apply_research(scene,draft,replace(core.parameters(draft),pitch=edited_pitch),confirmed=True)
core.commit_draft(scene,2,draft)
after=[core.parameters(c) for c in core.enabled_cameras(scene)]
assert before[0]==after[0] and before[2]==after[2]
assert before[1].location==after[1].location and after[1].pitch==edited_pitch
edited=core.layout_dict(scene)
scene.frame_set(61);bpy.context.view_layer.update()
assert core.config_equal(core.layout_dict(scene),edited), 'frame update reset user optics to defaults'
scene.frame_set(1);bpy.context.view_layer.update()
assert core.config_equal(core.layout_dict(scene),edited)
history.undo(scene)
assert core.config_equal(core.layout_dict(scene),original)
# Invalid box installation is rejected without partially changing its cameras.
try:rig.move_box(scene,(0,0,2),0.)
except ValueError:pass
else:raise AssertionError('invalid box installation accepted')
assert core.config_equal(core.layout_dict(scene),original)
# Fault after publishing cameras must roll back the housing as well.
checkpoint=core._publication_checkpoint
def fail(stage,slot=None):
    if stage=='commit':raise RuntimeError('box rollback probe')
core._publication_checkpoint=fail
try:rig.move_box(scene,center,0.)
except RuntimeError:pass
else:raise AssertionError('rollback probe not reached')
finally:core._publication_checkpoint=checkpoint
assert core.config_equal(core.layout_dict(scene),original)
# User edits remain active until the explicit ordinary MAPPO reload.
draft=core.begin_draft(scene,2)
core.apply_research(scene,draft,replace(core.parameters(draft),pitch=edited_pitch),confirmed=True)
core.commit_draft(scene,2,draft)
assert core.parameters(core.get_camera(scene,2)).pitch==edited_pitch
addon.load_demo_scene()
scene=bpy.context.scene
assert_default(scene)
assert sum(o.name.startswith(rig.PREFIX) for o in scene.objects)==counts[0]
out=Path(os.environ.get('WFRL_TEST_OUTPUT','/tmp/wfrl-three-camera-default'))
out.mkdir(parents=True,exist_ok=True)
(out/'default-validation.json').write_text(json.dumps({'status':'PASS','module':MODULE,
    'addon_path':addon.__file__,'blender':bpy.app.version_string,'preset_path':str(default_path),
    'checks':['ordinary load and reload use bundled supported pose and exact optics','geometry-only load has no rig',
              'initial history empty','legacy C4 rejected','tilted housing rigid move','per-camera edit persists over frames',
              'undo restores supported default','invalid move unchanged','atomic publish rollback',
              'explicit reload resets user edits to bundled default','tip trails and deflection aids disabled']},indent=2))
print('THREE_CAMERA_DEFAULT_PASS',counts,addon.__file__,flush=True)
