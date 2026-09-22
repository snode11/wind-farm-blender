"""Independent delivery audit; no camera_video implementation imports."""
from pathlib import Path
import csv, hashlib, json, subprocess
import numpy as np
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
P=ROOT/'results/camera-video/t1-single-5min-4k-20fps-20260919'
S=ROOT/'blender_frontend/wfrl_blender/assets/mappo'
rows=list(csv.DictReader((P/'frames.csv').open()))
assert len(rows)==6000
sim=np.array([float(r['sim_time_s']) for r in rows])
assert all(int(r['frame_id'])==i and int(r['video_pts'])==i and float(r['video_time_s'])==i/20 and r['turbine_id']=='T1' and int(r['source_frame_id'])==i%1200 and int(r['source_cycle_id'])==i//1200 for i,r in enumerate(rows))
assert np.allclose(sim,117+(np.arange(6000)%1200+.5)/20,rtol=0,atol=1e-12)
z=np.load(P/'frame_geometry.npz'); cam=json.loads((P/'camera.json').read_text())
mount_error=float(abs(np.linalg.solve(z['nacelle_world_transform'],z['camera_world_transform'])-np.array(cam['camera_mount'])).max())
assert mount_error<1e-10
valid=z['clearance_valid']; tips=z['tip_reference_world_m']; nearest=z['nearest_tower_world_m']
dist=np.linalg.norm((tips-nearest)[:,:,:2],axis=2)
clearance_error=0.
for b in range(3):
 for i,r in enumerate(rows):
  assert (r[f'clearance_b{b+1}_valid']=='True')==bool(valid[i,b])
  if valid[i,b]:
   clearance_error=max(clearance_error,abs(abs(float(r[f'clearance_b{b+1}_m']))-dist[i,b]))
   assert abs(tips[i,b,2]-nearest[i,b,2])<1e-8
  else:
   assert r[f'clearance_b{b+1}_m']=='' and np.isnan(nearest[i,b]).all()
assert clearance_error<1e-9
m=json.loads((S/'manifest.json').read_text()); g=np.load(S/'geometry.npz'); ref=np.load(S/'reference-surfaces.npz')
t=np.asarray(g['times']); ix=np.searchsorted(t,sim); assert np.allclose(t[ix],sim,rtol=0,atol=1e-9)
tr=g['transforms'][ix,0,:,-1].astype(float); centroid=ref['blades'].astype(float).reshape(3,19,-1,3)[:,-1].mean(axis=1)
expected=np.einsum('nbij,bj->nbi',tr[...,:3],centroid)+tr[...,3]+np.array(m['layout_m'][0])
tip_error=float(abs(expected-tips).max()); assert tip_error<1e-9
motion=json.loads((S/'data.json').read_text())['T1']['motion']
rpm=np.array([r['rotor_speed_rpm'] for r in motion])[ix]
rpm_error=float(abs(rpm-np.array([float(r['rpm']) for r in rows])).max()); assert rpm_error<1e-10
print('Independent source/CSV/geometry checks passed',flush=True)
# Decode every actual output frame with strict error handling. Reduce only the
# diagnostic RGB output; compressed input and decoder stay at native 4K.
cmd=['ffmpeg','-v','error','-xerror','-threads','0','-i',str(P/'video.mp4'),'-map','0:v:0','-vf','scale=640:360','-pix_fmt','rgb24','-f','rawvideo','-']
selected={0,14,120,360,600,900,1198,1199,1200,1201,2399,2400,3599,3600,4799,4800,5999}
hashes=[]; diffs=[]; means=[]; snapshots={}; prev=None; count=0
with (OUT/'decode-errors.log').open('wb') as log:
 p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=log)
 while True:
  raw=p.stdout.read(640*360*3)
  if not raw: break
  assert len(raw)==640*360*3
  a=np.frombuffer(raw,np.uint8).reshape(360,640,3)
  hashes.append(hashlib.sha256(raw).hexdigest());means.append(float(a.mean()))
  if prev is not None: diffs.append(float(abs(a.astype(np.int16)-prev).mean()))
  if count in selected: snapshots[count]=Image.fromarray(a.copy())
  prev=a.astype(np.int16); count+=1
  if count%1200==0: print('decoded',count,flush=True)
 assert p.wait()==0
assert count==6000 and hashes==hashes[:1200]*5
assert (OUT/'decode-errors.log').stat().st_size==0
adj=np.array(diffs[:1199]); boundaries={str(i/20):diffs[i-1] for i in (1200,2400,3600,4800)}
cols=3; tilew,tileh=480,294
sheet=Image.new('RGB',(cols*tilew,((len(snapshots)+cols-1)//cols)*tileh),(24,28,32));draw=ImageDraw.Draw(sheet)
for j,(i,img) in enumerate(sorted(snapshots.items())):
 x=j%cols*tilew;y=j//cols*tileh
 sheet.paste(img.resize((480,270)),(x,y+24));draw.text((x+8,y+5),f'frame {i} | video {i/20:.2f}s | source {i%1200/20:.2f}s',fill='white')
sheet.save(OUT/'encoded-contact-sheet.jpg',quality=90)
report={'status':'passed','frame_count':count,'independent_source_tip_max_error_m':tip_error,'rpm_max_error':rpm_error,'fixed_camera_mount_max_matrix_error':mount_error,'clearance_distance_max_error_m':clearance_error,'missing_values_preserved':True,'strict_native_4k_decode_errors':0,'decoded_rgb_repeats_exactly':True,'diagnostic_rgb_resolution':[640,360],'brightness_mean_range_8bit':[min(means),max(means)],'adjacent_frame_mean_absolute_rgb_difference_8bit':{'median':float(np.median(adj)),'p95':float(np.percentile(adj,95)),'max':float(adj.max())},'repeat_boundary_rgb_difference_8bit':boundaries,'unique_adjacent_frame_hash_duplicates':sum(a==b for a,b in zip(hashes[:1199],hashes[1:1200])),'sampled_frames':sorted(snapshots)}
(OUT/'independent-checks.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
