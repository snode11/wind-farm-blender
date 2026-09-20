"""Minimal Blender regression for the Down-view flexible tip trajectories.

Run from the source checkout with ``open_blade_flex_down.py`` first.  The
script deliberately exercises the native timeline instead of calling private
sampling helpers: frame changes must leave the curve and the blade pose in
agreement, while a redraw at the same frame must not add another point.
"""
from pathlib import Path
import runpy
import sys

import bpy


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "blender_frontend"), str(ROOT)]
runpy.run_path(str(ROOT / "scripts/blender/open_blade_flex_down.py"))

scene = bpy.context.scene
assert scene.get("wfrl_flex_view") == "Down gimbal"
assert scene.wfrl_flex_show_tip_trails is True

from wfrl_blender import tip_tracking

trail = tip_tracking.active(scene)
assert trail is not None and trail.scene == scene and trail.enabled
assert len(trail.objects) == 3
assert all(obj.name == f"WFRL.Turbine.T1.TipTrail.B{i}" for i, obj in trail.objects.items())

def point_count(obj):
    return len(trail.points[int(obj["blade_id"])])

def colors(obj):
    return tuple(round(float(v), 3) for v in obj.data.materials[0].diffuse_color[:3])

expected_colors = ((1.0, 0.05, 0.04), (0.05, 1.0, 0.12), (0.05, 0.35, 1.0))
assert tuple(colors(trail.objects[i]) for i in (1, 2, 3)) == expected_colors
assert all(len(trail.points[i]) == 1 for i in (1, 2, 3))

# Calling the frame handler twice for one paused frame is a redraw, not a new
# sample.  The curves must remain byte-for-byte stable in point count.
scene.frame_set(8)
counts_before = tuple(point_count(trail.objects[i]) for i in (1, 2, 3))
trail.update(scene)
counts_after = tuple(point_count(trail.objects[i]) for i in (1, 2, 3))
assert counts_after == counts_before

# A consecutive frame extends the line by one sample.
scene.frame_set(9)
counts_next = tuple(point_count(trail.objects[i]) for i in (1, 2, 3))
assert all(count == before + 1 for count, before in zip(counts_next, counts_after))

# Native timeline seeking starts a fresh visible pass at the selected frame.
scene.frame_set(24)
counts_seek = tuple(point_count(trail.objects[i]) for i in (1, 2, 3))
assert all(count == 1 for count in counts_seek)
scene.frame_set(4)
counts_back = tuple(point_count(trail.objects[i]) for i in (1, 2, 3))
assert all(count == 1 for count in counts_back)

# FRAME_DROP skips frames during playback: preserve the samples already seen.
from unittest.mock import patch
with patch.object(tip_tracking, '_is_playing', return_value=True):
    scene.frame_set(12)
assert all(point_count(obj) == 2 for obj in trail.objects.values())
for bid in (1, 2, 3):
    blade = scene.objects[f'WFRL.Turbine.T1.Blade{bid}']
    expected = blade.matrix_world @ blade.data.vertices[-1].co
    from mathutils import Vector
    assert (Vector(trail.points[bid][-1]) - expected).length < 1e-5
before = tuple(len(trail.points[i]) for i in (1, 2, 3))
tip_tracking.enable(scene)
assert tuple(len(trail.points[i]) for i in (1, 2, 3)) == before

# The actual panel property controls visibility and starts a fresh pass.
scene.wfrl_flex_show_tip_trails = False
assert all(obj.hide_get() and obj.hide_render for obj in trail.objects.values())
scene.wfrl_flex_show_tip_trails = True
assert all(not obj.hide_get() and not obj.hide_render for obj in trail.objects.values())
assert all(len(points) == 1 for points in trail.points.values())
for frame in range(13, 260):
    scene.frame_set(frame)
assert all(len(points) <= tip_tracking.MAX_POINTS for points in trail.points.values())
from wfrl_blender.panels.clearance import progress_set
with patch.object(tip_tracking, '_is_playing', return_value=True):
    progress_set(scene, 50)
assert all(len(points) == 1 for points in trail.points.values())
bpy.ops.wfrl.clearance_restart()
assert scene.frame_current == scene.frame_start
assert all(len(points) == 1 for points in trail.points.values())

# Leave a useful frame available for optional native render evidence.
for frame in range(2, 181):
    scene.frame_set(frame)
import os
output = os.environ.get('WFRL_TEST_OUTPUT')
if output:
    target = Path(output)
    target.mkdir(parents=True, exist_ok=True)
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 800
    scene.render.resolution_percentage = 100
    scene.render.filepath = str(target / 'down-tip-trails.png')
    bpy.ops.render.render(write_still=True)

print("TIP_TRAIL_REGRESSION_PASS", {
    "camera": scene.get("wfrl_camera"),
    "frame": scene.frame_current,
    "counts": counts_back,
    "colors": expected_colors,
}, flush=True)

if not bpy.app.background:
    bpy.ops.wm.quit_blender()
