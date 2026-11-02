"""Native source bindings and independent indexed presentation clocks."""
from pathlib import Path
from unittest.mock import patch
import importlib
import json
import os
import sys
import unittest
import numpy as np
import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
if MODULE == 'wfrl_blender':
    addon.register()
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
radar = importlib.import_module(MODULE+'.radar_x100.runtime')
api = importlib.import_module(MODULE+'.radar_x100.core')
farm = importlib.import_module(MODULE+'.farm_flex')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
addon.load_demo_scene(farm.default_package())
measurements = {}


class BindingContracts(unittest.TestCase):
    def setUp(self):
        self.scene = bpy.context.scene
        radar.stop(self.scene)
        self.scene.wfrl_x100_source = 'SIMULATION'
        self.scene.wfrl_x100_candidates = 64
        self.scene.wfrl_x100_mount = 'NACELLE'
        self.flex = farm.active_for(self.scene)
        self.flex.update(self.scene, sim_time_s=float(self.flex.times[0]), record_telemetry=False)
        self.session = radar.start(self.scene)
        self.yaw = self.scene.objects['WFRL.Turbine.T1.YawRoot']

    def tearDown(self):
        radar.stop(self.scene)

    def withdrawn(self):
        bpy.context.view_layer.update()
        radar.tick()
        self.assertFalse(self.session.display_valid)
        self.assertIsNone(self.session.frame)
        self.assertTrue(self.scene.get('wfrl_x100_error'))
        with self.assertRaises((ValueError, ReferenceError)):
            self.session.sampler.sample(self.session.sampler.interval[0])

    def test_reparented_yaw_with_translated_rotated_parent_withdraws_while_paused_and_advancing(self):
        parent = self.yaw.parent
        helper = bpy.data.objects.new('X100.Binding.Parent', None)
        self.scene.collection.objects.link(helper)
        try:
            self.yaw.parent = helper
            helper.location.x = 5.
            helper.rotation_euler.z = .2
            self.withdrawn()
            self.flex.update(self.scene, sim_time_s=float(self.flex.times[0])+.1, record_telemetry=False)
            self.withdrawn()
            self.assertEqual(self.yaw.parent, helper)
            self.assertEqual(helper.location.x, 5.)
        finally:
            self.yaw.parent = parent
            bpy.data.objects.remove(helper, do_unlink=True)
        self.session = radar.start(self.scene)
        self.assertTrue(self.session.display_valid)

    def test_parent_inverse_edit_is_detected_even_with_compensating_basis(self):
        for obj in (self.yaw, self.session.sampler.shell):
            with self.subTest(object=obj.name):
                inverse, basis = obj.matrix_parent_inverse.copy(), obj.matrix_basis.copy()
                try:
                    edited = inverse.copy()
                    edited.translation.x += 2.
                    obj.matrix_parent_inverse = edited
                    obj.matrix_basis = edited.inverted() @ inverse @ basis
                    self.withdrawn()
                    self.assertEqual(obj.matrix_parent_inverse, edited)
                finally:
                    obj.matrix_parent_inverse, obj.matrix_basis = inverse, basis
                radar.tick()
                self.assertTrue(self.session.display_valid)

    def test_bound_objects_must_remain_in_the_owner_scene_even_if_linked_elsewhere(self):
        tower = self.session.sampler.towers[0][0]
        other = bpy.data.scenes.new('X100.Binding.Other')
        for obj in (self.yaw.parent, self.yaw, tower, self.session.sampler.blades[-1][0]):
            with self.subTest(object=obj.name):
                collections = list(obj.users_collection)
                other.collection.objects.link(obj)
                try:
                    for collection in collections:
                        collection.objects.unlink(obj)
                    self.withdrawn()
                    self.assertIn(obj.name, other.objects)
                    self.assertNotIn(obj.name, self.scene.objects)
                finally:
                    for collection in collections:
                        collection.objects.link(obj)
                    other.collection.objects.unlink(obj)
                radar.tick()
                self.assertTrue(self.session.display_valid)
        bpy.data.scenes.remove(other)

    def test_same_name_object_replacement_and_mesh_replacement_are_not_the_bound_source(self):
        obj = self.session.sampler.towers[0][0]
        name = obj.name
        clone = obj.copy()
        try:
            obj.name = name+'.Original'
            clone.name = name
            self.scene.collection.objects.link(clone)
            self.withdrawn()
            self.assertEqual(self.scene.objects.get(name), clone)
        finally:
            bpy.data.objects.remove(clone, do_unlink=True)
            obj.name = name
        radar.tick()
        self.assertTrue(self.session.display_valid)
        mesh = obj.data
        replacement = mesh.copy()
        try:
            obj.data = replacement
            self.withdrawn()
            self.assertEqual(obj.data, replacement)
        finally:
            obj.data = mesh
            bpy.data.meshes.remove(replacement)
        radar.tick()
        self.assertTrue(self.session.display_valid)

    def test_unlinked_mount_withdraws_without_changing_user_links(self):
        mount = self.session.mount
        collections = list(mount.users_collection)
        try:
            for collection in collections:
                collection.objects.unlink(mount)
            radar.tick()
            self.assertIsNone(self.session.frame)
            self.assertFalse(self.session.display_valid)
            self.assertEqual(len(mount.users_collection), 0)
        finally:
            for collection in collections:
                collection.objects.link(mount)
        radar.tick()
        self.assertTrue(self.session.display_valid)

    def test_saved_farmflex_dynamic_poses_remain_valid(self):
        errors = []
        start, end = self.session.sampler.interval
        for stamp in (start, start+.037, start+1., end):
            self.flex.update(self.scene, sim_time_s=stamp, record_telemetry=False)
            bpy.context.view_layer.update()
            self.session.update()
            self.assertTrue(self.session.display_valid)
            expected = self.session.sampler.mount_world_at(stamp) @ self.session.sampler.mount_from_sensor
            error = float(np.max(np.abs(np.asarray(self.session.mount.matrix_world)-expected)))
            errors.append(error)
            self.assertLess(error, 2e-5)
        measurements['dynamic_mount_max_errors'] = errors


