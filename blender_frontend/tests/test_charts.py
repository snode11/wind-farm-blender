"""History contracts run without Blender or GPU dependencies."""
import unittest
import pytest
from wfrl_blender.charts import ChartHistory, segments, FARM


def record(value, **changes):
    result = dict(value=value, unit='MW', fidelity='DIRECT', validity='valid', provenance={'backend': 'fake'})
    result.update(changes)
    return result


def snapshot(step=0, ids=('T1', 'T2'), **changes):
    payload = dict(mode='replay', step=step, timestamp=dict(value=step / 10, timebase='simulation_seconds'),
                   farm={'reward': record(0.5, unit='')},
                   turbines=[dict(turbine_id=tid, channels={'power': record(index + step)}) for index, tid in enumerate(ids)])
    payload.update(changes)
    return dict(session_id='session', sequence=step+1, payload=payload)


class ChartTests(unittest.TestCase):
    def test_history_is_bounded_and_by_id_not_position(self):
        history = ChartHistory(3)
        for step in range(8):
            history.ingest(snapshot(step, ('T2', 'T1') if step % 2 else ('T1', 'T2')))
        self.assertEqual(len(history.points('T1', 'power')), 3)
        self.assertEqual(history.points('T1', 'power')[-1].value, 8)
        self.assertEqual(history.points('T2', 'power')[-1].value, 7)
        self.assertEqual(history.points(FARM, 'reward')[-1].value, 0.5)

    def test_missing_nonfinite_and_array_values_are_gaps(self):
        history = ChartHistory()
        for step, value in enumerate((0, None, float('nan'), [1, 2], True, 5)):
            data = snapshot(step)
            data['payload']['turbines'][0]['channels']['power'] = record(value)
            history.ingest(data)
        points = history.points('T1', 'power')
        self.assertEqual([p.value for p in points], [0, None, None, None, None, 5])
        self.assertEqual(len(segments(points)), 2)
        self.assertIsNone(history.points('T1', 'torque')[-1].value)

    def test_source_metadata_is_copied_and_units_split_segments(self):
        history = ChartHistory()
        data = snapshot()
        history.ingest(data)
        data['payload']['turbines'][0]['channels']['power']['provenance']['backend'] = 'mutated'
        self.assertEqual(history.points('T1', 'power')[0].provenance['backend'], 'fake')
        data = snapshot(1)
        data['payload']['turbines'][0]['channels']['power']['unit'] = 'W'
        history.ingest(data)
        self.assertEqual(len(segments(history.points('T1', 'power'))), 2)

    def test_new_session_backward_replay_and_turbine_removal(self):
        history = ChartHistory()
        history.ingest(snapshot(5))
        history.ingest(snapshot(6, ('T2',)))
        self.assertEqual(history.turbine_ids(), ('T2',))
        data = snapshot(1); data['sequence'] = 8
        history.ingest(data)
        self.assertEqual(len(history.points('T2', 'power')), 1)
        data = snapshot(2); data['session_id'] = 'new'
        history.ingest(data)
        self.assertEqual(history.session, 'new')
        self.assertEqual(len(history.points('T2', 'power')), 1)

    def test_duplicates_ignored_and_stale_not_drawn(self):
        history = ChartHistory()
        data = snapshot()
        data['payload']['turbines'][0]['channels']['power'].update(source_age_seconds=2, stale_after_seconds=2)
        self.assertTrue(history.ingest(data))
        self.assertFalse(history.ingest(data))
        self.assertEqual(history.points('T1', 'power')[-1].validity, 'stale')
        self.assertIsNone(history.points('T1', 'power')[-1].value)

    def test_simulation_time_reset_breaks_line_without_discarding_history(self):
        history = ChartHistory()
        for step, stamp in ((4, 12), (5, 0), (6, 3)):
            history.ingest(snapshot(step, timestamp=dict(value=stamp, timebase='simulation_seconds')))
        points = history.points('T1', 'power')
        self.assertEqual(len(points), 3)
        self.assertEqual(segments(points), [[(12, 4)], [(0, 5), (3, 6)]])

    def test_episode_reset_at_same_global_step_preserves_boundary_and_training_stats(self):
        history = ChartHistory()
        first = snapshot(4, context=dict(episode=1))
        history.ingest(first)
        training_stats = history.series[(FARM, 'mean_reward')] = history.series[(FARM, 'reward')].copy()
        second = snapshot(4, context=dict(episode=2))
        second['sequence'] = first['sequence'] + 1
        history.ingest(second)
        points = history.points('T1', 'power')
        self.assertEqual([p.episode for p in points], [1, 2])
        self.assertEqual(len(segments(points)), 2)
        self.assertIs(history.series[(FARM, 'mean_reward')], training_stats)
        self.assertEqual(len(training_stats), 1)


class PlotReadabilityTests(unittest.TestCase):
    def test_constant_and_single_sample_axes_have_room(self):
        from wfrl_blender.charts import axis_ticks
        for value in (0., -2., 1.7e9):
            ticks = axis_ticks([value], count=3)
            self.assertLess(ticks[0], value)
            self.assertGreater(ticks[-1], value)
            self.assertEqual(len(ticks), 3)

    def test_ticks_cover_negative_and_positive_values(self):
        from wfrl_blender.charts import axis_ticks
        ticks = axis_ticks([-12., 5.], count=5)
        self.assertLessEqual(ticks[0], -12.)
        self.assertGreaterEqual(ticks[-1], 5.)
        self.assertTrue(all(a < b for a, b in zip(ticks, ticks[1:])))


