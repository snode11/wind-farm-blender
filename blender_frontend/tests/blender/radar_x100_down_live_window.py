"""Visible live X100 output at the T1 Down camera position on the self-modified blade.

Loads the MAPPO replay, selects the self-modified blade shape (rootkink), applies calibrated
DETECTIONS at the DOWN_CAMERA preset, opens the point-cloud window and plays the timeline so the
window consumes successive 33 Hz samples. A blade-shape switch while the radar runs resamples the
new shape. Screenshots of the point-cloud window and the 3D view are review artifacts.
WFRL_TEST_QUIT=1 exits after the final report and marker, on success or failure.
"""
from pathlib import Path
import importlib
import json
import os
import sys
import time
import traceback

import bpy

sys.path.append(str(Path(__file__).resolve().parent))
import _native
MODULE = _native.addon_module()
addon = importlib.import_module(MODULE)
atomic_json = importlib.import_module(MODULE + '.atomic_json')
farm = importlib.import_module(MODULE + '.farm_flex')
radar = importlib.import_module(MODULE + '.radar_x100.runtime')
output = importlib.import_module(MODULE + '.radar_x100.output')
configuration = importlib.import_module(MODULE + '.radar_x100.configuration')
display = importlib.import_module(MODULE + '.radar_x100.display')
sidebar = importlib.import_module(MODULE + '.cameras')
OUT = _native.output_directory()
STATE = dict(step=0, checks={}, sequences=[], draws=[])
PLAY_S = 3.


def finish(result):
    result = dict(result, final=True)
    atomic_json.write_json(OUT / 'result.json', result)
    print('X100_DOWN_LIVE_' + result['status'], json.dumps(result, ensure_ascii=False), flush=True)
    if os.environ.get('WFRL_TEST_QUIT') == '1':
        bpy.ops.wm.quit_blender()


def main_context():
    return bpy.context.temp_override(window=STATE['window'], area=STATE['area'], region=STATE['region'])


def screenshot(window, area, name):
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=str(OUT / name))


def assert_down_detection(session):
    frame = session.frame
    assert session.display_valid and frame.stream == 'DETECTIONS'
    assert display.observation_lines(frame)[1] == '标定状态：USER_CALIBRATED'
    fov = frame.provenance['field_of_view_deg']
    assert abs(fov[0] - 45.7464) < 1e-3 and abs(fov[1] - 30.) < 1e-3, fov
    assert 0 < len(frame.points) <= radar.schema.PROFILE['max_points']
    return frame


def run():
    try:
        checks, step = STATE['checks'], STATE['step']
        if step == 0:
            if MODULE == 'wfrl_blender':
                addon.register()
            addon.load_demo_scene(farm.default_package(), camera_rig=False)
            scene, window = bpy.context.scene, bpy.context.window
            scene.wfrl_blade_model = 'rootkink'
            assert farm.active_for(scene).model == 'rootkink', scene.get('wfrl_blade_model_error')
            scene.wfrl_x100_source, scene.wfrl_x100_observation_mode = 'SIMULATION', 'DETECTIONS'
            radar.start(scene)
            session = radar.apply_mount_preset(scene, 'DOWN_CAMERA')
            assert session.observation_mode == 'DETECTIONS' and not session.pending_configuration()
            assert_down_detection(session)
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            area.spaces.active.show_region_ui = True
            sidebar.select_sidebar_category(area, 'X100')
            STATE.update(scene=scene, window=window, area=area, region=region)
            with main_context():
                assert bpy.ops.wfrl.x100_output() == {'RUNNING_MODAL'}
            STATE['item'] = output.for_scene(scene)
            checks.update(self_modified_blade_selected=True, down_camera_calibrated_detections=True)
            STATE['step'] = 1
            return 1.
        scene, item = STATE['scene'], STATE['item']
        if step == 1:
            assert not item.error and item.draw_count > 0, item.error
            STATE['play_start'] = time.monotonic()
            with main_context():
                bpy.ops.screen.animation_play()
            STATE['step'] = 2
            return .05
        if step == 2:
            session = radar.session_for(scene)
            frame = session.frame
            if not STATE['sequences'] or STATE['sequences'][-1][0] != frame.sequence:
                t0 = session.sampler.interval[0]
                STATE['sequences'].append((frame.sequence, frame.time.source_time_s - t0, len(frame.points)))
            STATE['draws'].append((item.draw_count, id(item.frame)))
            if time.monotonic() - STATE['play_start'] < PLAY_S:
                return .02
            with main_context():
                bpy.ops.screen.animation_cancel(restore_frame=False)
            sequences = STATE['sequences']
            assert len(sequences) >= 10, sequences
            for sequence, offset, _ in sequences:
                assert abs(offset - sequence / configuration.SAMPLE_RATE_HZ) < 1e-6, (sequence, offset)
            consumed = {frame_id for _, frame_id in STATE['draws']}
            assert len(consumed) >= 5, 'Point-cloud window must consume successive live frames'
            assert_down_detection(radar.session_for(scene))
            checks.update(live_window_consumes_frames=len(consumed), sampled_sequences=len(sequences),
                          rate_hz=configuration.SAMPLE_RATE_HZ,
                          points_per_frame=[n for *_, n in sequences][:20])
            STATE['step'] = 3
            return 1.
        if step == 3:
            window, area = item.alive()
            screenshot(window, area, 'x100_down_live_output.png')
            screenshot(STATE['window'], STATE['area'], 'x100_down_live_view.png')
            # Blade shape switch with an applied radar resamples the new shape at the same time.
            before = radar.session_for(scene)
            scene.wfrl_blade_model = 'nrel5mw'
            after = radar.session_for(scene)
            assert farm.active_for(scene).model == 'nrel5mw'
            assert after is not before and after.sampler.flex is farm.active_for(scene)
            assert not scene.get('wfrl_x100_error'), scene.get('wfrl_x100_error')
            assert_down_detection(after)
            scene.wfrl_blade_model = 'rootkink'
            assert radar.session_for(scene).sampler.flex.model == 'rootkink'
            assert_down_detection(radar.session_for(scene))
            checks['blade_switch_resamples_radar'] = True
            STATE['step'] = 4
            return 1.
        assert not item.error, item.error
        finish(dict(status='PASS', addon=addon.__file__, checks=checks))
        return None
    except Exception:
        traceback.print_exc()
        finish(dict(status='FAIL', error=traceback.format_exc(), checks=STATE['checks']))
        return None


bpy.app.timers.register(run, first_interval=1.)