class ReplayContracts(unittest.TestCase):
    def setUp(self):
        self.scene = bpy.context.scene
        radar.stop(self.scene)
        self.scene.wfrl_x100_source = 'REPLAY'

    def tearDown(self):
        radar.stop(self.scene)

    def source(self, name, times, source_times=None, *, change_clock=False, repeated_identity=False):
        sink = api.recording.Recorder(OUT/name)
        points = np.zeros(1, dtype=api.schema.POINT_DTYPE)
        for index, stamp in enumerate(times):
            clock = api.schema.FrameTime('DEVICE:'+str(index if change_clock else 0),
                None if source_times is None else source_times[index], quality='SOURCE')
            frame = api.schema.RadarFrame('X100', str(index if change_clock else 'boot'),
                0 if repeated_identity else index, 'TEST', 'NEAR', points, time=clock)
            sink.append(frame, stamp)
        sink.close()
        self.scene.wfrl_x100_path = str(sink.path)
        self.scene.wfrl_x100_replay_s = 0.
        return radar.start(self.scene)

    def test_forward_rerecord_uses_index_times_and_preserves_frame_time(self):
        cases = [('decreasing', [100.1, 100., 99.], False),
                 ('equal', [100., 100., 100.], False),
                 ('unknown', None, False), ('clock-sessions', [9., 0., -2.], True)]
        for name, clocks, change in cases:
            with self.subTest(case=name):
                session = self.source(name, [5., 5.1, 5.2], clocks, change_clock=change)
                session.seek_frame(0)
                path = OUT/(name+'-rerecord')
                session.start_recording(path)
                session.seek_frame(1)
                session.seek_frame(2)
                session.stop_recording()
                replay = api.recording.Replay(path)
                self.assertEqual(replay.times, [t-5. for t in session.replay.times])
                for index in range(3):
                    self.assertEqual(replay.frame(index).time, session.replay.frame(index).time)
                    self.assertEqual(replay.frame(index).metadata(), session.replay.frame(index).metadata())
                radar.stop(self.scene)

    def test_equal_times_and_repeated_identity_remain_accessible_by_index(self):
        session = self.source('equal-presentation', [5., 5., 5.1], repeated_identity=True)
        session.seek_frame(0)
        path = OUT/'equal-presentation-rerecord'
        session.start_recording(path)
        for index in (1, 2):
            session.seek_frame(index)
            self.assertEqual(session.selected_replay_index(), index)
        session.stop_recording()
        self.assertEqual(api.recording.Replay(path).times, [0., 0., 5.1-5.])

    def test_active_backward_seek_fails_recording_including_equal_time_rows(self):
        for name, times in [('reverse', [0., .1, .2]), ('reverse-equal', [0., 0., .1])]:
            with self.subTest(case=name):
                session = self.source(name, times)
                session.seek_frame(1)
                path = OUT/(name+'-rerecord')
                session.start_recording(path)
                session.seek_frame(0)
                with self.assertRaisesRegex(RuntimeError, 'backward'):
                    session.stop_recording()
                manifest = json.loads((path/'manifest.json').read_text())
                self.assertEqual(manifest['state'], 'FAILED')
                self.assertTrue((path/'INCOMPLETE').exists())
                radar.stop(self.scene)

    def test_native_float_quantization_does_not_control_auto_end_or_restart(self):
        endpoints = []
        for duration in (.7, .2, 12345.7):
            with self.subTest(duration=duration):
                session = self.source('endpoint-'+str(duration), [0., duration/2, duration])
                with patch.object(radar.time, 'monotonic', return_value=0.) as clock:
                    self.assertEqual(bpy.ops.wfrl.x100_play(), {'FINISHED'})
                    clock.return_value = duration-1e-6
                    session.update()
                    self.assertTrue(session.playing)
                    self.assertEqual(session.frame.sequence, 1)
                    clock.return_value = duration
                    session.update()
                    self.assertFalse(session.playing)
                    self.assertEqual(session.frame.sequence, 2)
                    self.assertEqual(session.replay_time_s, duration)
                    session.update()
                    self.assertEqual(session.frame.sequence, 2)
                    endpoints.append(dict(duration=duration, rna=self.scene.wfrl_x100_replay_s,
                                          sequence=session.frame.sequence, playing=session.playing))
                    self.assertEqual(bpy.ops.wfrl.x100_play(), {'FINISHED'})
                    session.update()
                    self.assertTrue(session.playing)
                    self.assertEqual(session.frame.sequence, 0)
                radar.stop(self.scene)
        measurements['endpoints'] = endpoints

    def test_pause_resume_keeps_double_clock_and_explicit_rna_seek_pauses(self):
        session = self.source('pause-resume', [0., .1, .7])
        with patch.object(radar.time, 'monotonic', return_value=0.) as clock:
            bpy.ops.wfrl.x100_play()
            clock.return_value = .123456789
            bpy.ops.wfrl.x100_play()
            self.assertFalse(session.playing)
            self.assertEqual(session.replay_time_s, .123456789)
            clock.return_value = 10.
            bpy.ops.wfrl.x100_play()
            self.assertAlmostEqual(session.play_anchor, 10.-.123456789)
            session.update()
            self.assertEqual(session.frame.sequence, 1)
            self.scene.wfrl_x100_replay_s = .05
            self.assertFalse(session.playing)
            session.update()
            self.assertEqual(session.frame.sequence, 0)

    def test_auto_rerecord_uses_original_rows_and_equal_endpoint_indices(self):
        session = self.source('auto-rerecord', [0., .1, .7, .7], [9., 8., 7., 6.])
        path = OUT/'auto-rerecord-output'
        session.start_recording(path)
        with patch.object(radar.time, 'monotonic', return_value=0.) as clock:
            bpy.ops.wfrl.x100_play()
            clock.return_value = .1
            session.update()
            clock.return_value = .7
            session.update()
            self.assertEqual(session.frame.sequence, 3)
            self.assertFalse(session.playing)
            session.seek_frame(2)
            self.assertEqual(session.frame.sequence, 2)
        with self.assertRaisesRegex(RuntimeError, 'backward'):
            session.stop_recording()
        session.seek_frame(2)
        second = OUT/'endpoint-indices-output'
        session.start_recording(second)
        session.seek_frame(3)
        session.stop_recording()
        replay = api.recording.Replay(second)
        self.assertEqual(replay.times, [0., 0.])
        self.assertEqual([replay.frame(i).sequence for i in range(2)], [2, 3])


suite = unittest.TestSuite((unittest.defaultTestLoader.loadTestsFromTestCase(BindingContracts),
                           unittest.defaultTestLoader.loadTestsFromTestCase(ReplayContracts)))
result = unittest.TextTestRunner(verbosity=2).run(suite)
(OUT/'result.json').write_text(json.dumps(dict(status='PASS' if result.wasSuccessful() else 'FAIL',
    tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), measurements=measurements,
    addon_path=addon.__file__, core_path=api.schema.__file__), indent=2))
addon.unregister()
if not result.wasSuccessful():
    raise RuntimeError('X100 binding and replay contracts failed')
print('X100_BINDING_REPLAY_PASS')
