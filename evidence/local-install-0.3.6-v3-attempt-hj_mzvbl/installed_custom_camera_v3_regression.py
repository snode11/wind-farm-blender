import bpy, addon_utils, json, hashlib, sys, os
from pathlib import Path
MODULE='bl_ext.user_default.wfrl_blender'
INSTALLED=Path('/Users/eason/Library/Application Support/Blender/5.2/extensions/user_default/wfrl_blender')
OUT_INSTALL=Path('/var/folders/9w/yltr8zcx3gxb3rs6lr0kxwth0000gn/T/wfrl-install-v3-dl5f09l8')
assert not any('blender_frontend' in p for p in sys.path)
before=addon_utils.check(MODULE)
assert before[0], ('extension was not enabled in saved user preferences',before)
addon_utils.enable(MODULE,default_set=False,persistent=True)
import bl_ext.user_default.wfrl_blender as installed_addon
assert Path(installed_addon.__file__).parent==INSTALLED
inventory=json.loads((OUT_INSTALL/'build/wfrl_blender-0.3.6.inventory.json').read_text())
assert all(hashlib.sha256((INSTALLED/f['path']).read_bytes()).hexdigest()==f['sha256'] for f in inventory['files'])
(OUT_INSTALL/'installation-load.json').write_text(json.dumps({'module':installed_addon.__file__,'saved_preference_enabled':before[0],'loaded':addon_utils.check(MODULE)[1],'verified_files':len(inventory['files']),'version':'0.3.6'},indent=2))

"""Native transactional history and source checks, no GPU/window automation claim."""
import copy
import json
import os
from pathlib import Path
import sys
from dataclasses import replace
import bpy
import bl_ext.user_default.wfrl_blender as addon
from bl_ext.user_default.wfrl_blender import custom_cameras as c, custom_camera_history as h
from bl_ext.user_default.wfrl_blender import custom_camera_capture as capture
from bl_ext.user_default.wfrl_blender.panels import custom_cameras as panel, custom_camera_output as output
addon_utils.enable(MODULE,default_set=False)
scene=bpy.context.scene
for obj in list(scene.objects):bpy.data.objects.remove(obj,do_unlink=True)
root=bpy.data.objects.new(c.ROOT_NAME,None);scene.collection.objects.link(root)
bpy.ops.mesh.primitive_cube_add(size=2)
shell=bpy.context.object;shell.name=c.SURFACE_NAMES[0];shell.parent=root
bpy.context.view_layer.update()
checks={}
def install(slot,location=(0,0,1.1),research=False):
    cam=c.begin_draft(scene,slot)
    p=c.CameraParameters(location,123,-45,37,90,60,72,800,.006,2000)
    if research:c.apply_research(scene,cam,p,True)
    else:c.apply_parameters(cam,p,c.validate_position(scene,p.location)[0])
    cam['custom_label']='备注 '+str(slot)
    return c.commit_draft(scene,slot,cam)
install(1);install(2,(0,-1.1,0));install(3,(7,0,0),True)
base=c.layout_dict(scene)
frame=(scene.frame_current,scene.frame_subframe)
scene['v3_statistics_sentinel']='keep'
# No-op confirmation must not enter history.
size=len(h.HISTORY.entries);draft=c.begin_draft(scene,1);c.commit_draft(scene,1,draft)
assert len(h.HISTORY.entries)==size
checks['noop_confirmation']=True
# One confirm aggregates all draft mutations.
draft=c.begin_draft(scene,1)
for fov in (70,80,100):c.apply_parameters(draft,replace(c.parameters(draft),fov=fov))
c.commit_draft(scene,1,draft)
assert len(h.HISTORY.entries)==size+1
h.undo(scene);assert c.layout_hash(c.layout_dict(scene))==c.layout_hash(base)
checks['aggregated_full_restore']=True
# Participation and clearing each contribute one step.
c.set_enabled(scene,2,False);c.clear_slot(scene,3)
h.undo(scene);assert c.get_camera(scene,3)['custom_research_confirmed']
h.undo(scene);assert c.layout_hash(c.layout_dict(scene))==c.layout_hash(base)
checks['participation_clear_undo']=True
# Surface restore from a research target clears stale acknowledgement/warning.
surface=copy.deepcopy(base)
surface['cameras'][2]=copy.deepcopy(base['cameras'][0]);surface['cameras'][2]['slot_id']=3
c.import_layout(scene,surface,overwrite=True)
assert not c.get_camera(scene,3).get('custom_research_confirmed',False)
assert 'custom_nearest_distance' not in c.get_camera(scene,3)
h.undo(scene);assert c.layout_hash(c.layout_dict(scene))==c.layout_hash(base)
checks['research_surface_fields']=True
# Faults at each stage must preserve original object identities, links and refs.
changed=copy.deepcopy(base);changed['cameras'][0]['parameters']['fov']=120
originals={s:c.get_camera(scene,s) for s in c.SLOTS}
scene.camera=originals[1]
expected=c.live_layout_hash(scene);history_size=len(h.HISTORY.entries)
checkpoint=c._publication_checkpoint
for stage in ('stage','retire','publish','references','detach','commit'):
    def fail(point,slot=None):
        if point==stage:raise RuntimeError('injected '+stage)
    c._publication_checkpoint=fail
    try:c.import_layout(scene,changed,overwrite=True)
    except RuntimeError:pass
    else:raise AssertionError('fault not reached: '+stage)
    assert c.live_layout_hash(scene)==expected,stage
    assert all(c.get_camera(scene,s)==obj for s,obj in originals.items()),stage
    assert scene.camera==originals[1] and len(h.HISTORY.entries)==history_size, (stage,scene.camera,originals[1],len(h.HISTORY.entries),history_size,h.HISTORY.notice)
    assert not any(obj.get('wfrl_custom_draft') for obj in scene.objects)
