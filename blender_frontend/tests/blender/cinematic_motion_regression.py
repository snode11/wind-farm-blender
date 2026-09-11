"""Track an existing moving tracer in world space during random wind changes."""
from pathlib import Path
import sys, math
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
import wfrl_blender
from wfrl_blender import cinematic
from mathutils import Vector
wfrl_blender.register()
scene=bpy.context.scene
collection=bpy.data.collections.new('MotionRegression');scene.collection.children.link(collection)
root=bpy.data.objects.new('WFRL.Turbine.T1.YawRoot',None);collection.objects.link(root)
root.location.z=87.6
scene['wfrl_wind_speed_mps']=8.0
bpy.context.view_layer.update()
scene.wfrl_wake_display='CINEMATIC';scene.wfrl_cinematic_wind_mode='RANDOM'
# Index 7 is an advecting tracer, not a stationary age sample of the flow.
# Follow its same vertices across 1/60 s, excluding the invisible respawn.
worst=0
for t in (4.5,5.6,12.5,13.6,20.5):
    positions=[]
    for at in (t,t+1/60):
        cinematic.update(scene,at*.9);bpy.context.view_layer.update()
        obj=scene.objects['WFRL.Cinematic.T1'];points=obj.data.splines[7].points
        positions.append([(obj.matrix_world @ p.co.xyz,p.radius) for p in points])
    for (a,ra),(b,rb) in zip(*positions):
        if min(ra,rb)>.15:
            worst=max(worst,(a-b).length*60)
print('MAX_VISIBLE_TRACER_SPEED',worst)
assert worst<24, f'Rigid wake sweep: {worst:.2f} m/s for 8 m/s wind'
print('CINEMATIC_MOTION_REGRESSION=PASS')
