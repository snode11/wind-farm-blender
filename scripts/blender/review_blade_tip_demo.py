"""Render the actual replay and verify inner-tip camera/paint invariants."""
from pathlib import Path
import json
import math
import os
import runpy
import bpy

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', ROOT / 'evidence/gimbal-inner-20260915/review'))
OUT.mkdir(parents=True, exist_ok=True)
runpy.run_path(str(ROOT / 'scripts/blender/open_blade_tip_demo.py'))
from wfrl_blender import clearance_replay
from wfrl_blender.cameras import ensure_gimbal, down_gimbal
from wfrl_blender.materials import ensure_blade_tip_markings
from wfrl_blender.panels import gimbal

scene = bpy.context.scene
camera = scene.camera
frame = scene.frame_current
sample = clearance_replay.sample(scene)
location = camera.location.copy()
gimbal.change(camera, dx=10, dy=5, zoom=-3)
assert camera['gimbal_yaw'] == 170 and camera['gimbal_pitch'] == -72
assert camera['gimbal_fov'] == 82
assert ensure_gimbal(scene, 'T1') == camera
down_gimbal(camera)
assert camera.location == location and clearance_replay.sample(scene) == sample
assert camera['gimbal_fov'] == 85 and camera['gimbal_roll'] == 180
blade = scene.objects['WFRL.Turbine.T1.Blade1']
mesh = blade.data
vertices = [tuple(v.co) for v in mesh.vertices]
indices = [p.material_index for p in mesh.polygons]
ensure_blade_tip_markings(scene.objects)
assert vertices == [tuple(v.co) for v in mesh.vertices]
assert indices == [p.material_index for p in mesh.polygons]
red = mesh.materials.find('WFRL.BladeTipRed')
assert red >= 0 and sum(p.material_index == red for p in mesh.polygons) > 0
tip = max(v.co.z for v in mesh.vertices)
rows = sorted(set(round(tip - sum(mesh.vertices[i].co.z for i in p.vertices)/len(p.vertices), 5)
                  for p in mesh.polygons if p.material_index == red))
groups = 1 + sum(b-a > .9 for a, b in zip(rows, rows[1:]))
assert groups == 3, ('Expected three separated paint bands', rows)
scene.render.engine = 'BLENDER_EEVEE'
scene.render.resolution_x, scene.render.resolution_y = 960, 540
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.eevee.taa_render_samples = 16
records = []
for offset in (-4, -2, 0, 2, 4):
    scene.frame_set(max(scene.frame_start, min(scene.frame_end, frame + offset)))
    scene.render.filepath = str(OUT / f'day-{offset:+03d}.png')
    bpy.ops.render.render(write_still=True)
    records.append({'frame': scene.frame_current, 'time_s': clearance_replay.sample(scene)['time_s'],
                    'image': scene.render.filepath})
scene.frame_set(frame)
if os.environ.get('WFRL_EXPORT_VIDEO') == '1':
    video_frames = OUT / 'video-frames'
    video_frames.mkdir(exist_ok=True)
    for number, replay_frame in enumerate(range(scene.frame_start, scene.frame_end + 1, 2)):
        scene.frame_set(replay_frame)
        scene.render.filepath = str(video_frames / f'{number:04d}.png')
        bpy.ops.render.render(write_still=True)
    scene.frame_set(frame)
# Deliberate low-light visibility check, not a calibrated night simulation.
for obj in scene.objects:
    if obj.type == 'LIGHT':
        obj.data.energy *= .015
for node in scene.world.node_tree.nodes:
    if node.type == 'BACKGROUND':
        node.inputs['Strength'].default_value = .003
scene.render.filepath = str(OUT / 'night.png')
bpy.ops.render.render(write_still=True)
(OUT / 'review.json').write_text(json.dumps({'status': 'PASS', 'camera_location': list(location),
    'paint_band_groups': groups, 'unchanged_vertices': len(vertices), 'frames': records,
    'note': 'Actual offline replay poses; illustrative camera mounting and low-light paint'}, indent=2))
print('BLADE_TIP_REVIEW_PASS')
