"""Frontend observation modes retain synthetic diagnostics across display and transport."""
from contextlib import nullcontext
from dataclasses import replace
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender.radar_x100 import configuration, display, runtime
from wfrl.radar_x100 import acquisition, channel, recording, schema, tracking


@pytest.fixture
def detection_frame():
    points = np.zeros(3, dtype=schema.POINT_DTYPE)
    points['x'] = [20, 21, 22]
    for field in ('mag', 'rcs', 'snr'):
        points[field] = np.nan
    values = np.zeros(3, dtype=schema.MODEL_DIAGNOSTIC_DTYPE)
    values['model_snr_linear'] = [100, 4, 0]
    values['false_alarm'] = [0, 0, 1]
    return schema.RadarFrame('X100', 'test-detections', 0, 'ENGINEERING_SIMULATION', 'DETECTIONS',
        points, radial_velocity_m_s=np.zeros(3),
        spatial=dict(frame=display.SENSOR_AXES, units='m', alignment='SIMULATION',
                     world_from_sensor=np.eye(4).tolist()),
        time=schema.FrameTime('SIMULATION:test', 0., (0., 0.), 'INSTANT', 'SIMULATION'),
        provenance=dict(observation_model=dict(parameters=dict(calibration_status='UNCALIBRATED'))),
        model_diagnostics=schema.ModelDiagnostics('test-diagnostics', values))


