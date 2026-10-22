"""Revolution comparison, native package/cameras, and resource check."""
from pathlib import Path
import math
import json
import os
import runpy
import time
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
started = time.perf_counter()
runpy.run_path(str(ROOT / 'scripts/blender/open_farm_flex.py'))
from wfrl_blender import tip_tracking as tt, clearance_replay
scene = bpy.context.scene
load_s = time.perf_counter() - started
trail = tt.active(scene)
assert trail and trail.enabled

def snapshot():
    return ({b: list(v) for b, v in trail.times.items()},
            {b: list(v) for b, v in trail.points.items()},
            {b: [(s.material_index, [tuple(p.co) for p in s.points])
                 for s in obj.data.splines] for b, obj in trail.objects.items()})

for f in range(1, 242):
    scene.frame_set(f)
now = clearance_replay.sample(scene)['time_s']
assert all(len(v) <= 2 for v in trail.completed.values())
assert all(len(p) <= tt.MAX_POINTS for p in trail.points.values())
for bid in (1, 2, 3):
    blade = scene.objects[f'WFRL.Turbine.T1.Blade{bid}']
    if trail.points[bid]:
        assert np.linalg.norm(np.array(trail.points[bid][-1]) - np.array(blade.matrix_world @ blade.data.vertices[-1].co)) < 1e-5
    levels = [s.material_index for s in trail.objects[bid].data.splines]
    assert len(set(levels)) <= 1  # whole revolutions share opacity
before = snapshot()
for fps in (24, 30, 60, 120):
    scene.render.fps = fps
    trail.update(scene)
    assert snapshot() == before
    for tid, angle in [('T1', 'FRONT'), ('T1', 'DOWN'), ('all', 'DOWN')]:
        bpy.ops.wfrl.farm_flex_view(turbine=tid, angle=angle)
        assert tt.active(scene) is trail and snapshot() == before

# The real progress operator explicitly resets even during playback.
from wfrl_blender.panels.clearance import progress_set
with patch.object(tt, '_is_playing', return_value=True):
    progress_set(scene, 50)
assert all(len(p) == 1 for p in trail.points.values())
scene.frame_set(scene.frame_current-1)
assert all(len(p) == 1 for p in trail.points.values())
scene.wfrl_flex_show_tip_trails = False
assert all(not p for p in trail.points.values())
assert all(not obj.data.splines and obj.hide_get() for obj in trail.objects.values())
scene.wfrl_flex_show_tip_trails = True
assert all(len(p) == 1 for p in trail.points.values())
for tid in ('T2', 'T3', 'T1'):
    bpy.ops.wfrl.farm_flex_view(turbine=tid, angle='DOWN')
    trail = tt.active(scene)
    assert trail.turbine_id == tid and all(len(p) == 1 for p in trail.points.values())

# Exercise the sampler at different rates including dropped frames, isolating
# its resource cost from the unchanged full-farm deformation upload.
counts, costs = [], []
def resources():
    return (len(bpy.data.objects), len(bpy.data.curves), len(bpy.data.materials))
baseline = resources()
for loop in range(10):
    trail.clear('test_loop')
    rate = (24, 40, 60, 120, 15)[loop % 5]
    with patch.object(tt, '_is_playing', return_value=True):
        for tick in range(16*rate+1):
            t = 117.0 + tick/rate
            scene.objects['WFRL.Turbine.T1.Rotor'].rotation_euler.x = (tick/rate)*math.tau/4
            start = time.perf_counter()
            with patch.object(tt, '_simulation_time', return_value=t):
                trail.update(scene)
            costs.append(time.perf_counter()-start)
            assert all(len(p) <= tt.MAX_POINTS for p in trail.points.values())
            assert all(len(v) <= 2 for v in trail.completed.values())
    assert resources() == baseline
    counts.append([len(p) for p in trail.points.values()])

out = Path(os.environ['WFRL_TEST_OUTPUT'])
out.mkdir(parents=True, exist_ok=True)
result = dict(status='PASS', blender=bpy.app.version_string, lifetime_s=tt.LIFETIME_S,
              interval_s=tt.SAMPLE_INTERVAL_S, max_points=tt.MAX_POINTS,
              fade_levels=tt.FADE_LEVELS, ten_loop_counts=counts,
              resources_before=baseline, resources_after=resources(),
              sampler_p95_ms=float(np.percentile(costs,95)*1000), load_s=load_s,
              boundary='Native background checks; timings isolate sampler; window FPS and RSS separate')
(out/'checks.json').write_text(json.dumps(result, indent=2))
print('TIP_FADE_PASS', json.dumps(result), flush=True)
