"""Actual-window Down view, live pitch ramp and interactive Down MP4 export."""
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
down = importlib.import_module(MODULE + '.manual_down')
jobs = importlib.import_module(MODULE + '.manual_down_jobs')
rendering = importlib.import_module(MODULE + '.manual_down_render')
panel = importlib.import_module(MODULE + '.panels.manual_control')
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)
STATE = dict(step='setup', draw_errors=[], ramp=[])
original_draw = panel.WFRL_PT_ManualControl.draw


def traced_draw(self, context):
    try:
        original_draw(self, context)
    except Exception:
        STATE['draw_errors'].append(traceback.format_exc())
        raise


panel.WFRL_PT_ManualControl.draw = traced_draw
addon.register()


def view3d():
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return window, area, region


def finish(status, **extra):
    result = dict(status=status, **STATE, **extra)
    (OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n',
                                     encoding='utf-8')
    print('MANUAL_DOWN_WINDOW_' + status, json.dumps(result, ensure_ascii=False, default=str), flush=True)
    bpy.ops.wm.quit_blender()


def observe():
    try:
        window, area, region = view3d()
        if STATE['step'] == 'setup':
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
                scene = bpy.context.scene
                scene.wfrl_manual_target = 'T1'
                scene.wfrl_manual_blade_model = 'rootkink'
                assert bpy.ops.wfrl.manual_down_view(turbine='T1') == {'FINISHED'}
            space = area.spaces.active
            assert space.region_3d.view_perspective == 'CAMERA' and space.camera.get('wfrl_down_camera')
            t1 = scene.wfrl_manual_turbines[0]
            t1.azimuth_deg = 180.0
            # The scene enters feathered at 90 deg; place fine pitch at once, then ramp blade 1.
            scene.wfrl_manual_pitch_rate_deg_s = 0.0
            t1.pitch_deg = 0.0
            t1.individual_pitch = True
            scene.wfrl_manual_pitch_rate_deg_s = 5.0
            t1.pitch_b1_deg = 10.0
            STATE['ramp_started'] = time.monotonic()
            STATE['step'] = 'ramp'
            return .25
        scene = bpy.context.scene
        t1 = scene.wfrl_manual_turbines[0]
        if STATE['step'] == 'ramp':
            STATE['ramp'].append(round(t1.pitch_actual_b1_deg, 3))
            if time.monotonic() - STATE['ramp_started'] > 15:
                return finish('FAIL', reason='ramp did not finish')
            if control.pitch_ramping(t1):
                return .25
            assert abs(t1.pitch_actual_b1_deg - 10) < 1e-6 and any(0 < v < 10 for v in STATE['ramp'])
            STATE['ramp_seconds'] = round(time.monotonic() - STATE['ramp_started'], 2)
            t1.rpm = 6.0
            STATE['shading_before'] = area.spaces.active.shading.type
            started = time.monotonic()
            with bpy.context.temp_override(window=window, area=area, region=region):
                result = bpy.ops.wfrl.manual_down_video(
                    'EXEC_DEFAULT', filepath=str(OUT / 'viewport_down.mp4'), turbine='T1', length_mode='FRAMES',
                    frame_count=750, fps=25, engine='VIEWPORT_MATERIAL', from_start=True)
            assert result == {'FINISHED'}, result
            STATE['viewport_seconds_per_frame'] = round((time.monotonic() - started) / 750, 4)
            viewport = json.loads((OUT / 'viewport_down.json').read_text(encoding='utf-8'))
            assert viewport['frames'] == 750 and viewport['render_mode'] == 'VIEWPORT_MATERIAL'
            STATE['encoder'], STATE['gpu'] = viewport['encoder'], viewport['gpu']
            assert viewport['encoder'] == ('ffmpeg libx264 (offscreen)' if down.ffmpeg_executable()
                                           else 'blender ffmpeg (render.opengl)')
            assert viewport['samples'][-1]['T1']['pitch_deg'] == [10.0, 0.0, 0.0]
            assert area.spaces.active.shading.type == STATE['shading_before']
            assert scene.view_settings.view_transform == 'AgX' and not scene.get('wfrl_manual_exporting')
            clip = bpy.data.movieclips.load(str(OUT / 'viewport_down.mp4'))
            STATE['viewport_clip'] = [clip.frame_duration, list(clip.size)]
            assert clip.frame_duration == 750 and tuple(clip.size) == (1920, 1080)
            # Without FFmpeg the viewport export falls back to render.opengl.
            found = rendering.ffmpeg_executable
            rendering.ffmpeg_executable = lambda context=None: None
            try:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    result = bpy.ops.wfrl.manual_down_video(
                        'EXEC_DEFAULT', filepath=str(OUT / 'fallback_down.mp4'), turbine='T1', length_mode='FRAMES',
                        frame_count=50, fps=25, resolution='720', engine='VIEWPORT_SOLID', from_start=True)
            finally:
                rendering.ffmpeg_executable = found
            assert result == {'FINISHED'}, result
            fallback = json.loads((OUT / 'fallback_down.json').read_text(encoding='utf-8'))
            assert fallback['encoder'] == 'blender ffmpeg (render.opengl)' and fallback['resolution'] == [1280, 720]
            clip = bpy.data.movieclips.load(str(OUT / 'fallback_down.mp4'))
            assert clip.frame_duration == 50 and tuple(clip.size) == (1280, 720)
            with bpy.context.temp_override(window=window, area=area, region=region):
                result = bpy.ops.wfrl.manual_down_video(
                    'EXEC_DEFAULT', filepath=str(OUT / 'window_down.mp4'), turbine='T1',
                    length_mode='DURATION', duration_s=2.0, fps=10, engine='SCENE', eevee_samples=4, from_start=True)
            assert result == {'FINISHED'}, result
            STATE['step'] = 'render'
            STATE['render_started'] = time.monotonic()
            return .5
        if STATE['step'] == 'render':
            target = OUT / 'window_down.mp4'
            if target.exists() and target.with_suffix('.json').exists() and not jobs._EXPORT:
                sidecar = json.loads(target.with_suffix('.json').read_text(encoding='utf-8'))
                assert sidecar['samples'][0]['T1']['pitch_deg'] == [0.0, 0.0, 0.0]
                assert sidecar['samples'][-1]['T1']['pitch_deg'][0] == 9.5
                assert not scene.get('wfrl_manual_exporting')
                assert scene.render.image_settings.media_type == 'IMAGE'
                assert not STATE['draw_errors']
                return finish('PASS', video_bytes=target.stat().st_size,
                              render_seconds=round(time.monotonic() - STATE['render_started'], 1))
            if time.monotonic() - STATE['render_started'] > 600:
                return finish('FAIL', reason='render timeout')
            return .5
    except Exception:
        return finish('FAIL', error=traceback.format_exc())


bpy.app.timers.register(observe, first_interval=1.0)
