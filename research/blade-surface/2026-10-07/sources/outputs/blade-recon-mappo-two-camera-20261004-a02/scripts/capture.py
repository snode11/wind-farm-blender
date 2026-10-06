"""Two actual default cameras, timestamped RGB. Geometry written only to evaluation."""
import argparse,hashlib,importlib,json,math,sys,time
from pathlib import Path
import bpy,numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--full',action='store_true')
a=ap.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
mod='bl_ext.user_default.wfrl_blender';addon=importlib.import_module(mod)
farm=importlib.import_module(mod+'.farm_flex');cameras=importlib.import_module(mod+'.cameras')
geom=importlib.import_module(mod+'.turbine_geometry')
if not hasattr(bpy.types.Scene,'wfrl_gimbal_kind'):addon.register()
addon.load_demo_scene(farm.default_package(),camera_rig=False)
sc=bpy.context.scene;sc.name='MAPPO original · T1 Down + nacelle · 117–177 s'
down=cameras.ensure_gimbal(sc,'T1');cameras.down_gimbal(down)
nacelle=cameras.ensure_nacelle_gimbal(sc,'T1')
camlist=[('T1Down',down),('NacelleT1',nacelle)]
sc.wfrl_flex_show_tip_trails=False;sc.render.engine='BLENDER_WORKBENCH'
sc.render.resolution_x=960;sc.render.resolution_y=540;sc.render.resolution_percentage=100
sc.render.pixel_aspect_x=sc.render.pixel_aspect_y=1
sc.render.image_settings.file_format='PNG';sc.render.image_settings.color_mode='RGB'
sc.render.film_transparent=False;sc.world.color=(.035,.055,.08)
sc.view_settings.view_transform='Standard';sc.view_settings.look='None'
sc.view_settings.exposure=0;sc.view_settings.gamma=1;sc.display.render_aa='8'
scalars=geom.geometry_data()['scalars'];hub=np.asarray(geom.hub_position())
cfg=dict(hub_height_m=float(hub[2]),overhang_m=float(-hub[0]),tilt_deg=5.,cone_deg=2.5,
 hub_radius_m=1.5,tip_radius_m=63.,prebend_tip_m=1.,n_sections=40,n_ring=32,spin=1)
root=sc.objects['WFRL.Turbine.T1.YawRoot']
blades=[sc.objects['WFRL.Turbine.T1.Blade'+str(b)] for b in (1,2,3)]
flip=np.diag([-1.,-1.,1.,1.]);flip[:3,3]=[0,0,-scalars['TowerHt']]
W,H=960,540;path=P/('input' if a.full else 'probe')
for name,cam in camlist:(path/name).mkdir(parents=True,exist_ok=True);(P/'evaluation/id_masks'/name).mkdir(parents=True,exist_ok=True)
indices=list(range(601)) if a.full else [0,17,50,150,300,450,600]
representatives=set([0,17,50,150,300,450,600]);records=[];tips=[];meshes=[]
errors={name:{'all':[],'inframe':[]} for name,_ in camlist};start=time.time()
for k in indices:
 sc.frame_set(1+6*k);farm._ACTIVE.update(sc);bpy.context.view_layer.update()
 Mmodel=np.asarray(root.matrix_world)@flip;invmodel=np.linalg.inv(Mmodel)
 vv=[];tt=[];worldvertices=[]
 for ob in blades:
  local=np.empty(len(ob.data.vertices)*3,np.float32);ob.data.vertices.foreach_get('co',local);local=local.reshape(-1,3)
  mo=np.asarray(ob.matrix_world);world=local@mo[:3,:3].T+mo[:3,3];worldvertices.append(world)
  points=world@invmodel[:3,:3].T+invmodel[:3,3];vv.append(points.astype(np.float32));tt.append(points[-2])
 tips.append(tt)
 if k in representatives:meshes.append((k,np.stack(vv)))
 row=dict(frame=k,blender_frame=1+6*k,t=k/10,sim_t=117+k/10,T_world_from_model=Mmodel.tolist(),cameras=[])
 for name,cam in camlist:
  sc.camera=cam;Mcam=np.asarray(cam.matrix_world).copy();Mcam[:3,:3]/=np.linalg.norm(Mcam[:3,:3],axis=0)
  Mcv=Mcam@np.diag([1.,-1.,-1.,1.]);T=np.linalg.inv(Mcv)@Mmodel
  fx=W/(2*math.tan(math.radians(cam['gimbal_fov']/2)))
  K=np.array([[fx,0,(W-1)/2],[0,fx,(H-1)/2],[0,0,1.]])
  row['cameras'].append(dict(name=name,object_name=cam.name,W=W,H=H,K=K.tolist(),T_cv_from_world=T.tolist(),T_world_from_camera=Mcam.tolist()))
  if k in representatives:
   for world in worldvertices:
    for wp in world[::500]:
     ndc=world_to_camera_view(sc,cam,Vector(wp));cvp=np.linalg.inv(Mcv)@np.r_[wp,1]
     if cvp[2]>.05:
      uv=(K@cvp[:3])[:2]/cvp[2];bpyuv=np.array([ndc.x*W-.5,(1-ndc.y)*H-.5]);err=float(np.linalg.norm(uv-bpyuv))
      errors[name]['all'].append(err)
      if 0<=uv[0]<W and 0<=uv[1]<H:errors[name]['inframe'].append(err)
  shade=sc.display.shading;shade.light='STUDIO';shade.color_type='MATERIAL';shade.show_shadows=True
  shade.show_cavity=False;shade.show_specular_highlight=False;shade.background_type='WORLD'
  sc.render.filepath=str(path/name/f'{k:06d}.png');bpy.ops.render.render(write_still=True)
  if k in representatives:
   for ob in sc.objects:ob.color=(0,0,0,1)
   for b,ob in enumerate(blades):col=[0,0,0,1];col[b]=1;ob.color=col
   shade.color_type='OBJECT';shade.light='FLAT';shade.background_type='VIEWPORT';shade.background_color=(0,0,0);shade.show_shadows=False
   sc.render.filepath=str(P/'evaluation/id_masks'/name/f'{k:06d}.png');bpy.ops.render.render(write_still=True)
 records.append(row)
 if k%50==0 or not a.full:print('CAPTURE',k,'sim',117+k/10,'elapsed',round(time.time()-start,2),flush=True)
