from pathlib import Path
import json,hashlib,shutil,tempfile,sys
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'blender_frontend')]
from wfrl_blender.farm_flex import read_package
from wfrl_blender.deflection import read_comparison
source=ROOT/'results/prebend-20260918/short-replay-surface'
checks={}
for kind in ('unchanged','curve','model','deflection_axes','missing_deflection','geometry_hash','unknown_schema'):
 with tempfile.TemporaryDirectory() as temp:
  p=Path(temp)/'package';shutil.copytree(source,p)
  m=json.loads((p/'manifest.json').read_text())
  if kind in ('curve','model'):
   r=json.loads((p/'blade-reference.json').read_text())
   if kind=='curve':r['curve'][-1][0]=-.5
   else:r['model']='unmodified NREL 5MW'
   (p/'blade-reference.json').write_text(json.dumps(r));m['files']['blade-reference.json']=hashlib.sha256((p/'blade-reference.json').read_bytes()).hexdigest()
  if kind=='deflection_axes':
   r=json.loads((p/'deflection-t1.json').read_text());r['frame']='ElastoDyn unpitched coned xc,yc,zc'
   (p/'deflection-t1.json').write_text(json.dumps(r));m['files']['deflection-t1.json']=hashlib.sha256((p/'deflection-t1.json').read_bytes()).hexdigest()
  if kind=='missing_deflection':del m['files']['deflection-t1.json']
  if kind=='geometry_hash':m['files']['geometry.npz']='0'*64
  if kind=='unknown_schema':m['schema']='wfrl.farm-flex-review.v99'
  (p/'manifest.json').write_text(json.dumps(m))
  try:
   m,t,tr,poses,readers=read_package(p);read_comparison(p,m,t,poses)
   assert kind=='unchanged',kind
   checks[kind]='accepted'
  except ValueError as e:
   assert kind!='unchanged',str(e)
   checks[kind]='rejected: '+str(e)
(ROOT/'evidence/prebend-20260918/probe-contract.json').write_text(json.dumps(checks,indent=2));print(checks)
