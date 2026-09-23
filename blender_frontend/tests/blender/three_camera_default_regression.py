"""Default demo replacement, rebuild lifecycle and legacy-layout rejection."""
import copy
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')), str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import custom_cameras as core, stacked_camera_rig as rig
addon.register()
assert core.SLOTS == (1, 2, 3)
counts=[]
for attempt in range(2):
    addon.load_demo_scene()
    scene=bpy.context.scene
    cams=core.enabled_cameras(scene)
    assert [c['wfrl_custom_slot'] for c in cams] == [1,2,3]
    assert scene.objects.get(rig.PREFIX)
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
print('THREE_CAMERA_DEFAULT_PASS', counts, addon.__file__, flush=True)

from wfrl_blender import custom_camera_history as history
from dataclasses import replace
original=core.layout_dict(scene)
pitches=[core.parameters(c).pitch for c in core.enabled_cameras(scene)]
center=list(rig.pose(scene)['center']);center[1]-=.15
rig.move_box(scene,center,rig.pose(scene)['housing_yaw_deg']+5)
assert [core.parameters(c).pitch for c in core.enabled_cameras(scene)] == pitches
assert abs(rig.pose(scene)['center'][1]-center[1]) < 1e-5
assert all(abs(core.parameters(c).location[1]-center[1]) < 1e-5 for c in core.enabled_cameras(scene))
history.undo(scene)
assert core.config_equal(core.layout_dict(scene),original)
# Pitch change moves no optical centre and affects no other camera.
before=[core.parameters(c) for c in core.enabled_cameras(scene)]
draft=core.begin_draft(scene,2)
core.apply_research(scene,draft,replace(core.parameters(draft),pitch=-30.),confirmed=True)
core.commit_draft(scene,2,draft)
after=[core.parameters(c) for c in core.enabled_cameras(scene)]
assert before[0]==after[0] and before[2]==after[2]
assert before[1].location==after[1].location and after[1].pitch==-30.
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
print('BOX_PLACEMENT_PITCH_UNDO_ROLLBACK_PASS',flush=True)
