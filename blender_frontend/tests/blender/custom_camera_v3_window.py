"""Real GPU regression and timing probes; remains open for separate CUA input.

Scripted actions are labelled automated; external input is measured separately.
Set WFRL_V3_AUTO_QUIT=1 to exit after automated checks. No user scene is saved.
"""
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import traceback
import bpy
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import custom_cameras as c, custom_camera_history as h
from wfrl_blender import custom_camera_capture as capture, custom_camera_preview as preview
from wfrl_blender import custom_camera_diagnostics as diag
from wfrl_blender.panels import custom_cameras as panel, custom_camera_output as output
OUT=Path(os.environ.get('WFRL_TEST_OUTPUT','/tmp/wfrl-v3-window'));OUT.mkdir(parents=True,exist_ok=True)
STATE={'phase':0,'checks':{},'draws':[]}

def start(mode,slot=1):
    bpy.context.window_manager.wfrl_custom_slot=slot
    bpy.ops.wfrl.custom_camera('INVOKE_DEFAULT',mode=mode)
    return panel._ACTIVE


def persist():
    (OUT/'trace.json').write_text(json.dumps(list(diag.EVENTS),ensure_ascii=False,indent=2))
    op=panel._ACTIVE
    status={'phase':STATE['phase'],'stage':op.stage if op else None,
            'slot':op.slot if op else None,'history_count':len(h.HISTORY.entries),
            'image_version':op.preview.version if op else None,
            'image_status':op.preview.status() if op else None,
            'layout':c.layout_dict(bpy.context.scene),
            'draft':c.parameters(op.camera).__dict__ if op and op.stage not in panel._VIEWING else None,
            'scene_frame':bpy.context.scene.frame_current,
            'capture_progress':capture.progress()}
    (OUT/'live-state.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))


def tick():
    try:
        if STATE['phase']==0:
            addon.register();addon.load_demo_scene()
            scene=bpy.context.scene
            window=bpy.context.window;area=next(a for a in window.screen.areas if a.type=='VIEW_3D')
            region=next(r for r in area.regions if r.type=='WINDOW')
            STATE.update(window=window,area=area,region=region)
            area.spaces.active.show_region_ui=True
            scene.frame_set(755,subframe=.25)
            geometry=c._geometry(scene)[0]
            for slot,target in zip(c.SLOTS,[(0,-10,.2),(0,10,.2),(0,0,10),(10,0,.2)]):
                point,normal,*_=geometry[-1].find_nearest(Vector(target))
                cam=c.begin_draft(scene,slot)
                c.place_on_surface(cam,c.SurfaceAnchor(tuple(point),tuple(normal),.1,geometry[0]))
                c.apply_parameters(cam,replace(c.parameters(cam),yaw=180,pitch=-45,output_long_edge_px=640))
                cam['custom_label']='独立表面位置 '+str(slot)
                c.commit_draft(scene,slot,cam)
            STATE['base']=c.layout_dict(scene)
            diag.start();STATE['phase']=1
            return .5
        with bpy.context.temp_override(window=STATE['window'],area=STATE['area'],region=STATE['region']):
            scene=bpy.context.scene;phase=STATE['phase']
            if phase==1:
                start('WATCH');STATE['phase']=2
                return .8
            if phase==2:
                op=panel._ACTIVE;assert len(op.preview.images)==1,op.preview.error
                STATE['single_first_s']=op.preview.elapsed_s
                checkpoint=c._publication_checkpoint
                def fail_commit(stage,slot=None):
                    if stage=='commit':raise RuntimeError('watch rollback checkpoint')
                c._publication_checkpoint=fail_commit
                original=c.get_camera(scene,1)
                try:c.clear_slot(scene,1)
                except RuntimeError:pass
                else:raise AssertionError('watch rollback failure not injected')
                finally:c._publication_checkpoint=checkpoint
                assert op.stage=='WATCH' and op.camera==original
                STATE['checks']['failed_clear_restores_watch_reference']=True
                c.clear_slot(scene,1)
                assert op.stage=='LAYOUT' and op.camera is None and not op.preview.images
                h.undo(scene)
                assert c.get_camera(scene,1) is not None and op.camera==c.get_camera(scene,1)
                STATE['checks']['clear_watch_returns_to_layout_and_undo_restores']=True
                start('WATCH');STATE['phase']=3
                return 1.
            if phase==3:
                op=panel._ACTIVE;assert len(op.preview.images)==1,op.preview.error
                STATE['repeat_single_s']=op.preview.elapsed_s
                old=op.preview.images;meta=copy.deepcopy(op.preview.metadata)
                cam=c.get_camera(scene,1);c.apply_parameters(cam,replace(c.parameters(cam),fov=81))
                op.preview.observe(bpy.context,[op.camera])
                assert '等待更新' in op.preview.status()
                saved=preview.render_group
                def fail(*args,**kwargs):raise RuntimeError('可控 GPU 失败，测试旧图保留')
                preview.render_group=fail;op.preview.last=0
                op.preview.refresh(bpy.context,[op.camera])
                preview.render_group=saved
                assert op.preview.images is old and op.preview.metadata==meta and op.preview.error
                STATE['checks']['parameter_stale_failure_old_group']=True
                op.preview.attempt=0;op.preview.refresh(bpy.context,[op.camera])
                assert not op.preview.error and op.preview.images is not old
                # Timeline changes cannot invalidate layout-only undo.
                c.set_enabled(scene,4,False);scene.frame_set(760)
                h.status(scene);assert h.HISTORY.entries
                h.undo(scene);assert scene.frame_current==760 and c.get_camera(scene,4)['custom_enabled']
                assert panel._ACTIVE.stage=='WATCH' and panel._ACTIVE.camera==c.get_camera(scene,1)
                STATE['checks']['live_undo_refs_timeline']=True
                # Directory invoke must not overwrite the shared scene settings.
                scene.wfrl_capture_mode='SEQUENCE'
                at=capture.current_time(scene)
                scene.wfrl_capture_start=at;scene.wfrl_capture_end=at+.26;scene.wfrl_capture_step=.1
                times=output.requested_times(scene);assert len(times)==3
                assert not capture.preflight(bpy.context,times)['errors']
                STATE['checks']['shared_settings_grid_summary']=True
                from wfrl_blender import clearance_replay, farm_flex, charts
                STATE['protected']=(scene.frame_current,scene.frame_subframe,
                    json.dumps(clearance_replay.sample(scene)['statistics'],sort_keys=True),
                    repr(charts.history.__dict__),farm_flex._ACTIVE.last_telemetry_frame)
                transaction=capture.Capture(bpy.context,OUT,times);transaction.step(bpy.context)
                accepted=time.perf_counter();transaction.request_cancel()
                assert transaction.closed and transaction.index==1
                assert transaction.step(bpy.context) and transaction.index==1
                manifest=json.loads((transaction.path/'manifest.json').read_text())
                assert manifest['status']=='incomplete' and manifest['completed_samples']==1
                assert len((transaction.path/'frames.jsonl').read_text().splitlines())==4
                assert STATE['protected']==(scene.frame_current,scene.frame_subframe,
                    json.dumps(clearance_replay.sample(scene)['statistics'],sort_keys=True),
                    repr(charts.history.__dict__),farm_flex._ACTIVE.last_telemetry_frame)
                STATE['cancel_call_to_accept_s']=transaction.cancel_accepted_at-accepted
                STATE['cancel_accept_to_stop_s']=transaction.cancel_stopped_at-transaction.cancel_accepted_at
                STATE['checks']['cancel_between_groups_no_extra_group_restore']=True
                # Re-entrant test hook requests cancellation during a synchronous
                # group; real input cannot arrive until the GPU call returns.
                transaction=capture.Capture(bpy.context,OUT,times)
                def request_during_group(*args,**kwargs):
                    images=saved(*args,**kwargs);transaction.request_cancel();return images
                preview.render_group=request_during_group
                transaction.step(bpy.context);preview.render_group=saved
                assert transaction.closed and transaction.index==1
                assert json.loads((transaction.path/'manifest.json').read_text())['status']=='incomplete'
                STATE['checks']['cancel_during_group_complete_boundary']=True
                scene.wfrl_capture_mode='CURRENT'
                start('EDIT');STATE['phase']=4;STATE['count']=0
                return .3
            if phase==4:
                # Scripted numeric inputs, separate from subsequent external drag.
                op=panel._ACTIVE
                if not STATE['checks'].get('pending_camera_matrix_first_attempt'):
                    op.change(dx=1.3,dy=.7)
                    op.preview.last=op.preview.attempt=0
                    op.preview.refresh(bpy.context,[op.camera])
                    assert not op.preview.error,op.preview.error
                    assert op.preview.key==op.preview.request
                    STATE['checks']['pending_camera_matrix_first_attempt']=True
                if STATE['count']<10:
                    bpy.context.window_manager.wfrl_custom_fov=76+STATE['count']
                    STATE['count']+=1
                    return .35
                assert op.preview.metadata is not None and op.preview.key==op.preview.request
                STATE['checks']['final_numeric_state_committed']=True
                STATE['phase']=40;STATE['count']=0
                STATE['motion_start_version']=op.preview.version
                return .2
            if phase==40:
                op=panel._ACTIVE
                if STATE['count']<10:
                    for _ in range(5):op.change(dx=.2,dy=.1)
                    STATE['count']+=1
                    return .035
                STATE['motion']={'input_events':50,
                    'final_parameters':c.parameters(op.camera).__dict__,
                    'source':'scripted change calls; not OS mouse event latency'}
                STATE['release_at']=time.perf_counter()
                diag.record('scripted_drag_release',version=op.preview.version)
                STATE['phase']=41
                return .05
            if phase==41:
                op=panel._ACTIVE
                op.preview.observe(bpy.context,[op.camera])
                if op.preview.key!=op.preview.request:
                    assert time.perf_counter()-STATE['release_at']<5,op.preview.status()
                    return .05
                final=next(e for e in reversed(diag.EVENTS) if e['kind']=='t3' and e.get('renderer')==id(op.preview))
                STATE['motion'].update(final_version=op.preview.version,
                    draws=op.preview.version-STATE['motion_start_version'],
                    release_to_final_commit_s=max(0,final['at']-STATE['release_at']))
                STATE['phase']=42
                return .7
            if phase==42:
                op=panel._ACTIVE
                assert op.preview.version==STATE['motion']['final_version']
                assert c.parameters(op.camera).__dict__==STATE['motion']['final_parameters']
                STATE['motion']['no_stale_catchup_after_stop']=True
                STATE['checks']['scripted_continuous_direction_latest_state']=True
                bpy.ops.wfrl.custom_camera_action(action='CANCEL')
                start('WATCH')
                STATE['phase']=5
                return .8
            if phase==5:
                import gpu
                environment={'machine':platform.machine(),'system':platform.platform(),
                    'gpu':gpu.platform.renderer_get(),'vendor':gpu.platform.vendor_get(),
                    'blender':bpy.app.version_string,'window':[STATE['window'].width,STATE['window'].height],
                    'ui_scale':bpy.context.preferences.system.ui_scale,'preview_long_edge':640,
                    'source':scene.get('wfrl_farm_flex_path'),'model':c.model_signature(scene),
                    'fingerprints':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in (ROOT/'blender_frontend/wfrl_blender').glob('custom_camera*.py')}}
                result={'status':'PASS','checks':STATE['checks'],'environment':environment,
                    'single_first_s':STATE['single_first_s'],'repeat_single_s':STATE['repeat_single_s'],
                    'cancel_call_to_accept_s':STATE['cancel_call_to_accept_s'],
                    'cancel_accept_to_stop_s':STATE['cancel_accept_to_stop_s'],'continuous_direction':STATE['motion'],
                    'measurement_boundary':'Scripted handler/GPU/callback probes; no external t0 in this phase'}
                (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
                persist();print('V3_WINDOW_AUTOMATED_PASS',flush=True)
                if os.environ.get('WFRL_V3_AUTO_QUIT')=='1':
                    addon.unregister();bpy.ops.wm.quit_blender();return None
                STATE['phase']=6
                # Test-only persistence of timing evidence; production probes
                # never save. External CUA input can now use this isolated window.
                return .5
            persist()
            return 1.
    except Exception:
        error=traceback.format_exc();print(error,flush=True)
        (OUT/'result.json').write_text(json.dumps({'status':'FAIL','phase':STATE['phase'],'error':error},indent=2))
        try:capture.shutdown();addon.unregister()
        except Exception:pass
        bpy.ops.wm.quit_blender();return None
    return .2
bpy.app.timers.register(tick,first_interval=1.)
