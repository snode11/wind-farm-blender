"""Actual-window manual sidebar, timer motion, and demo return checks."""
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
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
panel = importlib.import_module(MODULE + '.panels.manual_control')
clearance = importlib.import_module(MODULE + '.clearance_replay')
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)
STATE = dict(step='setup', draw_count=0, manual_draw_count=0, draw_errors=[])
original_draw = panel.WFRL_PT_ManualControl.draw


def traced_draw(self, context):
    STATE['draw_count'] += 1
    if control.is_manual(context.scene):
        STATE['manual_draw_count'] += 1
    try:
        original_draw(self, context)
    except Exception:
        STATE['draw_errors'].append(traceback.format_exc())
        raise


panel.WFRL_PT_ManualControl.draw = traced_draw


def action(value):
    assert bpy.ops.wfrl.manual_action(action=value) == {'FINISHED'}, value


def entries(scene):
    return {entry.turbine_id: entry for entry in scene.wfrl_manual_turbines}


def mesh_digest(obj):
    values = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get('co', values)
    return hashlib.sha256(values.tobytes()).hexdigest()


def source_snapshot(scene):
    return (scene.frame_current, clearance.sample(scene)['time_s'],
            tuple((obj.name, tuple(v for row in obj.matrix_world for v in row))
                  for obj in scene.objects if obj.name.startswith('WFRL.Turbine.')),
            mesh_digest(scene.objects['WFRL.Turbine.T1.Blade1']))


def show_panel(area):
    area.spaces.active.show_region_ui = True
    area.tag_redraw()


def delay(step, seconds):
    STATE.update(step=step, deadline=time.monotonic() + seconds)
    return .1


