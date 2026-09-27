"""Capture a chosen supported layout and inspect its native three-view window."""
from pathlib import Path
import json
import os
import sys
import traceback
import bpy
from mathutils import Vector, Matrix
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')), str(ROOT)]
import importlib
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
rig = importlib.import_module(MODULE + '.stacked_camera_rig')
core = importlib.import_module(MODULE + '.custom_cameras')
native = importlib.import_module(MODULE + '.native_camera_views')
capture = importlib.import_module(MODULE + '.custom_camera_capture')
OUT = Path(os.environ['WFRL_TEST_OUTPUT']); OUT.mkdir(parents=True, exist_ok=True)
state = {'phase': 0}
DEFAULT_ONLY = os.environ.get('WFRL_CHECK_DEFAULT_ONLY') == '1'

def tick():
    try:
        if state['phase'] == 0:
            addon.register(); addon.load_demo_scene()
            scene = bpy.context.scene; scene.frame_set(1)
            assert not scene.wfrl_flex_show_tip_trails, 'demo unexpectedly enabled tip trails'
            if DEFAULT_ONLY:
                # Verify the ordinary entry point itself; no external layout
                # import, restore, or annotation overrides may prepare this view.
                assert not scene.wfrl_deflection_visible, 'demo unexpectedly enabled deflection helpers'
                bundled = json.loads((Path(addon.__file__).parent/'assets/cameras/t1-three-camera-default.json').read_text())
                actual = core.layout_dict(scene)
                assert core.config_equal(actual['cameras'], bundled['cameras'])
                assert actual['rig_pose']['surface_mount'] == bundled['rig_pose']['surface_mount']
                assert actual['rig_pose']['surface_mount']['type'] == 'SUPPORT'
                assert actual['rig_pose']['surface_mount']['aim_deg'] == -30.
            else:
                scene.wfrl_flex_show_tip_trails = False
                scene.wfrl_deflection_visible = False
                importlib.import_module(MODULE + '.tip_tracking').disable(scene, 'three_camera_review')
                layout_path = Path(os.environ['WFRL_CAMERA_LAYOUT'])
                core.import_layout(scene, json.loads(layout_path.read_text()), overwrite=True)
                expected = core.layout_dict(scene)
                core.import_layout(scene, expected, overwrite=True)
                assert core.config_equal(expected, core.layout_dict(scene)), 'layout roundtrip changed cameras'
            bpy.context.view_layer.update()
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
        (OUT/'window-validation.json').write_text(json.dumps({'status':'PASS','module':MODULE,'blender':bpy.app.version_string,
            'capture_path':state['capture_path'], 'native_views':3, 'tip_trails':False, 'deflection_helpers':False,
            'layout_roundtrip':not DEFAULT_ONLY, 'ordinary_demo_load':DEFAULT_ONLY,
            'external_layout_import':not DEFAULT_ONLY, 'saved_scene':'static reference snapshot'}, indent=2))
        print('RECOMMENDED_WINDOW_PASS', flush=True)
        if os.environ.get('WFRL_TEST_QUIT') == '1': bpy.ops.wm.quit_blender()
    except Exception:
        (OUT/'window-error.txt').write_text(traceback.format_exc())
        print(traceback.format_exc(), flush=True)
        if os.environ.get('WFRL_TEST_QUIT') == '1': bpy.ops.wm.quit_blender()
    return None

bpy.app.timers.register(tick, first_interval=1.)