c._publication_checkpoint=checkpoint
checks['atomic_stage_publish_reference_detach_commit_failures']=True
# Ordinary undo failure must not consume valid history.
c.import_layout(scene,changed,overwrite=True);size=len(h.HISTORY.entries);current=c.live_layout_hash(scene)
def fail(point,slot=None):
    if point=='detach':raise RuntimeError('undo detach failure')
c._publication_checkpoint=fail
try:h.undo(scene)
except RuntimeError:pass
else:raise AssertionError('undo failure not injected')
assert c.live_layout_hash(scene)==current and len(h.HISTORY.entries)==size
c._publication_checkpoint=checkpoint;h.undo(scene)
checks['undo_failure_retains_history']=True
# JSON no-op import and stale review cannot overwrite newer user changes.
size=len(h.HISTORY.entries)
c.import_layout(scene,json.loads(json.dumps(c.layout_dict(scene))),overwrite=True)
assert len(h.HISTORY.entries)==size
review=output.prepare_import(scene,changed)
c.set_enabled(scene,1,False);expected=c.live_layout_hash(scene)
try:output.commit_import(scene,review)
except ValueError as exc:assert '已变化' in str(exc)
else:raise AssertionError('stale import review accepted')
assert c.live_layout_hash(scene)==expected
h.undo(scene)
review=output.prepare_import(scene,changed)
output.commit_import(scene,review)
assert len(h.HISTORY.entries)==size+1
h.undo(scene)
checks['json_noop_stale_review_atomic_confirm']=True
# A failure of post-commit garbage collection leaves a complete new layout;
# detached old objects are retried without reappearing in any slot.
remove=c._remove
def no_remove(obj):raise RuntimeError('temporary cleanup failure')
c._remove=no_remove
c.import_layout(scene,changed,overwrite=True)
assert c.parameters(c.get_camera(scene,1)).fov==120
assert c._RETIRED and all(not obj.users_collection for obj in c._RETIRED)
c._remove=remove;c.cleanup_retired();assert not c._RETIRED
h.undo(scene)
checks['postcommit_cleanup_retry']=True
# Draft and capture guards do not secretly cancel either operation.
draft=c.begin_draft(scene,1)
try:h.undo(scene)
except ValueError as exc:assert '草稿' in str(exc)
else:raise AssertionError('undo during draft')
assert draft.get('wfrl_custom_draft');c.cancel_draft(draft)
from types import SimpleNamespace
capture._ACTIVE=SimpleNamespace(closed=False)
try:h.undo(scene)
except ValueError as exc:assert '采集' in str(exc)
else:raise AssertionError('undo during capture')
capture._ACTIVE=None
checks['draft_capture_guards']=True
# Timeline changes alone preserve history and undo changes no timeline/statistics.
c.set_enabled(scene,1,False)
scene.frame_set(47,subframe=.25);h.status(scene)
assert h.HISTORY.entries
h.undo(scene)
assert (scene.frame_current,scene.frame_subframe)==(47,.25) and scene['v3_statistics_sentinel']=='keep'
checks['timeline_statistics_preserved']=True
# Native external transform and in-place mesh edits invalidate history.
c.set_enabled(scene,1,False);c.get_camera(scene,1).rotation_euler.z+=.1
h.status(scene);assert not h.HISTORY.entries and '外部' in h.HISTORY.notice
c.set_enabled(scene,1,True);shell.data.vertices[0].co.x-=.125;shell.data.update();bpy.context.view_layer.update()
h.status(scene);assert not h.HISTORY.entries and '模型' in h.HISTORY.notice
shell.data.vertices[0].co.x+=.125;shell.data.update();bpy.context.view_layer.update()
checks['external_transform_inplace_mesh_invalidation']=True
# Preflight and shared sampling. Restore valid layout first.
c.import_layout(scene,base,overwrite=True)
assert not capture.preflight(bpy.context)['errors']
assert output.summary_check(bpy.context,None)['cameras']==3
c.get_camera(scene,2)['custom_enabled']=False
assert output.summary_check(bpy.context,None)['cameras']==2
c.get_camera(scene,2)['custom_enabled']=True
assert output.summary_check(bpy.context,None)['cameras']==3
checks['summary_external_participation_cache_invalidation']=True
scene.wfrl_capture_mode='SEQUENCE';scene.wfrl_capture_start=0;scene.wfrl_capture_end=.25;scene.wfrl_capture_step=.1
assert len(output.requested_times(scene))==3
assert any('正式 FarmFlex' in e for e in capture.preflight(bpy.context,output.requested_times(scene))['errors'])
scene.wfrl_capture_mode='CURRENT';assert output.requested_times(scene) is None
for slot in (1,2,3):c.set_enabled(scene,slot,False)
assert any('没有参与相机' in e for e in capture.preflight(bpy.context)['errors'])
checks['preflight_static_sequence_participation']=True
# Session crossing invalidates even if camera layout is otherwise unchanged.
other=bpy.data.scenes.new('Other');h.status(other);assert not h.HISTORY.entries
checks['scene_invalidation']=True
out=Path(os.environ.get('WFRL_TEST_OUTPUT','/tmp/wfrl-v3-native'));out.mkdir(parents=True,exist_ok=True)
(out/'result.json').write_text(json.dumps({'blender':bpy.app.version_string,'checks':checks},indent=2))
addon.unregister()
print('CUSTOM_CAMERA_V3_NATIVE_PASS',len(checks),flush=True)