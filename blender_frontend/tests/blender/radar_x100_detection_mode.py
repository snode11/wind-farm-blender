"""Native observation switching, timeline sampling, recording, replay and saved-scene recovery."""
from pathlib import Path
import importlib
import json
import sys
import time

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
configuration = importlib.import_module(MODULE + '.radar_x100.configuration')
atomic_json = importlib.import_module(MODULE + '.atomic_json')
OUT = _native.output_directory()
checks = {}


def finish_recording(session):
    deadline = time.monotonic() + 5
    while not session.stop_recording():
        assert time.monotonic() < deadline, 'Recording did not finish'
        time.sleep(.005)


def assert_detection(session):
    frame = session.frame
    assert session.display_valid and frame is not None
    assert frame.source_kind == 'ENGINEERING_SIMULATION' and frame.stream == 'DETECTIONS'
    assert frame.material_ids is None and 'material_point_indices' not in frame.provenance
    assert frame.model_diagnostics is not None
    assert 'UNCALIBRATED' in display.observation_lines(frame)[1]
    assert len(frame.points) <= radar.schema.PROFILE['max_points']
    assert np.isnan(frame.points['snr']).all()
    colors, limits, unknown = display.point_style(frame, 'MODEL_SNR')
    assert len(colors) == int(frame.finite_mask.sum()) and unknown == 0
    return frame


