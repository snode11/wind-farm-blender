"""Part 4 only: live wall-time integration and metadata semantics."""
import math
import unittest
from types import SimpleNamespace
from wfrl_blender.animation import KinematicState, apply_pose, integrate_rotor_angle, pose_values


def record(value, unit='deg', **updates):
    result = dict(value=value, unit=unit, validity='valid', error=None, fidelity='DIRECT',
                  provenance={'backend_session': 'run', 'channel': 'test'},
                  source_age_seconds=0, stale_after_seconds=2)
    result.update(updates)
    return result


def snapshot(rpm=12):
    return {'scene': {'name': 'live', 'backend': 'floris', 'turbine': 'nrel5mw', 'dt': 1,
                      'layout': [{'id': 'custom', 'x': 0, 'y': 0}],
                      'inflow': {'speed': 8, 'direction': 270}}, 'step': 99, 'timestamp': {'value': 1e8, 'timebase': 'simulation_seconds'},
            'mode': 'interactive_training', 'farm': {'reward': record(1, '')},
            'turbines': [{'turbine_id': 'custom', 'channels': {
                'yaw': record(30), 'pitch': record(5), 'rotor_speed': record(rpm, 'rpm')}}]}


class AnimationMathTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.state = KinematicState(clock=lambda: self.now)
        self.state.apply_snapshot(snapshot())

    def test_wall_time_partition_and_snapshot_does_not_reset_phase(self):
        self.state.advance(.25); self.state.advance(.25)
        expected = 12 * math.tau / 60 * .5
        self.assertAlmostEqual(self.state.rotor_angles['custom'], expected)
        self.state.apply_snapshot(snapshot(6)); self.state.advance(.5)
        self.assertAlmostEqual(self.state.rotor_angles['custom'], expected + 6 * math.tau / 60 * .5)

    def test_pause_disconnect_and_staleness_freeze(self):
        self.state.advance(1, running=False); self.state.advance(1, connected=False)
        self.now += 2
        self.state.advance(1)
        self.assertEqual(self.state.rotor_angles['custom'], 0)
        self.assertEqual(self.state.validity(record(2)), 'stale')

    def test_units_axes_ids_and_invalid_never_zero(self):
        objects = {f'WFRL.Turbine.custom.{name}': SimpleNamespace(rotation_euler=SimpleNamespace(x=0., z=7.))
                   for name in ['YawRoot', 'Rotor', 'Blade1', 'Blade2', 'Blade3']}
        self.state.apply_objects(objects)
        self.assertAlmostEqual(objects['WFRL.Turbine.custom.YawRoot'].rotation_euler.z, math.pi / 6)
        self.assertAlmostEqual(objects['WFRL.Turbine.custom.Blade2'].rotation_euler.z, math.radians(5))
        data = snapshot(); data['turbines'][0]['channels']['yaw'] = record(None, validity='invalid', error='missing')
        self.state.apply_snapshot(data); self.state.apply_objects(objects)
        self.assertAlmostEqual(objects['WFRL.Turbine.custom.YawRoot'].rotation_euler.z, math.pi / 6)

    def test_metadata_preserved_and_source_age_used(self):
        data = snapshot(); data['farm']['reward'] = record(None, '', validity='unsupported', error='no reward', fidelity='SYNTH', provenance={'formula': 'none'})
        self.state.apply_snapshot(data)
        self.assertEqual(self.state.snapshot, data)
        self.assertEqual(self.state.validity(record(1, source_age_seconds=2)), 'stale')
        self.assertEqual(self.state.validity(data['farm']['reward']), 'unsupported')

    def test_exact_integration_and_paused_manual_priority(self):
        self.assertAlmostEqual(integrate_rotor_angle(0.0, 12.0, .5), math.pi / 5)
        self.assertAlmostEqual(integrate_rotor_angle(1.0, 12.0, .5, running=False), 1.0)
        self.assertEqual(pose_values(4.0, 3.0, manual=(9.0, 8.0), manual_enabled=True, paused=True), (9.0, 8.0))
        self.assertEqual(pose_values(4.0, 3.0, manual=(9.0, 8.0), manual_enabled=True, paused=False), (4.0, 3.0))
