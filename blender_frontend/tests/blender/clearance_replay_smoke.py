"""Blender integration with explicitly synthetic TEST fixture (not a deliverable)."""
from pathlib import Path
import sys
import math
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'blender_frontend')]
import bpy
import wfrl_blender as addon
from wfrl_blender import clearance_replay as playback
from wfrl_blender.cameras import ensure_gimbal, aim_gimbal
from wfrl.lidar.replay import ReplayPackage, ReplayReader, precompute
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
motion = [dict(time_s=t, azimuth_deg=359+2*t, yaw_deg=12, pitch_deg=[1,2,3], rotor_speed_rpm=0) for t in (0,10)]
rows = [dict(time_s=1,blade_id=1,expected=True,passage_id='one',truth_m=5,
             beams={'B2':dict(valid=True,estimate_m=5.1,error_m=.1)})]
cumulative, stats = precompute(rows,motion,dict(threshold_m=5,hysteresis_m=.2,max_hold_s=2,passage_margin=1.2))
reader = ReplayReader(ReplayPackage({'segment':{'start_s':0,'end_s':10}},motion,rows,cumulative,stats))
playback._READERS[scene.as_pointer()] = reader
scene['wfrl_scene_kind'] = 'clearance_replay'; scene['wfrl_clearance_turbine'] = 'T1'
scene.frame_start = 1; scene.frame_end = 251; scene.render.fps=25
scene.frame_set(26)
first = playback.sample(scene)
assert first['measurement']['time_s'] == 1
assert math.isclose(scene.objects['WFRL.Turbine.T1.Rotor'].rotation_euler.x, math.radians(361), abs_tol=1e-6)
addon._update_demo_status(scene)
assert math.isclose(scene.objects['WFRL.Turbine.T1.Rotor'].rotation_euler.x, math.radians(361), abs_tol=1e-6)
scene.frame_set(201); assert playback.sample(scene)['measurement'] is None
scene.frame_set(26); assert playback.sample(scene) == first
assert playback.sample(scene) == first  # paused clock and measurement age freeze
scene.frame_set(1); assert playback.sample(scene)['measurement'] is None
playback.clear(scene); assert playback.sample(scene) is None
addon.unregister()
assert playback.update not in bpy.app.handlers.frame_change_post
print('CLEARANCE_REPLAY_SMOKE_PASS (synthetic test fixture; no physics acceptance)')
