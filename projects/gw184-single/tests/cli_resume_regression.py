"""RENDERS MEDIA: opt-in integration test, excluded from no-render preflight."""
import argparse
from pathlib import Path
import json
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[3]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--render-authorized',action='store_true')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
if not args.render_authorized:
    raise SystemExit('New rendering is disabled; explicit user authorization and --render-authorized required')
output=args.output.resolve()
if output.exists():
    raise RuntimeError('Choose a fresh test output; never overwrite earlier evidence')
base=['/Applications/Blender.app/Contents/MacOS/Blender','--background','--factory-startup',
      '--python-exit-code','1','--python',str(ROOT/'scripts/blender/gw184_single.py'),'--',
      '--mode','clip','--render-authorized','--output',str(output),'--width','320','--duration-s','0.1']
log=output.with_name(output.name+'-resume-test.log')
if log.exists():
    raise RuntimeError('Choose a fresh log path')
log.parent.mkdir(parents=True,exist_ok=True)
with log.open('w') as f:
    subprocess.run(base+['--max-frames','1'],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
    partial=json.loads((output/'status.json').read_text())
    assert partial['state']=='partial' and set(partial['counts'].values())=={1}
    assert not (output/'videos').exists()
    locked=json.loads((output/'job.json').read_text())['code_and_sources']
    assert 'blender_frontend/wfrl_blender/scene_builder.py' in locked
    assert 'blender_frontend/wfrl_blender/camera_projection.py' in locked
    first=(output/'frames/C1/000000.png').stat().st_mtime_ns
    subprocess.run(base,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
    assert json.loads((output/'status.json').read_text())['state']=='complete'
    assert (output/'frames/C1/000000.png').stat().st_mtime_ns==first
    data=json.loads((output/'metadata/video-verification.json').read_text())
    assert data['frame_count']==3 and len(data['videos'])==3 and not data['full_resolution']
    bad=subprocess.run(base+['--width','640'],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
    assert bad.returncode!=0
    assert json.loads((output/'status.json').read_text())['state']=='complete'
print('PASS: partial batch, resume preserves prior frame, three-stream encode verification, mismatched configuration rejected')
