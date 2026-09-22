from pathlib import Path
import json, math, hashlib, struct
import numpy as np
from PIL import Image
root=Path.cwd(); out=Path(Path('/tmp/wfrl-t1-review-location').read_text())
folders=list((out/'window-final').glob('capture_*'))
legacy=root/'evidence/camera-v3-20260922/legacy-window-fixed'
folders+=list(legacy.glob('capture_*'))
results=[]
for folder in sorted(folders):
 m=json.loads((folder/'manifest.json').read_text()); layout=json.loads((folder/'layout.json').read_text())
 records=[json.loads(l) for l in (folder/'frames.jsonl').read_text().splitlines()]
 active=m['active_camera_ids'];groups={};used=set()
 layout_hash=hashlib.sha256(json.dumps(layout,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
 assert layout_hash==m['camera_layout_hash']
 for r in records:
  groups.setdefault(r['sample_index'],[]).append(r)
  path=folder/r['image_relative_path'];used.add(path)
  with Image.open(path) as im:
   im.load();assert im.format=='PNG' and im.size==(r['image_width'],r['image_height'])
  hf,vf=map(math.radians,(r['hfov_requested'],r['vfov_requested']))
  w,h=r['image_width'],r['image_height'];K=np.array(r['K'])
  assert np.allclose(K,[[w/(2*math.tan(hf/2)),0,(w-1)/2],[0,h/(2*math.tan(vf/2)),(h-1)/2],[0,0,1]])
  world=np.array(r['T_world_from_camera_blender']);view=np.array(r['T_camera_blender_from_world'])
  assert np.allclose(view@world,np.eye(4),atol=2e-5)
  assert r['camera_layout_hash']==layout_hash and r['capture_id']==m['capture_id']
  assert r['source_status']==m['source_status'] and r['manifest_hash']==m['manifest_hash']
 assert len(groups)==m['completed_samples'] and sorted(groups)==list(range(m['completed_samples']))
 for idx,group in groups.items():
  assert len(group)==len(active) and sorted(r['camera_id'] for r in group)==sorted(active)
  assert len({r['time_s'] for r in group})==1 and group[0]['time_s']==m['requested_times_s'][idx]
  assert len({(r['scene_frame'],r['scene_subframe']) for r in group})==1
  assert len({r['simulation_state_hash'] for r in group})==1
 orphans=set(folder.glob('C*/*.png'))-used
 if m['status']=='complete':assert len(groups)==len(m['requested_times_s']) and not orphans
 if 'cancel_accepted_at' in m:assert m['status']=='incomplete' and m['cancel_stopped_at']>=m['cancel_accepted_at']
 results.append({'path':str(folder),'status':m['status'],'complete_groups':len(groups),'records':len(records),'PNG_files':len(used)+len(orphans),'orphan_PNG_unreferenced':[str(p.relative_to(folder)) for p in orphans],'times':[g[0]['time_s'] for g in groups.values()],'error':m.get('error'),'PNG_sizes':sorted(set((r['image_width'],r['image_height']) for r in records)), 'audit':'PASS'})
(out/'capture-audit.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False,indent=2))
