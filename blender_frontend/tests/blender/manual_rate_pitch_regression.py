"""Rate-limited manual pitch, frozen trials and independent Down cameras."""
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
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
control = importlib.import_module('wfrl_blender.manual_control')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)


class ManualRatePitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        addon.register()
        assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
        cls.scene = bpy.context.scene

    def setUp(self):
        self.assertTrue(hasattr(self.scene, 'wfrl_manual_pitch_rate_deg_s'))
        self.now = [100.0]
        timer = patch.object(control.time, 'monotonic', side_effect=lambda: self.now[0])
        timer.start()
        self.addCleanup(timer.stop)
        if control.trial_active(self.scene):
            control.trial_action(self.scene, 'END')
        self.scene.wfrl_manual_pitch_rate_deg_s = 0
        self.scene.wfrl_manual_target = 'ALL'
        bpy.ops.wfrl.manual_action(action='RESET')
        self.scene.wfrl_manual_target = 'T1'
        self.entry = self.scene.wfrl_manual_turbines[0]
        self.entry.pitch_deg = 0

    def tick(self, seconds):
        self.now[0] += seconds
        control.tick(now=self.now[0])

    def pitch(self, index=1):
        return -math.degrees(getattr(self.entry, f'blade{index}_object').rotation_euler.z)

    def test_pause_feathers_and_start_retargets_without_jump(self):
        self.scene.wfrl_manual_pitch_rate_deg_s = 5
        self.entry.pitch_deg = 20
        self.tick(2)
        self.assertAlmostEqual(self.pitch(), 10, places=3)
        bpy.ops.wfrl.manual_action(action='PAUSE')
        self.tick(4)
        self.assertAlmostEqual(self.pitch(), 30, places=3)
        self.assertFalse(self.entry.running)
        bpy.ops.wfrl.manual_action(action='START')
        self.assertAlmostEqual(self.pitch(), 30, places=3)
        self.tick(1)
        self.assertAlmostEqual(self.pitch(), 25, places=3)

    def test_pose_snapshot_uses_actual_pitch_and_load_settles(self):
        self.scene.wfrl_manual_pitch_rate_deg_s = 5
        self.entry.pitch_deg = 20
        self.tick(1)
        pose = control.pose_snapshot(self.scene)['T1']
        self.assertAlmostEqual(pose['pitch_deg'], 5, places=3)
        self.assertAlmostEqual(pose['pitch_b1_deg'], 5, places=3)
        control._apply_poses(self.scene, {'T1': pose})
        self.tick(10)
        self.assertAlmostEqual(self.pitch(), 5, places=3)
        self.assertFalse(self.entry.running)

    def test_frozen_trial_uses_its_own_clock_and_remains_pauseable(self):
        self.scene.wfrl_manual_pitch_rate_deg_s = 1
        trial = self.scene.wfrl_manual_trial
        trial.duration_s, trial.interpolation, trial.target_b1_deg = 4, 'LINEAR', 20
        trial.select_b1, trial.select_b2, trial.select_b3 = True, False, False
        control.confirm_trial(self.scene)
        self.tick(2)
        self.assertAlmostEqual(self.pitch(), 10, places=3)
        control.trial_action(self.scene, 'PAUSE')
        self.tick(10)
        self.assertAlmostEqual(self.pitch(), 10, places=3)
        self.assertAlmostEqual(self.pitch(2), 0, places=3)

    def test_retarget_uses_actual_pitch_and_does_not_jump(self):
        self.scene.wfrl_manual_pitch_rate_deg_s = 5
        self.entry.pitch_deg = 20
        self.tick(1)
        self.entry.pitch_deg = -10
        self.assertAlmostEqual(self.pitch(), 5, places=3)
        self.tick(1)
        self.assertAlmostEqual(self.pitch(), 0, places=3)

    def test_pitch_can_resume_without_starting_zero_rpm_rotor(self):
        self.entry.rpm = 0
        self.scene.wfrl_manual_pitch_rate_deg_s = 5
        self.entry.pitch_deg = 20
        self.tick(1)
        control.pause_scene(self.scene)
        self.tick(10)
        self.assertAlmostEqual(self.pitch(), 5, places=3)
        self.assertEqual(bpy.ops.wfrl.manual_pitch_resume(), {'FINISHED'})
        self.tick(1)
        self.assertAlmostEqual(self.pitch(), 10, places=3)
        self.assertFalse(self.entry.running)
        self.assertEqual(self.entry.azimuth_deg, 0)

    def test_rate_change_cannot_write_a_shared_blade(self):
        self.scene.wfrl_manual_pitch_rate_deg_s = 5
        self.entry.pitch_deg = 20
        self.tick(1)
        other = bpy.data.scenes.new('Shared blade owner')
        self.addCleanup(lambda: bpy.data.scenes.remove(other))
        other.collection.objects.link(self.entry.blade1_object)
        self.assertFalse(control.validate_scene(self.scene)[0])
        self.scene.wfrl_manual_pitch_rate_deg_s = 0
        self.assertAlmostEqual(self.pitch(), 5, places=3)

    def test_rate_change_preserves_native_rotation_conflict(self):
        self.scene.wfrl_manual_pitch_rate_deg_s = 5
        self.entry.pitch_deg = 20
        self.tick(1)
        self.entry.yaw_object.rotation_euler.z = .25
        self.scene.wfrl_manual_pitch_rate_deg_s = 2
        self.assertAlmostEqual(self.entry.yaw_object.rotation_euler.z, .25, places=5)
        self.assertTrue(self.entry.pitch_paused)

    def test_constraint_stop_keeps_last_materialized_pose_resumable(self):
        self.scene.wfrl_manual_pitch_rate_deg_s = 5
        self.entry.pitch_deg = 20
        self.tick(1)
        constraint = self.entry.blade1_object.constraints.new('COPY_ROTATION')
        constraint_name = constraint.name
        constraints = self.entry.blade1_object.constraints
        self.addCleanup(lambda: constraints.remove(constraints[constraint_name])
                        if constraints.get(constraint_name) is not None else None)
        self.tick(1)
        self.assertAlmostEqual(self.entry.pitch_actual_b1_deg, 5, places=3)
        self.assertAlmostEqual(self.pitch(), 5, places=3)
        self.entry.blade1_object.constraints.remove(constraint)
        self.assertEqual(bpy.ops.wfrl.manual_pitch_resume(), {'FINISHED'})
        self.tick(1)
        self.assertAlmostEqual(self.pitch(), 10, places=3)

    def test_model_switch_preserves_pose_and_freezes_during_trial(self):
        self.assertTrue(hasattr(self.scene, 'wfrl_manual_blade_shape'))
        from wfrl_blender import manual_scene
        poses = control.pose_snapshot(self.scene)
        self.scene.wfrl_manual_blade_shape = 'nrel5mw'
        self.assertEqual(self.scene.get('wfrl_manual_blade_model'), 'nrel5mw')
        self.assertEqual(control.pose_snapshot(self.scene), poses)
        self.scene.wfrl_manual_blade_shape = 'rootkink'
        control.confirm_trial(self.scene)
        self.scene.wfrl_manual_blade_shape = 'nrel5mw'
        self.assertEqual(self.scene.get('wfrl_manual_blade_model'), 'rootkink')
        self.assertEqual(self.scene.wfrl_manual_blade_shape, 'rootkink')

    @classmethod
    def tearDownClass(cls):
        addon.unregister()


result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ManualRatePitchTests))
(OUT / 'result.json').write_text(json.dumps(dict(
    status='PASS' if result.wasSuccessful() else 'FAIL', passed=result.wasSuccessful(),
    tests=result.testsRun, addon_file=addon.__file__, blender=bpy.app.version_string,
    failures=[dict(test=test.id(), traceback=detail) for test, detail in result.failures],
    errors=[dict(test=test.id(), traceback=detail) for test, detail in result.errors])), encoding='utf-8')
if not result.wasSuccessful():
    raise AssertionError('Manual rate-pitch regression failed')
print('MANUAL_RATE_PITCH_PASS', flush=True)
