"""Native manual turbine state, zero-speed, pitch and isolation checks."""
import importlib
import json
import math
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy


ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(addon.__file__).resolve().parent == Path(expected_root).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)


def parameters(scene):
    fields = ('yaw_deg', 'pitch_deg', 'rpm', 'azimuth_deg', 'running',
              'individual_pitch', 'pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg')
    return {entry.turbine_id: tuple(getattr(entry, field) for field in fields)
            for entry in scene.wfrl_manual_turbines}


def pose(entry):
    bpy.context.view_layer.update()
    return {field: tuple(value for row in getattr(entry, field).matrix_world for value in row)
            for field in ('yaw_object', 'rotor_object', 'blade1_object',
                          'blade2_object', 'blade3_object')}


class ManualControlState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        addon.register()
        if bpy.ops.wfrl.manual_enter() != {'FINISHED'}:
            raise AssertionError('Could not enter the native manual scene')
        cls.scene = bpy.context.scene
        cls.entries = {entry.turbine_id: entry for entry in cls.scene.wfrl_manual_turbines}
        if set(cls.entries) != {'T1', 'T2', 'T3'}:
            raise AssertionError('Manual scene must contain T1, T2 and T3')
        valid, reason = control.validate_scene(cls.scene)
        if not valid:
            raise AssertionError(reason)

    def setUp(self):
        self.clock = [100.0]
        self.time_patch = patch.object(control.time, 'monotonic', side_effect=lambda: self.clock[0])
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)
        bpy.context.window.scene = self.scene
        control.pause_all()
        # Pitch geometry is checked at once; ramps are tested with an explicit rate.
        self.scene.wfrl_manual_pitch_rate_deg_s = 0.0
        self.scene.wfrl_manual_target = 'ALL'
        self.assertEqual(bpy.ops.wfrl.manual_action(action='RESET'), {'FINISHED'})
        self.scene.wfrl_manual_target = 'T1'
        self.t1 = self.entries['T1']

    def action(self, value):
        return bpy.ops.wfrl.manual_action(action=value)

    def advance(self, seconds):
        self.clock[0] += seconds
        control.tick(now=self.clock[0])

    def assertAngle(self, actual, expected):
        difference = (actual - expected + 180.0) % 360.0 - 180.0
        self.assertAlmostEqual(difference, 0.0, delta=1e-3)

    def assertBladePitch(self, entry, expected):
        for index, pitch in enumerate(expected, 1):
            with self.subTest(turbine=entry.turbine_id, blade=index):
                blade = getattr(entry, f'blade{index}_object')
                self.assertAlmostEqual(math.degrees(blade.rotation_euler.z), -pitch, delta=1e-3)

    def test_stopped_turbine_is_feathered_and_running_pitches_to_fine(self):
        rate_property = bpy.types.Scene.bl_rna.properties['wfrl_manual_pitch_rate_deg_s']
        with self.subTest(check='defaults'):
            self.assertEqual(rate_property.default, 5.0)
            self.assertEqual(self.t1.bl_rna.properties['pitch_deg'].default, 90.0)
        with self.subTest(check='reset_feathers_at_once'):
            self.assertEqual(self.t1.pitch_deg, 90.0)
            self.assertBladePitch(self.t1, (90.0,) * 3)
        self.scene.wfrl_manual_pitch_rate_deg_s = 5.0
        self.t1.rpm = 12.0
        self.assertEqual(self.action('START'), {'FINISHED'})
        self.assertEqual(self.t1.pitch_deg, 0.0)
        self.advance(0.0)  # the first tick starts the ramp clock
        self.advance(2.0)
        with self.subTest(check='start_ramps_at_rate'):
            self.assertBladePitch(self.t1, (80.0,) * 3)
        self.advance(16.0)
        with self.subTest(check='running_fine_pitch'):
            self.assertFalse(control.pitch_ramping(self.t1))
            self.assertBladePitch(self.t1, (0.0,) * 3)
        for name in ('PAUSE', 'STOP'):
            self.t1.rpm = 12.0
            self.assertEqual(self.action('START'), {'FINISHED'})
            self.advance(0.0)
            self.advance(20.0)
            self.assertBladePitch(self.t1, (0.0,) * 3)
            self.assertEqual(self.action(name), {'FINISHED'})
            self.advance(0.0)
            self.advance(4.0)
            with self.subTest(check='feather_ramp', action=name):
                self.assertFalse(self.t1.running)
                self.assertEqual(self.t1.pitch_deg, 90.0)
                self.assertBladePitch(self.t1, (20.0,) * 3)
            self.advance(20.0)
            self.assertBladePitch(self.t1, (90.0,) * 3)

    def test_manual_scene_enters_feathered(self):
        for entry in self.entries.values():
            with self.subTest(turbine=entry.turbine_id):
                self.assertFalse(entry.running)
                self.assertEqual(entry.pitch_deg, 90.0)

    def test_metadata_references_allow_controls_but_scene_links_reject_them(self):
        other = bpy.data.scenes.new('Manual metadata references')
        nested = bpy.data.collections.new('Manual metadata collection')
        other.collection.children.link(nested)
        controlled = self.t1.blade1_object
        try:
            other['control_reference'] = controlled
            nested['control_reference'] = controlled
            valid, reason = control.validate_scene(self.scene)
            self.assertTrue(valid, reason)
            nested.objects.link(controlled)
            self.assertFalse(control.validate_scene(self.scene)[0])
            nested.objects.unlink(controlled)
            valid, reason = control.validate_scene(self.scene)
            self.assertTrue(valid, reason)
        finally:
            bpy.data.scenes.remove(other)
            bpy.data.collections.remove(nested)

    def test_single_zero_rpm_start_is_rejected_and_azimuth_is_editable(self):
        self.t1.rpm = 0.0
        self.t1.yaw_deg, self.t1.pitch_deg, self.t1.azimuth_deg = 21.0, 14.0, 37.0
        before = parameters(self.scene)
        result = self.action('START')
        with self.subTest(check='cancelled'):
            self.assertEqual(result, {'CANCELLED'})
        with self.subTest(check='state_unchanged'):
            self.assertEqual(parameters(self.scene), before)
        with self.subTest(check='paused'):
            self.assertFalse(self.t1.running)
        self.t1.azimuth_deg = 127.0
        self.assertAngle(self.t1.azimuth_deg, 127.0)
        self.assertAngle(math.degrees(self.t1.rotor_object.rotation_euler.x), 127.0)
        self.advance(.25)
        self.assertAngle(self.t1.azimuth_deg, 127.0)

    def test_zero_rpm_edit_pauses_immediately_and_preserves_phase(self):
        self.t1.rpm, self.t1.azimuth_deg = 12.0, 17.0
        self.assertEqual(self.action('START'), {'FINISHED'})
        self.advance(.25)
        self.assertAngle(self.t1.azimuth_deg, 35.0)
        self.clock[0] += .1
        self.t1.rpm = 0.0
        with self.subTest(check='immediately_paused'):
            self.assertFalse(self.t1.running)
        with self.subTest(check='phase_continuity'):
            self.assertAngle(self.t1.azimuth_deg, 42.2)
        self.advance(.5)
        self.assertAngle(self.t1.azimuth_deg, 42.2)
        self.t1.rpm = 9.0
        with self.subTest(check='speed_edit_does_not_resume'):
            self.assertFalse(self.t1.running)
        self.advance(.25)
        with self.subTest(check='remains_paused'):
            self.assertAngle(self.t1.azimuth_deg, 42.2)

    def test_all_mixed_rpm_starts_only_positive_rpm_turbines(self):
        for index, entry in enumerate(self.entries.values()):
            entry.rpm = 12.0 if entry.turbine_id == 'T2' else 0.0
            entry.azimuth_deg, entry.yaw_deg, entry.pitch_deg = 31.0 + index, 10.0 + index, 4.0 + index
        before = parameters(self.scene)
        self.scene.wfrl_manual_target = 'ALL'
        self.assertEqual(self.action('START'), {'FINISHED'})
        for turbine_id, entry in self.entries.items():
            with self.subTest(check='running', turbine=turbine_id):
                self.assertEqual(entry.running, turbine_id == 'T2')
        self.advance(.25)
        for turbine_id, entry in self.entries.items():
            with self.subTest(check='motion_and_parameters', turbine=turbine_id):
                self.assertAngle(entry.azimuth_deg, before[turbine_id][3] + (18.0 if turbine_id == 'T2' else 0.0))
                # Only the started turbine leaves its set pitch for the running pitch.
                pitch = control.RUN_PITCH_DEG if turbine_id == 'T2' else before[turbine_id][1]
                self.assertEqual((entry.yaw_deg, entry.pitch_deg, entry.rpm),
                                 (before[turbine_id][0], pitch, before[turbine_id][2]))

    def test_all_zero_rpm_start_is_rejected_without_parameter_changes(self):
        for index, entry in enumerate(self.entries.values()):
            entry.rpm, entry.yaw_deg, entry.pitch_deg = 0.0, 11.0 + index, 7.0 + index
            entry.azimuth_deg = 33.0 + index
            entry.individual_pitch = True
            entry.pitch_b2_deg = 21.0 + index
        self.scene.wfrl_manual_target = 'ALL'
        before = parameters(self.scene)
        before_poses = {tid: pose(entry) for tid, entry in self.entries.items()}
        result = self.action('START')
        with self.subTest(check='cancelled'):
            self.assertEqual(result, {'CANCELLED'})
        with self.subTest(check='parameters_unchanged'):
            self.assertEqual(parameters(self.scene), before)
        self.advance(.25)
        with self.subTest(check='poses_unchanged'):
            self.assertEqual({tid: pose(entry) for tid, entry in self.entries.items()}, before_poses)

    def test_positive_speed_changes_and_pause_preserve_motion_phase(self):
        self.t1.rpm, self.t1.azimuth_deg = 12.0, 14.0
        self.assertEqual(self.action('START'), {'FINISHED'})
        self.advance(.25)
        self.assertAngle(self.t1.azimuth_deg, 32.0)
        self.clock[0] += .1
        self.t1.rpm = 20.0
        self.assertTrue(self.t1.running)
        self.assertAngle(self.t1.azimuth_deg, 39.2)
        self.advance(.25)
        self.assertAngle(self.t1.azimuth_deg, 69.2)
        self.clock[0] += .1
        self.assertEqual(self.action('PAUSE'), {'FINISHED'})
        self.assertFalse(self.t1.running)
        self.assertEqual(self.t1.rpm, 20.0)
        self.assertAngle(self.t1.azimuth_deg, 81.2)
        self.advance(.5)
        self.assertAngle(self.t1.azimuth_deg, 81.2)
        self.assertEqual(self.action('START'), {'FINISHED'})
        self.advance(.25)
        self.assertAngle(self.t1.azimuth_deg, 111.2)

    def test_collective_pitch_overrides_individual_pitch_values(self):
        self.t1.pitch_deg = 12.0
        self.t1.individual_pitch = True
        self.assertBladePitch(self.t1, (12.0, 12.0, 12.0))
        for index, pitch in enumerate((3.0, 16.0, 27.0), 1):
            setattr(self.t1, f'pitch_b{index}_deg', pitch)
        self.assertEqual(self.t1.pitch_deg, 12.0)
        self.assertBladePitch(self.t1, (3.0, 16.0, 27.0))
        self.t1.pitch_deg = 23.0
        self.assertTrue(self.t1.individual_pitch)
        self.assertEqual(tuple(getattr(self.t1, f'pitch_b{index}_deg') for index in range(1, 4)), (23.0,) * 3)
        self.assertBladePitch(self.t1, (23.0,) * 3)
        self.t1.pitch_b2_deg = 5.0
        self.t1.individual_pitch = False
        self.assertBladePitch(self.t1, (23.0,) * 3)
        self.t1.individual_pitch = True
        self.assertBladePitch(self.t1, (23.0,) * 3)

    def test_batch_collective_pitch_overrides_only_pitch(self):
        for index, entry in enumerate(self.entries.values()):
            entry.yaw_deg, entry.rpm, entry.azimuth_deg = 10.0 + index, 7.0 + index, 33.0 + index
            entry.pitch_deg = 12.0 + index
            entry.individual_pitch = True
            entry.pitch_b2_deg = 27.0 + index
        before = parameters(self.scene)
        self.scene.wfrl_manual_target = 'ALL'
        self.assertEqual(parameters(self.scene), before)
        self.scene.wfrl_manual_batch.pitch_deg = 24.0
        for turbine_id, entry in self.entries.items():
            with self.subTest(turbine=turbine_id):
                self.assertTrue(entry.individual_pitch)
                self.assertEqual(entry.pitch_deg, 24.0)
                self.assertEqual(tuple(getattr(entry, f'pitch_b{index}_deg') for index in range(1, 4)), (24.0,) * 3)
                self.assertEqual((entry.yaw_deg, entry.rpm, entry.azimuth_deg, entry.running),
                                 (before[turbine_id][0], before[turbine_id][2],
                                  before[turbine_id][3], before[turbine_id][4]))
                self.assertBladePitch(entry, (24.0,) * 3)

    def configure_mixed_pitch_batch(self):
        for index, entry in enumerate(self.entries.values(), 1):
            entry.yaw_deg, entry.azimuth_deg = index * 11.0, index * 13.0
            entry.rpm = (0.0, 6.0, 9.0)[index - 1]
        self.scene.wfrl_manual_target = 'ALL'
        self.assertEqual(self.action('START'), {'FINISHED'})
        self.assertEqual(tuple(entry.running for entry in self.entries.values()), (False, True, True))
        # 运行 sets the running pitch; pitch stays adjustable while running.
        self.scene.wfrl_manual_target = 'T1'
        for index, entry in enumerate(self.entries.values(), 1):
            entry.pitch_deg = index * 5.0
        t2 = self.entries['T2']
        t2.individual_pitch = True
        for index, pitch in enumerate((3.0, 9.0, 21.0), 1):
            setattr(t2, f'pitch_b{index}_deg', pitch)
        self.scene.wfrl_manual_target = 'ALL'
        return parameters(self.scene)

    def test_batch_individual_pitch_enable_preserves_existing_independent_values(self):
        before = self.configure_mixed_pitch_batch()
        self.scene.wfrl_manual_batch.individual_pitch = True
        expected = {'T1': (5.0,) * 3, 'T2': (3.0, 9.0, 21.0), 'T3': (15.0,) * 3}
        after = parameters(self.scene)
        for turbine_id, entry in self.entries.items():
            with self.subTest(turbine=turbine_id):
                self.assertTrue(entry.individual_pitch)
                self.assertEqual(after[turbine_id][:5], before[turbine_id][:5])
                self.assertEqual(after[turbine_id][6:], expected[turbine_id])
                self.assertBladePitch(entry, expected[turbine_id])

    def test_repeated_batch_individual_pitch_enable_preserves_values(self):
        self.configure_mixed_pitch_batch()
        for index, entry in enumerate(self.entries.values(), 1):
            entry.individual_pitch = True
            for blade, pitch in enumerate((index * 2.0, index * 4.0, index * 8.0), 1):
                setattr(entry, f'pitch_b{blade}_deg', pitch)
        before = parameters(self.scene)
        for repeat in range(2):
            self.scene.wfrl_manual_batch.individual_pitch = True
            with self.subTest(repeat=repeat):
                self.assertEqual(parameters(self.scene), before)
                for turbine_id, entry in self.entries.items():
                    self.assertBladePitch(entry, before[turbine_id][6:])

    def test_batch_individual_pitch_reenable_initializes_current_collective(self):
        self.configure_mixed_pitch_batch()
        before = parameters(self.scene)
        self.scene.wfrl_manual_batch.individual_pitch = False
        for turbine_id, entry in self.entries.items():
            with self.subTest(check='disabled', turbine=turbine_id):
                self.assertFalse(entry.individual_pitch)
                self.assertEqual(parameters(self.scene)[turbine_id][:5], before[turbine_id][:5])
                self.assertEqual(parameters(self.scene)[turbine_id][6:], before[turbine_id][6:])
                self.assertBladePitch(entry, (entry.pitch_deg,) * 3)
        for index, entry in enumerate(self.entries.values(), 1):
            entry.pitch_deg = index * 7.0
            entry.pitch_b2_deg = index * 3.0
        before = parameters(self.scene)
        self.scene.wfrl_manual_batch.individual_pitch = True
        for turbine_id, entry in self.entries.items():
            with self.subTest(check='reenabled', turbine=turbine_id):
                self.assertTrue(entry.individual_pitch)
                self.assertEqual(parameters(self.scene)[turbine_id][:5], before[turbine_id][:5])
                self.assertEqual(parameters(self.scene)[turbine_id][6:], (entry.pitch_deg,) * 3)
                self.assertBladePitch(entry, (entry.pitch_deg,) * 3)

    def test_single_turbine_controls_leave_other_turbines_unchanged(self):
        before = parameters(self.scene)
        before_poses = {tid: pose(self.entries[tid]) for tid in ('T2', 'T3')}
        self.t1.yaw_deg, self.t1.pitch_deg, self.t1.rpm, self.t1.azimuth_deg = 57.0, 18.0, 10.0, 22.0
        self.t1.individual_pitch = True
        self.t1.pitch_b2_deg = 31.0
        self.assertEqual(self.action('START'), {'FINISHED'})
        self.advance(.25)
        self.assertEqual(self.action('PAUSE'), {'FINISHED'})
        self.t1.rpm = 0.0
        for turbine_id in ('T2', 'T3'):
            with self.subTest(turbine=turbine_id):
                self.assertEqual(parameters(self.scene)[turbine_id], before[turbine_id])
                self.assertEqual(pose(self.entries[turbine_id]), before_poses[turbine_id])


suite = unittest.defaultTestLoader.loadTestsFromTestCase(ManualControlState)
result = unittest.TestResult()
try:
    suite.run(result)
finally:
    addon.unregister()
report = dict(status='PASS' if result.wasSuccessful() else 'FAIL',
              scope='Isolated background Blender native software regression',
              addon_module=MODULE, addon_file=addon.__file__, blender=bpy.app.version_string,
              tests_run=result.testsRun,
              failures=[dict(test=test.id(), traceback=detail) for test, detail in result.failures],
              errors=[dict(test=test.id(), traceback=detail) for test, detail in result.errors])
(OUT / 'checks.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report), flush=True)
if not result.wasSuccessful():
    raise AssertionError('Manual control state regression failed; see checks.json')

print('MANUAL_CONTROL_STATE_PASS', flush=True)
