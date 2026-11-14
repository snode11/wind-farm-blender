"""Manual-scene pitch rate, Down cameras, blade shape and Down MP4 export."""
import importlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
down = importlib.import_module(MODULE + '.manual_down')
cameras = importlib.import_module(MODULE + '.cameras')
geometry = importlib.import_module(MODULE + '.turbine_geometry')
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)
addon.register()
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
scene = bpy.context.scene
assert control.is_manual(scene)
t1 = next(entry for entry in scene.wfrl_manual_turbines if entry.turbine_id == 'T1')
blades = [getattr(t1, f'blade{i}_object') for i in range(1, 4)]


def blade_pitch(blade):
    return -math.degrees(blade.rotation_euler.z)


# Rate 0 keeps instant pitch; a positive rate ramps and then holds.
# The scene enters feathered at 90 deg; start from fine pitch.
scene.wfrl_manual_pitch_rate_deg_s = 0.0
t1.pitch_deg = 0.0
t1.individual_pitch = True
t1.pitch_b1_deg = 4.0
assert abs(blade_pitch(blades[0]) - 4.0) < 1e-5
t1.pitch_b1_deg = 0.0
scene.wfrl_manual_pitch_rate_deg_s = 5.0
t1.pitch_b1_deg = 10.0
assert control.pitch_ramping(t1) and abs(blade_pitch(blades[0])) < 1e-5
assert abs(t1.pitch_start_b1_deg) < 1e-6
now = 1000.0
control.tick(now=now)
control.tick(now=now + 1.0)
assert abs(t1.pitch_actual_b1_deg - 5.0) < 1e-4, t1.pitch_actual_b1_deg
assert abs(blade_pitch(blades[0]) - 5.0) < 1e-4
control.tick(now=now + 3.0)
assert not control.pitch_ramping(t1)
assert abs(blade_pitch(blades[0]) - 10.0) < 1e-4
assert all(abs(blade_pitch(blade)) < 1e-5 for blade in blades[1:])
assert abs(t1.pitch_start_b1_deg) < 1e-6, 'The ramp start is kept for export'

# One shadow per tower: only the sky HDRI sun casts shadows, the lamp is fill light.
lamps = [obj for obj in scene.objects if obj.type == 'LIGHT']
assert len(lamps) == 1 and not lamps[0].data.use_shadow and scene.world.use_sun_shadow

# Down camera shares the recorded-scene preset.
view_area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
view_region = next(region for region in view_area.regions if region.type == 'WINDOW')
with bpy.context.temp_override(area=view_area, region=view_region):
    assert bpy.ops.wfrl.manual_down_view(turbine='T1') == {'FINISHED'}
camera = down.ensure_down_camera(scene, 'T1')
assert camera.parent == t1.yaw_object and cameras.is_down_camera(camera)
bpy.context.view_layer.update()
hub = Vector((-5, 0, 2.4))
offset = camera.location - hub
assert (offset - Vector((1.5, -1.15, -1.95))).length < .03, offset
assert abs(camera.data.lens - 8.0) < 1e-6
assert abs(math.degrees(camera.data.angle_x) - cameras.DOWN_HFOV_DEG) < 1e-3, math.degrees(camera.data.angle_x)
assert [camera['gimbal_yaw'], camera['gimbal_pitch'], camera['gimbal_roll']] == [
    cameras.DOWN_YAW_DEG, cameras.DOWN_PITCH_DEG, cameras.DOWN_ROLL_DEG]  # NREL keeps the recorded preset

# Blade number: camera-parented top-right box showing only the blade in view.
labels = {obj.name.rsplit('.DownCamera.', 1)[1]: obj for obj in camera.children_recursive}
assert set(labels) == {'Legend.B1', 'Legend.B2', 'Legend.B3', 'Legend.Back'}, set(labels)
assert [labels[f'Legend.B{i}'].data.body for i in (1, 2, 3)] == ['B1', 'B2', 'B3']
assert all(not obj.visible_shadow for obj in labels.values())
scene.wfrl_manual_blade_labels = False
assert all(obj.hide_render for obj in labels.values())
scene.wfrl_manual_blade_labels = True