def finish_failure():
    error = traceback.format_exc()
    report = dict(status='FAIL', addon_module=MODULE, addon_file=addon.__file__,
                  step=STATE['step'], error=error, draw_errors=STATE['draw_errors'])
    (OUT / 'window-checks.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('MANUAL_CONTROL_WINDOW_FAIL ' + json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def observe():
    try:
        if STATE['step'] == 'setup':
            assert not bpy.app.background, 'This check requires a visible Blender window'
            if hasattr(bpy.context.preferences.filepaths, 'use_save_on_exit'):
                bpy.context.preferences.filepaths.use_save_on_exit = False
            addon.register()
            addon.load_demo_scene(camera_rig=False)
            source, window = bpy.context.scene, bpy.context.window
            area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
            region = next(region for region in area.regions if region.type == 'WINDOW')
            source.frame_set(121)
            STATE.update(source=source, window=window, area=area, region=region)
            show_panel(area)
            STATE.update(step='select_panel', deadline=time.monotonic() + 180.0)
            print('MANUAL_CONTROL_WINDOW_WAITING_FOR_PANEL 风机控制', flush=True)
            return .2
        if STATE['step'] == 'select_panel':
            window, area, region = [STATE[name] for name in ('window', 'area', 'region')]
            selected = any(item.type == 'UI' and
                           item.active_panel_category == panel.WFRL_PT_ManualControl.bl_category
                           for item in area.regions)
            if not selected:
                assert time.monotonic() < STATE['deadline'], 'Timed out waiting for the 风机控制 sidebar tab'
                return .2
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.screen.animation_play() == {'FINISHED'}
            return delay('enter', .75)
        if time.monotonic() < STATE['deadline']:
            return .1
        window, area, region, source = [STATE[name] for name in ('window', 'area', 'region', 'source')]
        assert not STATE['draw_errors'], STATE['draw_errors']
        with bpy.context.temp_override(window=window, area=area, region=region):
            if STATE['step'] == 'enter':
                assert source.frame_current > 121
                assert window.screen.is_animation_playing
                assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
                manual = window.scene
                assert control.is_manual(manual) and manual != source
                assert not window.screen.is_animation_playing
                assert bpy.app.timers.is_registered(control._timer)
                assert control.validate_scene(manual)[0]
                STATE.update(manual=manual, source_held=source_snapshot(source),
                             source_reader=clearance.reader_for(source))
                manual.wfrl_manual_target = 'T1'
                t1 = entries(manual)['T1']
                t1.yaw_deg, t1.pitch_deg, t1.rpm, t1.azimuth_deg = 33.0, 14.0, 6.0, 20.0
                assert bpy.ops.wfrl.manual_focus() == {'FINISHED'}
                show_panel(area)
                action('START')
                STATE.update(start_angle=t1.azimuth_deg, start_time=time.monotonic())
                return delay('running', 2.0)

            manual = STATE['manual']
            t1 = entries(manual)['T1']
            if STATE['step'] == 'running':
                elapsed = time.monotonic() - STATE['start_time']
                delta = (t1.azimuth_deg - STATE['start_angle']) % 360.0
                assert delta > 20.0, (delta, elapsed)
                expected_delta = (elapsed * 36.0) % 360.0
                circular_error = abs((delta - expected_delta + 180.0) % 360.0 - 180.0)
                assert circular_error < 12.0, (delta, expected_delta, circular_error)
                assert abs(math.degrees(t1.rotor_object.rotation_euler.x) - t1.azimuth_deg) < .001
                assert source_snapshot(source) == STATE['source_held']
                assert clearance.reader_for(source) is STATE['source_reader']
                before = t1.azimuth_deg
                t1.rpm = 12.0
                assert (t1.azimuth_deg - before) % 360.0 < 12.0
                STATE.update(first_motion_deg=delta, phase_before_speed_change=before,
                             faster_start=t1.azimuth_deg)
                return delay('pause', .8)

            if STATE['step'] == 'pause':
                assert (t1.azimuth_deg - STATE['faster_start']) % 360.0 > 25.0
                action('PAUSE')
                assert not t1.running and t1.rpm == 12.0
                STATE['paused_angle'] = t1.azimuth_deg
                return delay('paused', .6)

            if STATE['step'] == 'paused':
                assert t1.azimuth_deg == STATE['paused_angle']
                assert source_snapshot(source) == STATE['source_held']
                t1.individual_pitch = True
                t1.pitch_b1_deg, t1.pitch_b2_deg, t1.pitch_b3_deg = 3.0, 9.0, 21.0
                for index, value in enumerate((3, 9, 21), 1):
                    actual = math.degrees(getattr(t1, f'blade{index}_object').rotation_euler.z)
                    assert abs(actual + value) < .001
                action('START')
                assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
                assert window.scene == source
                assert not t1.running
                assert window.screen.is_animation_playing
                STATE.update(return_frame=source.frame_current, held_manual_angle=t1.azimuth_deg)
                return delay('return', .8)

            if STATE['step'] == 'return':
                assert source.frame_current > STATE['return_frame']
                assert t1.azimuth_deg == STATE['held_manual_angle']
                addon._cancel_playback(source)
                assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
                assert window.scene == manual
                assert not t1.running and t1.rpm == 12.0
                assert (t1.yaw_deg, t1.pitch_b1_deg, t1.pitch_b2_deg, t1.pitch_b3_deg) == (33.0, 3.0, 9.0, 21.0)
                manual.wfrl_manual_target = 'ALL'
                manual.wfrl_manual_batch.rpm = 8.0
                manual.wfrl_manual_batch.yaw_deg = -20.0
                assert all(entry.rpm == 8.0 and entry.yaw_deg == -20.0
                           for entry in entries(manual).values())
                STATE['all_angles'] = {tid: entry.azimuth_deg for tid, entry in entries(manual).items()}
                action('START')
                show_panel(area)
                return delay('all_running', .8)

            if STATE['step'] == 'all_running':
                for tid, entry in entries(manual).items():
                    assert entry.running and (entry.azimuth_deg - STATE['all_angles'][tid]) % 360.0 > 15.0
                action('STOP')
                assert all(entry.rpm == 0.0 and not entry.running for entry in entries(manual).values())
                manual.wfrl_manual_target = 'T1'
                action('RESET')
                assert (t1.yaw_deg, t1.pitch_deg, t1.azimuth_deg, t1.rpm, t1.running) == (0.0, 0.0, 0.0, 9.0, False)
                t1.yaw_deg, t1.pitch_deg, t1.azimuth_deg = 28.0, 18.0, 72.0
                assert bpy.ops.wfrl.manual_focus() == {'FINISHED'}
                show_panel(area)
                return delay('capture', .6)

            assert STATE['step'] == 'capture'
            assert STATE['draw_count'] > 0 and STATE['manual_draw_count'] > 0, STATE
            screenshot = OUT / 'manual-control-panel.png'
            assert bpy.ops.screen.screenshot(filepath=str(screenshot)) == {'FINISHED'}
            assert screenshot.is_file() and screenshot.stat().st_size > 1000
            report = dict(status='PASS', scope='Actual native window, scripted operators and real timer',
                          addon_module=MODULE, addon_file=addon.__file__,
                          manual_panel_draws=STATE['manual_draw_count'],
                          real_timer_motion_deg=STATE['first_motion_deg'],
                          rpm_phase_continuity=True, pause_stop_reset=True,
                          individual_pitch=True, all_target_motion=True,
                          source_unchanged_during_manual=True,
                          demo_pause_and_resume=True, manual_paused_on_return=True,
                          screenshot=str(screenshot),
                          boundary='Short interaction check; no full-clip performance or physics validation')
            (OUT / 'window-checks.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print('MANUAL_CONTROL_WINDOW_PASS ' + json.dumps(report), flush=True)
            if os.environ.get('WFRL_QUIT_AFTER_TEST', '1') == '1':
                bpy.ops.wm.quit_blender()
            return None
    except Exception:
        return finish_failure()


bpy.app.timers.register(observe, first_interval=1.0)
