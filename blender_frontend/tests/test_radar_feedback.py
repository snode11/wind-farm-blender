"""Alarm and pulse semantics against saved measurements and time navigation."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from wfrl.lidar.replay import ReplayReader, precompute
from wfrl_blender.radar_feedback import alarm_state, beam_activity

DATA = Path(__file__).parents[1] / 'wfrl_blender/assets/mappo'


class RadarFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((DATA / 'manifest.json').read_text())
        cls.data = json.loads((DATA / 'data.json').read_text())
        cls.readers = {tid: ReplayReader(SimpleNamespace(manifest=cls.manifest, **payload))
                       for tid, payload in cls.data.items()}

    def test_actual_red_green_and_expired(self):
        reader = self.readers['T1']
        for t, expected in [(117, 'waiting'), (117.775, 'near_threshold'),
                            (142.1, 'above_threshold'), (166.825, 'near_threshold')]:
            self.assertEqual(alarm_state(reader.at(t)), expected)
        value = reader.at(166.825)
        self.assertAlmostEqual(value['measurement']['estimate_m'], 6.430365186)
        expiry = value['measurement']['expires_at_s']
        self.assertEqual(alarm_state(reader.at(expiry + .001)), 'waiting')

    def test_saved_alarm_counts_and_beam_identity(self):
        for tid, count, b1_count in [('T1', 10, 0), ('T2', 33, 3), ('T3', 27, 0)]:
            measurements = self.data[tid]['measurements']
            self.assertEqual(sum(m['beams']['B2']['valid'] and m['beams']['B2']['estimate_m'] <= 7
                                 for m in measurements), count)
            self.assertEqual(sum(m['beams']['B1']['valid'] for m in measurements), b1_count)
            reader = self.readers[tid]
            for record in measurements:
                active = beam_activity(reader, record['time_s'])
                for i, beam in enumerate(('B1', 'B2', 'B3')):
                    if record['beams'][beam]['valid']:
                        self.assertTrue(active[i])
                if not b1_count:
                    self.assertFalse(active[0])

    def test_seek_pause_and_pulse_expiry_are_independent_of_held_reading(self):
        reader = self.readers['T1']
        self.assertFalse(beam_activity(reader, 117.749)[1])
        self.assertTrue(beam_activity(reader, 117.75)[1])
        self.assertTrue(beam_activity(reader, 117.90)[1])
        self.assertFalse(beam_activity(reader, 118.0)[1])
        self.assertEqual(alarm_state(reader.at(118.0)), 'near_threshold')
        for t in [166.825, 142.1, 117.75, 117, 166.825]:
            a = (alarm_state(reader.at(t)), beam_activity(reader, t))
            self.assertEqual(a, (alarm_state(reader.at(t)), beam_activity(reader, t)))
        self.assertEqual(beam_activity(reader, 117), (False, False, False))

    def test_threshold_hysteresis_and_no_truth_fallback(self):
        source = next(r for r in self.data['T1']['measurements'] if r['beams']['B2']['valid'])
        records = []
        for t, estimate in [(117.1, 7.0), (117.2, 7.05), (117.3, 7.1)]:
            r = copy.deepcopy(source)
            r.update(time_s=t, truth_m=20.)
            r['beams']['B2'].update(estimate_m=estimate, error_m=estimate-20)
            records.append(r)
        cumulative, statistics = precompute(records, self.data['T1']['motion'], self.manifest['replay'])
        reader = ReplayReader(SimpleNamespace(manifest=self.manifest, motion=self.data['T1']['motion'],
                              measurements=records, cumulative=cumulative, statistics=statistics))
        self.assertEqual([alarm_state(reader.at(t)) for t in (117.1,117.2,117.3)],
                         ['near_threshold','near_threshold','above_threshold'])
        self.assertEqual(alarm_state(None), 'waiting')
        self.assertEqual(alarm_state({'measurement': None, 'status': 'above_threshold'}), 'waiting')


if __name__ == '__main__':
    unittest.main()
