"""Independent visible Blender playback/GUI evidence. Output directory required.

Run without --background, with --factory-startup and isolated BLENDER_USER_CONFIG.
Only this process is controlled; no preferences, extension install or solver.
"""
from pathlib import Path
import json
import math
import os
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
DELIVERY = json.loads((ROOT / 'dist/lidar-delivery.json').read_text())
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import bpy
from mathutils import Vector
import wfrl_blender as addon
from wfrl_blender import clearance_replay as playback
from wfrl_blender.panels.gimbal import WFRL_PT_Gimbal, camera_world_angles

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'blender-temp').mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'blender-temp')
if hasattr(bpy.context.preferences.filepaths, 'use_save_on_exit'):
    bpy.context.preferences.filepaths.use_save_on_exit = False
assert not bpy.app.background
bpy.context.preferences.view.show_splash = False
WFRL_PT_Gimbal.bl_category = 'Tool'
addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
scene.wfrl_show_wake = False
scene.render.fps = 25
scene.render.fps_base = 1
scene.sync_mode = 'FRAME_DROP'
scene.wfrl_clearance_normal_path = str(ROOT / DELIVERY['packages']['normal'])
scene.wfrl_clearance_near_tower_path = str(ROOT / DELIVERY['packages']['close'])
playback.load(scene, scene.wfrl_clearance_normal_path, 'normal')
bpy.ops.wfrl.clearance_view(view='MEASUREMENT')
camera = scene.camera
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        space = area.spaces.active
        space.use_local_camera = False
        space.camera = camera
        space.show_region_ui = True
        space.region_3d.view_perspective = 'CAMERA'
        space.shading.type = 'MATERIAL'
        space.overlay.show_overlays = False
        area.tag_redraw()

report = {'blender': bpy.app.version_string, 'native_gui': True, 'samples': [], 'checks': {}, 'errors': []}
phase = 'prepare'
started = time.monotonic()
phase_start = started
paused_sample = None
runs = {}

def persist():
    (OUT / 'report.json').write_text(json.dumps(report, indent=2))

def capture(name):
    path = OUT / (name + '.png')
    assert bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}
    report['checks'][name] = str(path)

def transition(name):
    global phase, phase_start
    phase, phase_start = name, time.monotonic()
    persist()

def sample_record():
    value = playback.sample(scene)
    return {'wall_s': time.monotonic(), 'frame': scene.frame_current,
            'simulation_s': value['time_s'], 'measurement': value['measurement'],
            'statistics': value['statistics'], 'playing': bpy.context.screen.is_animation_playing,
            'fps': scene.render.fps}

