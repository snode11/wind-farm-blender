"""Visible native player ownership across manual-scene round trips."""
import importlib
import json
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
control = importlib.import_module(MODULE + '.manual_control')
playback = importlib.import_module(MODULE + '.playback')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists()
OUT.mkdir(parents=True)
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
STATE = dict(step='setup', deadline=0)


def delay(step):
    STATE.update(step=step, deadline=time.monotonic() + .8)
    return .1


def observe():
    try:
        if STATE['step'] == 'capture':
            return None
        if time.monotonic() < STATE['deadline']:
            return .1
        if STATE['step'] == 'setup':
            assert not bpy.app.background
            addon.register()
            addon.load_demo_scene(camera_rig=False)
            source = bpy.context.scene
            source.frame_set(121)
            area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            STATE.update(source=source, window=bpy.context.window, area=area, region=region)
            with bpy.context.temp_override(window=STATE['window'], area=area, region=region):
                assert bpy.ops.screen.animation_play() == {'FINISHED'}
            return delay('enter')
        window, area, region, source = [STATE[k] for k in ('window', 'area', 'region', 'source')]
        with bpy.context.temp_override(window=window, area=area, region=region):
            if STATE['step'] == 'enter':
                assert source.frame_current > 121 and playback.is_playing(source)
                assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
                assert not playback.is_playing(source)
                manual = window.scene
                assert control.validate_scene(manual)[0]
                STATE.update(manual=manual, held_frame=source.frame_current)
                manual.wfrl_manual_target = 'ALL'
                manual.wfrl_manual_batch.rpm = 8
                assert bpy.ops.wfrl.manual_action(action='START') == {'FINISHED'}
                return delay('leave')
            manual = STATE['manual']
            if STATE['step'] == 'leave':
                assert source.frame_current == STATE['held_frame']
                assert all(e.running and e.azimuth_deg > 10 for e in manual.wfrl_manual_turbines)
                assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
                assert window.scene == source and not any(e.running for e in manual.wfrl_manual_turbines)
                return delay('resume')
            if STATE['step'] == 'resume':
                assert source.frame_current > STATE['held_frame'] and playback.is_playing(source)
                addon._cancel_playback(source)
                assert not playback.is_playing(source)
                STATE['paused_frame'] = source.frame_current
                assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
                assert window.scene == manual and not any(e.running for e in manual.wfrl_manual_turbines)
                assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
                return delay('paused')
            assert STATE['step'] == 'paused'
            assert source.frame_current == STATE['paused_frame'] and not playback.is_playing(source)
            assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
            assert window.scene == manual
            assert area.spaces.active.shading.type == 'MATERIAL'
            STATE['step'] = 'capture'
            STATE['deadline'] = time.monotonic() + 1
            return .1
    except Exception:
        report = dict(status='FAIL', step=STATE['step'], error=traceback.format_exc(), addon_file=addon.__file__)
        (OUT / 'checks.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        bpy.ops.wm.quit_blender()
        return None


def capture():
    if STATE['step'] != 'capture' or time.monotonic() < STATE['deadline']:
        return .2
    bpy.ops.screen.screenshot(filepath=str(OUT / 'manual-environment.png'))
    report = dict(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
                  demo_paused_on_enter=True, demo_resumed_on_return=True,
                  paused_demo_stays_paused=True, manual_motion=True, manual_reentry_paused=True,
                  scope='Actual native window and scripted operators; does not claim mouse input or sidebar selection')
    (OUT / 'checks.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(observe, first_interval=1)
bpy.app.timers.register(capture, first_interval=1)