# Blade shape switches in place for all manual blades.
mesh = blades[0].data
before = np.empty(len(mesh.vertices) * 3, np.float32)
mesh.vertices.foreach_get('co', before)
scene.wfrl_manual_blade_model = 'rootkink'
after = np.empty(len(mesh.vertices) * 3, np.float32)
mesh.vertices.foreach_get('co', after)
assert not np.allclose(before, after)
expected_vertices = np.array(geometry.blade_mesh(subdiv=8, ring_points=96, model='rootkink')[0], np.float32)
assert np.allclose(after.reshape(-1, 3), expected_vertices, atol=1e-4)
assert all(blade.data == mesh for entry in scene.wfrl_manual_turbines
           for blade in (entry.blade1_object, entry.blade2_object, entry.blade3_object))
assert mesh.get('wfrl_tip_marking_revision') == 1
# rootkink raises the Down view toward the tower.
assert camera['gimbal_pitch'] == down.MANUAL_DOWN_PITCH_DEG['rootkink'] == down.manual_down_pitch(scene)

# Rigid manual layout in the 1920x1080 Down image with blade 1 at its lowest position.
t1.azimuth_deg = 180.0
shown = [i for i in (1, 2, 3) if not labels[f'Legend.B{i}'].hide_render]
assert shown == [1] and not labels['Legend.Back'].hide_render, shown
bpy.context.view_layer.update()
label_uv = world_to_camera_view(scene, camera, labels['Legend.B1'].matrix_world.translation)
print('MANUAL_DOWN_LABEL_UV', round(label_uv.x, 3), round(1 - label_uv.y, 3), flush=True)
assert label_uv.x > .85 and 1 - label_uv.y < .15
for azimuth, blade in ((60.0, 2), (300.0, 3)):
    t1.azimuth_deg = azimuth
    assert [i for i in (1, 2, 3) if not labels[f'Legend.B{i}'].hide_render] == [blade], azimuth
t1.azimuth_deg = 180.0
bpy.context.view_layer.update()
render = scene.render
render.resolution_x, render.resolution_y = down.VIDEO_SIZE
co = after.reshape(-1, 3)
tip = co[:, 2].argmax()
world = blades[0].matrix_world @ Vector(co[tip])
tip_uv = world_to_camera_view(scene, camera, world)
layout = dict(tip_uv=[round(tip_uv.x, 4), round(1 - tip_uv.y, 4)])
tower = scene.objects[scene['wfrl_manual_owner'] + '.Turbine.T1.Tower']
base = world_to_camera_view(scene, camera, tower.matrix_world @ Vector((0, 0, 0)))
layout['tower_foundation_uv'] = [round(base.x, 4), round(1 - base.y, 4)]
# Manual Down framing: tower base near the image centre line at 0.28 of the height, tip below the midline.
assert abs(layout['tower_foundation_uv'][0] - .5) < .04 and abs(layout['tower_foundation_uv'][1] - .28) < .01, layout
assert .5 < layout['tip_uv'][1] < .56, layout
print('MANUAL_DOWN_LAYOUT', json.dumps(layout), flush=True)

# Export: blade 1 ramps 0 -> 10 deg at 5 deg/s, blades 2/3 stay at 0, rotor at 6 RPM.
t1.rpm = 6.0
state = {e.turbine_id: (e.azimuth_deg, e.pitch_actual_b1_deg) for e in scene.wfrl_manual_turbines}
target = OUT / 't1_down.mp4'
written = down.export_down_video(scene, 'T1', str(target), duration_s=3.0, fps=10, engine='WORKBENCH')
assert Path(written) == target and target.stat().st_size > 10_000, written
try:
    down.export_down_video(scene, 'T1', str(OUT / 'nope.mp4'), frames=5, engine='VIEWPORT_MATERIAL')
    raise AssertionError('Viewport export must need a window')
except ValueError:
    pass
short = down.export_down_video(scene, 'T1', str(OUT / 'frames7.mp4'), frames=7, fps=25, engine='WORKBENCH',
                               size=(960, 540), labels=False)
