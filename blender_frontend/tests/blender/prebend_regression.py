"""Load actual BeamDyn results through the native production playback path."""
from pathlib import Path
import os, runpy, json, time
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parents[3]
t0=time.perf_counter()
if os.environ.get('WFRL_TEST_RUNTIME'):
    import sys
    sys.path.insert(0,os.environ['WFRL_TEST_RUNTIME'])
    import wfrl_blender
    wfrl_blender.register()
    wfrl_blender.load_demo_scene(os.environ.get('WFRL_FARM_FLEX_PACKAGE'))
else:
    runpy.run_path(str(ROOT/'scripts/blender/open_farm_flex.py'))
from wfrl_blender import farm_flex, clearance_replay
from wfrl_blender.deflection import rigid_frame
p=farm_flex._ACTIVE;s=bpy.context.scene
load=time.perf_counter()-t0
assert p.reference is not None
errors=[];interpolated_errors=[];geometry=[];cost=[]
stride=3 if s.frame_end<400 else 30
frames=sorted({f+offset for f in range(1,s.frame_end+1,stride) for offset in (0,1,2) if f+offset<=s.frame_end})
for f in frames:
    t=time.perf_counter();s.frame_set(f);cost.append((time.perf_counter()-t)*1000)
    for row in p.comparison.rows.values():
        (interpolated_errors if row['interpolated'] else errors).extend(abs(row['error']))
    v=clearance_replay.sample(s)
    assert abs(v['time_s']-s['wfrl_clearance_time_s'])<1e-9
assert errors and max(errors)<.005, max(errors)
assert not interpolated_errors or max(interpolated_errors)<.005, max(interpolated_errors)
# Rest reference contains a full metre of prebend; first loaded deformation is not zero.
assert np.isclose(p.comparison.rest[-2,0],-1)
assert np.linalg.norm(p.comparison.rows[1]['simulation'])>0
for frame in (1,s.frame_end,s.frame_end//2,2,s.frame_end):
    s.frame_set(frame); before=clearance_replay.sample(s)
    for tid in p.readers:
        bpy.ops.wfrl.farm_flex_view(turbine=tid,angle='DOWN')
        for fps in (24,60,120):
            s.render.fps=fps;p.update(s)
            assert clearance_replay.sample(s)['time_s']==before['time_s']
    for tid,angle in [('T1','FRONT'),('all','DOWN'),('T1','DOWN')]:
        bpy.ops.wfrl.farm_flex_view(turbine=tid,angle=angle)
        assert clearance_replay.sample(s)['time_s']==before['time_s']
# Persistence must reconstruct the same reference once, including after saved meshes exist.
import tempfile
with tempfile.TemporaryDirectory() as temp:
    s.frame_set(min(181,s.frame_end));s.wfrl_deflection_visible=True
    before={b:row['actual'].copy() for b,row in p.comparison.rows.items()}
    filename=str(Path(temp)/'prebend.blend')
    bpy.ops.wm.save_as_mainfile(filepath=filename)
    bpy.ops.wm.open_mainfile(filepath=filename)
    s=bpy.context.scene;p=farm_flex._ACTIVE
    assert p and p.reference is not None
    for b,row in p.comparison.rows.items():np.testing.assert_allclose(row['actual'],before[b],atol=2e-5)
    assert len([o for o in s.objects if o.name.startswith('WFRL.Deflection.T1.')])==7
s.render.fps=60;s.frame_set(1)
out=Path(os.environ['WFRL_TEST_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
result=dict(status='PASS',turbines=list(p.readers),max_component_error_m=float(max(errors)),
    interpolated_component_error_m=float(max(interpolated_errors)) if interpolated_errors else None,
    load_s=load,frame_update_p95_ms=float(np.percentile(cost,95)),blender=bpy.app.version_string)
(out/'checks.json').write_text(json.dumps(result,indent=2));print('PREBEND_PASS',result,flush=True)