@pytest.mark.parametrize('mode', ['GEOMETRY', 'DETECTIONS'])
@pytest.mark.parametrize('missing', [False, True])
def test_dispatch_keeps_detection_integration_independent_of_geometry_budget(monkeypatch, mode, missing):
    calls, plans = [], []
    def plan(budget):
        plans.append(budget)
        return object()
    sampler = SimpleNamespace(interval=(0., 2.), sample=object(), visibility=object(),
        material_sample=object(), sampling_plan=plan, calibration_id='mount-calibration',
        sampling_transaction=nullcontext, field_of_view=(45.75, 30.))
    result = None if missing else SimpleNamespace(provenance={})
    def simulate(name):
        def call(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result
        return call
    monkeypatch.setattr(runtime.simulation, 'simulate', simulate('GEOMETRY'))
    monkeypatch.setattr(runtime.simulation, 'simulate_detections', simulate('DETECTIONS'))
    # The selected draft must not change an already applied session's producer.
    scene = SimpleNamespace(wfrl_x100_candidates=7, wfrl_x100_observation_mode='DETECTIONS')
    actual = runtime._simulation_frame(scene, sampler, 'session', 30, 1., observation_mode=mode)
    assert actual is result and len(calls) == 1
    name, args, kwargs = calls[0]
    assert name == mode and args == (sampler.sample, 1.)
    assert kwargs['interval_s'] == sampler.interval and kwargs['sequence'] == 30
    assert kwargs['visibility'] is sampler.visibility and kwargs['material_at'] is sampler.material_sample
    budget_field = 'integration_budget' if mode == 'DETECTIONS' else 'candidate_budget'
    assert plans == [configuration.DETECTION_INTEGRATION_BUDGET if mode == 'DETECTIONS' else 7]
    assert kwargs[budget_field] == plans[0]
    assert ('candidate_budget' if mode == 'DETECTIONS' else 'integration_budget') not in kwargs
    if mode == 'DETECTIONS':
        # Applied detections use the measured-PLY calibration with the applied rectangular FOV.
        assert kwargs['config'].calibration_status == 'USER_CALIBRATED'
        assert (kwargs['config'].azimuth_limit_deg, kwargs['config'].elevation_limit_deg) == (45.75, 30.)
        assert 'fov_deg' not in kwargs
    else:
        assert kwargs['fov_deg'] == (45.75, 30.) and 'config' not in kwargs
    if result is not None:
        assert result.provenance['calibration_id'] == sampler.calibration_id


def test_detection_status_counts_actual_frame_flags_and_keeps_missing_diagnostics_unknown(detection_frame):
    assert detection_frame.material_ids is None
    assert display.observation_lines(detection_frame) == (
        '观测模式：DETECTIONS · 工程检测', '标定状态：UNCALIBRATED', '每帧检测数：3 · 虚警标记：1')
    empty = replace(detection_frame, points=detection_frame.points[:0],
        radial_velocity_m_s=np.empty(0), model_diagnostics=schema.ModelDiagnostics(
            detection_frame.model_diagnostics.model_id, detection_frame.model_diagnostics.values[:0]))
    assert display.observation_lines(empty)[-1] == '每帧检测数：0 · 虚警标记：0'
    unknown = replace(detection_frame, model_diagnostics=None, provenance={})
    assert display.observation_lines(unknown)[1:] == ('标定状态：UNKNOWN', '每帧检测数：3 · 虚警标记：未知')
    geometry = replace(unknown, source_kind='GEOMETRY_SIMULATION', stream='GEOMETRY')
    assert display.observation_lines(geometry) == ('观测模式：GEOMETRY · 几何可见性',)


def test_model_snr_is_separate_from_vendor_snr_and_never_needs_material_ids(detection_frame):
    before = detection_frame.metadata(), detection_frame.points.tobytes()
    colors, limits, unknown = display.point_style(detection_frame, 'MODEL_SNR')
    assert limits == (0., 100.) and unknown == 0
    np.testing.assert_array_equal(colors[[0, 2]], [[1, 0, 0, 1], [0, 0, 1, 1]])
    colors, limits, unknown = display.point_style(detection_frame, 'SNR')
    assert limits is None and unknown == 3
    np.testing.assert_array_equal(colors, [[.5, .5, .5, 1]] * 3)
    assert (detection_frame.metadata(), detection_frame.points.tobytes()) == before
    raw = replace(detection_frame, model_diagnostics=None)
    assert display.point_style(raw, 'MODEL_SNR')[1:] == (None, 3)
    points = detection_frame.points.copy()
    points['x'][1] = np.nan
    finite = replace(detection_frame, points=points)
    assert len(display.point_style(finite, 'MODEL_SNR')[0]) == 2
    assert display.observation_lines(finite)[-1] == '每帧检测数：3 · 虚警标记：1'


@pytest.mark.parametrize('mode', ['GEOMETRY', 'DETECTIONS'])
def test_session_recording_replay_and_channel_preserve_observation_contract(tmp_path, detection_frame, mode):
    frame = detection_frame
    if mode == 'GEOMETRY':
        frame = replace(frame, source_kind='GEOMETRY_SIMULATION', stream='GEOMETRY',
            model_diagnostics=None, provenance={}, material_ids=(3, 4, 5))
    session = runtime.Session.__new__(runtime.Session)
    session.scene = SimpleScene()
    session.mode, session.frame, session.frame_index = 'SIMULATION', frame, None
    session.closing = session.recording_stopping = False
    session.fanout = None
    session.start_recording(tmp_path / mode)
    try:
        for index in (1, 2):
            session.frame = replace(frame, sequence=index, time=replace(frame.time,
                source_time_s=index / 30, acquisition_interval_s=(index / 30, index / 30)))
            session._record_current(session.frame)
        session._record_current(session.frame)
    finally:
        deadline = time.monotonic() + 5
        while not session.stop_recording():
            assert time.monotonic() < deadline
            time.sleep(.005)
    replay = recording.Replay(tmp_path / mode)
    assert len(replay.rows) == 3 and session.scene['wfrl_x100_record_state'] == 'COMPLETE'
    for index in range(3):
        restored = replay.frame(index)
        assert restored.sequence == index and restored.material_ids == frame.material_ids
        assert restored.points.tobytes() == frame.points.tobytes()
        assert display.observation_lines(restored) == display.observation_lines(frame)
        reader = channel.PacketReader()
        reader.feed(channel.hello(restored) + channel.encode_frame(restored))
        state = channel.LiveState()
        state.handshake(reader.pop())
        published = reader.pop()
        assert state.accept(published) and state.status(mode) == 'VALID'
        assert published.metadata() == restored.metadata()
        assert published.points.tobytes() == restored.points.tobytes()


class SimpleScene(dict):
    def as_pointer(self):
        return id(self)


def test_output_consumer_keeps_detection_points_and_model_colors(frontend_module, monkeypatch, detection_frame):
    output = frontend_module('wfrl_blender.radar_x100.output')
    scene = SimpleNamespace(wfrl_x100_color='MODEL_SNR', wfrl_x100_color_scale='FIXED',
                            wfrl_x100_velocity_limit=10.)
    surface = SimpleNamespace(as_pointer=lambda: 1)
    canvas = output.OutputWindow(scene, surface, surface)
    session = SimpleNamespace(frame=detection_frame, display_valid=True, generation='generation',
                              frame_index=None)
    monkeypatch.setattr(runtime, 'session_for', lambda scene: session)
    assert canvas.consume() == (session, detection_frame)
    assert len(canvas.xy) == 3 and canvas.original_indices.tolist() == [0, 1, 2]
    assert canvas.limits == (0., 100.) and canvas.unknown == 0
    info = []
    canvas._draw_frame_information(session, detection_frame,
        display.canvas_layout(640, 600, 1., observation_rows=3), 640, 1.,
        lambda value, *args, **kwargs: info.append(value))
    assert all(line in info for line in display.observation_lines(detection_frame))
    session.frame = replace(detection_frame, points=detection_frame.points[:0],
        radial_velocity_m_s=np.empty(0), model_diagnostics=schema.ModelDiagnostics(
            detection_frame.model_diagnostics.model_id, detection_frame.model_diagnostics.values[:0]))
    canvas.consume()
    assert canvas.xy.shape == (0, 2) and not len(canvas.original_indices)


@pytest.mark.parametrize('width,height', [(360, 480), (640, 600), (1280, 800)])
def test_detection_summary_has_space_above_point_canvas(width, height):
    layout = display.canvas_layout(width, height, 1., observation_rows=3)
    x, y, w, h = layout['rectangle']
    assert w > 0 and h > 0 and y + h < layout['info_top'] - 100


def test_live_session_consumes_published_detections_and_empty_frames(frontend_module, monkeypatch, detection_frame):
    frontend_module('wfrl_blender.farm_flex')
    monkeypatch.setattr(sys.modules['bpy'].app, 'background', True)
    scene = SimpleScene()
    for field, value in dict(source='LIVE', stream='DETECTIONS', color='MODEL_SNR',
                             color_scale='FIXED', velocity_limit=10., blade_tracking=False).items():
        setattr(scene, 'wfrl_x100_' + field, value)
    fanout = acquisition.Fanout()
    publisher = acquisition.LocalPublisher(fanout, port=0)
    client = channel.LocalClient(publisher.port, stale_after_s=5.)
    session = runtime.Session.__new__(runtime.Session)
    session.scene, session.mode, session.client = scene, 'LIVE', client
    session.key = session.frame = session.fanout = None
    session.blade_tracker = tracking.BladeTracker()
    try:
        for frame in (detection_frame, replace(detection_frame, sequence=1,
                points=detection_frame.points[:0], radial_velocity_m_s=np.empty(0),
                model_diagnostics=schema.ModelDiagnostics(detection_frame.model_diagnostics.model_id,
                                                          detection_frame.model_diagnostics.values[:0]))):
            fanout.submit(frame, frame.sequence / 30)
            deadline = time.monotonic() + 3
            while session.frame is None or session.frame.sequence != frame.sequence:
                session.update()
                assert time.monotonic() < deadline, client.error
                time.sleep(.005)
            assert session.display_valid and session.frame.material_ids is None
            assert session.frame.metadata() == frame.metadata()
            assert session.frame.points.tobytes() == frame.points.tobytes()
        assert session.frame.validity == 'VALID_EMPTY'
    finally:
        client.close()
        publisher.close()
        fanout.close()
