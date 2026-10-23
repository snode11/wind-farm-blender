"""Visible-window pitch animation, fixed Down framing and original demo return."""
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control, panel, clearance = [importlib.import_module(MODULE + '.' + name)
    for name in ('manual_control', 'panels.manual_control', 'clearance_replay')]
assert not bpy.app.background
assert Path(addon.__file__).resolve().parent == Path(os.environ.get(
    'WFRL_EXPECTED_ADDON_ROOT', ROOT / 'blender_frontend/wfrl_blender')).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
STATE = dict(step='setup', draws=0, draw_errors=[], captures=[])
original_draw = panel.WFRL_PT_ManualControl.draw


def draw(self, context):
    try:
        original_draw(self, context)
        if control.is_manual(context.scene):
            STATE['draws'] += 1
    except Exception:
        STATE['draw_errors'].append(traceback.format_exc())
        raise


panel.WFRL_PT_ManualControl.draw = draw


def op(action):
    assert bpy.ops.wfrl.manual_trial(action=action) == {'FINISHED'}


def snapshot(scene):
    return (scene.frame_current, clearance.sample(scene)['time_s'],
            tuple((obj.name, tuple(v for row in obj.matrix_world for v in row))
                  for obj in scene.objects if obj.name.startswith('WFRL.Turbine.')))


def capture(name):
    path = OUT / (name + '.png')
    assert bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}
    scene, entry = STATE['manual'], STATE['t1']
    STATE['captures'].append(dict(file=str(path), time_s=scene.wfrl_manual_trial.elapsed_s,
                                  azimuth_deg=entry.azimuth_deg,
                                  pitches=list(control.effective_pitches(entry)),
                                  camera=scene.camera.name))


def delay(step, seconds):
    STATE.update(step=step, deadline=time.monotonic() + seconds)
    return .05


