"""Native nine-blade replay, seeking and camera regression."""
from pathlib import Path
import json
import os
import runpy
import time
import numpy as np
import bpy

ROOT=Path(__file__).resolve().parents[3]
loaded=runpy.run_path(str(ROOT/'scripts/blender/open_farm_flex.py'))
preview=loaded['preview'];scene=bpy.context.scene
from wfrl_blender import clearance_replay,tip_tracking

out=Path(os.environ.get('WFRL_FARM_FLEX_EVIDENCE',ROOT/'evidence/flex-mappo/native-v1'))
out.mkdir(parents=True,exist_ok=True)
assert len(preview.blades)==9
assert len({obj.data.as_pointer() for obj,*_ in preview.blades})==9
expected_duration=float(os.environ.get("WFRL_EXPECT_DURATION",60))
assert scene.frame_end==1+round(expected_duration*60)
scene.frame_set(121)
bpy.ops.wfrl.farm_flex_view(turbine='all')
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
assert scene.camera.data.clip_end>2500
for x in (0,504,1008):
    projection=world_to_camera_view(scene,scene.camera,Vector((x,0,87.6)))
    assert 0<projection.x<1 and 0<projection.y<1 and 0<projection.z<scene.camera.data.clip_end,projection
saved=[np.array([v.co[:] for v in obj.data.vertices]) for obj,*_ in preview.blades]
stamp=clearance_replay.sample(scene)['time_s']
for tid in ('T2','T3','all','T1'):
    bpy.ops.wfrl.farm_flex_view(turbine=tid)
    assert scene.frame_current==121
    assert clearance_replay.sample(scene)['time_s']==stamp
scene.render.fps=24
assert clearance_replay.sample(scene)['time_s']==stamp
scene.render.fps=60
scene.frame_set(min(3001,scene.frame_end));scene.frame_set(121)
for (obj,*_),coords in zip(preview.blades,saved):
    assert np.allclose(coords,np.array([v.co[:] for v in obj.data.vertices]),atol=1e-5)
scene.wfrl_flex_show_tip_trails=False
trail=tip_tracking.active(scene)
assert trail is None or all(o.hide_render for o in trail.objects.values())
scene.wfrl_flex_show_tip_trails=True
samples=[];root_error=0.
for frame in range(201,261):
    begin=time.perf_counter();scene.frame_set(frame)
    samples.append((time.perf_counter()-begin)*1000)
    for obj,rest,*_ in preview.blades:
        indices=np.flatnonzero(rest[:,2]<1.501)
        if len(indices):
            current=np.array([obj.data.vertices[int(i)].co[:] for i in indices])
            root_error=max(root_error,float(np.linalg.norm(current-rest[indices],axis=1).max()))
trail=tip_tracking.active(scene)
counts=[len(p) for p in trail.points.values()]
scene.frame_set(260)
assert counts==[len(p) for p in trail.points.values()]
assert root_error<.01,root_error
assert len(preview.cache)<=3
scene.frame_set(scene.frame_end)
assert clearance_replay.sample(scene)['time_s']==preview.times[-1]
result=dict(status='PASS',blades=9,root_connection_max_error_m=root_error,
            mean_update_ms=float(np.mean(samples)),p95_update_ms=float(np.percentile(samples,95)),
            scope='background frame update; not actual viewport FPS',duration_s=expected_duration)
(out/'checks.json').write_text(json.dumps(result,indent=2))
print('FARM_FLEX_REGRESSION_PASS',json.dumps(result),flush=True)

# Switching from a long-hidden turbine must sample its updated mesh first.
bpy.ops.wfrl.farm_flex_view(turbine='T1')
scene.frame_set(1500)
bpy.ops.wfrl.farm_flex_view(turbine='T2')
trail=tip_tracking.active(scene)
for bid in (1,2,3):
    tip=tip_tracking._tip_world(scene.objects[f'WFRL.Turbine.T2.Blade{bid}'])
    assert np.linalg.norm(np.array(trail.points[bid][-1])-np.array(tip)) < 1e-5
# Verify the card receives the range from the same B2 measurement, including hold.
reader=preview.readers['T2']
row=next(r for r in reader.package.measurements if r['beams']['B2']['valid'])
value=reader.at(row['time_s'])
assert value['measurement']['slant_range_m']==row['beams']['B2']['slant_range_m']
from wfrl_blender.panels.clearance import draw_measurement
class Layout:
    def __init__(self): self.labels=[]
    def box(self): return self
    def column(self, **kw): return self
    def label(self, *, text, **kw): self.labels.append(text)
layout=Layout();draw_measurement(layout,scene,value)
assert any('雷达 → 叶片（B2）' in label and f"{row['beams']['B2']['slant_range_m']:.2f}" in label for label in layout.labels)
empty=Layout();draw_measurement(empty,scene,reader.at(reader.start_s))
assert any('雷达 → 叶片（B2）：-- m' == label for label in empty.labels)
# Rebuilding releases old RNA references before deleting objects.
import wfrl_blender
from wfrl_blender import farm_flex
previous = farm_flex._ACTIVE
wfrl_blender.load_demo_scene()
assert not previous.enabled
assert farm_flex._ACTIVE is not previous
assert farm_flex.is_active(scene)
assert scene.frame_end == 3601
scene.frame_set(20)
farm_flex.attach(scene,loaded['folder'])
wfrl_blender.unregister()
assert farm_flex._ACTIVE is None
assert farm_flex.update not in bpy.app.handlers.frame_change_post
assert farm_flex.on_load_pre not in bpy.app.handlers.load_pre
assert tip_tracking.active(scene) is None
print('FARM_FLEX_SWITCH_RANGE_LIFECYCLE_PASS',flush=True)
