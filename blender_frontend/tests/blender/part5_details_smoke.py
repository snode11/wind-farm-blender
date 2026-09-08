"""Check an already-built modelling revision, e.g. after its review renderer."""
import bpy
from mathutils import Vector
scene=bpy.context.scene
for name in ('GrassCoverPatches','BareSoilPatches'):
    obj=bpy.data.objects['WFRL.Landscape.'+name]
    assert min(p.normal.z for p in obj.data.polygons)>0, name
terrain=bpy.data.objects['WFRL.Terrain']
for tid in ('T1','T2','T3'):
    prefix='WFRL.Turbine.'+tid
    assert bpy.data.objects[prefix+'.ServiceDoor.Panel'].parent.name==prefix
    assert bpy.data.objects[prefix+'.Vent.Recess-1'].parent.name==prefix+'.YawRoot'
    assert bpy.data.objects[prefix+'.Blade1.RootFlange'].parent.name==prefix+'.Blade1.PitchRoot'
    base=bpy.data.objects[prefix+'.Foundation']
    assert min((base.matrix_world @ Vector(p)).z for p in base.bound_box)<0
    assert bpy.data.objects['WFRL.WakeProxy.'+tid+'.Volume'].hide_render
# A ray onto the louvre must hit the louvre rather than the tower/yaw bearing.
dg=bpy.context.evaluated_depsgraph_get()
for tid in ('T1','T2','T3'):
    yaw=bpy.data.objects['WFRL.Turbine.'+tid+'.YawRoot'].matrix_world
    origin=yaw @ Vector((.85,-10,.855))
    direction=yaw.to_3x3() @ Vector((0,1,0))
    hit,_,_,_,obj,_=scene.ray_cast(dg,origin,direction)
    assert hit and '.Vent.Louvre' in obj.name, obj.name
for obj in bpy.data.objects:
    if obj.name.startswith('WFRL.Landscape.Pad'):
        for vertex in obj.data.vertices:
            x,y,z=vertex.co
            hit,point,_,_=terrain.ray_cast(Vector((x,y,1500)),Vector((0,0,-1)))
            assert hit and .015<z-point.z<.225, (obj.name,z-point.z)
shrubs=[o for o in bpy.data.objects if o.name.startswith('WFRL.Landscape.Shrub')]
assert len({o.data.as_pointer() for o in shrubs})==3
assert all(len(o.data.materials)==2 for o in shrubs)
assert all(o.data.polygons[-1].normal.z>0 for o in shrubs)
print('WFRL_PART5_DETAILS_SMOKE=PASS')
print('Fitting parents, source-shared shrub prototypes, upward cover normals and pad ground clearance checked.')