def test_farm_records_detach_inputs_and_keep_history_metadata_independent(monkeypatch):
    from copy import deepcopy

    from wfrl_blender import charts
    from wfrl_blender.history import HistoryWindow
    for nested in (False, True):
        monkeypatch.setattr(charts, 'history', ChartHistory())
        monkeypatch.setattr(charts, 'raw_history', HistoryWindow(clock=lambda: 10.))
        monkeypatch.setattr(charts, '_local_sequence', 0)
        manifest = {'source': {'files': ['farm.out']} if nested else 'FAST.Farm',
                    'status': 'verified', 'policy': {'sha256': 'policy'},
                    'files': {'geometry.npz': 'geometry'}, 'turbine_ids': ['T1', 'T2']}
        extra = {'T1': {'power': {'value': 2., 'unit': 'MW', 'source_channel': ['GenPwr'] if nested else 'GenPwr',
                                'source_sha256': 'power-source'}},
                 'T2': {'power': {'value': 3., 'unit': 'MW', 'source_channel': 'GenPwr', 'source_sha256': 'power-source'}}}
        assert charts.record_farm(1.25, [(10., 0., 9., 1., 2., 3.), (20., 0., 8., 4., 5., 6.)], manifest, extra)
        raw = charts.raw_history.series[('snapshot', None, 'T1', 'power')][0]
        yaw = charts.raw_history.series[('snapshot', None, 'T1', 'yaw')][0]
        point = charts.history.points('T1', 'power')[0]
        expected = deepcopy(point.provenance)
        manifest['status'] = 'changed'
        manifest['files']['geometry.npz'] = 'changed'
        extra['T1']['power'].update(value=100., source_sha256='changed')
        if nested:
            manifest['source']['files'].append('changed')
            extra['T1']['power']['source_channel'].append('changed')
            assert point.provenance['source'] == {'files': ['farm.out']}
            assert raw['data']['provenance']['source_channel'] == ['GenPwr']
        assert raw['data']['provenance'] == expected == point.provenance
        assert raw['data']['value'] == point.value == 2.
        assert charts.history.points(FARM, 'power')[0].value == 5.
        assert charts.history.points('T1', 'pitch')[0].value == 2.
        assert charts.history.points('T1', 'torque')[0].value is None
        point.provenance['status'] = 'chart-only'
        yaw['data']['provenance']['status'] = 'yaw-only'
        if nested:
            point.provenance['source']['files'].append('chart-only')
            yaw['data']['provenance']['source']['files'].append('yaw-only')
        assert raw['data']['provenance'] == expected
        assert raw['timestamp'] == {'value': 1.25, 'timebase': 'simulation_seconds'}


def test_scene_history_draw_and_export_never_read_another_scene():
    from wfrl_blender import charts
    class Scene:
        def as_pointer(self): return id(self)
    a, b = Scene(), Scene()
    charts.clear_scene_histories()
    try:
        charts.record_snapshot(snapshot(4), scene=a)
        assert charts.history_for(a).points('T1', 'power')
        assert not charts.history_for(b).points('T1', 'power')
        assert charts._plot_data('power', 'ALL', b).bounds is None
        assert not charts.raw_history_for(b).series
        charts.record_snapshot(snapshot(9), scene=b)
        before = charts.raw_history_for(b).snapshot()
        charts.clear(scene=a)
        charts.record_snapshot(snapshot(1), scene=a)
        assert charts.raw_history_for(b).snapshot() == before
        assert charts.history_for(b).points('T1', 'power')[-1].step == 9
        assert charts._plot_data('power', 'ALL', a).bounds != charts._plot_data('power', 'ALL', b).bounds
        charts.clear_scene_histories()
        assert not charts.history_for(a).points('T1', 'power')
    finally:
        charts.clear_scene_histories()


@pytest.mark.parametrize(('age', 'validity', 'values'), [
    (1.99, 'valid', [1., 2.]), (2., 'stale', [None, None]), (3., 'stale', [None, None]),
])
def test_curve_source_expiry_creates_gaps_without_rewriting_raw_observations(monkeypatch, age, validity, values):
    from wfrl_blender import charts
    from wfrl_blender.history import HistoryWindow
    from wfrl_blender.protocol import validate_message

    monkeypatch.setattr(charts, 'history', ChartHistory())
    monkeypatch.setattr(charts, 'raw_history', HistoryWindow())
    data = dict(value=[[0., 1.], [1., 2.]], unit='MW', fidelity='DIRECT', validity='valid', error=None,
                provenance={'backend_session': 'fixture', 'channel': 'power'},
                source_age_seconds=age, stale_after_seconds=2.)
    message = dict(protocol_version=1, type='curve', session_id='fixture', sequence=1, payload=dict(
        mode='replay', step=1, run_id='run', turbine_id='T1', channel='power',
        timestamp={'value': 1., 'timebase': 'simulation_seconds'}, data=data))
    validate_message(message)
    charts.record_curve(message)
    points = charts.history.points('T1', 'power')
    assert [point.value for point in points] == values
    assert [point.validity for point in points] == [validity, validity]
    assert charts.raw_history.snapshot()['series'][0]['records'][0]['data'] == data


if __name__ == '__main__':
    unittest.main()
