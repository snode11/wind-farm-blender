"""Manual rotor phase continuity across speed and motion-state changes."""
import unittest

from wfrl_blender.manual_motion import PitchTrial, RotorClock, TrialClock
from wfrl_blender import manual_motion


class RatePitchTests(unittest.TestCase):
    def test_rate_limited_pitch_holds_target_in_both_directions(self):
        self.assertTrue(callable(getattr(manual_motion, 'pitch_at', None)))
        for args, expected in (((0, 20, 5, 2), 10), ((20, 0, 5, 3), 5),
                               ((0, 20, 5, 9), 20), ((2, 20, 0, 0), 20)):
            self.assertEqual(manual_motion.pitch_at(*args), expected)

    def test_retarget_and_rate_change_preserve_actual_pitch(self):
        self.assertTrue(callable(getattr(manual_motion, 'PitchClock', None)))
        clock = manual_motion.PitchClock((0, 10, 20), (20, 0, 20), 5, True, 100)
        self.assertEqual(clock.configure(102, targets=(0, 20, 20), rate=2), (10, 0, 20))
        self.assertEqual(clock.sample(104), (6, 4, 20))
        self.assertEqual(clock.configure(105, running=False), (4, 6, 20))
        self.assertEqual(clock.sample(900), (4, 6, 20))
        clock.configure(901, running=True)
        self.assertEqual(clock.sample(902), (2, 8, 20))

    def test_partitioned_ticks_and_zero_rate(self):
        self.assertTrue(callable(getattr(manual_motion, 'PitchClock', None)))
        clock = manual_motion.PitchClock((0, 0, 0), (10, -10, 5), 5, True, 0)
        for i in range(1, 121):
            clock.sample(i / 60)
        for actual, expected in zip(clock.pitches, (10, -10, 5)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(clock.configure(2, targets=(20, 30, 40), rate=0), (20, 30, 40))

    def test_invalid_rate_and_targets_rejected(self):
        self.assertTrue(callable(getattr(manual_motion, 'pitch_at', None)))
        for args in ((0, 2, -1, 0), (0, float('nan'), 1, 1), (0, 2, 1, float('inf'))):
            with self.assertRaises(ValueError):
                manual_motion.pitch_at(*args)


class ManualMotionTests(unittest.TestCase):
    def test_speed_change_integrates_each_interval_at_its_own_rpm(self):
        clock = RotorClock(15.0, 10.0, True, 100.0)
        clock.configure(100.5, rpm=20.0)
        self.assertAlmostEqual(clock.azimuth_deg, 45.0)
        self.assertAlmostEqual(clock.sample(101.0), 105.0)

    def test_pause_retains_speed_and_resume_retains_phase(self):
        clock = RotorClock(0.0, 12.0, True, 0.0)
        clock.configure(1.0, running=False)
        self.assertEqual(clock.rpm, 12.0)
        self.assertAlmostEqual(clock.sample(20.0), 72.0)
        clock.configure(30.0, running=True)
        self.assertAlmostEqual(clock.sample(30.5), 108.0)

    def test_stop_does_not_reset_phase_and_zero_speed_does_not_move(self):
        clock = RotorClock(300.0, 12.0, True, 0.0)
        clock.configure(1.0, rpm=0.0, running=False)
        self.assertAlmostEqual(clock.azimuth_deg, 12.0)
        clock.configure(2.0, running=True)
        self.assertAlmostEqual(clock.sample(20.0), 12.0)

    def test_paused_angle_and_partitioned_ticks(self):
        clock = RotorClock(0.0, 9.0, False, 0.0)
        clock.configure(5.0, azimuth_deg=400.0, running=True)
        for step in range(1, 61):
            clock.sample(5.0 + step / 60.0)
        self.assertAlmostEqual(clock.azimuth_deg, 94.0)

    def test_invalid_speed_and_time_rejected(self):
        clock = RotorClock()
        for rpm in (-1.0, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                clock.configure(0.0, rpm=rpm)
        with self.assertRaises(ValueError):
            clock.sample(float('nan'))


class PitchTrialTests(unittest.TestCase):
    def spec(self, selected=(True, True, True), **changes):
        values = dict(azimuth_deg=341.0, rpm=9.0, start_pitches=(3.0, 7.0, 11.0),
                      targets=(23.0, -13.0, 41.0), selected=selected, duration_s=8.0)
        return PitchTrial(**{**values, **changes})

    def test_one_two_and_three_blades_hold_unselected_and_finish_together(self):
        for selected in ((True, False, False), (False, True, True), (True, True, True)):
            trial = self.spec(selected)
            self.assertEqual(trial.pose_at(0).pitches_deg, trial.start_pitches)
            expected = tuple((a + b) / 2 if enabled else a
                             for a, b, enabled in zip(trial.start_pitches, trial.targets, selected))
            self.assertEqual(trial.pose_at(4).pitches_deg, expected)
            targets = tuple(b if enabled else a
                            for a, b, enabled in zip(trial.start_pitches, trial.targets, selected))
            self.assertEqual(trial.pose_at(8).pitches_deg, targets)
            self.assertEqual(trial.pose_at(80).pitches_deg, targets)
            self.assertEqual(trial.pose_at(80).azimuth_deg, 341 + 54 * 80)

    def test_clock_pause_resume_reset_and_tick_partition(self):
        clock = TrialClock(self.spec(), running=True, last_time=100)
        direct = TrialClock(self.spec(), running=True, last_time=100)
        for step in range(1, 241):
            clock.sample(100 + step / 60)
        self.assertAlmostEqual(clock.elapsed_s, 4)
        self.assertEqual(clock.sample(104), direct.sample(104))
        paused = clock.configure(104, running=False)
        self.assertEqual(clock.sample(900), paused)
        self.assertEqual(clock.configure(901, running=True), paused)
        end = clock.sample(905)
        self.assertEqual(end, self.spec().pose_at(8))
        self.assertEqual(clock.configure(906, running=False, reset=True), self.spec().pose_at(0))

    def test_smooth_endpoints_and_order_independent_sampling(self):
        spec = self.spec()
        self.assertLess(abs(spec.pose_at(1e-5).pitches_deg[0] - 3), 1e-8)
        self.assertLess(abs(spec.pose_at(8 - 1e-5).pitches_deg[0] - 23), 1e-8)
        self.assertEqual(spec.pose_at(1), self.spec().pose_at(1))
        spec.pose_at(500)
        self.assertEqual(spec.pose_at(1), self.spec().pose_at(1))

    def test_linear_single_blade_five_degrees_per_second_and_long_hold(self):
        spec = self.spec(selected=(True, False, False), start_pitches=(0, 0, 0),
                         targets=(20, 0, 0), duration_s=4, interpolation='LINEAR')
        for index in range(4500):
            time_s = index / 25
            pose = spec.pose_at(time_s)
            with self.subTest(frame=index):
                self.assertAlmostEqual(pose.pitches_deg[0], min(20, 5 * time_s))
                self.assertEqual(pose.pitches_deg[1:], (0, 0))
                self.assertAlmostEqual(pose.azimuth_deg, 341 + 54 * time_s)
        self.assertEqual(spec.pose_at(4).pitches_deg, (20, 0, 0))
        self.assertEqual(spec.pose_at(180).pitches_deg, (20, 0, 0))

    def test_linear_return_uses_constant_negative_speed(self):
        spec = self.spec(selected=(True, False, False), start_pitches=(20, 7, 11),
                         targets=(0, 0, 0), duration_s=4, interpolation='LINEAR')
        for time_s in (0, .04, 1, 2, 3, 4, 180):
            self.assertEqual(spec.pose_at(time_s).pitches_deg,
                             (max(0, 20 - 5 * time_s), 7, 11))

    def test_atomic_spec_validation(self):
        for changes in (dict(selected=(False,) * 3), dict(rpm=0), dict(rpm=31),
                        dict(duration_s=0), dict(duration_s=float('nan')),
                        dict(targets=(1, 2, float('inf'))), dict(targets=(1, 2, 91)),
                        dict(start_pitches=(1, 2)), dict(selected=(1, True, False)),
                        dict(interpolation='UNKNOWN')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.spec(**changes)
        for time_s in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.spec().pose_at(time_s)


if __name__ == '__main__':
    unittest.main()