try:
    source_package = farm.default_package()
    addon.load_demo_scene(source_package)
    scene = bpy.context.scene
    assert scene.wfrl_x100_observation_mode == 'GEOMETRY'
    property_definition = scene.bl_rna.properties['wfrl_x100_observation_mode']
    assert property_definition.default == 'GEOMETRY'
    assert set(property_definition.enum_items.keys()) == {'GEOMETRY', 'DETECTIONS'}
    assert 'DETECTIONS' in scene.bl_rna.properties['wfrl_x100_stream'].enum_items
    assert 'MODEL_SNR' in scene.bl_rna.properties['wfrl_x100_color'].enum_items
    scene.wfrl_x100_candidates = 64
    assert bpy.ops.wfrl.x100_apply() == {'FINISHED'}
    session = radar.session_for(scene)
    assert session.frame.source_kind == 'GEOMETRY_SIMULATION'
    scene.wfrl_x100_observation_mode = 'DETECTIONS'
    assert session.pending_configuration() == ('观测模式',)
    scene.frame_set(scene.frame_current + 2)
    session.update()
    assert session.frame.source_kind == 'GEOMETRY_SIMULATION'
    assert session.frame.provenance['candidate_budget'] == 64
    # Installation controls apply the mount while preserving an unapplied observation choice.
    radar.apply_mount_preset(scene, 'ORIGINAL')
    assert session.pending_configuration() == ('观测模式',)
    assert session.observation_mode == 'GEOMETRY'
    checks['default_geometry_and_explicit_apply'] = True

    clock = scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s')
    assert bpy.ops.wfrl.x100_apply() == {'FINISHED'}
    session = radar.session_for(scene)
    frame = assert_detection(session)
    assert not session.pending_configuration() and session.observation_mode == 'DETECTIONS'
    assert (scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s')) == clock
    assert frame.provenance['integration_budget'] == configuration.DETECTION_INTEGRATION_BUDGET
    assert 'candidate_budget' not in frame.provenance
    frame_before = frame
    scene.wfrl_x100_candidates = 1
    scene.wfrl_x100_color = 'MODEL_SNR'
    session.update()
    assert session.frame is frame_before and session.render_color[0] == 'MODEL_SNR'
    assert not session.pending_configuration()
    checks['detection_dispatch_and_independent_budgets'] = True

    record_path = OUT / 'detections-recording'
    session.start_recording(record_path)
    sequences, counts = [frame.sequence], [len(frame.points)]
    for step in (2, 3, 5):
        scene.frame_set(scene.frame_current + step)
        radar.tick()
        frame = assert_detection(session)
        assert frame.sequence > sequences[-1]
        assert session.context is not None and session.blade_result is not None
        session.derive_analysis(((-100., -100., -150.), (100., 100., 50.)))
        assert session.analysis_result is not None and not session.analysis_error
        sequences.append(frame.sequence)
        counts.append(len(frame.points))
    finish_recording(session)
    assert scene['wfrl_x100_record_state'] == 'COMPLETE'
    replay = radar.recording.Replay(record_path)
    assert len(replay.rows) == len(sequences)
    for index, sequence in enumerate(sequences):
        saved_frame = replay.frame(index)
        assert saved_frame.sequence == sequence and saved_frame.material_ids is None
        assert saved_frame.stream == 'DETECTIONS' and saved_frame.model_diagnostics is not None
    checks['timeline_playback_analysis_and_recording_without_material_ids'] = True

    frame = assert_detection(session)
    saved_points, saved_diagnostics = frame.points.tobytes(), frame.model_diagnostics.values.tobytes()
    saved_time = frame.time.source_time_s
    clock = scene.frame_current, scene.frame_subframe
    saved = OUT / 'detection-mode.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(saved)) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=str(saved)) == {'FINISHED'}
    scene = bpy.context.scene
    assert scene.wfrl_x100_observation_mode == 'DETECTIONS'
    assert scene.wfrl_x100_color == 'MODEL_SNR' and scene.wfrl_x100_enabled
    radar.tick()
    session = radar.session_for(scene)
    assert session is not None, scene.get('wfrl_x100_error')
    restored = assert_detection(session)
    assert (scene.frame_current, scene.frame_subframe) == clock
    assert restored.time.source_time_s == saved_time
    assert restored.points.tobytes() == saved_points
    assert restored.model_diagnostics.values.tobytes() == saved_diagnostics
    assert not session.pending_configuration()
    checks['saved_scene_restores_detection_mode_time_and_sample'] = True

    radar.apply_mount_preset(scene, 'NACELLE_SIDE')
    assert_detection(session)
    assert session.observation_mode == 'DETECTIONS'
    radar.apply_mount_preset(scene, 'ORIGINAL')
    assert_detection(session)
    checks['detection_mount_presets'] = True

    radar.stop(scene)
    scene.wfrl_x100_source, scene.wfrl_x100_path = 'REPLAY', str(record_path)
    # Recorded observations determine display mode independently of the simulation draft.
    scene.wfrl_x100_observation_mode = 'GEOMETRY'
    session = radar.start(scene)
    assert_detection(session)
    session.start_recording(OUT / 'detection-rerecording')
    assert bpy.ops.wfrl.x100_play() == {'FINISHED'}
    assert session.playing
    session.play_anchor = time.monotonic() - session.replay.duration_s - .1
    session.update()
    assert not session.playing and session.frame.sequence == sequences[-1]
    assert_detection(session)
    finish_recording(session)
    rerecorded = radar.recording.Replay(OUT / 'detection-rerecording')
    assert rerecorded.frame(-1).model_diagnostics.values.tobytes() == saved_diagnostics
    assert bpy.ops.wfrl.x100_play() == {'FINISHED'}
    assert session.playing and session.replay_time_s == session.replay.times[0]
    assert bpy.ops.wfrl.x100_play() == {'FINISHED'}
    assert not session.playing
    session.seek_frame(0)
    assert_detection(session)
    checks['detection_file_playback_and_rerecording'] = True

    radar.stop(scene)
    scene.wfrl_x100_source = 'SIMULATION'
    assert bpy.ops.wfrl.x100_apply() == {'FINISHED'}
    assert radar.session_for(scene).frame.source_kind == 'GEOMETRY_SIMULATION'
    checks['switch_back_to_geometry'] = True
finally:
    addon.unregister()

result = dict(status='PASS', checks=checks, sequences=sequences, detection_counts=counts,
              module=addon.__name__, addon_path=addon.__file__,
              addon=addon.__file__, core=radar.simulation.__file__,
              **_native.installed_provenance(addon, farm_package=source_package))
atomic_json.write_json(OUT / 'result.json', result)
print('X100_DETECTION_MODE_PASS', json.dumps(result), flush=True)
