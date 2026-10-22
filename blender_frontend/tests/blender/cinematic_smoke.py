"""Real Blender regression for independent cinematic geometry and wind/yaw."""
from pathlib import Path
import math
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
import wfrl_blender
from wfrl_blender import cinematic, wake

wfrl_blender.register()
from wfrl_blender.materials import get_material
get_material("wake_line")
scene = bpy.context.scene
collection = bpy.data.collections.new('CinematicTest')
scene.collection.children.link(collection)
collection['wind_direction_deg'] = 270.0
collection['wind_speed_mps'] = 8.0
for tid in ('T1', 'T2', 'T3'):
    root = bpy.data.objects.new(f'WFRL.Turbine.{tid}.YawRoot', None)
    collection.objects.link(root)
    root.location = (0, int(tid[-1]) * 200, 87.6)
    legacy = bpy.data.objects.new(f'WFRL.WakeProxy.{tid}.Line0', bpy.data.curves.new(tid, 'CURVE'))
    collection.objects.link(legacy)
bpy.context.view_layer.update()
scene.wfrl_wake_display = 'CINEMATIC'
obj = scene.objects['WFRL.Cinematic.T1']
assert len(obj.data.splines) == cinematic.STRANDS
assert all(not spline.use_cyclic_u for spline in obj.data.splines)
assert all(o.hide_render for o in scene.objects if o.name.startswith('WFRL.WakeProxy.'))
assert obj.data.splines[0].points[0].co.x < 0
assert obj.data.splines[0].points[-1].co.x > 250
before = tuple(obj.data.splines[1].points[30].co)
wake.update_proxy_objects(scene, phase=5)
assert tuple(obj.data.splines[1].points[30].co) != before
wind_rotation = scene['wfrl_cinematic_direction_deg']
scene.objects['WFRL.Turbine.T2.YawRoot'].rotation_euler.z = .5
wake.update_proxy_objects(scene, phase=5)
assert scene['wfrl_cinematic_direction_deg'] == wind_rotation, 'non-reference yaw changed shared wind'
scene.wfrl_cinematic_offset = 10
assert math.isclose(scene['wfrl_cinematic_direction_deg'], 260, abs_tol=1e-6)
# The incoming geometry turns too; the UI value alone is not the contract.
p0=obj.data.splines[0].points[0].co
assert p0.x < -140 and p0.y < -15
assert all(math.isclose(o.rotation_euler.z, obj.rotation_euler.z) for o in scene.objects if o.name.startswith(cinematic.PREFIX))
scene.wfrl_cinematic_wind_mode = 'RANDOM'
a = cinematic.random_heading(6, 42, 8)
assert a == cinematic.random_heading(6, 42, 8)
assert a != cinematic.random_heading(6, 43, 8)
for boundary in (8, 16, 24):
    a = cinematic.random_heading(boundary - .0001, 42, 8)
    b = cinematic.random_heading(boundary + .0001, 42, 8)
    assert abs((a - b + math.pi) % math.tau - math.pi) < .001
wake.update_proxy_objects(scene, phase=5)
angle = scene['wfrl_cinematic_direction_deg']
scene.objects['WFRL.Turbine.T1.YawRoot'].rotation_euler.z = .5
wake.update_proxy_objects(scene, phase=5)
assert scene['wfrl_cinematic_direction_deg'] == angle, 'random world wind followed nacelle yaw'
counts = (len(bpy.data.objects), len(bpy.data.curves), len(bpy.data.materials))
started = time.perf_counter()
for step in range(30):
    wake.update_proxy_objects(scene, phase=step / 10)
assert counts == (len(bpy.data.objects), len(bpy.data.curves), len(bpy.data.materials))
print('CINEMATIC_UPDATE_MS', (time.perf_counter() - started) * 1000 / 30)
for mode in ('SCIENTIFIC', 'CINEMATIC', 'SCIENTIFIC', 'CINEMATIC'):
    scene.wfrl_wake_display = mode
    assert obj.hide_render == (mode == 'SCIENTIFIC')
    assert scene.objects['WFRL.WakeProxy.T1.Line0'].hide_render == (mode == 'CINEMATIC')
scene.wfrl_show_wake = False
assert obj.hide_render
scene.wfrl_show_wake = True
assert not obj.hide_render
assert counts == (len(bpy.data.objects), len(bpy.data.curves), len(bpy.data.materials))
print('WFRL_CINEMATIC_SMOKE=PASS')

scene['wfrl_wind_speed_mps']=0
wake.update_proxy_objects(scene,phase=5)
assert obj.hide_render, 'Zero wind should not generate moving tracers'
