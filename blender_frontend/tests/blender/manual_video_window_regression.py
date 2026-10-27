"""Visible manual video progress, completed output and cancellation controls."""
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
from wfrl_blender import manual_control as control, manual_video as video
from wfrl_blender.panels import manual_control as panel

assert not bpy.app.background
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
STATE = dict(draws=0, draw_errors=[], started=False, progress_capture=False,
             deadline=time.monotonic() + 90)
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


def observe():
    try:
        assert time.monotonic() < STATE['deadline'], 'Video window test timed out'
        assert not STATE['draw_errors'], STATE['draw_errors']
        window = bpy.context.window_manager.windows[0]
        area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            if not STATE['started']:
                addon.register()
                assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
                scene = bpy.context.scene
                scene.wfrl_manual_trial.duration_s = .08
                scene.wfrl_manual_video_hold_s = .04
                assert bpy.ops.wfrl.manual_trial(action='CONFIRM') == {'FINISHED'}
                assert bpy.ops.wfrl.manual_trial(action='PAUSE') == {'FINISHED'}
                assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
                area.spaces.active.show_region_ui = True
                STATE.update(started=True, pose=control.pose_snapshot(scene))
                return .3
            scene = bpy.context.scene
            sidebar = next(r for r in area.regions if r.type == 'UI')
            sidebar.active_panel_category = '风机控制'
            if 'job' not in STATE:
                assert bpy.ops.wfrl.manual_video(filepath=str(OUT / 'visible-output.mp4')) == {'FINISHED'}
                STATE['job'] = scene['wfrl_manual_video_job']
                return .1
            if video.active(scene):
                if not STATE['progress_capture'] and STATE['draws'] > 0:
                    assert bpy.ops.screen.screenshot(filepath=str(OUT / 'video-progress.png')) == {'FINISHED'}
                    STATE['progress_capture'] = True
                return .1
            record = video.status(scene)
            if not STATE.get('cancelling'):
                assert record.get('state') == 'COMPLETE', record
                assert STATE['progress_capture'] and STATE['draws'] > 0
                assert Path(record['output_mp4']).is_file()
                assert control.pose_snapshot(scene) == STATE['pose']
                if 'completed' not in STATE:
                    STATE['completed'] = record
                    return .3
                assert bpy.ops.screen.screenshot(filepath=str(OUT / 'video-complete.png')) == {'FINISHED'}
                assert bpy.ops.wfrl.manual_video(filepath=str(OUT / 'cancelled.mp4')) == {'FINISHED'}
                started = time.monotonic()
                assert bpy.ops.wfrl.manual_video_cancel() == {'FINISHED'}
                STATE['cancel_call_s'] = time.monotonic() - started
                assert STATE['cancel_call_s'] < .2, STATE['cancel_call_s']
                STATE['cancelling'] = True
                return .05
            assert record['state'] == 'CANCELLED' and record['final'], record
            assert not (OUT / 'cancelled.mp4').exists()
            video._poll()
            report = dict(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
                          panel_draws=STATE['draws'], progress_visible=True, complete_visible=True,
                          cancel_call_s=STATE['cancel_call_s'],
                          scripted_product_export_and_cancel=True, source_pose_preserved=True,
                          completed=STATE['completed'], job=STATE['job'],
                          boundary='Source visible window and scripted operators; no file-picker or mouse-input validation')
            (OUT / 'checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print('MANUAL_VIDEO_WINDOW_PASS ' + json.dumps(report), flush=True)
    except Exception:
        report = dict(status='FAILED', error=traceback.format_exc(), addon_file=addon.__file__,
                      draw_errors=STATE['draw_errors'])
        (OUT / 'checks.json').write_text(json.dumps(report, indent=2))
        print('MANUAL_VIDEO_WINDOW_FAILED ' + json.dumps(report), flush=True)
    panel.WFRL_PT_ManualControl.draw = original_draw
    if control.is_manual(bpy.context.scene):
        video.cancel(bpy.context.scene)
    addon.unregister()
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(observe, first_interval=1.0)
