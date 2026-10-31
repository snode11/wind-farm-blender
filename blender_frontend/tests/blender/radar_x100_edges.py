"""Native ownership, paused cache, frame cursor and capture guards for X100."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import json
import os
import sys
import time
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
capture = importlib.import_module(MODULE+'.custom_camera_capture')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
addon.load_demo_scene(farm.default_package())


class RadarEdges(unittest.TestCase):
    def setUp(self):
        capture._ACTIVE = None
        self.scene = bpy.context.scene
        radar.stop(self.scene)
        self.scene.wfrl_x100_source = 'SIMULATION'
        self.scene.wfrl_x100_color = 'RANGE'
        self.scene.wfrl_x100_fov = True
        self.scene.wfrl_x100_candidates = 64
        self.scene.frame_set(1)
        self.session = radar.start(self.scene)
        self.flex = farm.active_for(self.scene)

    def tearDown(self):
        capture._ACTIVE = None
        radar.stop(self.scene)

    def test_capture_blocks_installation_and_removal(self):
        capture._ACTIVE = SimpleNamespace(closed=False, scene=self.scene)
        mount = self.session.mount
        with self.assertRaises(ValueError):
            radar.start(self.scene)
        with self.assertRaises(ValueError):
            radar.stop(self.scene)
        self.assertIs(radar.session_for(self.scene), self.session)
        self.assertIn(mount.name, self.scene.objects)
        self.session.update()

    def test_capture_temporary_times_do_not_enter_geometry_observations(self):
        frame = self.session.frame
        capture._ACTIVE = SimpleNamespace(closed=False, scene=self.scene)
        at = float(self.flex.times[0]) + .1
        self.flex.update(self.scene, sim_time_s=at, record_telemetry=False)
        radar.tick()
        self.assertIs(self.session.frame, frame)
        self.assertEqual(self.scene['wfrl_clearance_time_s'], at)

    def test_live_receiver_continues_during_camera_capture(self):
        radar.stop(self.scene)
        fanout = api.acquisition.Fanout()
        publisher = api.acquisition.LocalPublisher(fanout, port=0)
        try:
            self.scene.wfrl_x100_source = 'LIVE'
            self.scene.wfrl_x100_port = publisher.port
            self.scene.wfrl_x100_stream = 'NEAR'
            self.session = radar.start(self.scene)
            capture._ACTIVE = SimpleNamespace(closed=False, scene=self.scene)
            points = np.zeros(1, dtype=api.schema.POINT_DTYPE)
            points['x'] = 10
            fanout.submit(api.schema.RadarFrame('X100', 'live-test', 1, 'TEST', 'NEAR', points), 0.)
            deadline = time.monotonic() + 3
            while self.session.frame is None and time.monotonic() < deadline:
                radar.tick()
                time.sleep(.005)
            self.assertIsNotNone(self.session.frame)
            self.assertEqual(self.session.frame.sequence, 1)
        finally:
            publisher.close()
            fanout.close()

    def test_recolor_uses_current_observation_without_resampling(self):
        original = api.simulation.simulate
        calls = []
        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        api.simulation.simulate = counted
        frame = self.session.frame
        try:
            self.scene.wfrl_x100_color = 'HEIGHT'
            self.session.update()
            self.assertEqual(calls, [])
            self.assertIs(self.session.frame, frame)
        finally:
            api.simulation.simulate = original

    def test_paused_source_rebind_clears_observation(self):
        row = self.flex.blades[0]
        rest = row[1].copy()
        rest.flags.writeable = False
        self.flex.blades[0] = (row[0], rest, *row[2:])
        try:
            radar.tick()
            self.assertFalse(self.session.display_valid)
            self.assertIsNone(self.session.frame)
        finally:
            self.flex.blades[0] = row

    def test_native_root_edit_clears_observation_without_overwriting_edit(self):
        root = self.scene.objects['WFRL.Turbine.T1.YawRoot'].parent
        location = root.location.copy()
        try:
            root.location.x += 1
            bpy.context.view_layer.update()

            edited = root.location.copy()
            radar.tick()
            self.assertFalse(self.session.display_valid)
            self.assertEqual(root.location, edited)
        finally:
            root.location = location
            bpy.context.view_layer.update()

    def test_native_mount_edit_clears_observation_without_resetting_pose(self):
        self.session.mount.location.x += .5
        edited = self.session.mount.location.copy()
        radar.tick()
        self.assertFalse(self.session.display_valid)
        self.assertEqual(self.session.mount.location, edited)

    def test_restoring_valid_source_clears_stale_diagnostic(self):
        root = self.scene.objects['WFRL.Turbine.T1.YawRoot'].parent
        location = root.location.copy()
        try:
            root.location.x += 1
            radar.tick()
            self.assertTrue(self.scene.get('wfrl_x100_error'))
        finally:
            root.location = location
            bpy.context.view_layer.update()
        radar.tick()
        self.assertTrue(self.session.display_valid)
        self.assertFalse(self.scene.get('wfrl_x100_error'))

    def test_fov_preserves_view_layer_choices_between_updates_and_toggles(self):
        first = self.scene.view_layers[0]
        second = self.scene.view_layers.new('X100.HiddenLayer')
        try:
            self.session.fov.hide_set(True, view_layer=second)
            self.session.update()
            self.assertTrue(self.session.fov.hide_get(view_layer=second))
            self.scene.wfrl_x100_fov = False
            self.session.update()
            self.assertTrue(self.session.fov.hide_get(view_layer=first))
            self.scene.wfrl_x100_fov = True
            self.session.update()
            self.assertFalse(self.session.fov.hide_get(view_layer=first))
            self.assertTrue(self.session.fov.hide_get(view_layer=second))
        finally:
            self.scene.view_layers.remove(second)

    def test_index_step_visits_frames_with_equal_presentation_times(self):
        radar.stop(self.scene)
        path = OUT/'equal-times'
        sink = api.recording.Recorder(path)
        for sequence, t in enumerate((0., 0., .1)):
            points = np.zeros(1, dtype=api.schema.POINT_DTYPE)
            points['x'] = 10 + sequence
            sink.append(api.schema.RadarFrame('X100', 'boot', sequence, 'TEST', 'NEAR', points), t)
        sink.close()
        self.scene.wfrl_x100_source = 'REPLAY'
        self.scene.wfrl_x100_path = str(path)
        self.scene.wfrl_x100_replay_s = 0
        self.session = radar.start(self.scene)
        self.assertEqual(self.session.frame.sequence, 1)
        for direction, expected_sequence in ((-1,0),(1,1),(1,2),(-1,1)):
            self.assertEqual(bpy.ops.wfrl.x100_step(direction=direction), {'FINISHED'})
            self.assertEqual(self.session.frame.sequence, expected_sequence)


selected = os.environ.get('WFRL_X100_EDGE_TESTS', '').split(',')
suite = (unittest.TestSuite(RadarEdges(name) for name in selected) if selected[0]
         else unittest.defaultTestLoader.loadTestsFromTestCase(RadarEdges))
result = unittest.TextTestRunner(verbosity=2).run(suite)
(OUT/'result.json').write_text(json.dumps(dict(status='PASS' if result.wasSuccessful() else 'FAIL',
    tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
    addon_path=addon.__file__, core_path=api.schema.__file__), indent=2))
addon.unregister()
if not result.wasSuccessful():
    raise RuntimeError('X100 edge contracts failed')
print('X100_EDGES_PASS')
