"""Detection-only inference, truthful display states and frame-bound result transport."""
from copy import deepcopy
from dataclasses import replace
import json
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

from tests.radar_x100.test_clearance import fixture as analytic_loft
from wfrl.radar_x100 import analysis, recording, schema, tracking
from wfrl_blender.radar_x100 import clearance, display, runtime, scene as scene_api


class Scene(dict):
    wfrl_x100_blade_tracking = True
    wfrl_x100_echo_classes = True
    wfrl_x100_color = 'RANGE'
    wfrl_x100_color_scale = 'FIXED'
    wfrl_x100_velocity_limit = 10.

    def as_pointer(self):
        return id(self)


@pytest.fixture
def detector(monkeypatch):
    shape, priors, blade, _, vertices, triangles = analytic_loft()
    tower = np.column_stack((np.full(20, -8.), np.linspace(-1, 1, 20), np.linspace(20, 50, 20)))
    xyz = np.vstack((blade, tower, [5., 7., 40.]))
    points = np.zeros(len(xyz), dtype=schema.POINT_DTYPE)
    for i, field in enumerate(('x', 'y', 'z')):
        points[field] = xyz[:, i]
    diagnostics = np.zeros(len(points), dtype=schema.MODEL_DIAGNOSTIC_DTYPE)
    diagnostics['model_observed_power_w'] = 30.
    frame = schema.RadarFrame('X100', 'engineering', 30, 'ENGINEERING_SIMULATION', 'DETECTIONS', points,
        radial_velocity_m_s=np.zeros(len(points)),
        spatial=dict(frame=display.SENSOR_AXES, units='m', alignment='SIMULATION', world_from_sensor=np.eye(4).tolist()),
        time=schema.FrameTime('SIMULATION:analytic', 1., (1., 1.), 'INSTANT', 'SIMULATION'),
        model_diagnostics=schema.ModelDiagnostics('analytic', diagnostics),
        provenance=dict(observation_model=dict(parameters=dict(range_std_m=.05, azimuth_std_deg=0., elevation_std_deg=0.),
                                               derived_parameters=dict(detector_noise_power_w=1.))))
    session = runtime.Session.__new__(runtime.Session)
    session.scene, session.mode = Scene(), 'SIMULATION'
    session.source_session, session.generation = 'engineering', 'generation'
    session.frame = session.frame_index = session.fanout = None
    session.closing = session.recording_stopping = False
    session.blade_tracker = tracking.BladeTracker()
    session.sampler = SimpleNamespace(flex=object(), source_id='source', geometry_id='geometry', calibration_id='mount',
        analysis_context=lambda f, identity: analysis.Context(identity, np.eye(4), np.eye(4), vertices, triangles),
        blade_priors=lambda t: priors)
    monkeypatch.setattr(clearance, 'unloaded_shapes', lambda sampler: dict.fromkeys((1, 2, 3), shape))
    class Query:
        def __init__(self, context):
            self.context = context
        def query(self, points, roi, **kwargs):
            distances, nearest, _ = analysis.closest_reference(points, vertices, triangles)
            return dict(distance=distances, nearest=nearest)
    monkeypatch.setattr(scene_api, 'SideWallQuery', Query)
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(app=SimpleNamespace(background=True)))
    session._display(frame)
    assert session.blade_result is not None, session.blade_error
    return session


def finish(session):
    deadline = time.monotonic() + 5
    while not session.stop_recording():
        assert time.monotonic() < deadline
        time.sleep(.005)


def advance(session, sequence, *, empty=False):
    frame = session.frame
    t = sequence / 30
    new = replace(frame, sequence=sequence, time=replace(frame.time, source_time_s=t, acquisition_interval_s=(t, t)))
    if empty:
        new = replace(new, points=frame.points[:0], radial_velocity_m_s=np.empty(0),
            model_diagnostics=replace(frame.model_diagnostics, values=frame.model_diagnostics.values[:0]))
    session._display(new)


