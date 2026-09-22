"""Visible-window regression for leaving the custom-camera overlay."""
import json
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import custom_cameras as core, custom_camera_preview as preview
from wfrl_blender.panels import custom_cameras as panel

OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-camera-exit'))
OUT.mkdir(parents=True, exist_ok=True)
state = {'checks': [], 'stage': 0}


def tick():
    try:
        if state['stage'] == 0:
            addon.register()
            addon.load_demo_scene()
            state['stage'] = 1
            return 1.
        window = bpy.context.window
        area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            scene, wm = bpy.context.scene, bpy.context.window_manager
            if window.screen.is_animation_playing:
                bpy.ops.screen.animation_cancel(restore_frame=False)
            stamp = (scene.frame_current, scene.frame_subframe, scene['wfrl_clearance_time_s'])
            def start(mode='WATCH', slot=1):
                wm.wfrl_custom_slot = slot
                assert bpy.ops.wfrl.custom_camera('INVOKE_DEFAULT', mode=mode) == {'RUNNING_MODAL'}
                return panel._ACTIVE
            def gone(op):
                assert panel._ACTIVE is None and op.closed
                assert op.timer is None and op.handle is None and not op.preview.images
                assert op.preview not in preview._RENDERERS
                assert (scene.frame_current, scene.frame_subframe, scene['wfrl_clearance_time_s']) == stamp
            def event(kind, x=100, y=100):
                return SimpleNamespace(type=kind,value='PRESS',mouse_x=region.x+x,mouse_y=region.y+y,
                                       alt=False,ctrl=False,shift=False)
            assert panel._ACTIVE is None
            assert all(core.get_camera(scene, n) is None for n in core.SLOTS)
            geometry = core._geometry(scene)[0]
            point, normal, *_ = geometry[-1].find_nearest(Vector((0,-10,.2)))
            draft = core.begin_draft(scene,1)
            core.place_on_surface(draft,core.SurfaceAnchor(tuple(point),tuple(normal),.1,geometry[0]),.1)
            camera = core.commit_draft(scene,1,draft)
            assert bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='FRONT') == {'FINISHED'}
            target = scene.camera
            op = start()
            ui = max(1., bpy.context.preferences.system.ui_scale)
            x,y,w,h = preview.close_button(preview.available_rectangle(area,region),ui)
            assert op.modal(bpy.context,event('LEFTMOUSE',x+w/2,y+h/2)) == {'FINISHED'}
            gone(op)
            assert area.spaces.active.camera == target
            state['checks'].append('visible_close_button_restores_view_and_cleans_resources')
            op = start()
            op.modal(bpy.context,event('ESC'))
            gone(op)
            state['checks'].append('escape_closes_single_preview')
            for tid,angle in [('T1','DOWN'),('T1','FRONT'),('all','DOWN'),('T1','NACELLE'),('T1','NACELLE_SIDE')]:
                op = start()
                assert bpy.ops.wfrl.farm_flex_view(turbine=tid,angle=angle) == {'FINISHED'}
                gone(op)
                assert area.spaces.active.camera == scene.camera
                assert area.spaces.active.region_3d.view_perspective == 'CAMERA'
                expected = 'WFRL.Camera.FarmFlexOverview' if tid=='all' else 'WFRL.Camera.T1.FrontQuarter' if angle=='FRONT' else 'WFRL.Camera.T1.NacelleGimbal' if angle.startswith('NACELLE') else 'WFRL.Camera.T1.Gimbal'
                assert scene.camera.name == expected, (angle,scene.camera.name)
                state['checks'].append(f'single_preview_to_{tid}_{angle}')
            for name in ('Top','Side','World'):
                op = start()
                assert bpy.ops.wfrl.select_camera(camera_name=f'WFRL.Camera.{name}') == {'FINISHED'}
                gone(op)
                state['checks'].append(f'single_preview_to_{name}')
            op = start()
            assert bpy.ops.wfrl.gimbal_preset(preset='FRONT') == {'FINISHED'}
            gone(op)
            assert area.spaces.active.use_local_camera
            state['checks'].append('single_preview_to_gimbal')
            op = start()
            assert bpy.ops.wfrl.select_camera(camera_name='missing-camera') == {'CANCELLED'}
            assert panel._ACTIVE == op
            assert bpy.ops.wfrl.farm_flex_view(turbine='T9') == {'CANCELLED'}
            assert panel._ACTIVE == op
            state['checks'].append('invalid_target_keeps_single_preview')
            op.finish()
            before = core.parameters(camera)
            op = start('WATCH')
            assert bpy.ops.wfrl.farm_flex_view(turbine='T1',angle='FRONT') == {'FINISHED'}
            gone(op)
            bpy.ops.screen.animation_play()
            op = start('EDIT')
            assert not window.screen.is_animation_playing
            op.change(dx=12,dy=4)
            assert bpy.ops.wfrl.farm_flex_view(turbine='T1',angle='DOWN') == {'FINISHED'}
            gone(op)
            assert window.screen.is_animation_playing
            assert core.parameters(core.get_camera(scene,1)) == before
            assert not any(obj.get('wfrl_custom_draft') for obj in scene.objects)
            assert area.spaces.active.camera == scene.camera
            state['checks'].append('switch_cancels_draft_preserves_camera_restores_playback')
            bpy.ops.screen.animation_cancel(restore_frame=False)
            addon.unregister()
        result = {'status':'PASS','blender':bpy.app.version_string,'checks':state['checks']}
        (OUT/'result.json').write_text(json.dumps(result,indent=2))
        print('CUSTOM_CAMERA_EXIT_PASS',json.dumps(result),flush=True)
    except Exception:
        error = traceback.format_exc()
        print(error,flush=True)
        (OUT/'result.json').write_text(json.dumps({'status':'FAIL','checks':state['checks'],'error':error},indent=2))
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick,first_interval=1.)
