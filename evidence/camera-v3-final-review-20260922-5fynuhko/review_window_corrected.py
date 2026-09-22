import runpy, os, json, time, hashlib, traceback, copy
from pathlib import Path
import bpy
ROOT=Path('/Users/eason/Desktop/wfcrl/wind farm RL')
OUT=Path(os.environ['WFRL_TEST_OUTPUT'])
ns=runpy.run_path(str(ROOT/'blender_frontend/tests/blender/custom_camera_v3_window.py'))
from wfrl_blender import custom_cameras as c, custom_camera_history as h, custom_camera_preview as p, custom_camera_capture as cap, clearance_replay, farm_flex, charts
from wfrl_blender.panels import custom_cameras as ui
from dataclasses import asdict
finished=False
last=None

def raw(cam):
    return {'location':list(cam.location),'rotation':list(cam.rotation_euler),'world':[list(r) for r in cam.matrix_world],
      'lens':cam.data.lens,'near':cam.data.clip_start,'far':cam.data.clip_end,
      'props':{k:cam[k].to_list() if hasattr(cam[k],'to_list') else cam[k] for k in cam.keys()}}
def protected():
    s=bpy.context.scene
    return [s.frame_current,s.frame_subframe,s.get('wfrl_clearance_time_s'),bpy.context.window.screen.is_animation_playing,
            clearance_replay.sample(s)['statistics'],repr(charts.history.__dict__),farm_flex._ACTIVE.last_telemetry_frame]
def snapshot():
    op=ui._ACTIVE;s=bpy.context.scene
    return {'stage':op.stage if op else None,'history':len(h.HISTORY.entries),'protected':protected(),
      'committed':{str(n):raw(c.get_camera(s,n)) for n in c.SLOTS if c.get_camera(s,n)},
      'draft':raw(op.camera) if op and op.stage not in ui._VIEWING else None,
      'version':op.preview.version if op else None,'status':op.preview.status() if op else None,
      'metadata':op.preview.metadata if op else None,
      'images':{str(n):asdict(im.params) for n,im in op.preview.images.items()} if op else {}}

def extra():
    global finished,last
    try:
      if ns['STATE']['phase']!=6:return .5
      w,a,r=[ns['STATE'][k] for k in ('window','area','region')]
      with bpy.context.temp_override(window=w,area=a,region=r):
        s=bpy.context.scene
        if not finished:
          check={};proof={}
          bpy.context.view_layer.update()
          baseline={n:raw(c.get_camera(s,n)) for n in c.SLOTS};stamp=protected();size=len(h.HISTORY.entries)
          op=ns['start']('EDIT')
          for i in range(20):op.change(dx=.5,dy=-.2)
          bpy.context.window_manager.wfrl_custom_fov=97
          bpy.ops.wfrl.custom_camera_action(action='CONFIRM')
          assert len(h.HISTORY.entries)==size+1
          bpy.ops.wfrl.custom_camera_action(action='UNDO')
          op=ui._ACTIVE
          assert not op.preview.images
          op.preview.last=op.preview.attempt=0
          op.preview.refresh(bpy.context,c.enabled_cameras(s))
          assert not op.preview.error and set(op.preview.images)=={1,2,3,4}
          for n in c.SLOTS:
            actual=raw(c.get_camera(s,n));assert actual==baseline[n],(n,actual,baseline[n])
            im=op.preview.images[n]
            assert abs(im.params.fov-baseline[n]['props']['gimbal_fov'])<1e-6
            assert max(abs(im.world[i][j]-baseline[n]['world'][i][j]) for i in range(4) for j in range(4))<1e-5
          assert protected()==stamp
          proof['undo_first_refresh']=snapshot();check['undo_first_attempt_raw_layout_matrix_and_statistics']=True
          at=stamp[2];times=[at,at+.1,at+.2]
          job=cap.Capture(bpy.context,OUT,times)
          while not job.closed:job.step(bpy.context)
          assert protected()==stamp
          proof['complete_capture']=str(job.path);check['three_group_complete_restore']=True
          job=cap.Capture(bpy.context,OUT,times);job.step(bpy.context)
          original=cap.write_png;calls=0
          def failed_write(*args,**kwargs):
            global calls
            calls+=1
            if calls==2:raise OSError('final-review injected second PNG write failure')
            return original(*args,**kwargs)
          # Mutable closure for independent fault count.
          counter=[0]
          def failed_write(*args,**kwargs):
            counter[0]+=1
            if counter[0]==2:raise OSError('final-review injected second PNG write failure')
            return original(*args,**kwargs)
          cap.write_png=failed_write
          try:job.step(bpy.context)
          except OSError as exc:job.finish(error=str(exc))
          else:raise AssertionError('write failure not reached')
          finally:cap.write_png=original
          assert job.closed and job.index==1 and protected()==stamp and not cap.active()
          proof['failed_capture']=str(job.path);check['partial_png_failure_complete_group_count_restore']=True
          op.preview.last=op.preview.attempt=0;op.preview.refresh(bpy.context,c.enabled_cameras(s))
          assert not op.preview.error
          check['preview_recovers_after_failed_capture']=True
          (OUT/'supplement-result.json').write_text(json.dumps({'status':'PASS','checks':check,'evidence':proof},ensure_ascii=False,indent=2))
          finished=True
          print('FINAL_REVIEW_SUPPLEMENT_PASS',flush=True)
        value=snapshot();encoded=json.dumps(value,ensure_ascii=False,sort_keys=True)
        if encoded!=last:
          with (OUT/'ui-states.jsonl').open('a') as f:f.write(json.dumps({'wall_time':time.time(),'state':value},ensure_ascii=False)+'\n')
          (OUT/'ui-latest.json').write_text(json.dumps(value,ensure_ascii=False,indent=2));last=encoded
        if (OUT/'stop-review').exists():
          ns['addon'].unregister();bpy.ops.wm.quit_blender();return None
    except Exception:
      (OUT/'supplement-error.txt').write_text(traceback.format_exc());print(traceback.format_exc(),flush=True);return None
    return .5
bpy.app.timers.register(extra,first_interval=1.5)