def observe():
    try:
        if time.monotonic() < STATE.get('deadline', 0):
            return .05
        if STATE['step'] == 'setup':
            addon.register()
            addon.load_demo_scene(camera_rig=False)
            source = bpy.context.scene
            source.frame_set(121)
            bpy.ops.screen.animation_play()
            STATE['source'] = source
            return delay('enter', .4)
        window = bpy.context.window_manager.windows[0]
        area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            if STATE['step'] == 'enter':
                source_region = area.spaces.active.region_3d
                source_region.view_perspective = 'CAMERA'
                source_region.view_camera_zoom = 42
                source_region.view_camera_offset = (.12, -.07)
                STATE['source_camera_frame'] = (source_region.view_camera_zoom,
                                                tuple(source_region.view_camera_offset))
                assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
                scene = bpy.context.scene
                assert scene['wfrl_manual_blade_model'] == 'rootkink'
                t1 = next(e for e in scene.wfrl_manual_turbines if e.turbine_id == 'T1')
                t1.rpm, t1.azimuth_deg = 9, 148
                t1.individual_pitch = True
                for index, pitch in enumerate((0, 5, 10), 1):
                    setattr(t1, f'pitch_b{index}_deg', pitch)
                pending = scene.wfrl_manual_trial
                for index, target in enumerate((30, -15, 45), 1):
                    setattr(pending, f'select_b{index}', True)
                    setattr(pending, f'target_b{index}_deg', target)
                pending.duration_s = 8
                assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
                sidebar = next(r for r in area.regions if r.type == 'UI')
                sidebar.active_panel_category = '风机控制'
                area.spaces.active.show_region_ui = True
                STATE.update(manual=scene, t1=t1, source_held=snapshot(STATE['source']))
                return delay('confirm', 1.5)
            scene, t1 = STATE['manual'], STATE['t1']
            pending = scene.wfrl_manual_trial
            assert not STATE['draw_errors'], STATE['draw_errors']
            if STATE['step'] == 'confirm':
                op('CONFIRM')
                STATE['step'] = 'early'
                return .025
            if STATE['step'] in {'early', 'late'}:
                threshold = .6 if STATE['step'] == 'early' else 7.267
                if pending.elapsed_s < threshold:
                    return .01
                op('PAUSE')
                STATE['paused_pose'] = control.pose_snapshot(scene)
                STATE['paused_time'] = pending.elapsed_s
                assert snapshot(STATE['source']) == STATE['source_held']
                return delay(STATE['step'] + '_capture', .5)
            if STATE['step'] in {'early_capture', 'late_capture'}:
                assert control.pose_snapshot(scene) == STATE['paused_pose']
                assert pending.elapsed_s == STATE['paused_time']
                assert scene.camera.parent == t1.yaw_object
                capture(STATE['step'])
                if STATE['step'] == 'early_capture':
                    op('START')
                    STATE['step'] = 'late'
                    return .025
                op('RESET')
                assert not t1.running and pending.elapsed_s == 0
                assert abs(t1.azimuth_deg - 148) < 1e-4
                assert control.effective_pitches(t1) == (0, 5, 10)
                op('START')
                return delay('reset_running', .5)
            if STATE['step'] == 'reset_running':
                assert t1.running and pending.elapsed_s > .1
                op('PAUSE')
                before = control.pose_snapshot(scene)
                elapsed = pending.elapsed_s
                assert bpy.ops.wfrl.manual_focus() == {'FINISHED'}
                assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
                assert control.pose_snapshot(scene) == before and pending.elapsed_s == elapsed
                assert snapshot(STATE['source']) == STATE['source_held']
                assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
                assert window.scene == STATE['source'] and window.screen.is_animation_playing
                restored_region = area.spaces.active.region_3d
                assert restored_region.view_perspective == 'CAMERA'
                assert (restored_region.view_camera_zoom, tuple(restored_region.view_camera_offset)) == STATE['source_camera_frame']
                STATE['return_frame'] = STATE['source'].frame_current
                return delay('returned', .7)
            assert STATE['step'] == 'returned'
            assert STATE['source'].frame_current > STATE['return_frame']
            addon._cancel_playback(STATE['source'])
            assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
            scene = bpy.context.scene
            assert not t1.running
            op('RESET')
            op('END')
            assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
            area.spaces.active.show_region_ui = True
            next(r for r in area.regions if r.type == 'UI').active_panel_category = '风机控制'
            assert STATE['draws'] > 0
            report = dict(status='PASS', addon_module=MODULE, addon_file=addon.__file__,
                          blade_model=scene['wfrl_manual_blade_model'],
                          source_sha256={name: hashlib.sha256((Path(addon.__file__).parent / name).read_bytes()).hexdigest()
                                         for name in ('manual_motion.py', 'manual_control.py', 'manual_scene.py',
                                                      'cameras.py', 'panels/manual_control.py')},
                          blender_version=bpy.app.version_string, manual_panel_draws=STATE['draws'],
                          real_timer_shared_motion=True, pause_resume_and_reset=True,
                          down_same_geometry=True, camera_switch_preserves_time=True,
                          original_demo_pause_return_resume=True, captures=STATE['captures'],
                          original_camera_zoom_offset_restored=True,
                          boundary='Source visible window and scripted operators; no mouse-input, output, FPS or physical validation')
            (OUT / 'checks.json').write_text(json.dumps(report, indent=2))
            print('MANUAL_PITCH_WINDOW_PASS ' + json.dumps(report), flush=True)
            panel.WFRL_PT_ManualControl.draw = original_draw
            if os.environ.get('WFRL_QUIT_AFTER_TEST', '1') == '1':
                bpy.ops.wm.quit_blender()
            return None
    except Exception:
        report = dict(status='FAIL', error=traceback.format_exc(), step=STATE['step'],
                      addon_file=addon.__file__, draw_errors=STATE['draw_errors'],
                      context_mode=bpy.context.mode,
                      active_object_mode=bpy.context.object.mode if bpy.context.object else None)
        (OUT / 'checks.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        if os.environ.get('WFRL_QUIT_AFTER_TEST', '1') == '1':
            bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(observe, first_interval=1.0)
