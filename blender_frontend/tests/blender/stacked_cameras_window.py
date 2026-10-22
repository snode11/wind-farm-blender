"""Visible-window three-camera capture and housing inspection evidence."""
from pathlib import Path
import json
import os
import sys
import traceback
import bpy
from mathutils import Vector, Matrix
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import stacked_camera_rig as rig, custom_cameras as core
from wfrl_blender import native_camera_views as native, custom_camera_capture as capture
OUT = Path(os.environ['WFRL_TEST_OUTPUT']); OUT.mkdir(parents=True, exist_ok=True)
state = {'phase': 0}

def tick():
    try:
        if state['phase'] == 0:
            addon.register(); addon.load_demo_scene()
            scene = bpy.context.scene; scene.frame_set(1)
            cams = core.enabled_cameras(scene)
            assembly = scene.objects[rig.PREFIX]
            scene.camera = cams[1]
            scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
            area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            state.update(area=area, region=region, scene=scene, assembly=assembly)
            area.spaces.active.show_region_ui = True
            area.spaces.active.region_3d.view_perspective = 'CAMERA'
            area.spaces.active.shading.type = 'MATERIAL'
            for r in area.regions:
                if r.type == 'UI':
                    try: r.active_panel_category = 'View'
                    except AttributeError: pass
            (OUT/'three-stacked-cameras.json').write_text(json.dumps(core.layout_dict(scene), ensure_ascii=False, indent=2))
            (OUT/'housing-assumptions.json').write_text(assembly['assumptions_json'])
            bpy.context.preferences.filepaths.save_version = 0
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'three-stacked-cameras.blend'))
            state['phase'] = 1
            return 2.
        if state['phase'] == 1:
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            bpy.ops.screen.screenshot(filepath=str(OUT/'box-controls.png'))
            with bpy.context.temp_override(area=state['area'], region=state['region']):
                job = capture.Capture(bpy.context, OUT)
                try:
                    assert job.step(bpy.context)
                except Exception as exc:
                    job.finish(error=str(exc)); raise
                assert job.manifest['status'] == 'complete', job.manifest
                assert job.manifest['active_camera_ids'] == ['C1', 'C2', 'C3']
                state['capture_path'] = str(job.path)
                bpy.ops.wfrl.native_camera_view(mode='TRIPLE')
            state['phase'] = 2
            return 3.
        if state['phase'] == 2:
            if native._ACTIVE is None or len(native._ACTIVE.entries) != 3:
                raise AssertionError('Three-view window did not initialize')
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            with bpy.context.temp_override(window=native._ACTIVE.window):
                bpy.ops.screen.screenshot(filepath=str(OUT/'three-views.png'))
            native.shutdown()
            area = state['area']; space = area.spaces.active
            space.show_region_ui = False; space.overlay.show_overlays = False
            space.clip_start = .001
            space.lock_camera = False; space.lock_object = None
            space.region_3d.view_perspective = 'PERSP'
            root = state['scene'].objects[core.ROOT_NAME]
            assembly = state['assembly']
            point = assembly.matrix_world.translation.copy()
            local_eye = Vector((-.32, -.46, .16))
            eye = point + assembly.matrix_world.to_3x3() @ local_eye
            space.region_3d.view_rotation = (point-eye).to_track_quat('-Z', 'Y')
            space.region_3d.view_location = point
            space.region_3d.view_distance = .62
            space.region_3d.view_perspective = 'PERSP'
            state['phase'] = 3
            return 3.
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT/'housing-window.png'))
        (OUT/'window-validation.json').write_text(json.dumps({'status':'PASS','blender':bpy.app.version_string,
            'capture_path':state['capture_path'], 'native_views':3, 'saved_scene':'static reference snapshot'}, indent=2))
        print('STACKED_WINDOW_PASS', flush=True)
        if os.environ.get('WFRL_TEST_QUIT') == '1': bpy.ops.wm.quit_blender()
    except Exception:
        (OUT/'window-error.txt').write_text(traceback.format_exc())
        print(traceback.format_exc(), flush=True)
        if os.environ.get('WFRL_TEST_QUIT') == '1': bpy.ops.wm.quit_blender()
    return None

bpy.app.timers.register(tick, first_interval=1.)
