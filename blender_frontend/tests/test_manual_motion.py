"""Manual rotor phase continuity across speed and motion-state changes."""
import unittest

from wfrl_blender.manual_motion import RotorClock


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


if __name__ == '__main__':
    unittest.main()
