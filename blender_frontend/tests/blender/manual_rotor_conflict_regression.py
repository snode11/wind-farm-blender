"""Ordinary rotor motion preserves native edits and rejects competing controls."""
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(Path(__file__).resolve().parent))
import _native
MODULE = _native.addon_module()
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
_native.check_addon_root(addon)
OUT = _native.output_directory()
addon.register()
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
scene = bpy.context.scene


class OrdinaryRotorConflicts(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        timer = patch.object(control.time, 'monotonic', side_effect=lambda: self.now[0])
        timer.start()
        self.addCleanup(timer.stop)
        self.entries = {entry.turbine_id: entry for entry in scene.wfrl_manual_turbines}
        for entry in self.entries.values():
            for field in control._POINTER_FIELDS:
                obj = getattr(entry, field)
                obj.constraints.clear()
                obj.animation_data_clear()
        control.pause_all()
        scene.wfrl_manual_target = 'ALL'
        assert bpy.ops.wfrl.manual_action(action='RESET') == {'FINISHED'}
        scene.wfrl_manual_target = 'T1'
        self.t1 = self.entries['T1']

    def start(self):
        try:
            return bpy.ops.wfrl.manual_action(action='START')
        except RuntimeError:
            return {'CANCELLED'}

    def test_start_rejects_constraint_without_changing_the_rotor(self):
        self.t1.rotor_object.constraints.new('LIMIT_ROTATION')
        before = tuple(self.t1.rotor_object.rotation_euler)
        self.assertEqual(self.start(), {'CANCELLED'})
        self.assertFalse(self.t1.running)
        self.assertEqual(tuple(self.t1.rotor_object.rotation_euler), before)

    def test_start_rejects_native_rotation_without_overwriting_it(self):
        self.t1.rotor_object.rotation_euler.x = .25
        self.assertEqual(self.start(), {'CANCELLED'})
        self.assertFalse(self.t1.running)
        self.assertAlmostEqual(self.t1.rotor_object.rotation_euler.x, .25)

    def test_all_start_rejects_a_conflict_before_starting_any_turbine(self):
        scene.wfrl_manual_target = 'ALL'
        self.entries['T2'].rotor_object.constraints.new('LIMIT_ROTATION')
        self.assertEqual(self.start(), {'CANCELLED'})
        self.assertFalse(any(entry.running for entry in self.entries.values()))

    def test_running_rotor_pauses_when_a_native_constraint_is_added(self):
        self.assertEqual(self.start(), {'FINISHED'})
        self.now[0] = 100.25
        control.tick(now=self.now[0])
        before = self.t1.azimuth_deg
        self.t1.rotor_object.constraints.new('LIMIT_ROTATION')
        self.now[0] = 101.0
        control.tick(now=self.now[0])
        self.assertFalse(self.t1.running)
        self.assertEqual(self.t1.azimuth_deg, before)

    def test_running_rotor_pauses_without_overwriting_native_rotation(self):
        self.assertEqual(self.start(), {'FINISHED'})
        self.t1.rotor_object.rotation_euler.x = .25
        self.now[0] = 101.0
        control.tick(now=self.now[0])
        self.assertFalse(self.t1.running)
        self.assertAlmostEqual(self.t1.rotor_object.rotation_euler.x, .25)
        self.assertEqual(self.t1.azimuth_deg, 0.0)

    def test_scene_pause_preserves_native_rotation_before_the_next_tick(self):
        self.assertEqual(self.start(), {'FINISHED'})
        self.t1.rotor_object.rotation_euler.x = .25
        control.pause_scene(scene, now=101.0)
        self.assertFalse(self.t1.running)
        self.assertAlmostEqual(self.t1.rotor_object.rotation_euler.x, .25)
        self.assertEqual(self.t1.azimuth_deg, 0.0)

    def test_pause_button_preserves_native_rotation_before_the_next_tick(self):
        self.assertEqual(self.start(), {'FINISHED'})
        self.t1.rotor_object.rotation_euler.x = .25
        self.now[0] = 101.0
        try:
            bpy.ops.wfrl.manual_action(action='PAUSE')
        except RuntimeError:
            pass
        self.assertFalse(self.t1.running)
        self.assertAlmostEqual(self.t1.rotor_object.rotation_euler.x, .25)
        self.assertEqual(self.t1.azimuth_deg, 0.0)

    def test_pose_preset_validates_all_selected_entries_before_writing(self):
        scene.wfrl_manual_target = 'ALL'
        self.entries['T2'].rotor_object.constraints.new('LIMIT_ROTATION')
        before = tuple(entry.azimuth_deg for entry in self.entries.values())
        try:
            result = bpy.ops.wfrl.manual_pose_preset(preset='BLADE_DOWN')
        except RuntimeError:
            result = {'CANCELLED'}
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(tuple(entry.azimuth_deg for entry in self.entries.values()), before)
        self.entries['T2'].rotor_object.constraints.clear()
        self.t1.rotor_object.rotation_euler.x = .25
        self.assertEqual(bpy.ops.wfrl.manual_pose_preset(preset='BLADE_DOWN'), {'FINISHED'})
        self.assertEqual(tuple(entry.azimuth_deg for entry in self.entries.values()), (180.,) * 3)

    def test_direct_and_batch_values_reject_native_overrides(self):
        self.t1.yaw_object.constraints.new('LIMIT_ROTATION')
        before = tuple(entry.yaw_deg for entry in self.entries.values())
        self.t1.yaw_deg = 35.
        self.assertEqual(self.t1.yaw_deg, before[0])
        self.assertIn('约束', scene.wfrl_manual_error)
        scene.wfrl_manual_target = 'ALL'
        scene.wfrl_manual_batch.yaw_deg = 50.
        self.assertEqual(tuple(entry.yaw_deg for entry in self.entries.values()), before)
        self.assertEqual(scene.wfrl_manual_batch.yaw_deg, before[0])

    def test_paused_pose_export_rejects_native_rotation_constraint_and_driver(self):
        target=OUT/'paused-pose.json'
        target.write_text('preserved existing export')
        for kind in ('rotation','constraint','driver'):
            control._apply_poses(scene, {'T1':control.pose_snapshot(scene)['T1']}, pause=True, ramp=False)
            if kind=='rotation': self.t1.rotor_object.rotation_euler.x += .2
            elif kind=='constraint': self.t1.yaw_object.constraints.new('COPY_ROTATION')
            else: self.t1.blade1_object.driver_add('rotation_euler',2)
            try:
                try: result=bpy.ops.wfrl.manual_pose_export(filepath=str(target))
                except RuntimeError: result={'CANCELLED'}
                self.assertEqual(result,{'CANCELLED'})
                self.assertEqual(target.read_text(),'preserved existing export')
            finally:
                self.t1.yaw_object.constraints.clear()
                self.t1.blade1_object.animation_data_clear()
                # Generic validation remains usable for restoring the controlled pose.
                self.assertTrue(control.validate_scene(scene)[0])
                control._apply_poses(scene, {'T1':control.pose_snapshot(scene)['T1']}, pause=True, ramp=False)
        self.assertEqual(bpy.ops.wfrl.manual_pose_export(filepath=str(target)),{'FINISHED'})
        self.assertEqual(json.loads(target.read_text())['schema'],'wfrl.manual-pose.v1')


result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OrdinaryRotorConflicts))
(OUT / 'checks.json').write_text(json.dumps(dict(
    status='PASS' if result.wasSuccessful() else 'FAIL', tests=result.testsRun,
    failures=len(result.failures), errors=len(result.errors), addon_file=addon.__file__,
    blender=bpy.app.version_string), indent=2) + '\n')
if not result.wasSuccessful():
    raise AssertionError('Ordinary manual rotor conflict checks failed')
print('MANUAL_ROTOR_CONFLICT_PASS', flush=True)