def tick():
    global paused_sample
    try:
        now = time.monotonic()
        if now - started > 75:
            raise AssertionError('GUI acceptance timed out')
        if phase == 'prepare':
            main = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            region = next((r for r in main.regions if r.type == 'UI'), None)
            category = region.active_panel_category if region else 'Tool'
            bpy.utils.unregister_class(WFRL_PT_Gimbal)
            WFRL_PT_Gimbal.bl_category = category or 'Tool'
            bpy.utils.register_class(WFRL_PT_Gimbal)
            # This dedicated process presents only the product's Camera panel.
            for panel in reversed(bpy.types.Panel.__subclasses__()):
                if (panel.__name__.startswith(('VIEW3D_PT_', 'WORKSPACE_PT_'))
                        and getattr(panel, 'bl_space_type', '') == 'VIEW_3D'
                        and getattr(panel, 'bl_region_type', '') == 'UI'
                        and getattr(panel, 'bl_category', '') == WFRL_PT_Gimbal.bl_category
                        and panel.is_registered):
                    bpy.utils.unregister_class(panel)
            reader = playback.reader_for(scene)
            row = next(r for r in reader.package.measurements if r['beams']['B2']['valid'])
            frame = 1 + (row['time_s'] - reader.start_s) * 25
            scene.frame_set(math.ceil(frame))
            transition('capture_normal')
        elif phase == 'capture_normal':
            capture('normal-paused')
            report['checks']['render_camera_angles'] = list(camera_world_angles(camera, bpy.context.evaluated_depsgraph_get()))
            scene.frame_set(1)
            scene.render.fps = 25
            assert bpy.ops.screen.animation_play() == {'FINISHED'}
            runs['normal_start'] = sample_record()
            transition('normal_play')
        elif phase == 'normal_play' and now - phase_start >= 1.5:
            runs['normal_end'] = sample_record()
            assert runs['normal_end']['playing'] and runs['normal_end']['frame'] > 1
            addon._cancel_playback()
            paused_sample = playback.sample(scene)
            transition('paused')
        elif phase == 'paused' and now - phase_start >= .5:
            assert playback.sample(scene) == paused_sample and not bpy.context.screen.is_animation_playing
            report['checks']['pause_freezes_measurement_age_and_stats'] = True
            # Changing actual playback FPS must leave the current frame's data intact.
            scene.render.fps = 50
            assert playback.sample(scene) == paused_sample
            scene.frame_set(1)
            assert bpy.ops.screen.animation_play() == {'FINISHED'}
            runs['fast_start'] = sample_record()
            transition('fast_play')
        elif phase == 'fast_play' and now - phase_start >= 1.5:
            runs['fast_end'] = sample_record()
            assert runs['fast_end']['playing'] and runs['fast_end']['frame'] > 1
            addon._cancel_playback()
            for name in ('normal', 'fast'):
                a, b = runs[name + '_start'], runs[name + '_end']
                runs[name + '_sim_seconds_per_wall_second'] = ((b['simulation_s'] - a['simulation_s']) / (b['wall_s'] - a['wall_s']))
            ratio = runs['fast_sim_seconds_per_wall_second'] / runs['normal_sim_seconds_per_wall_second']
            assert 1.4 < ratio < 2.7, ('Actual playback speed ratio', ratio, runs)
            report['checks']['actual_playback_2x_ratio'] = ratio
            report['samples'] = runs
            scene.frame_set(scene.frame_end)
            assert playback.sample(scene)['statistics'] == playback.reader_for(scene).package.statistics
            report['checks']['full_clip_end_after_speed_change'] = True
            assert bpy.ops.wfrl.clearance_clip(demo='near_tower') == {'FINISHED'}
            assert bpy.context.screen.is_animation_playing and scene.frame_current == 1
            transition('close_play')
        elif phase == 'close_play' and now - phase_start >= .5:
            assert scene.frame_current > 1
            addon._cancel_playback()
            reader = playback.reader_for(scene)
            row = next(r for r in reader.package.measurements if r['beams']['B2']['valid'])
            scene.frame_set(math.ceil(1 + (row['time_s'] - reader.start_s) * scene['wfrl_clearance_timebase_fps']))
            transition('capture_close')
        elif phase == 'capture_close':
            assert playback.sample(scene)['status'] == 'near_threshold'
            capture('close-paused')
            report['checks']['demo_button_switches_and_starts_playback'] = True
            scene.frame_set(1)
            assert playback.sample(scene)['measurement'] is None
            assert playback.sample(scene)['status'] == 'waiting'
            transition('capture_waiting')
        elif phase == 'capture_waiting':
            capture('waiting-gray')
            scene.wfrl_clearance_show_details = True
            scene.wfrl_clearance_show_config = True
            transition('capture_footer')
        elif phase == 'capture_footer':
            capture('footer-scientific-disclaimers')
            scene.wfrl_clearance_show_details = False
            scene.wfrl_clearance_show_config = False
            frozen = playback.sample(scene)
            from wfrl_blender.panels import gimbal
            main = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in main.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=bpy.context.window, area=main, region=region):
                assert bpy.ops.wfrl.gimbal_mode('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
                assert gimbal._ACTIVE is not None
            assert bpy.ops.wfrl.clearance_view(view='WORLD') == {'FINISHED'}
            assert playback.sample(scene) == frozen and not bpy.context.screen.is_animation_playing
            assert gimbal._ACTIVE is None
            report['checks']['active_gimbal_exits_on_overview'] = True
            report['checks']['overview_preserves_paused_replay'] = True
            transition('capture_overview')
        elif phase == 'capture_overview':
            capture('overview-same-replay')
            assert bpy.ops.wfrl.clearance_view(view='MEASUREMENT') == {'FINISHED'}
            assert bpy.ops.wfrl.clearance_restart() == {'FINISHED'}
            assert scene.frame_current == 1 and bpy.context.screen.is_animation_playing
            report['checks']['restart_resets_and_plays'] = True
            addon._cancel_playback()
            from types import SimpleNamespace
            from wfrl_blender.panels.clearance import WFRL_OT_ClearanceClip
            scene.wfrl_clearance_normal_path = str(OUT / 'missing-package')
            errors = []
            op = SimpleNamespace(demo='normal', report=lambda level, msg: errors.append(msg))
            assert WFRL_OT_ClearanceClip.execute(op, bpy.context) == {'CANCELLED'}
            assert playback.sample(scene) is None and scene.wfrl_clearance_show_config
            assert errors and not bpy.context.screen.is_animation_playing
            transition('capture_error')
        elif phase == 'capture_error':
            capture('missing-package-recovery')
            scene.wfrl_clearance_normal_path = str(ROOT / DELIVERY['packages']['normal'])
            assert bpy.ops.wfrl.clearance_clip(demo='normal') == {'FINISHED'}
            assert not scene.wfrl_clearance_show_config and scene.frame_current == 1
            addon._cancel_playback()
            report['checks']['missing_package_exposes_config_and_recovers'] = True
            report['passed'] = True
            persist()
            print('CLEARANCE_GUI_ACCEPTANCE_PASS', flush=True)
            bpy.ops.wm.quit_blender()
            return None
        persist()
        return .1
    except Exception:
        report['errors'].append(traceback.format_exc())
        report['passed'] = False
        persist()
        print(report['errors'][-1], flush=True)
        addon._cancel_playback()
        bpy.ops.wm.quit_blender()
        return None

bpy.app.timers.register(tick, first_interval=2)
