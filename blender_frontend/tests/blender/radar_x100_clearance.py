"""Native detection clearance, independent recordings and saved-scene restoration."""
from copy import deepcopy
from dataclasses import replace
import importlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import bpy
import numpy as np

sys.dont_write_bytecode = True
sys.path.append(str(Path(__file__).resolve().parent))
import _native

assert bpy.app.background, 'This regression requires background execution'
MODULE = _native.addon_module()
addon = importlib.import_module(MODULE)
if MODULE == 'wfrl_blender':
    addon.register()
_native.check_addon_root(addon)
farm = importlib.import_module(MODULE + '.farm_flex')
radar = importlib.import_module(MODULE + '.radar_x100.runtime')
display = importlib.import_module(MODULE + '.radar_x100.display')
clearance = importlib.import_module(MODULE + '.radar_x100.clearance')
panels = importlib.import_module(MODULE + '.panels.radar_x100')
helpers = importlib.import_module(MODULE + '.panels.ui_helpers')
atomic_json = importlib.import_module(MODULE + '.atomic_json')
OUT = _native.output_directory()
checks = {}


def panel_text(scene):
    messages, original = [], helpers.message
    helpers.message = lambda layout, text, *args, **kwargs: messages.append(text)
    try:
        panels.WFRL_PT_X100Blades.draw(SimpleNamespace(layout=SimpleNamespace(prop=lambda *args: None)),
                                       SimpleNamespace(scene=scene))
    finally:
        helpers.message = original
    return messages


def supported(session):
    assert session.display_valid and session.frame.stream == 'DETECTIONS'
    assert session.frame.material_ids is None
    assert session.blade_result is not None, session.blade_error
    return [row for row in session.blade_result['blades'] if row['status'] == 'SUPPORTED']


def verify_display(session):
    rows = supported(session)
    messages = panel_text(session.scene)
    assert clearance.NOTICE in messages and clearance.PRIORS in messages
    for row in session.blade_result['blades']:
        if row['status'] == 'SUPPORTED':
            lo, hi = row['clearance_interval95_m']
            assert 0 <= lo < row['clearance_m'] < hi
            assert any(f"{row['clearance_m']:.3f} m" in text and '95%' in text for text in messages)
        else:
            assert any(f"B{row['blade']} 不可估计" in text for text in messages)
    colors, limits, unknown = display.point_style(session.frame, 'ECHO_CLASS', classification=session.classification())
    assert colors.shape == (int(session.frame.finite_mask.sum()), 4) and unknown == 0
    assert limits == (0., 2.) and session.render_color[0] == 'ECHO_CLASS'
    return rows


def finish_recording(session):
    deadline = time.monotonic() + 5
    while not session.stop_recording():
        assert time.monotonic() < deadline
        time.sleep(.005)


