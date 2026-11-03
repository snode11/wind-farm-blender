"""Native ownership, paused cache, frame cursor and capture guards for X100."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import json
import os
import sys
import time
import threading
import unittest
from unittest.mock import patch
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


def finish_recording(session):
    deadline = time.perf_counter() + 3
    while not session.stop_recording():
        assert time.perf_counter() < deadline, 'Recording did not finish'
        time.sleep(.005)


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

    def test_same_frame_gpu_retry_does_not_resample_or_record(self):
        # Real Blender source state with a controlled GPU constructor adapter.
        frame=self.session.frame
        samples, records=[],[]
        original=radar._simulation_frame
        with patch.object(radar,'_simulation_frame',side_effect=lambda *a,**kw: samples.append(True) or original(*a,**kw)), \
             patch.object(self.session,'_record_frame',side_effect=lambda frame:records.append(frame)), \
             patch.object(radar.view,'batch',side_effect=[RuntimeError('GPU once'),('shader','batch')]), \
             patch.dict(sys.modules, {'bpy': SimpleNamespace(app=SimpleNamespace(background=False))}):
            self.session._display(frame,record=False)
            self.assertIsNone(self.session.gpu_batch)
            self.session.gpu_retry_at=0.
            self.session.retry_display()
            self.assertEqual(self.session.gpu_batch,('shader','batch'))
        self.assertIs(self.session.frame,frame)
        self.assertFalse(samples or records)


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

    def test_native_mount_constraint_withdraws_observation_and_preserves_edit(self):
        target = bpy.data.objects.new('X100.ConstraintTarget', None)
        self.scene.collection.objects.link(target)
        target.location = (100., 100., 100.)
        constraint = self.session.mount.constraints.new('COPY_LOCATION')
        constraint.target = target
        try:
            bpy.context.view_layer.update()
            radar.tick()
            self.assertFalse(self.session.display_valid)
            self.assertIsNone(self.session.frame)
            self.assertIn('约束', self.scene['wfrl_x100_error'])
            self.assertEqual(constraint.target, target)
            with self.assertRaises(ValueError):
                radar.apply_mount_preset(self.scene, 'NACELLE_SIDE')
        finally:
            self.session.mount.constraints.remove(constraint)
            bpy.data.objects.remove(target, do_unlink=True)
        radar.tick()
        self.assertTrue(self.session.display_valid)

    def test_native_mount_animation_withdraws_observation(self):
        mount = self.session.mount
        mount.keyframe_insert(data_path='location', frame=self.scene.frame_current)
        try:
            radar.tick()
            self.assertFalse(self.session.display_valid)
            self.assertIn('动画', self.scene['wfrl_x100_error'])
        finally:
            action = mount.animation_data.action
            mount.animation_data_clear()
            if action and action.users == 0:
                bpy.data.actions.remove(action)
        radar.tick()
        self.assertTrue(self.session.display_valid)

    def test_record_failure_remains_visible_after_stop_and_clears_for_new_recording(self):
        start = self.session.sampler.interval[0]
        self.flex.update(self.scene, sim_time_s=start+1., record_telemetry=False)
        self.session.update()
        self.session.start_recording(OUT / 'backward-recording')
        deadline = time.monotonic()+3
        while self.session.fanout.recorder.rows == 0 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(self.session.fanout.recorder.rows, 1)
        self.flex.update(self.scene, sim_time_s=start, record_telemetry=False)
        self.session.update()
        while self.session.fanout.recorder.state == 'RECORDING' and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(self.session.fanout.recorder.state, 'FAILED')
        self.session.fanout.request_close()
        self.session.fanout.worker.join(timeout=3)
        self.assertFalse(self.session.fanout.worker.is_alive())
        with self.assertRaises(RuntimeError):
            bpy.ops.wfrl.x100_record()
        for _ in range(3):
            radar.tick()
            self.assertIn('记录失败', self.scene['wfrl_x100_error'])
        self.assertEqual(self.scene['wfrl_x100_record_state'], 'FAILED')
        self.assertTrue((OUT / 'backward-recording/INCOMPLETE').exists())
        self.session.start_recording(OUT / 'new-recording')
        finish_recording(self.session)
        radar.tick()
        self.assertFalse(self.scene['wfrl_x100_error'])
        self.assertEqual(self.scene['wfrl_x100_record_state'], 'COMPLETE')

    def test_deleted_recording_scene_still_closes_worker_and_releases_ownership(self):
        scene = bpy.data.scenes.new('X100.RecordingOwner')
        key = scene.as_pointer()
        sink = api.recording.Recorder(OUT/'deleted-owner-recording')
        fanout = api.acquisition.Fanout(sink)
        fanout.submit(self.session.frame, 0.)
        session = radar.Session.__new__(radar.Session)
        session.scene, session.fanout, session.owner = scene, fanout, 'deleted-recording-owner'
        session.recording_stopping = session.closing = False
        session.close_remove, session.restore_after_close = True, False
        session.client = session.frame = session.gpu_batch = session.fov_gpu_batch = None
        session.display_valid = False
        radar._SESSIONS[key] = session
        bpy.data.scenes.remove(scene)
        try:
            deadline = time.perf_counter() + 3
            while key in radar._SESSIONS:
                radar.tick()
                self.assertLess(time.perf_counter(), deadline)
                time.sleep(.005)
            self.assertNotIn(key, radar._SESSIONS)
            self.assertFalse(fanout.worker.is_alive())
            self.assertEqual(sink.state, 'COMPLETE')
        finally:
            fanout.close()
            radar._SESSIONS.pop(key, None)

    def test_busy_recording_closes_through_history_and_unregistration_without_waiting(self):
        for action in ('history', 'unregister'):
            with self.subTest(action=action):
                session = radar.session_for(self.scene)
                entered, release = threading.Event(), threading.Event()
                append = api.recording.Recorder.append
                def held_append(sink, frame, t):
                    entered.set()
                    assert release.wait(5)
                    return append(sink, frame, t)
                with patch.object(api.recording.Recorder, 'append', held_append):
                    session.start_recording(OUT / ('busy-' + action))
                    fanout = session.fanout
                    try:
                        self.assertTrue(entered.wait(3))
                        with patch.object(fanout.worker, 'join', side_effect=AssertionError('Blocking join')):
                            radar.on_load_pre(None) if action == 'history' else radar.unregister()
                            radar.tick()
                        self.assertIs(session.fanout, fanout)
                        self.assertTrue(fanout.worker.is_alive())
                        self.assertTrue(bpy.app.timers.is_registered(radar.tick))
                    finally:
                        release.set()
                        fanout.worker.join(timeout=3)
                        radar.tick()
                        if action == 'unregister':
                            radar.register()
                    self.assertEqual(fanout.recorder.state, 'COMPLETE')
                    self.assertIsNone(session.fanout)
                if action == 'history':
                    restored = radar.session_for(self.scene)
                    self.assertIsNot(restored, session)
                    self.assertTrue(restored.display_valid)
                    self.assertIsNone(restored.fanout)
                else:
                    self.assertIsNone(radar.session_for(self.scene))

    def test_material_subset_matches_full_geometry_for_mounts_and_source_endpoints(self):
        scene_api = importlib.import_module(MODULE+'.radar_x100.scene')
        for mount in ('NACELLE', 'WORLD', 'TOWER_BASE'):
            sampler = self.session.sampler.with_installation(mount,
                scene_api.installation_matrix((-3.5, -1.7, -2.), (0., 82.5, 180.)))
            for t in (sampler.interval[0], sampler.interval[0]+.234, sampler.interval[1]):
                with self.subTest(mount=mount, time=t):
                    kwargs = dict(sensor_id='X100', session_id='equivalence', sequence=0,
                        interval_s=sampler.interval, candidate_budget=512, visibility=sampler.visibility)
                    expected = api.simulation.simulate(sampler.sample, t, **kwargs)
                    actual = api.simulation.simulate(sampler.sample, t,
                        material_at=sampler.material_sample, **kwargs)
                    self.assertEqual(actual.points.tobytes(), expected.points.tobytes())
                    self.assertEqual(actual.material_ids, expected.material_ids)
                    np.testing.assert_allclose(actual.radial_velocity_m_s, expected.radial_velocity_m_s,
                                               rtol=1e-6, atol=1e-5)

    def test_native_shell_transform_edit_invalidates_paused_and_advancing_observations(self):
        shell = self.scene.objects['WFRL.Turbine.T1.Nacelle']
        location = shell.location.copy()
        geometry_id = self.session.sampler.geometry_id
        try:
            shell.location.x += .5
            bpy.context.view_layer.update()
            edited = shell.location.copy()
            for advance in (False, True):
                with self.subTest(advance=advance):
                    if advance:
                        self.flex.update(self.scene, sim_time_s=float(self.flex.times[0]) + .1,
                                         record_telemetry=False)
                    radar.tick()
                    self.assertFalse(self.session.display_valid)
                    self.assertIsNone(self.session.frame)
                    self.assertIn('nacelle', self.scene['wfrl_x100_error'].lower())
                    self.assertEqual(shell.location, edited)
                    with self.assertRaises(ValueError):
                        self.session.sampler.sample(float(self.flex.times[0]))
            self.session = radar.start(self.scene)
            self.assertTrue(self.session.display_valid)
            self.assertNotEqual(self.session.sampler.geometry_id, geometry_id)
            self.assertEqual(shell.location, edited)
        finally:
            shell.location = location
            bpy.context.view_layer.update()

    def test_native_shell_vertex_edit_invalidates_and_restoration_recovers(self):
        shell = self.scene.objects['WFRL.Turbine.T1.Nacelle']
        vertex = shell.data.vertices[0]
        position = vertex.co.copy()
        try:
            vertex.co.x += .5
            shell.data.update()
            edited = vertex.co.copy()
            radar.tick()
            self.assertFalse(self.session.display_valid)
            self.assertIsNone(self.session.frame)
            self.assertEqual(vertex.co, edited)
        finally:
            vertex.co = position
            shell.data.update()
        radar.tick()
        self.assertTrue(self.session.display_valid)
        self.assertFalse(self.scene.get('wfrl_x100_error'))

    def test_native_shell_mesh_rebind_invalidates_observation(self):
        shell = self.scene.objects['WFRL.Turbine.T1.Nacelle']
        original = shell.data
        replacement = original.copy()
        try:
            shell.data = replacement
            radar.tick()
            self.assertFalse(self.session.display_valid)
            self.assertIsNone(self.session.frame)
            self.assertEqual(shell.data, replacement)
        finally:
            shell.data = original
            bpy.data.meshes.remove(replacement)

    def test_native_shell_topology_edit_invalidates_observation(self):
        import bmesh
        mesh = self.scene.objects['WFRL.Turbine.T1.Nacelle'].data
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.faces.ensure_lookup_table()
            bm.faces[0].normal_flip()
            bm.to_mesh(mesh)
            mesh.update()
            radar.tick()
            self.assertFalse(self.session.display_valid)
            self.assertIsNone(self.session.frame)
        finally:
            bm.faces[0].normal_flip()
            bm.to_mesh(mesh)
            bm.free()
            mesh.update()

    def test_native_shell_edit_mode_withdraws_observation_without_committing_mesh(self):
        shell = self.scene.objects['WFRL.Turbine.T1.Nacelle']
        active = bpy.context.view_layer.objects.active
        selected = list(bpy.context.selected_objects)
        try:
            for obj in selected:
                obj.select_set(False)
            shell.select_set(True)
            bpy.context.view_layer.objects.active = shell
            bpy.ops.object.mode_set(mode='EDIT')
            radar.tick()
            self.assertFalse(self.session.display_valid)
            self.assertEqual(shell.mode, 'EDIT')
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
            shell.select_set(False)
            for obj in selected:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = active
        radar.tick()
        self.assertTrue(self.session.display_valid)

    def test_native_shell_deformation_drivers_invalidate_and_cannot_rebind(self):
        shell = self.scene.objects['WFRL.Turbine.T1.Nacelle']
        for kind in ('MODIFIER', 'SHAPE_KEY'):
            with self.subTest(kind=kind):
                try:
                    if kind == 'MODIFIER':
                        modifier = shell.modifiers.new('X100.Deformation', 'DISPLACE')
                    else:
                        shell.shape_key_add(name='Basis')
                        key = shell.shape_key_add(name='X100.Deformation')
                        key.data[0].co.x += .5
                        key.value = 1.
                    radar.tick()
                    self.assertFalse(self.session.display_valid)
                    with self.assertRaises(ValueError):
                        radar.start(self.scene)
                finally:
                    if kind == 'MODIFIER':
                        shell.modifiers.remove(modifier)
                    else:
                        shell.shape_key_clear()
                    self.session = radar.start(self.scene)

    def test_single_ply_requires_source_declaration(self):
        radar.stop(self.scene)
        path = OUT / 'undeclared.ply'
        path.write_bytes(api.ply.encode_ply(np.zeros(0, dtype=api.schema.POINT_DTYPE)))
        self.scene.wfrl_x100_source = 'REPLAY'
        self.scene.wfrl_x100_path = str(path)
        with self.assertRaisesRegex(ValueError, '来源'):
            radar.start(self.scene)
        self.assertIsNone(radar.session_for(self.scene))

    def test_single_ply_uses_declared_origin_and_directory_preserves_it(self):
        radar.stop(self.scene)
        path = OUT / 'declared.ply'
        path.write_bytes(api.ply.encode_ply(np.zeros(0, dtype=api.schema.POINT_DTYPE)))
        self.scene.wfrl_x100_source = 'REPLAY'
        self.scene.wfrl_x100_path = str(path)
        self.scene.wfrl_x100_ply_source = 'GEOMETRY_SIMULATION'
        self.session = radar.start(self.scene)
        frame = self.session.frame
        self.assertEqual(frame.source_kind, 'GEOMETRY_SIMULATION')
        self.assertEqual(frame.provenance['source_declaration'], 'EXPLICIT')
        self.assertIsNone(frame.time.source_time_s)
        self.assertEqual(frame.spatial['alignment'], 'UNALIGNED')
        self.assertEqual(frame.validity, 'VALID_EMPTY')
        path = OUT / 'declared-recording'
        sink = api.recording.Recorder(path)
        sink.append(frame, 0.)
        sink.close()
        radar.stop(self.scene)
        self.scene.wfrl_x100_path = str(path)
        self.scene.wfrl_x100_ply_source = 'MEASURED_REPLAY'
        self.session = radar.start(self.scene)
        self.assertEqual(self.session.frame.source_kind, 'GEOMETRY_SIMULATION')

    def test_changing_ply_path_requires_new_declaration_and_apply(self):
        radar.stop(self.scene)
        paths = [OUT / name for name in ('first.ply', 'second.ply')]
        for path in paths:
            path.write_bytes(api.ply.encode_ply(np.zeros(0, dtype=api.schema.POINT_DTYPE)))
        self.scene.wfrl_x100_source = 'REPLAY'
        self.scene.wfrl_x100_path = str(paths[0])
        self.scene.wfrl_x100_ply_source = 'TEST'
        self.session = radar.start(self.scene)
        self.scene.wfrl_x100_path = str(paths[1])
        self.assertEqual(self.scene.wfrl_x100_ply_source, 'UNDECLARED')
        radar.tick()
        self.assertIsNone(self.session.frame)
        self.scene.wfrl_x100_ply_source = 'TEST'
        radar.tick()
        self.assertIsNone(self.session.frame)
        self.session = radar.start(self.scene)
        self.assertEqual(self.session.frame.provenance['file'], 'second.ply')

    def test_changing_ply_declaration_withdraws_old_frame_until_apply(self):
        radar.stop(self.scene)
        path = OUT / 'change-origin.ply'
        path.write_bytes(api.ply.encode_ply(np.zeros(0, dtype=api.schema.POINT_DTYPE)))
        self.scene.wfrl_x100_source = 'REPLAY'
        self.scene.wfrl_x100_path = str(path)
        self.scene.wfrl_x100_ply_source = 'MEASURED_REPLAY'
        self.session = radar.start(self.scene)
        self.assertEqual(self.session.frame.source_kind, 'MEASURED_REPLAY')
        self.scene.wfrl_x100_ply_source = 'TEST'
        radar.tick()
        self.assertIsNone(self.session.frame)
        self.session = radar.start(self.scene)
        self.assertEqual(self.session.frame.source_kind, 'TEST')

    def test_source_declaration_survives_save_and_reopen(self):
        radar.stop(self.scene)
        path = OUT / 'saved-source.ply'
        path.write_bytes(api.ply.encode_ply(np.zeros(0, dtype=api.schema.POINT_DTYPE)))
        self.scene.wfrl_x100_source = 'REPLAY'
        self.scene.wfrl_x100_path = str(path)
        self.scene.wfrl_x100_ply_source = 'TEST'
        self.session = radar.start(self.scene)
        saved = OUT / 'declared-source.blend'
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(saved)), {'FINISHED'})
        self.assertEqual(bpy.ops.wm.open_mainfile(filepath=str(saved)), {'FINISHED'})
        self.scene = bpy.context.scene
        radar.tick()
        self.session = radar.session_for(self.scene)
        self.assertIsNotNone(self.session, self.scene.get('wfrl_x100_error'))
        self.assertEqual(self.scene.wfrl_x100_ply_source, 'TEST')
        self.assertEqual(self.session.frame.source_kind, 'TEST')

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

    def test_shared_objects_are_unlinked_only_from_closing_scene(self):
        other = bpy.data.scenes.new('X100.ObjectLinks')
        objects = [obj for obj in self.scene.objects if obj.get('wfrl_x100_owner') == self.session.owner]
        try:
            for obj in objects:
                other.collection.objects.link(obj)
            radar.stop(self.scene)
            self.assertFalse(any(obj in tuple(self.scene.objects) for obj in objects))
            self.assertTrue(all(obj in tuple(other.objects) for obj in objects))
            self.session = radar.start(self.scene)
            self.assertEqual(sum(obj.get('wfrl_x100_owner') == self.session.owner for obj in self.scene.objects), 3)
            radar.stop(self.scene)
            self.assertTrue(all(obj in tuple(other.objects) for obj in objects))
        finally:
            bpy.data.scenes.remove(other)
            radar.view.remove_orphans(self.session.owner)


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