def test_frontend_infers_clearance_interval_and_all_three_echo_classes(detector):
    result = detector.blade_result
    assert result['blades'][0]['status'] == 'SUPPORTED'
    value = result['blades'][0]['clearance_m']
    assert value == pytest.approx(9.5, abs=.035)
    lo, hi = result['blades'][0]['clearance_interval95_m']
    assert lo < value < hi
    assert set(detector.classification()) == {0, 1, 2}
    assert '95%' in clearance.lines(result)[0]
    assert all('不可估计' in line for line in clearance.lines(result)[1:])
    assert 'UNCALIBRATED' in clearance.NOTICE and '先验' in clearance.PRIORS


def test_truth_labels_signal_only_power_and_materials_cannot_change_inference(detector):
    original = deepcopy(detector.blade_result)
    values = detector.frame.model_diagnostics.values.copy()
    values['false_alarm'] = 1
    # Signal-only response stays zero, while observed noisy power is unchanged.
    poisoned = replace(detector.frame, model_diagnostics=replace(detector.frame.model_diagnostics, values=values),
        material_ids=tuple('wrong-tower-truth' for _ in values),
        provenance=detector.frame.provenance | dict(truth=dict(tip_world=[999., 999., 999.], clearance_m=999.)))
    detector.clearance_tracker.reset()
    detector._display(poisoned)
    assert detector.blade_result == original
    values['false_alarm'] = 0
    values['model_power_w'], values['model_snr_linear'] = 1e12, 1e12
    detector.clearance_tracker.reset()
    detector._display(replace(poisoned, model_diagnostics=replace(poisoned.model_diagnostics, values=values)))
    assert detector.blade_result == original


@pytest.mark.parametrize('sequence', [29, 30, 60])
def test_noncausal_or_long_gap_frames_reset_history(detector, sequence):
    advance(detector, 31)
    assert detector.blade_result['blades'][0]['consecutive_frames'] == 2
    advance(detector, sequence)
    assert detector.blade_result['blades'][0]['consecutive_frames'] == 1


def test_empty_invalid_and_disabled_frames_withdraw_numbers_and_colors(detector):
    advance(detector, 31, empty=True)
    assert all(row['clearance_m'] is None for row in detector.blade_result['blades'])
    assert all('不可估计' in line for line in clearance.lines(detector.blade_result))
    assert detector.classification() == [] and not detector.clearance_tracker.tracks
    detector._display(None)
    assert detector.classification() is detector.blade_result is None
    detector.scene.wfrl_x100_blade_tracking = False
    detector.derive_blades()
    assert detector.blade_result is None


@pytest.mark.parametrize('missing', ['velocity', 'errors', 'noise'])
def test_missing_observable_inputs_leave_raw_frame_recordable(detector, missing):
    frame = detector.frame
    metadata = deepcopy(frame.provenance)
    if missing == 'velocity':
        frame = replace(frame, radial_velocity_m_s=None)
    else:
        metadata['observation_model'].pop('parameters' if missing == 'errors' else 'derived_parameters')
        frame = replace(frame, provenance=metadata)
    detector._display(frame)
    assert detector.frame is frame and detector.display_valid
    assert detector.blade_result is detector.classification() is None
    assert '不可估计' in clearance.lines(None, detector.blade_error)[0]
    assert not detector.clearance_tracker.tracks


def test_temporary_failure_recovers_same_frame_without_resampling(detector, monkeypatch):
    original = detector.blade_result
    detector.blade_key = None
    detector.clearance_tracker.reset()
    update = detector.clearance_tracker.update
    calls = []
    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('temporary fit failure')
        return update(*args, **kwargs)
    monkeypatch.setattr(detector.clearance_tracker, 'update', flaky)
    detector.derive_blades()
    assert detector.blade_result is None and detector.blade_error
    detector.derive_blades()
    assert len(calls) == 1
    detector.blade_retry_at = 0.
    detector.derive_blades()
    assert len(calls) == 2 and detector.blade_result == original