cal=dict(turbine=cfg,fps=10.,coordinate_frame='moving nacelle model frame; x upwind, y right-handed, z up',
 note='CV transforms map moving model coordinates; each camera has separate actual optical centre and pose',cameras=records[0]['cameras'],frames=records)
(path/'cameras.json').write_text(json.dumps({key:value for key,value in cal.items() if key!='frames'},indent=2))
(path/'calibration.json').write_text(json.dumps(cal,indent=1))
np.savez_compressed(P/'evaluation'/('full_tips.npz' if a.full else 'probe_tips.npz'),frame=np.array(indices),model_structural_tips=np.asarray(tips))
np.savez_compressed(P/'evaluation/representative_meshes.npz',frame=np.array([i for i,_ in meshes]),model_vertices=np.stack([v for _,v in meshes]))
sc.camera=nacelle;sc.display.shading.color_type='MATERIAL';sc.display.shading.background_type='WORLD';sc.display.shading.light='STUDIO';sc.display.shading.show_shadows=True
if a.full:
 sc.frame_set(103);bpy.context.view_layer.update();bpy.context.preferences.filepaths.save_version=0
 bpy.ops.wm.save_as_mainfile(filepath=str(P/'MAPPO_original.blend'))
files=['__init__.py','farm_flex.py','cameras.py','turbine_geometry.py','assets/mappo/manifest.json','assets/mappo/geometry.npz','assets/mappo/blade-reference.json']
sources={f:hashlib.sha256((Path(addon.__file__).parent/f).read_bytes()).hexdigest() for f in files}
report=dict(status='CAPTURED',addon_file=addon.__file__,blender_version=bpy.app.version_string,source_package=str(farm.default_package()),source_hashes=sources,
 cameras=[dict(name=name,object=cam.name,parent=root.name,local=list(cam.location),lens_mm=cam.data.lens,hfov_deg=cam['gimbal_fov'],yaw=cam['gimbal_yaw'],pitch=cam['gimbal_pitch'],roll=cam['gimbal_roll']) for name,cam in camlist],
 baseline_m=float((down.location-nacelle.location).length),static_scalars=scalars,model_config=cfg,duration_s=60.,sim_start_s=117.,sim_end_s=177.,source_hz=40.,display_hz=60.,capture_hz=10.,samples=len(records),resolution=[W,H],
 render_engine=sc.render.engine,shading='STUDIO material / Standard; actual occluders retained',
 projection={name:dict(inframe_max_error_px=max(e['inframe']),inframe_samples=len(e['inframe']),all_points_max_error_px=max(e['all'])) for name,e in errors.items()},
 local_extrinsic_max_change={name:float(np.max(np.abs(np.array([r['cameras'][j]['T_cv_from_world'] for r in records])-np.array(records[0]['cameras'][j]['T_cv_from_world'])))) for j,(name,_) in enumerate(camlist)},
 id_masks='evaluation only; not algorithm inputs',elapsed_s=time.time()-start)
(path/'capture_report.json').write_text(json.dumps(report,indent=2));print('CAPTURE_DONE',json.dumps(report),flush=True)
