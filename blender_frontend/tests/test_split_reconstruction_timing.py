"""Saved-sample alignment, reverse seeking, and invalid clock metadata."""
import math
import unittest

from wfrl_blender.split_reconstruction_timing import (
    METADATA_KEYS, TimingSpec, spec_from_scene, step_sample_frame, timing_for_frame,
)


class SplitReconstructionTimingTests(unittest.TestCase):
    def test_native_hold_exposes_sample_age_instead_of_relabelling_it(self):
        cases = [
            (1, 117.0, 117.0, 0.0, 0),
            (6, 117.0 + 5 / 60, 117.0, 1000 * 5 / 60, 0),
            (7, 117.1, 117.1, 0.0, 1),
            (52, 117.85, 117.8, 50.0, 8),
            (3601, 177.0, 177.0, 0.0, 600),
        ]
        for frame, playback, sample, age_ms, index in cases:
            with self.subTest(frame=frame):
                value = timing_for_frame(frame)
                self.assertTrue(value.in_range)
                self.assertAlmostEqual(value.timeline_time_s, playback)
                self.assertAlmostEqual(value.sample_time_s, sample)
                self.assertAlmostEqual(value.age_ms, age_ms)
                self.assertEqual(value.index, index)

    def test_off_grid_step_goes_strictly_to_previous_or_next_saved_sample(self):
        for frame, previous, following in [(1, 1, 7), (6, 1, 7), (7, 1, 13),
                                            (52, 49, 55), (3601, 3595, 3601)]:
            with self.subTest(frame=frame):
                self.assertEqual(step_sample_frame(frame, -1), previous)
                self.assertEqual(step_sample_frame(frame, 1), following)
        # Moving backward and then forward lands on actual samples, even when
        # the initial position was between two saved observations.
        self.assertEqual(step_sample_frame(step_sample_frame(52, -1), 1), 55)

    def test_outside_saved_interval_is_explicit_and_step_is_clamped(self):
        for frame, index, boundary in [(-100, 0, 1), (0, 0, 1), (3602, 600, 3601),
                                       (9999, 600, 3601)]:
            with self.subTest(frame=frame):
                value = timing_for_frame(frame)
                self.assertFalse(value.in_range)
                self.assertIsNone(value.age_s)
                self.assertIsNone(value.age_ms)
                self.assertEqual(value.index, index)
                self.assertEqual(step_sample_frame(frame, -1), boundary)
                self.assertEqual(step_sample_frame(frame, 1), boundary)

    def test_subframe_before_sample_does_not_read_future_geometry(self):
        value = timing_for_frame(6.5)
        self.assertEqual(value.index, 0)
        self.assertAlmostEqual(value.age_ms, 1000 * 5.5 / 60)
        self.assertEqual(step_sample_frame(6.5, 1), 7)
        self.assertEqual(step_sample_frame(6.5, -1), 1)

    def test_saved_metadata_controls_mapping_without_render_fps(self):
        values = dict(start_s=200, timeline_fps=50, stride=5, samples=3, frame_start=11)
        scene = {METADATA_KEYS[name]: value for name, value in values.items()}
        # Blender playback settings are intentionally irrelevant to source time.
        scene['render_fps'] = 24
        spec = spec_from_scene(scene)
        value = timing_for_frame(19, spec)
        self.assertAlmostEqual(value.timeline_time_s, 200.16)
        self.assertAlmostEqual(value.sample_time_s, 200.1)
        self.assertAlmostEqual(value.age_ms, 60)
        self.assertEqual(value.index, 1)
        self.assertEqual(spec.frame_end, 21)
        self.assertEqual(spec.sampling_hz, 10)
        self.assertEqual(step_sample_frame(19, 1, spec), 21)

    def test_legacy_fallback_does_not_mix_with_partial_metadata(self):
        self.assertEqual(spec_from_scene({'split_reconstruction_review': True}), TimingSpec())
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            spec_from_scene({METADATA_KEYS['start_s']: 120})
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            spec_from_scene({METADATA_KEYS['frame_start']: 9})

    def test_invalid_mapping_and_frame_values_are_rejected(self):
        for fields in [dict(timeline_fps=0), dict(timeline_fps=-60), dict(start_s=math.nan),
                       dict(stride=0), dict(stride=1.5), dict(samples=0), dict(samples=True),
                       dict(frame_start=1.2), dict(timeline_fps='60')]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                TimingSpec(**fields)
        for frame in (math.inf, math.nan, True, '52'):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                timing_for_frame(frame)
        for direction in (0, 2, True):
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                step_sample_frame(52, direction)

    def test_single_sample_does_not_create_later_observations(self):
        spec = TimingSpec(samples=1)
        self.assertEqual(step_sample_frame(1, 1, spec), 1)
        self.assertEqual(step_sample_frame(1, -1, spec), 1)
        self.assertFalse(timing_for_frame(2, spec).in_range)


if __name__ == '__main__':
    unittest.main()
