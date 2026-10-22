"""History contracts run without Blender or GPU dependencies."""
import unittest
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


if __name__ == '__main__':
    unittest.main()
