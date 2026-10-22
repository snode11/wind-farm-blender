"""Fixed Down-view proof at identical B1 rotor phase on three revolutions."""
from pathlib import Path
import json,os,runpy,time
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(os.environ.get('WFRL_GUST_PREVIEW_DIR', ROOT/'evidence/down-gust-preview/v3-random'))
os.environ['WFRL_GUST_TRAILS']='0'
loaded=runpy.run_path(str(ROOT/'scripts/blender/open_down_gust_preview.py'))
preview=loaded['preview'];scene=bpy.context.scene
from wfrl_blender import clearance_replay
from bpy_extras.object_utils import world_to_camera_view
scene.render.engine='BLENDER_EEVEE';scene.eevee.taa_render_samples=8
scene.render.resolution_x=960;scene.render.resolution_y=540;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
cam=scene.camera;cam_matrix=np.array(cam.matrix_world)
records=[]
for frame in (321,721,1121):
 scene.frame_set(frame);bpy.context.view_layer.update()
 value=clearance_replay.sample(scene)
 assert abs(value['motion']['azimuth_deg']%360-180)<1e-4
 assert np.allclose(cam_matrix,np.array(cam.matrix_world))
 obj=scene.objects['WFRL.Turbine.T1.Blade1']
 tip=obj.matrix_world@obj.data.vertices[-1].co
 uv=world_to_camera_view(scene,cam,tip)
 path=OUT/f'Down-B1-{frame:04d}.png';scene.render.filepath=str(path)
 bpy.ops.render.render(write_still=True)
 # Read the saved render, not the empty background Render Result pixel buffer.
 im=bpy.data.images.load(str(path),check_existing=False)
 rgb=np.array(im.pixels[:],dtype=np.float32).reshape(-1,4)[:,:3]
 red=(rgb[:,0]>.15)&(rgb[:,0]>rgb[:,1]*1.7)&(rgb[:,0]>rgb[:,2]*1.7)
 bpy.data.images.remove(im)
 records.append(dict(frame=frame,time_s=value['time_s'],azimuth_deg=value['motion']['azimuth_deg'],
                     tip_world_m=list(tip),tip_screen_uv=list(uv),red_pixels=int(red.sum()),
                     measurement=value['measurement'],image=path.name))
(OUT/'same-phase.json').write_text(json.dumps(records,indent=2))
print('SAME_PHASE_RECORDS',json.dumps(records),flush=True)
if os.environ.get('WFRL_GUST_VIDEO')=='1':
 folder=OUT/'frames';folder.mkdir(exist_ok=True)
 for i in range(540):
  path=folder/f'{i:04d}.png'
  if path.is_file():continue
  scene.frame_set(271+i) # Source t=22.5 .. 31.4833, normal speed, same fixed Down.
  scene.render.filepath=str(path);bpy.ops.render.render(write_still=True)
# Native regression: default-off, opt-in progressive trails, deterministic mesh,
# unchanged root connection and time mapping. Timing excludes viewport drawing.
from wfrl_blender import tip_tracking
assert not scene.wfrl_flex_show_tip_trails
scene.wfrl_flex_show_tip_trails=True
for frame in range(400,410): scene.frame_set(frame)
trail=tip_tracking.active(scene)
assert trail is not None and len(trail.objects)==3
counts=[len(o.data.splines[0].points) for o in trail.objects.values()]
scene.frame_set(409)
assert counts==[len(o.data.splines[0].points) for o in trail.objects.values()]
scene.wfrl_flex_show_tip_trails=False
assert all(o.hide_render for o in trail.objects.values())
assert len({o.data.as_pointer() for o,*_ in preview.blades})==3
samples=[]
root_error=0.
for frame in range(601,661):
 begin=time.perf_counter();scene.frame_set(frame);bpy.context.view_layer.update()
 samples.append(time.perf_counter()-begin)
 for blade,rest,*_ in preview.blades:
  indices=np.flatnonzero(rest[:,2]<1.501)
  coords=np.array([blade.data.vertices[int(i)].co[:] for i in indices])
  root_error=max(root_error,float(np.max(np.linalg.norm(coords-rest[indices],axis=1))))
assert root_error<.001,root_error
(OUT/'verification.json').write_text(json.dumps(dict(root_error_m=root_error,
 mean_update_ms=float(np.mean(samples)*1000),p95_update_ms=float(np.percentile(samples,95)*1000),
 timing_scope='background frame update only; viewport FPS not measured',
 source_fit_error_m=preview.meta['max_surface_fit_error_m'],
 status='REVIEW_ONLY'),indent=2))
# Verify state sharing, repeated seeking and independent meshes.
scene.frame_set(721)
obj=scene.objects['WFRL.Turbine.T1.Blade1'];coords=np.array([v.co[:] for v in obj.data.vertices])
scene.frame_set(1321);assert clearance_replay.sample(scene)['time_s']==40
scene.frame_set(721);assert np.allclose(coords,np.array([v.co[:] for v in obj.data.vertices]))
assert scene.frame_end==1321 and scene.render.fps==60
print('DOWN_GUST_REVIEW_PASS',flush=True)