short_sidecar = json.loads(Path(short).with_suffix('.json').read_text(encoding='utf-8'))
assert short_sidecar['frames'] == 7 and short_sidecar['resolution'] == [960, 540]
assert short_sidecar['blade_labels'] is False
sidecar = json.loads(target.with_suffix('.json').read_text(encoding='utf-8'))
assert sidecar['frames'] == 30 and sidecar['resolution'] == [1920, 1080] and sidecar['blade_labels'] is True
assert sidecar['gpu'] == 'unknown'  # background mode has no GPU context
pitches = [row['T1']['pitch_deg'] for row in sidecar['samples']]
assert pitches[0] == [0.0, 0.0, 0.0]
assert abs(pitches[10][0] - 5.0) < 1e-6 and pitches[10][1:] == [0.0, 0.0]
assert all(abs(row[0] - 10.0) < 1e-6 for row in pitches[20:])
azimuth = [row['T1']['azimuth_deg'] for row in sidecar['samples']]
assert abs(azimuth[10] - (180.0 + 36.0)) < 1e-6
assert not scene.get('wfrl_manual_exporting') and scene.camera.name.endswith('.Camera')
assert render.image_settings.media_type == 'IMAGE'
assert {e.turbine_id: (e.azimuth_deg, e.pitch_actual_b1_deg) for e in scene.wfrl_manual_turbines} == state
assert abs(blade_pitch(blades[0]) - 10.0) < 1e-4

# A ramp in progress holds while an export blocks the clock: the next tick must not add the
# export duration to the ramp, so the ramp clock restarts when the export finishes.
scene.wfrl_manual_pitch_rate_deg_s = 1.0
t1.pitch_b1_deg = 30.0
control.tick()
assert control.pitch_ramping(t1)
held = t1.pitch_actual_b1_deg
started = time.monotonic()
down.export_down_video(scene, 'T1', str(OUT / 'hold.mp4'), frames=3, fps=10, engine='WORKBENCH',
                       size=(64, 64), labels=False, dynamics=False)
assert control._PITCH_TIMES[control._scene_key(scene)] > started, 'Ramp clock kept the export time'
assert t1.pitch_actual_b1_deg == held
scene.wfrl_manual_pitch_rate_deg_s = 0.0
t1.pitch_b1_deg = 10.0
scene.wfrl_manual_pitch_rate_deg_s = 5.0
assert abs(blade_pitch(blades[0]) - 10.0) < 1e-4

# A fixed seed reproduces the shaken, bending export frame for frame; another seed differs.
FFMPEG = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'


def seeded(name, seed):
    path = Path(down.export_down_video(scene, 'T1', str(OUT / f'{name}.mp4'), frames=4, fps=10,
                                       engine='WORKBENCH', size=(96, 54), labels=False, seed=seed))
    data = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    assert data['camera_shake']['seed'] == seed and data['structural_motion']['seed'] == seed, data
    assert any(row['camera_shake_deg'] != [0.0, 0.0] for row in data['samples'])
    return subprocess.run([FFMPEG, '-v', 'error', '-i', str(path), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                          capture_output=True, check=True).stdout


first, again, other = seeded('seed7a', 7), seeded('seed7b', 7), seeded('seed8', 8)
assert len(first) == 4 * 96 * 54 * 3 and first == again and first != other

# Frame 15 (1.5 s) of the deterministic export pose: blade 1 at 7.5 deg.
item = sidecar['turbines'][0]
assert item['turbine'] == 'T1'
assert down.pose_at(item, 1.5)[1] == [7.5, 0.0, 0.0]

# Still frames at the lowest passage for visual comparison: 10 deg blade vs 0 deg blade.
for name, pitch in (('pitch00', 0.0), ('pitch10', 10.0)):
    control.apply_pitch(t1, [pitch, 0.0, 0.0])
    scene.camera = camera
    render.engine = 'BLENDER_EEVEE'
    render.filepath = str(OUT / f'down-b1-{name}.png')
    render.image_settings.file_format = 'PNG'
    bpy.ops.render.render(write_still=True)
control.apply_pitch(t1, [t1.pitch_actual_b1_deg, 0.0, 0.0])

result = dict(status='PASS', video=target.name, video_bytes=target.stat().st_size, layout=layout,
              blender=bpy.app.version_string)
(OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('MANUAL_DOWN_VIDEO_PASS', json.dumps(result, ensure_ascii=False), flush=True)
