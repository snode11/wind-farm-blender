"""Nacelle topology, fitting visibility and yaw attachment regression checks."""
import sys, math
from pathlib import Path
import bpy, bmesh
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from wfrl_blender.scene_builder import _make_turbine
from wfrl_blender.scene_model import TurbineDTO
c=bpy.data.collections.new('NacelleReview');bpy.context.scene.collection.children.link(c)
_make_turbine(c,TurbineDTO('Review',0,0));bpy.context.view_layer.update()
p='WFRL.Turbine.Review';shell=bpy.data.objects[p+'.Nacelle'];yaw=bpy.data.objects[p+'.YawRoot']
m=bmesh.new();m.from_mesh(shell.data)
assert all(e.is_manifold and e.is_contiguous for e in m.edges)
assert m.calc_volume(signed=True)>0
assert min(f.calc_area() for f in m.faces)>1e-8
m.free()
assert all(abs(a-b)<1e-5 for a,b in zip(shell.dimensions,(8.03,3.3,3.3)))
seal=bpy.data.objects[p+'.YawSeal'];skirt=bpy.data.objects[p+'.YawSkirt'];bearing=bpy.data.objects[p+'.YawBearing']
def bounds(obj):
 return [obj.matrix_world@Vector(v) for v in obj.bound_box]
# Seal is separated from the fixed bearing and rotating skirt by millimetre gaps.
assert min(v.z for v in bounds(seal))>max(v.z for v in bounds(bearing))
assert min(v.z for v in bounds(skirt))>max(v.z for v in bounds(seal))
fixed=seal.matrix_world.copy();local=skirt.matrix_basis.copy()
for degrees in (0,45,90,180):
 yaw.rotation_euler.z=math.radians(degrees);bpy.context.view_layer.update()
 assert all(abs(seal.matrix_world[i][j]-fixed[i][j])<1e-6 for i in range(4) for j in range(4))
 expected=yaw.matrix_world@local
 assert all(abs(skirt.matrix_world[i][j]-expected[i][j])<1e-6 for i in range(4) for j in range(4))
 origin=yaw.matrix_world@Vector((.85,-10,.855));direction=yaw.matrix_world.to_3x3()@Vector((0,1,0))
 hit,_,_,_,obj,_=bpy.context.scene.ray_cast(bpy.context.evaluated_depsgraph_get(),origin,direction)
 assert hit and '.Vent.Louvre' in obj.name,obj.name
print('WFRL_NACELLE_REVISION_SMOKE=PASS')
yaw.rotation_euler.z=0;bpy.context.view_layer.update()
if '--render-review' in sys.argv:
 bpy.ops.object.camera_add();cam=bpy.context.object;bpy.context.scene.camera=cam
 target=Vector((-.5,0,87.5));cam.location=(12,-18,96);cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO';cam.data.ortho_scale=13
 s=bpy.context.scene;s.render.engine='BLENDER_WORKBENCH';s.display.shading.light='STUDIO';s.display.shading.color_type='MATERIAL';s.display.shading.show_cavity=True
 s.render.resolution_x=1200;s.render.resolution_y=900;s.render.resolution_percentage=100;s.render.filepath='/tmp/wfrl-nacelle-review.png';bpy.ops.render.render(write_still=True)
