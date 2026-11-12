"""Live solver clocks, actuator roles and individual blade curves agree end to end."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from wfrl.fastfarm_clock import coupling_timestamp
from wfrl.scene.runtime import SceneRuntime
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl.blender_bridge.messages import BLADE_COMPONENTS, encode_message
from wfrl_blender.protocol import FrameDecoder
from wfrl_blender import charts
from wfrl_blender.history import HistoryWindow


def observation(*, index=37, interval=3., step=1, episode=0):
    clock = coupling_timestamp(index, interval)
    clock.update(servo_time_steps=[.00625, .0125], power_window_steps=1)
    scene = NS(turbines=[NS(id='T1'), NS(id='T2')], dt=interval, controls=['yaw'], backend='fastfarm')
    sample = NS(step=step, episode=episode, phase='sampling', events=[], reward=0., farm_power=3.,
        ctx={'timestamp': {k:v for k,v in clock.items() if k not in ('servo_time_steps', 'power_window_steps')},
             't': clock['value'], 'control_elapsed_seconds': step * interval},
        measure={'yaw': np.array([15., -12.]), 'yaw_command': np.array([15., -12.]),
                 'yaw_measured': np.array([1.25, -2.]), 'pitch_meas': np.array([2., 3.]),
                 'power': np.array([1., 2.]), 'torque': np.array([12000., 15000.]),
                 'm_flap': np.array([[1., 2., 3.], [4., 5., 6.]]) * 1e6,
                 'm_edge': np.array([[-1., -2., -3.], [-4., -5., -6.]]) * 1e6,
                 'solver_clock': clock})
    return scene, sample


def wire(scene, sample, sequence=1):
    payload = SnapshotAdapter(scene, 's').encode(sample)
    message = dict(protocol_version=1, type='snapshot', session_id='s', sequence=sequence, payload=payload)
    return FrameDecoder().feed(encode_message(message))[0]


def test_solver_clock_and_control_clock_remain_distinct_through_export():
    scene, sample = observation()
    message = wire(scene, sample)
    payload = message['payload']
    assert payload['timestamp']['value'] == 111.
    assert payload['control_elapsed_seconds'] == 3.
    records = payload['turbines'][0]['channels']
    assert records['power']['timestamp']['value'] == 110.99375
    assert records['m_flap_1']['timestamp']['value'] == 111.
    assert records['yaw']['value'] == records['yaw_measured']['value'] == 1.25
    assert records['yaw_command']['value'] == 15.
    assert records['yaw_command']['provenance']['role'] == 'command'
    assert records['yaw']['provenance']['role'] == 'measurement'
    history, raw = charts.ChartHistory(), HistoryWindow()
    history.ingest(message)
    raw.ingest(message)
    assert history.points('T1', 'power')[0].time == 110.99375
    assert history.points('T1', 'yaw')[0].time == 111.
    assert history.points('T1', 'yaw_command')[0].value == 15.
    exported = raw.snapshot()['series']
    record = next(s['records'][0] for s in exported if s['key'][-1] == 'm_flap_2')
    assert record['data']['value'] == 2e6
    assert record['control_elapsed_seconds'] == 3.
    assert record['timestamp']['source'] == 'mpi_exchange_index'


def test_missing_yaw_feedback_never_uses_the_command_as_measured_pose():
    scene, sample = observation()
    sample.measure.pop('yaw_measured')
    payload = wire(scene, sample)['payload']
    records = payload['turbines'][0]['channels']
    assert records['yaw']['value'] is None
    assert records['yaw']['validity'] == 'unsupported'
    assert records['yaw_command']['value'] == 15.


def test_runtime_passes_solver_time_and_measured_pose_to_sensors():
    scene, sample = observation()
    runtime = SceneRuntime.__new__(SceneRuntime)
    runtime.scene, runtime.driver = NS(dt=3., n=2), object()
    runtime._t, runtime.u_inf, runtime.done = 1, 8., False
    runtime._active_sensors = lambda: []
    runtime._emit(sample.measure)
    assert runtime.last_ctx['t'] == 111.
    assert runtime.last_ctx['control_elapsed_seconds'] == 3.
    np.testing.assert_array_equal(runtime.last_ctx['yaw'], [1.25, -2.])
    np.testing.assert_array_equal(runtime.last_measure['yaw'], [15., -12.])


def test_blade_series_preserve_components_gaps_and_episode_boundaries(monkeypatch):
    history = charts.ChartHistory()
    monkeypatch.setattr(charts, 'history', history)
    charts.invalidate_render_cache()
    scene, sample = observation()
    history.ingest(wire(scene, sample))
    for name, (axis, component) in BLADE_COMPONENTS.items():
        point = history.points('T2', name)[0]
        assert point.value == sample.measure[axis][1, component]
        assert point.unit == 'N m'
        assert point.provenance['blade_index'] == component + 1
    data = charts._plot_data('m_flap', 'T1')
    assert [s[0] for s in data.series] == ['T1 / B1', 'T1 / B2', 'T1 / B3']
    assert [s[1][0][0][1] for s in data.series] == [1e6, 2e6, 3e6]
    assert data.units == ('N m',)
    assert len(charts._plot_data('m_edge', 'ALL').series) == 6
    sample.step += 1
    sample.measure['m_flap'][0, 1] = np.nan
    message = wire(scene, sample, 2)
    assert message['payload']['turbines'][0]['channels']['m_flap']['validity'] == 'invalid'
    history.ingest(message)
    assert history.points('T1', 'm_flap_2')[-1].value is None
    assert history.points('T1', 'm_flap_1')[-1].value == 1e6
    next_scene, next_sample = observation(index=52, interval=2., step=sample.step, episode=1)
    history.ingest(wire(next_scene, next_sample, 3))
    assert len(charts.segments(history.points('T1', 'm_flap_1'))) == 2
    assert history.points('T1', 'm_flap_1')[-1].time == 104.
    aged = wire(next_scene, next_sample, 4)
    for channel in aged['payload']['turbines'][0]['channels'].values():
        channel['source_age_seconds'] = 2.
    history.ingest(aged)
    assert history.points('T1', 'm_edge_3')[-1].validity == 'stale'
    assert history.points('T1', 'm_edge_3')[-1].value is None


@pytest.mark.parametrize('mutate', [
    lambda p: p['timestamp'].update(value=3.),
    lambda p: p['turbines'][0]['channels']['m_flap_1'].update(unit='kN m'),
    lambda p: p['turbines'][0]['channels']['yaw_command'].update(value=True),
    lambda p: p['turbines'][0]['channels']['m_flap'].update(value=[1., 2.]),
    lambda p: p['turbines'][0]['channels']['power']['timestamp'].update(timebase='unix_seconds'),
    lambda p: p['turbines'][0]['channels']['power']['timestamp'].update(value=112.),
])
def test_wire_rejects_inconsistent_timing_units_and_shapes(mutate):
    scene, sample = observation()
    message = wire(scene, sample)
    mutate(message['payload'])
    from wfrl.blender_bridge.messages import ProtocolError as BackendProtocolError
    with pytest.raises(BackendProtocolError):
        encode_message(message)


def test_native_output_time_units_and_source_survive_wire_curves_and_export(monkeypatch):
    from wfrl.blender_bridge.messages import SOLVER_OUTPUT_CHANNELS
    scene, sample = observation()
    sample.measure['output_channels'] = [
        {key: dict(value=30. + i, unit=spec['unit'], validity='valid', error=None,
                   fidelity='DIRECT', provenance=dict(file='/run/Case.T1.out', channel=spec['native']),
                   source_age_seconds=0., stale_after_seconds=2.,
                   timestamp=dict(value=110.75, timebase='simulation_seconds', source='openfast_output'))
         for i, (key, spec) in enumerate(SOLVER_OUTPUT_CHANNELS.items())} for _ in scene.turbines]
    message = wire(scene, sample)
    history, raw = charts.ChartHistory(), HistoryWindow()
    history.ingest(message)
    raw.ingest(message)
    monkeypatch.setattr(charts, 'history', history)
    for key, spec in SOLVER_OUTPUT_CHANNELS.items():
        point = history.points('T1', key)[0]
        assert point.time == 110.75 and point.fidelity == 'DIRECT'
        assert point.provenance['channel'] == spec['native']
        record = next(s['records'][0] for s in raw.snapshot()['series'] if s['key'][-1] == key)
        assert record['data']['timestamp']['value'] == 110.75
        assert record['data']['value'] == point.value
    for group in charts.SOLVER_OUTPUT_GROUPS:
        plot = charts._plot_data(group, 'T1')
        assert len(plot.series) == 3 and len(plot.units) == 1
        assert all(series[1] for series in plot.series)
    assert history.points('T1', 'rotor_speed')[0].value == 30.


def test_fastfarm_adapter_never_uses_unattributed_or_power_inferred_rpm():
    scene, sample = observation()
    sample.measure['rotor_speed'] = np.array([20., 20.])
    records = wire(scene, sample)['payload']['turbines'][0]['channels']
    assert records['rotor_speed']['value'] is None
    assert records['rotor_speed']['validity'] == 'unsupported'


@pytest.mark.parametrize('values', [(9.025, 9.03, 9.035, 9.04), (876., 876.5, 877., 877.5), (-.0001, 0., .0001)])
def test_narrow_range_curve_ticks_remain_distinct(values):
    labels = charts.tick_labels(values)
    assert len(set(labels)) == len(values)
    assert all(float(label) == value for label, value in zip(labels, values))
