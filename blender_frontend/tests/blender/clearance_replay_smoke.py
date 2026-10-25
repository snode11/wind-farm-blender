"""Synthetic replay lifecycle; package deserialization is an in-memory fixture.

This checks the public scene loader and reader integration, not package-file
validation or physics acceptance. No publishable result package is generated.
"""
from pathlib import Path
import sys
import math
import json
import os
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'blender_frontend')]
import bpy
import wfrl_blender as addon
from wfrl_blender import clearance_replay as playback, farm_flex
from wfrl_blender.cameras import ensure_gimbal, aim_gimbal
try:
    from wfrl_blender._vendor.lidar.replay import ReplayPackage, precompute
except ImportError:
    from wfrl.lidar.replay import ReplayPackage, precompute
from wfrl_blender.turbine_geometry import geometry_data
addon.register(); addon.load_demo_scene()
scene = bpy.context.scene
from wfrl_blender.panels.gimbal import WFRL_PT_Gimbal
assert WFRL_PT_Gimbal.is_registered
radar = scene.objects['WFRL.Turbine.T1.ClearanceRadar']
assert radar.parent.name == 'WFRL.Turbine.T1.YawRoot'
beams = [scene.objects[f'{radar.name}.Beam{i}'] for i in (1, 2, 3)]
assert all(b.parent == radar.parent for b in beams)
assert all(math.isclose((b.data.splines[0].points[1].co.xyz-b.data.splines[0].points[0].co.xyz).length, 59.85, abs_tol=1e-4) for b in beams)
camera = ensure_gimbal(scene, 'T1')
bpy.context.view_layer.update(); before = radar.matrix_world.copy()
aim_gimbal(camera, 35, -40, 65); bpy.context.view_layer.update()
assert radar.matrix_world == before
geometry = geometry_data()['scalars']
replay = dict(threshold_m=5, hysteresis_m=.2, max_hold_s=2, passage_margin=1.2)
manifest = dict(source='SYNTHETIC_TEST_ONLY', turbine_id='T1',
                segment=dict(id='normal', start_s=0, end_s=10),
                model=dict(id='nrel5mw', tower_height_m=geometry['TowerHt'],
                           rotor_radius_m=geometry['TipRad']),
                calibration=dict(origin_m=[-2, 0, 87.6], beam_directions=[
                    [-math.sin(math.radians(a)), 0, -math.cos(math.radians(a))]
                    for a in (6.45, 8.5, 10.54)]), replay=replay)
motion = [dict(time_s=t, azimuth_deg=359+2*t, yaw_deg=12, pitch_deg=[1,2,3],
               rotor_speed_rpm=1/3, nacelle_position_m=[0, 0, 87.6],
               nacelle_orientation_deg=[0, 0, 12]) for t in (0,10)]
hit = [origin + 58 * direction for origin, direction in
       zip(manifest['calibration']['origin_m'], manifest['calibration']['beam_directions'][1])]
rows = [dict(time_s=1,blade_id=1,expected=True,passage_id='one',truth_m=5,
             truth_tip_point_m=[hit[0] + .1, hit[1], hit[2]],
             truth_wall_point_m=[hit[0] + 5.1, hit[1], hit[2]],
             beams={
                 'B1':dict(valid=False,reason='synthetic_miss',estimate_m=None,error_m=None),
                 'B2':dict(valid=True,estimate_m=5.1,error_m=.1,
                           slant_range_m=58,hit_point_m=hit),
                 'B3':dict(valid=False,reason='synthetic_miss',estimate_m=None,error_m=None)})]
cumulative, stats = precompute(rows, motion, replay)
fixture = ReplayPackage(manifest, motion, rows, cumulative, stats)
previous = farm_flex.active_for(scene)
assert previous is not None and scene.get('wfrl_farm_flex_path')
assert scene['wfrl_clearance_timebase_fps'] == 60
# The independent synthetic clip explicitly starts with a 25 Hz timebase.
scene.render.fps = 25
scene.render.fps_base = 1
del scene['wfrl_clearance_timebase_fps']
with patch.object(ReplayPackage, 'load', return_value=fixture) as deserialize:
    playback.load(scene, 'synthetic-test-only', 'normal')
deserialize.assert_called_once_with('synthetic-test-only')
assert not previous.enabled and farm_flex.active_for(scene) is None
assert 'wfrl_farm_flex_path' not in scene
assert scene['wfrl_clearance_timebase_fps'] == 25
assert (scene.frame_start, scene.frame_end) == (1, 251)
scene.frame_set(26)
first = playback.sample(scene)
assert first['time_s'] == 1
assert first['measurement']['time_s'] == 1
assert [beam['wfrl_beam_active'] for beam in beams] == [False, True, False]
assert math.isclose(scene.objects['WFRL.Turbine.T1.Rotor'].rotation_euler.x, math.radians(361), abs_tol=1e-6)
addon._update_demo_status(scene)
assert math.isclose(scene.objects['WFRL.Turbine.T1.Rotor'].rotation_euler.x, math.radians(361), abs_tol=1e-6)
scene.render.fps = 60
playback.update(scene)
assert scene['wfrl_clearance_timebase_fps'] == 25
assert playback.sample(scene) == first
scene.frame_set(201); assert playback.sample(scene)['measurement'] is None
scene.frame_set(26); assert playback.sample(scene) == first
assert playback.sample(scene) == first  # paused clock and measurement age freeze
scene.frame_set(1); assert playback.sample(scene)['measurement'] is None
playback.clear(scene); assert playback.sample(scene) is None
addon.unregister()
assert playback.update not in bpy.app.handlers.frame_change_post
result = dict(status='PASS', synthetic_fixture=True, package_file_validation='NOT_RUN',
              public_scene_loader=True, three_beams=True, farm_detached=True,
              simulation_timebase_fps=25, display_fps_independent=True,
              seeks=True, unregister=True, physics_acceptance='NOT_RUN')
if os.environ.get('WFRL_TEST_OUTPUT'):
    Path(os.environ['WFRL_TEST_OUTPUT']).write_text(json.dumps(result, indent=2) + '\n')
print('CLEARANCE_REPLAY_SMOKE_PASS', json.dumps(result), flush=True)
