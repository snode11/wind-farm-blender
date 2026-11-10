"""Native nominal geometry and applied configuration versus editing, display and sample timing."""
from pathlib import Path
import importlib
import json
import os
import sys
import time
from unittest.mock import patch
import numpy as np
import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
farm = importlib.import_module('wfrl_blender.farm_flex')
radar = importlib.import_module('wfrl_blender.radar_x100.runtime')
api = importlib.import_module('wfrl_blender.radar_x100.core')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
expected = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT']).resolve()
assert Path(addon.__file__).resolve().parent == expected
checks = {}


def reopen(name):
    path = str(OUT / (name + '.blend'))
    assert bpy.ops.wm.save_as_mainfile(filepath=path) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=path) == {'FINISHED'}
    scene = bpy.context.scene
    radar.tick()
    session = radar.session_for(scene)
    assert session is not None and scene.wfrl_x100_enabled, scene.get('wfrl_x100_error')
    return scene, session


def assert_fov(session, angles, shape):
    assert tuple(session.frame.provenance['field_of_view_deg']) == angles
    assert session.frame.provenance['field_of_view_shape'] == shape
    assert tuple(session.fov.data['wfrl_x100_field_of_view_deg']) == angles
    assert session.fov.data.get('wfrl_x100_field_of_view_shape', 'ANGULAR') == shape


def relocate_replay(scene, *, directory):
    name = 'relative-directory' if directory else 'relative-ply'
    before, after = OUT / name, OUT / (name + '-moved')
    before.mkdir()
    project = before / 'scene.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(project)) == {'FINISHED'}
    source_name = 'recording' if directory else 'sample.ply'
    points = np.zeros(1, dtype=api.schema.POINT_DTYPE)
    points['x'] = 10.
    if directory:
        sink = api.recording.Recorder(before / source_name)
        sink.append(api.schema.RadarFrame('X100', 'relocation-test', 0, 'TEST', 'NEAR', points), 0.)
        sink.close()
    else:
        (before / source_name).write_bytes(api.ply.encode_ply(points))
    relative = '//' + source_name
    draft = '//pending' if directory else '//pending.ply'
    absolute = str(before / source_name)
    scene.wfrl_x100_source = 'REPLAY'
    if not directory:
        # An applied v1 absolute reference stays pinned until the relative draft is applied.
        scene.wfrl_x100_path, scene.wfrl_x100_ply_source = absolute, 'TEST'
        radar.start(scene)
        assert bpy.ops.wm.save_as_mainfile(filepath=str(project)) == {'FINISHED'}
        assert bpy.ops.wm.open_mainfile(filepath=str(project)) == {'FINISHED'}
        scene = bpy.context.scene
        radar.tick()
        restored = radar.session_for(scene)
        assert restored.display_valid and restored.applied_config['path'] == absolute
    scene.wfrl_x100_path = relative
    if not directory:
        scene.wfrl_x100_ply_source = 'TEST'
        previous_frame = restored.frame
        radar.tick()
        assert restored.pending_configuration() == ('回放路径',)
        assert restored.frame is previous_frame and restored.display_valid
        assert radar.configuration.saved(scene)['path'] == absolute
    assert bpy.ops.wfrl.x100_apply() == {'FINISHED'}
    session = radar.session_for(scene)
    assert session.display_valid and session.replay_path == before / source_name
    assert session.applied_config['path'] == relative
    assert not session.pending_configuration()
    previous_frame = session.frame
    scene.wfrl_x100_path = absolute
    if not directory:
        scene.wfrl_x100_ply_source = 'TEST'
    radar.tick()
    assert session.pending_configuration() == ('回放路径',)
    assert session.frame is previous_frame and session.display_valid
    assert radar.configuration.saved(scene)['path'] == relative
    scene.wfrl_x100_path = draft
    if not directory:
        scene.wfrl_x100_ply_source = 'MEASURED_REPLAY'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(project)) == {'FINISHED'}
    before.rename(after)
    assert bpy.ops.wm.open_mainfile(filepath=str(after / project.name)) == {'FINISHED'}
    scene = bpy.context.scene
    radar.tick()
    session = radar.session_for(scene)
    assert session is not None, scene.get('wfrl_x100_error')
    assert session.replay_path == after / source_name
    assert session.applied_config['path'] == relative and scene.wfrl_x100_path == draft
    assert not session.display_valid
    pending = ('回放路径',) if directory else ('回放路径', '单帧 PLY 来源')
    assert session.pending_configuration() == pending
    scene.wfrl_x100_path = relative
    if not directory:
        scene.wfrl_x100_ply_source = 'TEST'
    radar.tick()
    assert session.display_valid and not session.pending_configuration()
    assert session.frame.source_kind == 'TEST' and session.frame.points.tobytes() == points.tobytes()
    radar.stop(scene)
    return scene