def test_class_colors_keep_original_indices_and_ignore_geometry_mode(detector):
    frame, result = detector.frame, detector.blade_result
    colors, limits, unknown = display.point_style(frame, 'ECHO_CLASS', classification=result['labels'])
    assert limits == (0., 2.) and unknown == 0
    np.testing.assert_allclose(colors[-21:-1], [display.ECHO_COLORS[2]]*20)
    np.testing.assert_allclose(colors[-1], display.ECHO_COLORS[0])
    points = frame.points.copy()
    points['x'][1] = np.nan
    masked = replace(frame, points=points)
    assert len(display.point_style(masked, 'ECHO_CLASS', classification=result['labels'])[0]) == len(points)-1
    assert display.point_style(frame, 'ECHO_CLASS')[1:] == (None, len(points))
    geometry = replace(frame, source_kind='GEOMETRY_SIMULATION', stream='GEOMETRY', model_diagnostics=None)
    assert display.style_key(detector.scene, geometry)[0] == 'RANGE'
    np.testing.assert_array_equal(display.point_style(geometry, 'ECHO_CLASS')[0], display.point_style(geometry, 'RANGE')[0])


def test_geometry_keeps_original_tracker_and_never_builds_clearance_priors(detector, monkeypatch):
    def forbidden(*args):
        raise AssertionError('Detection estimator must not run on geometry frames')
    monkeypatch.setattr(clearance, 'unloaded_shapes', forbidden)
    monkeypatch.setattr(detector.clearance_tracker, 'update', forbidden)
    detector._display(replace(detector.frame, source_kind='GEOMETRY_SIMULATION', stream='GEOMETRY', model_diagnostics=None))
    detector.derive_blades()
    assert detector.blade_result is not None and not detector.blade_error
    assert 'labels' not in detector.blade_result and detector.classification() is None


def test_queue_archives_each_current_result_without_altering_raw_metadata(detector, tmp_path):
    first, before = deepcopy(detector.blade_result), detector.frame.metadata()
    detector.start_recording(tmp_path / 'record')
    advance(detector, 31)
    second = deepcopy(detector.blade_result)
    advance(detector, 32, empty=True)
    empty = deepcopy(detector.blade_result)
    finish(detector)
    replay = recording.Replay(tmp_path / 'record')
    assert len(replay.rows) == 3 and replay.frame(0).metadata() == before
    for index, expected in enumerate((first, second, empty)):
        actual, error = clearance.read_recorded(replay, index, replay.frame(index))
        assert actual == expected and not error
    assert 'world_from_analysis' not in (tmp_path / 'record/clearance/00000000.json').read_text(encoding='utf-8')
    detector.mode, detector.replay = 'REPLAY', replay
    detector.scene.wfrl_x100_observation_mode = 'GEOMETRY'
    detector.sampler = None
    detector.frame_index = 0
    detector._display(replay.frame(0))
    assert detector.blade_result == first and detector.context is None
    assert display.style_key(detector.scene, detector.frame)[0] == 'ECHO_CLASS'
    detector.frame_index = 2
    detector._display(replay.frame(2))
    assert detector.blade_result == empty
    detector.frame_index = 0
    detector._display(replay.frame(0))
    assert detector.blade_result == first
    detector.start_recording(tmp_path / 'rerecord')
    finish(detector)
    rereplay = recording.Replay(tmp_path / 'rerecord')
    assert clearance.read_recorded(rereplay, 0, rereplay.frame(0))[0] == first


