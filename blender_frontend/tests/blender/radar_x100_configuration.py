"""Native nominal geometry and applied configuration versus editing, display and sample timing."""
from pathlib import Path
import importlib
import json
import os
import sys
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
addon.register()
try:
    addon.load_demo_scene(farm.default_package())
    scene = bpy.context.scene
    session = radar.start(scene)
    assert not session.pending_configuration()
    housing = next(obj for obj in scene.objects if obj.get('wfrl_x100_role') == 'physical')
    xyz = np.array([v.co[:] for v in housing.data.vertices])
    h,w,d = api.schema.PROFILE['dimensions_hwd_m']
    np.testing.assert_allclose(np.ptp(xyz,axis=0), [d,w,h], atol=1e-8)
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
    scene.wfrl_x100_ply_source = 'TEST'; radar.tick()
    assert session.display_valid and not session.pending_configuration()
    session.start_recording(OUT/'recording')
    scene.wfrl_x100_record_path = str(OUT/'next-recording')
    assert not session.pending_configuration() and session.fanout.recorder.path == OUT/'recording'
    session.stop_recording(); radar.stop(scene)
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
    session = radar.start(scene)
    assert session.client is not client and not session.pending_configuration()
    assert session.client.state.stale_after_s == 1.
    checks['connection_changes_wait_for_apply_stream_is_immediate'] = True
    radar.stop(scene)
    result=dict(status='PASS',checks=checks,addon_path=addon.__file__,core_path=api.schema.__file__,blender=bpy.app.version_string)
    (OUT/'result.json').write_text(json.dumps(result,indent=2))
    print('X100_CONFIGURATION_PASS',json.dumps(result),flush=True)
finally:
    addon.unregister()
