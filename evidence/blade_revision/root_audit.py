import sys,math,json
from pathlib import Path
import bpy,numpy as np
from mathutils.bvhtree import BVHTree
sys.path.insert(0,str(Path.cwd()/'blender_frontend'))
from wfrl_blender.scene_builder import _make_turbine
from wfrl_blender.scene_model import TurbineDTO
from wfrl_blender.turbine_geometry import geometry_data
c=bpy.data.collections.new('Audit');bpy.context.scene.collection.children.link(c);_make_turbine(c,TurbineDTO('Audit',0,0));bpy.context.view_layer.update()
p='WFRL.Turbine.Audit';blade=bpy.data.objects[p+'.Blade1'];rotor=bpy.data.objects[p+'.Rotor'];yaw=bpy.data.objects[p+'.YawRoot']
verts=np.array([(*v.co,1) for v in blade.data.vertices]);stations=np.array(geometry_data()['tower_stations']);best=(1e9,None);rows=[]
def tree(obj):
 deps=bpy.context.evaluated_depsgraph_get();o=obj.evaluated_get(deps);m=o.to_mesh();v=[o.matrix_world@x.co for x in m.vertices];f=[list(x.vertices) for x in m.polygons];t=BVHTree.FromPolygons(v,f);o.to_mesh_clear();return t
for pitch in (-5,0,2,25,45,70,90):
 blade.rotation_euler.z=math.radians(pitch);rotor.rotation_euler.x=0;bpy.context.view_layer.update();bt=tree(blade)
 rows.append({'pitch':pitch,'surface_intersections':{n:len(bt.overlap(tree(bpy.data.objects[p+n]))) for n in ('.Blade1.RootFairing','.Blade1.RootFlange','.Blade1.PitchSeal','.Hub','.Spinner')}})
 for azi in range(0,360,5):
  rotor.rotation_euler.x=math.radians(azi);bpy.context.view_layer.update();v=verts@np.array(blade.matrix_world).T;v=v[(v[:,2]>=0)&(v[:,2]<=stations[-1,0])]
  if not len(v):continue
  gap=np.hypot(v[:,0],v[:,1])-np.interp(v[:,2],stations[:,0],stations[:,1])/2
  value=float(gap.min())
  if value<best[0]:best=(value,{'pitch':pitch,'azimuth':azi})
result={'scope':'Rigid Blade1, full 360-degree sweep at 5-degree intervals; other blades are phase shifts. Vertex-to-tower radial clearance, not certified surface clearance or elastic deflection analysis.','minimum_sampled_radial_clearance_m':best[0],'at':best[1],'root_surface_intersections':rows}
Path('evidence/blade_revision/root_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
