import unittest
from array import array
from types import SimpleNamespace
from wfrl_blender import overlays
from wfrl_blender.animation import KinematicState


class LiveOverlayTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.poses = KinematicState(clock=lambda: self.now)
        self.ui = SimpleNamespace(connection='CONNECTED', confirmed=True, run_status='RUNNING')
        record = dict(value=1., unit='MW', fidelity='DIRECT', validity='valid',
                      source_age_seconds=0., stale_after_seconds=2.)
        self.poses.apply_snapshot(dict(step=1, timestamp={'value': 3., 'timebase': 'simulation_seconds'},
            scene={'backend': 'fastfarm', 'inflow': {'speed': 8}},
            turbines=[{'turbine_id': 'T1', 'channels': {'power': record}},
                      {'turbine_id': 'T2', 'channels': {'power': dict(record)}}]))

    def lines(self):
        return '\n'.join(overlays.live_overlay(self.ui, self.poses))

    def test_blender_compatible_numeric_array(self):
        self.assertIn('POWER   3.00 MW', overlays.scene_overlay({'wfrl_power_mw': array('d', [1, 2])}))

    def test_authoritative_lifecycle_and_fresh_power(self):
        self.assertIn('POWER   2.00 MW', self.lines())
        self.ui.run_status = 'PAUSED'
        self.assertIn('STATUS  PAUSED', self.lines())

    def test_expiry_without_new_snapshot(self):
        self.now = 2.
        self.assertIn('STALE', self.lines())
        self.assertNotIn('2.00 MW', self.lines())

    def test_disconnect_and_unconfirmed_reconnect_hide_power(self):
        self.ui.connection = 'DISCONNECTED'
        self.assertIn('DISCONNECTED', self.lines())
        self.assertNotIn('2.00 MW', self.lines())
        self.ui.connection = 'CONNECTED'
        self.ui.confirmed = False
        self.assertIn('UNCONFIRMED', self.lines())
        self.assertNotIn('2.00 MW', self.lines())

    def test_missing_turbine_is_not_a_farm_total(self):
        self.poses.snapshot['turbines'][1]['channels'].clear()
        self.assertIn('PARTIAL 1/2', self.lines())
        self.assertNotIn('1.00 MW', self.lines())

    def test_units_are_converted_before_summing(self):
        self.poses.snapshot['turbines'][1]['channels']['power'].update(value=2000000., unit='W')
        self.assertIn('POWER   3.00 MW', self.lines())

    def test_waiting_does_not_read_previous_scene_values(self):
        self.poses.snapshot = None
        self.assertIn('WAITING', self.lines())
        self.assertNotIn('MW', self.lines())
