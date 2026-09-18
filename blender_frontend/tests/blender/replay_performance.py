"""Ten full-timeline traversals with explicit dropped frames and bounded storage."""
from pathlib import Path
import os,json,runpy,time,resource
from unittest.mock import patch
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[3]
start=time.perf_counter();runpy.run_path(str(ROOT/'scripts/blender/open_farm_flex.py'));load=time.perf_counter()-start
from wfrl_blender import farm_flex,tip_tracking
scene=bpy.context.scene
scene.wfrl_deflection_visible=False
p=farm_flex._ACTIVE
frames=list(range(1,scene.frame_end+1,31))
if frames[-1]!=scene.frame_end:frames.append(scene.frame_end)
def resources():return [len(bpy.data.objects),len(bpy.data.curves),len(bpy.data.materials)]
with patch.object(tip_tracking,'_is_playing',return_value=True):
    for f in frames:scene.frame_set(f)
    before=resources();rss0=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    costs=[];loops=[]
    for n in range(10):
        for f in frames:
            start=time.perf_counter();scene.frame_set(f);costs.append((time.perf_counter()-start)*1000)
            trail=tip_tracking.active(scene)
            assert all(len(v)<=tip_tracking.MAX_POINTS for v in trail.points.values())
        loops.append(dict(resources=resources(),peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2))
        assert resources()==before
result=dict(schema='wfrl.replay-performance.v1',package=os.environ.get('WFRL_FARM_FLEX_PACKAGE','legacy built-in'),
    load_s=load,p95_frame_ms=float(np.percentile(costs,95)),median_frame_ms=float(np.median(costs)),
    rss_growth_mib=(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss-rss0)/1024**2,loops=loops,
    frames_per_loop=len(frames),scope='Native background frame updates at 31/60 simulation-second spacing (both saved and interpolated frames); skipped frames intentional; not viewport FPS; dense trail sampling tested separately')
out=Path(os.environ['WFRL_TEST_OUTPUT']);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2));print('PERFORMANCE',result,flush=True)
