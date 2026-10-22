"""Visible-window GPU, export, replay and lifecycle acceptance on local Blender.

WFRL_TEST_OUTPUT controls isolated evidence files. No source .blend is saved.
"""
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
from dataclasses import replace
from types import SimpleNamespace
import bpy
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
from wfrl_blender import custom_cameras as core, custom_camera_preview as preview
from wfrl_blender import custom_camera_capture as capture, camera_projection as projection
from wfrl_blender.panels import custom_cameras as panel
import wfrl_blender as addon
OUT=Path(os.environ.get('WFRL_TEST_OUTPUT','/tmp/wfrl-three-camera-window'));OUT.mkdir(parents=True,exist_ok=True)
STATE={'stage':0,'checks':{},'save_calls':0}


def saved(*_):STATE['save_calls']+=1


def event(kind,value='PRESS',x=140,y=150,shift=False):
    region=STATE['region']
    return SimpleNamespace(type=kind,value=value,mouse_x=region.x+x,mouse_y=region.y+y,
        alt=False,ctrl=False,shift=shift)


def start(mode,slot=1):
    bpy.context.window_manager.wfrl_custom_slot=slot
    assert bpy.ops.wfrl.custom_camera('INVOKE_DEFAULT',mode=mode)=={'RUNNING_MODAL'}
    return panel._ACTIVE


def action(name):
    assert bpy.ops.wfrl.custom_camera_action(action=name)=={'FINISHED'}