try:
    addon.load_demo_scene(farm.default_package())
    scene = bpy.context.scene
    scene.wfrl_x100_observation_mode = 'DETECTIONS'
    scene.wfrl_x100_echo_classes = True
    # A fixed research installation exercises a supported passage on the bundled source.
    scene.wfrl_x100_location = (-2.1264325082302094, 0., .21256005764007568)
    scene.wfrl_x100_rotation = (36.08677798897994, 72.85363421204708, -145.14414665707494)
    scene.frame_set(scene.frame_start + 20)
    session = radar.start(scene)
    for offset in range(20, 73, 2):
        scene.frame_set(scene.frame_start + offset)
        radar.tick()
        if supported(session):
            break
    assert supported(session), session.blade_result
    verify_display(session)
    source_frame, original = session.frame, deepcopy(session.blade_result)
    checks['source_playback_supported_clearance_and_class_colors'] = True

    # Geometry deformation APIs are forbidden during inference of an existing observation.
    deform = session.sampler.flex.deform_points
    def forbidden(*args, **kwargs):
        raise AssertionError('Loaded blade geometry entered the estimate')
    session.sampler.flex.deform_points = forbidden
    try:
        session.frame = replace(source_frame, material_ids=tuple('wrong-truth' for _ in source_frame.points),
            provenance=source_frame.provenance | dict(truth=dict(tip_world=[999., 999., 999.])))
        session.blade_key = session.clearance_source = None
        session.derive_blades()
        fresh = deepcopy(session.blade_result)
        assert fresh is not None, session.blade_error
        session.frame = source_frame
        session.blade_key = session.clearance_source = None
        session.derive_blades()
        assert fresh == session.blade_result
    finally:
        session.sampler.flex.deform_points = deform
        session.frame = source_frame
    checks['inference_ignores_truth_and_never_deforms_blades'] = True

    record_path = OUT / 'detection-clearance-recording'
    session.start_recording(record_path)
    archived = [deepcopy(session.blade_result)]
    sequences = [session.frame.sequence]
    for _ in range(4):
        scene.frame_set(scene.frame_current + 2)
        radar.tick()
        verify_display(session)
        archived.append(deepcopy(session.blade_result))
        sequences.append(session.frame.sequence)
    finish_recording(session)
    assert scene['wfrl_x100_record_state'] == 'COMPLETE'
    replay = radar.recording.Replay(record_path)
    assert len(replay.rows) == len(archived)
    for index, expected in enumerate(archived):
        frame = replay.frame(index)
        assert frame.sequence == sequences[index]
        assert clearance.read_recorded(replay, index, frame) == (expected, '')
        assert 'clearance' not in frame.provenance
    checks['recording_keeps_raw_frames_and_bound_derived_results'] = True

    # Save a supported source frame; reopening recomputes from the saved source time.
    source_offset = offset
    scene.frame_set(scene.frame_start + source_offset)
    radar.tick()
    assert verify_display(session)
    stamp = session.frame.time.source_time_s
    saved = OUT / 'detection-clearance.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    scene = bpy.context.scene
    radar.tick()
    session = radar.session_for(scene)
    assert session is not None, scene.get('wfrl_x100_error')
    assert scene.wfrl_x100_echo_classes and scene.wfrl_x100_observation_mode == 'DETECTIONS'
    assert session.frame.time.source_time_s == stamp
    assert verify_display(session)
    checks['source_save_reopen_displays_supported_clearance'] = True

    radar.stop(scene)
    scene.wfrl_x100_source, scene.wfrl_x100_path = 'REPLAY', str(record_path)
    scene.wfrl_x100_observation_mode = 'GEOMETRY'
    scene.wfrl_x100_replay_s = 0.
    session = radar.start(scene)
    assert session.blade_result == archived[0] and verify_display(session)
    assert session.context is None
    clock = scene.frame_current, scene.frame_subframe
    assert bpy.ops.wfrl.x100_play() == {'FINISHED'}
    assert session.playing
    session.play_anchor = time.monotonic() - replay.duration_s - .1
    session.update()
    assert not session.playing and session.blade_result == archived[-1]
    assert (scene.frame_current, scene.frame_subframe) == clock
    session.seek_frame(0)
    assert session.blade_result == archived[0]
    assert verify_display(session)
    checks['independent_recording_playback_and_rewind'] = True

    saved_replay = OUT / 'clearance-recording-replay.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved_replay))
    bpy.ops.wm.open_mainfile(filepath=str(saved_replay))
    scene = bpy.context.scene
    radar.tick()
    session = radar.session_for(scene)
    assert session is not None and session.mode == 'REPLAY', scene.get('wfrl_x100_error')
    assert session.blade_result == archived[0] and verify_display(session)
    checks['recording_replay_save_reopen_keeps_exact_result'] = True

    session.scene.wfrl_x100_blade_tracking = False
    session.update()
    assert session.blade_result is session.classification() is None
    assert '不可估计：估计未启用' in panel_text(scene)
    session.scene.wfrl_x100_blade_tracking = True
    session.update()
    assert session.blade_result == archived[0]
    checks['toggle_withdrawal_and_recovery'] = True
    radar.stop(scene)
    scene.wfrl_x100_source = 'SIMULATION'
    session = radar.start(scene)
    assert session.frame.stream == 'GEOMETRY' and session.classification() is None
    assert session.render_color[0] == scene.wfrl_x100_color
    checks['geometry_mode_keeps_original_display'] = True
finally:
    addon.unregister()

result = dict(status='PASS', checks=checks, sequences=sequences, sample_estimates=archived,
              addon=addon.__file__, estimator=clearance.estimator.__file__)
atomic_json.write_json(OUT / 'result.json', result)
print('X100_CLEARANCE_PASS', json.dumps(dict(checks=checks, sequences=sequences)), flush=True)