addon.register()
try:
    addon.load_demo_scene(farm.default_package())
    scene = bpy.context.scene
    session = radar.start(scene)
    assert not session.pending_configuration()
    housing = next(obj for obj in scene.objects if obj.get('wfrl_x100_role') == 'physical')
    from wfrl_blender.radar_x100 import housing_model
    xyz = np.array([v.co[:] for v in housing.data.vertices])
    faces = [tuple(p.vertices) for p in housing.data.polygons]
    materials = [p.material_index for p in housing.data.polygons]
    lo, hi = housing_model.unit_bounds(xyz, faces, materials)
    w,h,d = api.schema.UNIT_ENVELOPE['dimensions_whd_m']
    np.testing.assert_allclose(hi - lo, [d,w,h], atol=1e-6)
    np.testing.assert_allclose((hi + lo) / 2, 0, atol=1e-6)
    assert [m.name for m in housing.data.materials] == [row[0] for row in housing_model.MATERIALS]
    rays = np.array([v.co[:] for v in session.fov.data.vertices])[1:]
    np.testing.assert_allclose(np.linalg.norm(rays,axis=1), api.schema.DISPLAY_DEFAULTS['fov_length_m'], atol=1e-5)
    np.testing.assert_allclose(np.max(np.abs(np.degrees(np.arctan2(rays[:,1],rays[:,0])))), api.schema.PROFILE['azimuth_deg'],atol=1e-5)
    np.testing.assert_allclose(np.abs(np.degrees(np.arctan2(rays[:,2],np.hypot(rays[:,0],rays[:,1])))), api.schema.PROFILE['elevation_deg'],atol=1e-5)
    assert scene.bl_rna.properties['wfrl_x100_candidates'].hard_max == api.schema.PROFILE['max_points']
    checks['nominal_housing_fov_and_budget'] = True
    original_frame, original_calibration = session.frame, session.sampler.calibration_id
    original_installation = session.sampler.mount_from_sensor.copy()
    scene.wfrl_x100_location[0] += .1
    assert session.pending_configuration() == ('安装位置',)
    session.update()
    assert session.frame is original_frame and session.sampler.calibration_id == original_calibration
    np.testing.assert_array_equal(session.sampler.mount_from_sensor, original_installation)
    scene.wfrl_x100_location = session.applied_config['location']
    assert not session.pending_configuration()
    checks['editing_does_not_hot_apply_and_revert_clears_pending'] = True
    scene.wfrl_x100_candidates = 4096
    scene.wfrl_x100_color = 'SNR'
    scene.wfrl_x100_color_scale = 'FRAME'
    scene.wfrl_x100_velocity_limit = 20.
    scene.wfrl_x100_fov = False
    session.update()
    assert not session.pending_configuration() and session.frame is original_frame
    assert session.render_color == ('SNR','FRAME',20.) and session.fov.hide_get(view_layer=scene.view_layers[0])
    assert session.frame.provenance['candidate_budget'] == 512
    scene['wfrl_clearance_time_s'] = session.sampler.interval[0]+1/30
    session.update()
    assert session.frame.provenance['candidate_budget'] == 4096 and not session.pending_configuration()
    assert session.frame.provenance['geometry_id'] == session.sampler.geometry_id
    assert session.frame.provenance['calibration_id'] == original_calibration
    checks['immediate_display_and_next_sample_budget'] = True
    radar.apply_mount_preset(scene,'NACELLE_SIDE')
    assert not session.pending_configuration()
    assert session.applied_config['location'] == tuple(scene.wfrl_x100_location)
    radar.apply_mount_preset(scene,'NACELLE_SIDE')
    assert not session.pending_configuration()
    checks['preset_capture_and_repeat'] = True
    scene.wfrl_x100_location[0] += .02
    session = radar.start(scene)
    assert not session.pending_configuration() and session.sampler.calibration_id != original_calibration
    assert session.applied_config['location'] == tuple(scene.wfrl_x100_location)
    checks['explicit_apply_captures_installation'] = True

    scene.frame_set(scene.frame_current + 1)
    session.update()
    clock = scene.frame_current, scene.frame_subframe, scene['wfrl_clearance_time_s']
    scene.wfrl_x100_observation_mode = 'DETECTIONS'
    scene.wfrl_x100_field_of_view = (10., 8.)
    scene.wfrl_x100_field_of_view_shape = 'PROJECTIVE'
    assert session.pending_configuration() == ('观测模式', '视场', '视场形状')
    scene, session = reopen('pending-simulation')
    assert session.pending_configuration() == ('观测模式', '视场', '视场形状')
    assert session.observation_mode == 'GEOMETRY'
    assert_fov(session, (60., 22.), 'ANGULAR')
    assert (scene.frame_current, scene.frame_subframe, scene['wfrl_clearance_time_s']) == clock
    checks['saved_simulation_preserves_applied_fov_and_pending_drafts'] = True

    frame, mount, mesh, owner = session.frame, session.mount, session.fov.data, session.owner
    previous = session.applied_config.copy()
    resources = tuple(map(len, (bpy.data.objects, bpy.data.collections, bpy.data.meshes)))
    scene.wfrl_x100_field_of_view = (90., 10.)
    try:
        radar.start(scene)
    except ValueError as exc:
        assert '视场半角' in str(exc), str(exc)
    else:
        raise AssertionError('Invalid projective FOV was applied')
    assert radar.session_for(scene) is session and scene.wfrl_x100_enabled
    assert session.frame is frame and session.mount is mount and session.fov.data == mesh
    assert session.applied_config == previous and scene.objects.get(mount.name) == mount
    assert tuple(scene.wfrl_x100_field_of_view) == (90., 10.)
    checks['invalid_apply_preserves_active_observation_and_draft'] = True

    scene.wfrl_x100_field_of_view = (10., 8.)
    with patch.object(radar, '_simulation_frame', side_effect=RuntimeError('Sampling failed')):
        try:
            radar.start(scene)
        except RuntimeError as exc:
            assert str(exc) == 'Sampling failed'
        else:
            raise AssertionError('Sampling failure was ignored')
    assert radar.session_for(scene) is session and session.frame is frame
    assert session.mount is mount and session.fov.data == mesh
    assert tuple(map(len, (bpy.data.objects, bpy.data.collections, bpy.data.meshes))) == resources
    checks['failed_preparation_releases_staged_installation'] = True

    session.start_recording(OUT / 'before-apply')
    fanout = session.fanout
    try:
        try:
            radar.start(scene)
        except ValueError as exc:
            assert '记录' in str(exc), str(exc)
        else:
            raise AssertionError('Apply replaced a session with active recording')
        assert radar.session_for(scene) is session and session.fanout is fanout
        assert not session.closing and not session.recording_stopping and session.frame is frame
    finally:
        deadline = time.perf_counter() + 3
        while not session.stop_recording():
            assert time.perf_counter() < deadline, 'Recording did not finish'
            time.sleep(.005)
    checks['apply_preserves_active_recording'] = True

    assert bpy.ops.wfrl.x100_apply() == {'FINISHED'}
    session = radar.session_for(scene)
    assert session.observation_mode == 'DETECTIONS' and not session.pending_configuration()
    assert_fov(session, (10., 8.), 'PROJECTIVE')
    assert not any(obj.get('wfrl_x100_owner') == owner for obj in scene.objects)
    assert tuple(map(len, (bpy.data.objects, bpy.data.collections, bpy.data.meshes))) == resources
    checks['valid_apply_replaces_installation_and_publishes_matching_frame'] = True

    # A compatible file without a saved applied snapshot carries the applied FOV on its mesh.
    radar.apply_mount_preset(scene, 'ORIGINAL')
    scene.pop('wfrl_x100_applied_config', None)
    session.fov.data.pop('wfrl_x100_field_of_view_shape', None)
    scene.wfrl_x100_field_of_view = (10., 8.)
    scene.wfrl_x100_field_of_view_shape = 'PROJECTIVE'
    scene, session = reopen('legacy-angular-mesh')
    assert session.pending_configuration() == ('视场', '视场形状')
    assert_fov(session, (60., 22.), 'ANGULAR')
    checks['legacy_mesh_restores_applied_fov'] = True

    # Presets explicitly apply their pose and FOV while leaving a draft observation mode pending.
    scene.wfrl_x100_observation_mode = 'GEOMETRY'
    radar.apply_mount_preset(scene, 'DOWN_CAMERA')
    angles = tuple(session.sampler.field_of_view)
    scene, session = reopen('preset-with-pending-mode')
    assert session.pending_configuration() == ('观测模式',)
    assert session.observation_mode == 'DETECTIONS'
    assert_fov(session, angles, 'PROJECTIVE')
    checks['preset_persists_calibration_and_keeps_pending_observation_mode'] = True

    scene.wfrl_x100_source = 'REPLAY'
    assert session.pending_configuration() == ('数据源',)
    radar.tick()
    assert not session.display_valid and session.mode == 'SIMULATION'
    radar.stop(scene)
    checks['source_edit_withdraws_without_switching'] = True
    points = np.zeros(2,dtype=api.schema.POINT_DTYPE); points['x'] = [10,20]
    path = OUT/'input.ply'; path.write_bytes(api.ply.encode_ply(points))
    scene.wfrl_x100_path,scene.wfrl_x100_ply_source = str(path),'TEST'
    session = radar.start(scene)
    assert not session.pending_configuration() and session.applied_config['ply_source'] == 'TEST'
    scene.wfrl_x100_ply_source = 'MEASURED_REPLAY'
    assert session.pending_configuration() == ('单帧 PLY 来源',)
    radar.tick()
    assert not session.display_valid and session.static_frame.source_kind == 'TEST'
    scene.wfrl_x100_path = str(OUT / 'pending-input.ply')
    scene.wfrl_x100_ply_source = 'MEASURED_REPLAY'
    scene, session = reopen('pending-ply')
    assert session.pending_configuration() == ('回放路径', '单帧 PLY 来源')
    assert session.replay_path == path and session.static_frame.source_kind == 'TEST'
    assert not session.display_valid
    scene.wfrl_x100_path = str(path)
    scene.wfrl_x100_ply_source = 'TEST'; radar.tick()
    assert session.display_valid and not session.pending_configuration()
    session.start_recording(OUT/'recording')
    scene.wfrl_x100_record_path = str(OUT/'next-recording')
    assert not session.pending_configuration() and session.fanout.recorder.path == OUT/'recording'
    deadline = time.perf_counter()+3
    while not session.stop_recording():
        assert time.perf_counter()<deadline, 'Recording did not finish'
        time.sleep(.005)
    radar.stop(scene)
    scene.wfrl_x100_path = str(OUT/'recording')
    session = radar.start(scene)
    assert 'ply_source' not in session.applied_config
    scene.wfrl_x100_ply_source = 'MEASURED_REPLAY'
    assert not session.pending_configuration()
    session.update()
    assert session.frame.source_kind == 'TEST'
    checks['ply_declaration_and_record_directory_origin'] = True
    radar.stop(scene)
    scene.wfrl_x100_source = 'LIVE'
    scene.wfrl_x100_port = 19877
    session = radar.start(scene)
    client = session.client
    scene.wfrl_x100_port = 19878
    scene.wfrl_x100_stale_s = 1.
    scene.wfrl_x100_stream = 'FAR'
    session.update()
    assert session.pending_configuration() == ('本地端口','过期阈值')
    assert session.client is client and abs(client.state.stale_after_s-.5) < 1e-6
    scene, session = reopen('pending-live')
    assert session.applied_config['port'] == 19877
    assert session.pending_configuration() == ('本地端口', '过期阈值')
    client = session.client
    assert abs(client.state.stale_after_s-.5) < 1e-6
    session = radar.start(scene)
    assert session.client is not client and not session.pending_configuration()
    assert session.client.state.stale_after_s == 1.
    checks['connection_changes_wait_for_apply_stream_is_immediate'] = True
    radar.stop(scene)
    scene = relocate_replay(scene, directory=False)
    scene = relocate_replay(scene, directory=True)
    checks['relative_ply_and_directory_survive_relocation_with_pending_drafts'] = True
    result=dict(status='PASS',checks=checks,addon_path=addon.__file__,core_path=api.schema.__file__,blender=bpy.app.version_string)
    (OUT/'result.json').write_text(json.dumps(result,indent=2))
    print('X100_CONFIGURATION_PASS',json.dumps(result),flush=True)
finally:
    addon.unregister()