@pytest.mark.parametrize('failure', ['missing', 'changed-frame', 'corrupt', 'invalid-interval'])
def test_replay_missing_or_invalid_estimate_does_not_hide_raw_points(detector, tmp_path, failure):
    detector.start_recording(tmp_path / 'record')
    finish(detector)
    replay = recording.Replay(tmp_path / 'record')
    path = replay.path / 'clearance/00000000.json'
    if failure == 'missing':
        path.unlink()
    elif failure != 'changed-frame':
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['result']['blades'][0]['clearance_interval95_m'] = [20., 1.]
        if failure == 'invalid-interval':
            payload.pop('sha256')
            payload['sha256'] = schema.digest(payload)
        path.write_text(json.dumps(payload), encoding='utf-8')
    detector.mode, detector.replay, detector.frame_index = 'REPLAY', replay, 0
    frame = replay.frame(0)
    if failure == 'changed-frame':
        frame = replace(frame, sequence=100)
    detector._display(frame)
    assert detector.frame is frame and detector.display_valid
    assert detector.blade_result is detector.classification() is None
    assert detector.blade_error and '不可估计' in clearance.lines(None, detector.blade_error)[0]


def test_companion_write_failure_marks_recording_incomplete(detector, monkeypatch, tmp_path):
    from wfrl_blender import atomic_json
    def failed(*args, **kwargs):
        raise OSError('sidecar disk failure')
    monkeypatch.setattr(atomic_json, 'write_json', failed)
    detector.start_recording(tmp_path / 'record')
    with pytest.raises(RuntimeError, match='sidecar disk failure'):
        finish(detector)
    assert (tmp_path / 'record/INCOMPLETE').exists()
    with pytest.raises(ValueError, match='incomplete'):
        recording.Replay(tmp_path / 'record')


@pytest.mark.parametrize('state', ['disabled', 'unmatched'])
def test_recording_never_carries_a_disabled_or_mismatched_cached_estimate(detector, tmp_path, state):
    if state == 'disabled':
        detector.scene.wfrl_x100_blade_tracking = False
    else:
        detector.blade_key = ('different-frame',)
    detector.start_recording(tmp_path / 'record')
    finish(detector)
    replay = recording.Replay(tmp_path / 'record')
    assert clearance.read_recorded(replay, 0, replay.frame(0))[0] is None


def test_output_colors_rebuild_after_current_frame_inference_is_withdrawn(detector, frontend_module, monkeypatch):
    output = frontend_module('wfrl_blender.radar_x100.output')
    monkeypatch.setattr(runtime, 'session_for', lambda scene: detector)
    surface = SimpleNamespace(as_pointer=lambda: 1)
    canvas = output.OutputWindow(detector.scene, surface, surface)
    canvas.view = 'SIDE'
    canvas.consume()
    assert canvas.color == 'ECHO_CLASS' and canvas.unknown == 0
    np.testing.assert_allclose(canvas.colors[-1], display.ECHO_COLORS[0])
    detector.scene.wfrl_x100_blade_tracking = False
    detector.derive_blades()
    canvas.consume()
    assert canvas.unknown == len(detector.frame.points) and canvas.limits is None


def test_panel_displays_engineering_scope_supported_intervals_and_unavailable_reasons(detector, frontend_module, monkeypatch):
    panels = frontend_module('wfrl_blender.panels.radar_x100')
    helper = frontend_module('wfrl_blender.panels.ui_helpers')
    monkeypatch.setattr(runtime, 'session_for', lambda scene: detector)
    messages = []
    monkeypatch.setattr(helper, 'message', lambda layout, text, *args, **kwargs: messages.append(text))
    panel = panels.WFRL_PT_X100Blades()
    panel.layout = SimpleNamespace(prop=lambda *args: None)
    context = SimpleNamespace(scene=detector.scene)
    panel.draw(context)
    assert clearance.NOTICE in messages and clearance.PRIORS in messages
    value = f"{detector.blade_result['blades'][0]['clearance_m']:.3f} m"
    assert any('95%' in text and value in text for text in messages)
    assert any('B2 不可估计' in text for text in messages)
    messages.clear()
    detector.scene.wfrl_x100_blade_tracking = False
    panel.draw(context)
    assert '不可估计：估计未启用' in messages
    assert not any('估计叶尖塔距' in text for text in messages)
