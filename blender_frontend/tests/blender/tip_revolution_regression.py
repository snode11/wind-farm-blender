"""Native curves: complete revolutions, frozen comparison, fade and reset."""
from pathlib import Path
import sys
from unittest.mock import patch
import math
import bpy
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'blender_frontend'))
from wfrl_blender import tip_tracking as tt
scene = bpy.context.scene
collection = scene.collection
for name in ('YawRoot', 'Rotor'):
    obj = bpy.data.objects.new('WFRL.Turbine.T1.' + name, None)
    collection.objects.link(obj)
for bid in (1, 2, 3):
    mesh = bpy.data.meshes.new('tip')
    mesh.from_pydata([(0, 0, 1)], [], [])
    obj = bpy.data.objects.new(f'WFRL.Turbine.T1.Blade{bid}', mesh)
    collection.objects.link(obj)
trail = tt.TipTrail(scene)
rotor = scene.objects['WFRL.Turbine.T1.Rotor']
def tick(t, angle):
    rotor.rotation_euler.x = angle % math.tau
    for bid in (1, 2, 3):
        phase = angle + (bid-1)*math.tau/3
        scene.objects[f'WFRL.Turbine.T1.Blade{bid}'].data.vertices[-1].co = (t*.02, math.sin(phase), math.cos(phase))
    with patch.object(tt, '_simulation_time', return_value=t), patch.object(tt, '_is_playing', return_value=True):
        trail.update(scene)
def snapshot():
    return {bid: [(s.material_index, [tuple(p.co) for p in s.points]) for s in obj.data.splines] for bid, obj in trail.objects.items()}
for i in range(81):
    tick(i*.05, i*math.tau/80)
assert len(trail.completed[1]) == 1
first = tuple(trail.completed[1][0])
for i in range(81, 161):
    tick(i*.05, i*math.tau/80)
assert tuple(trail.completed[1][0]) == first
assert all(len(v) == 2 for v in trail.completed.values())
assert all(len(obj.data.splines) == 2 for obj in trail.objects.values())
assert all(not p for p in trail.points.values())
assert trail.completed[1][0] != trail.completed[1][1]
before = snapshot()
tick(9, 2.25*math.tau)
assert snapshot() == before
scene.render.fps = 120
tick(9, 2.25*math.tau)
assert snapshot() == before
tick(10.4, 2.6*math.tau)
assert all(0 < s.material_index < tt.FADE_LEVELS-1 for obj in trail.objects.values() for s in obj.data.splines)
tick(10.8, 2.7*math.tau)
assert all(not v for v in trail.completed.values())
assert all(len(p) == 1 for p in trail.points.values())
tick(2, .5*math.tau)
assert scene['wfrl_tip_trail_clear_reason'] == 'timeline_seek'
assert all(len(p) == 1 for p in trail.points.values())
trail.clear()
# Slow rotation still retains its starting point with bounded storage.
for i in range(1301):
    tick(i*.05, i*math.tau/1300)
assert len(trail.completed[1]) == 1
assert trail.completed[1][0][0][0] == 0
assert len(trail.completed[1][0]) <= tt.MAX_POINTS+1
trail.disable()
assert all(not obj.data.splines and obj.hide_render for obj in trail.objects.values())
assert all(not p for p in trail.completed.values())
print('TIP_REVOLUTION_PASS', flush=True)