def tick():
    try:
        if STATE['stage']==0:
            if hasattr(bpy.context.preferences.filepaths,'use_save_on_exit'):
                bpy.context.preferences.filepaths.use_save_on_exit=False
            addon.register();addon.load_demo_scene(camera_rig=False)
            scene=bpy.context.scene;window=bpy.context.window
            area=next(a for a in window.screen.areas if a.type=='VIEW_3D')
            region=next(r for r in area.regions if r.type=='WINDOW')
            STATE.update(window=window,area=area,region=region)
            area.spaces.active.show_region_ui=True

            STATE['original']=(scene.camera,scene.render.resolution_x,scene.render.resolution_y,scene.render.pixel_aspect_x,scene.render.pixel_aspect_y)
            STATE['display_scale']=(bpy.context.preferences.system.ui_scale,bpy.context.preferences.system.pixel_size,bpy.context.preferences.system.dpi)
            STATE['screens']=tuple(a.as_pointer() for a in window.screen.areas)
            bpy.app.handlers.save_pre.append(saved)
            assert all(core.get_camera(scene,slot) is None for slot in core.SLOTS)
            scene.frame_set(755)
            STATE['stage']=1
            return 1.
        window,area,region=(STATE[k] for k in ('window','area','region'))
        with bpy.context.temp_override(window=window,area=area,region=region):
            scene=bpy.context.scene;wm=bpy.context.window_manager
            stage=STATE['stage']
            if stage==1:
                op=start('LAYOUT')
                assert bpy.ops.wfrl.custom_camera_action(action='SLOT',slot=3)=={'FINISHED'}
                assert op.slot==3 and op.camera is None
                # Install in the original editor; the removed offscreen quad
                # no longer owns card buttons or a three-image cache.
                def open_position(slot):
                    edit=start('PLACE',slot)
                    assert edit.stage=='PLACE' and edit.slot==slot
                    return edit
                edit=open_position(2)
                assert not edit.ready
                action('CANCEL')
                assert panel._ACTIVE.stage=='LAYOUT' and panel._ACTIVE.slot==2
                assert core.get_camera(scene,2) is None
                # Three independently selected physical mount points. Extreme
                # FOV/roll below remain test coverage, not a user demo layout.
                geometry=core._geometry(scene)[0]
                targets=[(0,-10,.2),(0,10,.2),(0,0,10),(10,0,.2)]
                optics=[(90,60,0),(40,100,37),(75,75,90),(100,50,180)]
                for slot,target,(h,v,roll) in zip(core.SLOTS,targets,optics):
                    before={n:core.parameters(core.get_camera(scene,n)) for n in core.SLOTS if core.get_camera(scene,n)}
                    edit=open_position(slot)
                    point,normal,*_=geometry[-1].find_nearest(Vector(target))
                    anchor=core.SurfaceAnchor(tuple(point),tuple(normal),.1,geometry[0])
                    core.place_on_surface(edit.camera,anchor,.1)
                    params=replace(core.parameters(edit.camera),yaw=180,pitch=-50,roll=roll,fov=h,vfov=v)
                    core.apply_parameters(edit.camera,params)
                    edit.ready=True;panel.sync_fields(wm,edit.camera)
                    action('NEXT');action('CONFIRM')
                    assert panel._ACTIVE.stage=='LAYOUT' and panel._ACTIVE.slot==slot
                    assert all(core.parameters(core.get_camera(scene,n))==p for n,p in before.items())
                assert len({core.parameters(core.get_camera(scene,n)).location for n in core.SLOTS})==3
                original=core.layout_dict(scene)
                edit=open_position(3)
                core.place_on_surface(edit.camera,core.anchor(core.get_camera(scene,1)),.2)
                action('CANCEL')
                assert core.layout_dict(scene)==original and panel._ACTIVE.stage=='LAYOUT'
                # Removing an empty slot preserves the external layout session.
                # Restoring all three camera parameters remains atomic.
                action('CLEAR')
                assert panel._ACTIVE.stage=='LAYOUT' and panel._ACTIVE.camera is None
                assert core.get_camera(scene,3) is None
                core.import_layout(scene,original,overwrite=True)
                panel._ACTIVE.select_slot(3)
                STATE['checks']['external_layout_three_independent_positions_cancel_clear']=True
                STATE['mount_positions']={str(n):core.parameters(core.get_camera(scene,n)).location for n in core.SLOTS}
                # Genuine GPU contexts, three distinct independent raster sizes.
                hidden_before={obj.name:obj.hide_get() for obj in scene.objects}
                begin=time.perf_counter()
                images=preview.render_group(bpy.context,core.enabled_cameras(scene),640)
                STATE['checks']['three_route_gpu_seconds']=time.perf_counter()-begin
                for slot,image in images.items():
                    capture.write_png(OUT/f'preview-C{slot}.png',image.width,image.height,image.rgba())
                    assert (image.width,image.height)==projection.resolution(image.params.fov,image.params.vfov,640)
                    image.free()
                assert {obj.name:obj.hide_get() for obj in scene.objects}==hidden_before
                op=start('LAYOUT');STATE['layout']=op
                STATE['stage']=2
                return 1.
            if stage==2:
                op=STATE['layout']
                assert op.stage=='LAYOUT' and not op.preview.images
                print('LAYOUT_WINDOW_READY',flush=True)
                STATE['checks']['external_layout_has_no_offscreen_images']=True
                # Native frame navigation does not change installation; N remains usable.
                before=core.layout_hash(core.layout_dict(scene))
                for key in ('WHEELUPMOUSE','NUMPAD_0','MIDDLEMOUSE'):
                    op.modal(bpy.context,event(key))
                assert core.layout_hash(core.layout_dict(scene))==before
                STATE['window_layout_hash']=before
                area.spaces.active.show_region_ui=False
                STATE['stage']=3
                return .4
            if stage==3:
                assert core.layout_hash(core.layout_dict(scene))==STATE['window_layout_hash']
                area.spaces.active.show_region_ui=True
                bpy.ops.screen.animation_play()
                STATE['stage']=4
                return .3
            if stage==4:
                op=start('EDIT');STATE['edit']=op;STATE['pause_frame']=scene.frame_current
                assert not window.screen.is_animation_playing
                before=core.parameters(op.camera)
                op.modal(bpy.context,event('WHEELUPMOUSE'))
                assert core.parameters(op.camera)==before
                wm.wfrl_custom_linked_zoom=True
                op.modal(bpy.context,event('WHEELUPMOUSE'))
                after=core.parameters(op.camera)
                assert after.fov!=before.fov and after.vfov!=before.vfov
                assert abs(projection.aspect(after.fov,after.vfov)-projection.aspect(before.fov,before.vfov))<1e-6
                op.modal(bpy.context,event('LEFTMOUSE'))
                op.modal(bpy.context,event('MOUSEMOVE',x=175,y=165,shift=True))
                op.modal(bpy.context,event('LEFTMOUSE','RELEASE',x=175,y=165))
                assert core.parameters(op.camera).location==before.location
                assert core.parameters(op.camera).yaw!=before.yaw
                action('CANCEL')
                assert window.screen.is_animation_playing
                bpy.ops.screen.animation_cancel(restore_frame=False)
                STATE['checks']['default_wheel_locked_linked_zoom_drag_cancel_resume']=True
                scene.frame_set(755)
                STATE['stage']=5
                return .5
            if stage==5:
                from wfrl_blender import charts,farm_flex,clearance_replay
                STATE['telemetry']=farm_flex._ACTIVE.last_telemetry_frame
                STATE['statistics']=json.dumps(clearance_replay.sample(scene)['statistics'],sort_keys=True)
                STATE['before_layout']=core.layout_dict(scene)
                STATE['history']=repr(charts.history.__dict__) if hasattr(charts,'history') else None
                STATE['frame']=(scene.frame_current,scene.frame_subframe)
                bpy.ops.screen.animation_play()
                transaction=capture.Capture(bpy.context,OUT)
                assert not window.screen.is_animation_playing
                assert transaction.step(bpy.context)
                assert window.screen.is_animation_playing
                bpy.ops.screen.animation_cancel(restore_frame=False)
                STATE['before_path']=str(transaction.path)
                manifest=json.loads((transaction.path/'manifest.json').read_text())
                assert manifest['status']=='complete'
                rows=[json.loads(r) for r in (transaction.path/'frames.jsonl').read_text().splitlines()]
                assert len(rows)==3 and len({r['time_s'] for r in rows})==1
                assert {max(r['image_width'],r['image_height']) for r in rows}=={1920}
                assert len({r['capture_id'] for r in rows})==1
                assert len({r['simulation_state_hash'] for r in rows})==1
                # Alter layout, export at identical sample, then restore exact layout.
                cam=core.get_camera(scene,1);params=core.parameters(cam)
                core.apply_parameters(cam,replace(params,yaw=170,fov=100))
                after=capture.Capture(bpy.context,OUT);assert after.step(bpy.context)
                STATE['after_path']=str(after.path)
                after_rows=[json.loads(r) for r in (after.path/'frames.jsonl').read_text().splitlines()]
                assert {max(r['image_width'],r['image_height']) for r in after_rows}=={1920}
                assert [r['time_s'] for r in after_rows]==[r['time_s'] for r in rows]
                assert [r['render_profile'] for r in after_rows]==[r['render_profile'] for r in rows]
                # Higher resolution is a separate export, not a changed control
                # in the before/after layout comparison.
                core.apply_parameters(cam,replace(core.parameters(cam),output_long_edge_px=2560))
                high=capture.Capture(bpy.context,OUT);assert high.step(bpy.context)
                STATE['high_resolution_path']=str(high.path)
                high_rows=[json.loads(r) for r in (high.path/'frames.jsonl').read_text().splitlines()]
                assert max(high_rows[0]['image_width'],high_rows[0]['image_height'])==2560
                core.import_layout(scene,STATE['before_layout'],overwrite=True)
                assert core.layout_hash(core.layout_dict(scene))==core.layout_hash(STATE['before_layout'])
                STATE['checks']['current_group_1920_and_2560_export_layout_restore']=True
                STATE['stage']=6
                return .5
            if stage==6:
                from wfrl_blender import farm_flex,clearance_replay,charts
                time_s=capture.current_time(scene)
                times=[time_s,time_s+.05,time_s+.1]
                transaction=capture.Capture(bpy.context,OUT,times)
                while not transaction.closed:transaction.step(bpy.context)
                STATE['sequence_path']=str(transaction.path)
                rows=[json.loads(r) for r in (transaction.path/'frames.jsonl').read_text().splitlines()]
                assert len(rows)==9
                assert [rows[i*3]['time_s'] for i in range(3)]==times
                assert len({rows[i*3]['simulation_state_hash'] for i in range(3)})==3
                for i in range(3):assert len({r['simulation_state_hash'] for r in rows[i*3:(i+1)*3]})==1
                assert (scene.frame_current,scene.frame_subframe)==STATE['frame']
                assert farm_flex._ACTIVE.last_telemetry_frame==STATE['telemetry']
                assert repr(charts.history.__dict__)==STATE['history']
                assert json.dumps(clearance_replay.sample(scene)['statistics'],sort_keys=True)==STATE['statistics']
                cancel=capture.Capture(bpy.context,OUT,times);cancel.step(bpy.context);cancel.finish(error='test cancellation')
                assert json.loads((cancel.path/'manifest.json').read_text())['status']=='incomplete'
                assert (scene.frame_current,scene.frame_subframe)==STATE['frame']
                STATE['checks']['sequence_same_times_statistics_cancel_restore']=True
                STATE['stage']=7
                return .5
            if stage==7:
                start('LAYOUT')
                STATE['stage']=8
                return .6
            if stage==8:
                print('EXTERNAL_WINDOW_READY',flush=True)
                assert tuple(a.as_pointer() for a in window.screen.areas)==STATE['screens']
                assert (scene.camera,scene.render.resolution_x,scene.render.resolution_y,scene.render.pixel_aspect_x,scene.render.pixel_aspect_y)==STATE['original']
                assert STATE['save_calls']==0
                if panel._ACTIVE:panel._ACTIVE.finish()
                addon.unregister()
                assert not capture.active() and not preview._RENDERERS
                assert not bpy.app.timers.is_registered(panel.lifecycle_watchdog)
                STATE['checks']['lifecycle_no_save_no_screen_or_output_changes']=True
                result={'status':'PASS','blender':bpy.app.version_string,'checks':STATE['checks'],'display_scale':STATE['display_scale'],'mount_positions':STATE['mount_positions'],
                    'before_path':STATE['before_path'],'after_path':STATE['after_path'],'sequence_path':STATE['sequence_path'],'high_resolution_path':STATE['high_resolution_path']}
                (OUT/'result.json').write_text(json.dumps(result,indent=2))
                print('THREE_CAMERAS_WINDOW_PASS',json.dumps(result),flush=True)
                bpy.ops.wm.quit_blender()
                return None
    except Exception:
        error=traceback.format_exc();print(error,flush=True)
        (OUT/'result.json').write_text(json.dumps({'status':'FAIL','stage':STATE['stage'],'error':error},indent=2))
        try:capture.shutdown()
        except Exception:pass
        bpy.ops.wm.quit_blender()
        return None
    return .2

bpy.app.timers.register(tick,first_interval=1.)
