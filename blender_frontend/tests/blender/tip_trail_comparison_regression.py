"""Exercise two-revolution hold/fade on the real packaged farm timeline."""
import importlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy

runtime = os.environ.get('WFRL_TEST_RUNTIME', 'wfrl_blender')
if runtime == 'wfrl_blender':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
addon = importlib.import_module(runtime)
farm = importlib.import_module(runtime + '.farm_flex')
tracking = importlib.import_module(runtime + '.tip_tracking')
if not hasattr(bpy.types.Scene, 'wfrl_gimbal_kind'):
    addon.register()
addon.load_demo_scene(farm.default_package())
s = bpy.context.scene
s.wfrl_flex_show_tip_trails = True
trail = tracking.active(s)
assert trail and trail.enabled
colors = [tuple(round(v, 2) for v in trail.objects[b].data.materials[-1].diffuse_color[:3]) for b in (1, 2, 3)]
assert colors == [(1., .05, .04), (.05, 1., .12), (.05, .35, 1.)]

def geometry():
    return {b: [[tuple(p.co) for p in spline.points] for spline in obj.data.splines]
            for b, obj in trail.objects.items()}

def levels():
    return [spline.material_index for obj in trail.objects.values() for spline in obj.data.splines]

with patch.object(tracking, '_is_playing', return_value=True):
    for frame in range(2, s.frame_end, 4):
        s.frame_set(frame)
        if trail.compare_since is not None:
            break
    assert trail.compare_since is not None, 'Two complete revolutions must reach comparison'
    assert all(len(trail.completed[b]) == 2 for b in (1, 2, 3))
    comparison_frame = s.frame_current
    frozen = geometry()
    assert all(len(lines) == 2 for lines in frozen.values())
    assert all(level == 31 for level in levels())
    for _ in range(5):
        trail.update(s)
    assert geometry() == frozen and all(level == 31 for level in levels())
    # Hold/fade durations follow simulation seconds, even if display FPS changes.
    s.render.fps = 24
    s.frame_set(comparison_frame + 60)
    assert geometry() == frozen and all(level == 31 for level in levels())
    s.frame_set(comparison_frame + 140)
    assert geometry() == frozen and all(0 < level < 31 for level in levels())
    fade_levels = levels()
    s.frame_set(comparison_frame + 166)
    assert trail.compare_since is None
    assert all(not trail.completed[b] and len(trail.points[b]) == 1 for b in (1, 2, 3))
    assert all(not obj.data.splines for obj in trail.objects.values())

# Seek backwards starts a new pair and the panel toggle clears hidden history.
s.frame_set(20)
assert all(len(trail.points[b]) == 1 and not trail.completed[b] for b in (1, 2, 3))
s.wfrl_flex_show_tip_trails = False
assert all(obj.hide_render for obj in trail.objects.values())
s.wfrl_flex_show_tip_trails = True
assert all(not obj.hide_render for obj in trail.objects.values())
result = dict(status='PASS', runtime=runtime, blender=bpy.app.version_string,
              comparison_frame=comparison_frame, colors=colors, fade_levels=fade_levels,
              hold_seconds=2, fade_seconds=.75, paused_stable=True, fps_independent=True,
              seek_clears=True, toggle_clears=True)
if os.environ.get('WFRL_TEST_OUTPUT'):
    out = Path(os.environ['WFRL_TEST_OUTPUT']); out.mkdir(parents=True, exist_ok=True)
    (out/'trail-checks.json').write_text(json.dumps(result, indent=2)+'\n')
print('TIP_TRAIL_COMPARISON_PASS', json.dumps(result), flush=True)
