"""Three-camera Down rig geometry regression.

Run with WFRL_TEST_OUTPUT=/tmp/... Blender --background --factory-startup --python.

The side cameras share the Down lens and are the Down pose turned by
+/-TRIPLE_STEP_DEG about the rotor shaft.  A blade at azimuth (a ± step) in a
side camera lands at the same image position as that blade at azimuth a in the
center camera; only the ground, horizon and tower move.  This checks that
equivalence and that switching the active view does not disturb the rotor.
"""
import importlib
import json
import math
import os
from pathlib import Path
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
down = importlib.import_module(MODULE + '.manual_down')
cameras_mod = importlib.import_module(MODULE + '.cameras')
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
t1 = next(e for e in scene.wfrl_manual_turbines if e.turbine_id == 'T1')
t1.rpm = 0.0
bpy.context.view_layer.update()

step = down.TRIPLE_STEP_DEG

# ── Camera existence and lens match ──────────────────────────────────────────
center = down.ensure_down_camera(scene, 'T1', reset=True)
sides = down.ensure_side_cameras(scene, 'T1')
left, right = sides['Left'], sides['Right']

assert cameras_mod.is_down_camera(center)
assert math.isclose(left.data.lens, center.data.lens), (left.data.lens, center.data.lens)
assert math.isclose(right.data.lens, center.data.lens), (right.data.lens, center.data.lens)
assert math.isclose(left.data.sensor_width, center.data.sensor_width)
assert math.isclose(right.data.sensor_width, center.data.sensor_width)

# ── Forward-direction angle equals TRIPLE_STEP_DEG ───────────────────────────
bpy.context.view_layer.update()


def fwd(cam):
    return -(cam.matrix_world.to_3x3().col[2]).normalized()


center_dir = fwd(center)
for side, cam in (('Left', left), ('Right', right)):
    angle = math.degrees(math.acos(max(-1.0, min(1.0, fwd(cam).dot(center_dir)))))
    assert abs(angle - step) < 1.0, f'{side} forward angle {angle:.2f} deg, expected {step}'

# Both side cameras are at the same distance from the hub as the center camera.
hub = t1.rotor_object.matrix_world.translation
center_dist = (center.matrix_world.translation - hub).length
for side, cam in (('Left', left), ('Right', right)):
    d = (cam.matrix_world.translation - hub).length
    assert abs(d - center_dist) < 0.05, f'{side} hub dist {d:.3f} m vs center {center_dist:.3f} m'

# ── Blade posture equivalence ─────────────────────────────────────────────────
# A blade at azimuth (a ± step) in a side camera maps to the same image UV as
# blade at azimuth a in the center camera, because the side camera is the
# center pose turned by ±step about the rotor shaft.
scene.render.resolution_x, scene.render.resolution_y = down.VIDEO_SIZE
scene.render.resolution_percentage = 100


def blade_tip_world(azimuth):
    t1.azimuth_deg = azimuth
    bpy.context.view_layer.update()
    mesh = t1.blade1_object.data
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    tip_idx = co[:, 2].argmax()
    return t1.blade1_object.matrix_world @ Vector(co[tip_idx])


def uv_of(camera, world_point):
    v = world_to_camera_view(scene, camera, world_point)
    return v.x, 1.0 - v.y  # flip V to image convention (0 = top)


# Center: blade 1 at azimuth 180 (lowest passage) must be visible.
center_tip = blade_tip_world(180.0)
cu, cv = uv_of(center, center_tip)
assert 0.0 <= cu <= 1.0 and 0.0 <= cv <= 1.0, f'Center tip not in frame: ({cu:.3f}, {cv:.3f})'

# Side cameras: blade 1 at azimuth 180 ± step must land at the same UV.
# The shaft rotation turns the left camera by +step so it sees the blade
# that is +step ahead in azimuth; the right camera sees the blade that is
# -step ahead (i.e. 180 - step = 133° for step=47°).
for side, cam, az in (('Left', left, 180.0 + step), ('Right', right, 180.0 - step)):
    su, sv = uv_of(cam, blade_tip_world(az))
    assert abs(su - cu) < 0.02 and abs(sv - cv) < 0.02, (
        f'{side} blade UV ({su:.4f}, {sv:.4f}) differs from center ({cu:.4f}, {cv:.4f})')

# ── View switch does not disturb the rotor ────────────────────────────────────
t1.azimuth_deg = 137.0
bpy.context.view_layer.update()
assert bpy.ops.wfrl.manual_down_view(turbine='T1', side='LEFT') == {'FINISHED'}
assert math.isclose(t1.azimuth_deg, 137.0), t1.azimuth_deg
assert bpy.ops.wfrl.manual_down_view(turbine='T1', side='CENTER') == {'FINISHED'}
assert math.isclose(t1.azimuth_deg, 137.0), t1.azimuth_deg

# Only CENTER is implied by a button that does not set ``side``.
side_prop = bpy.ops.wfrl.manual_down_view.get_rna_type().properties['side']
assert side_prop.is_skip_save

# ── Blade numbers ─────────────────────────────────────────────────────────────
# Each camera labels the blade whose mid-span is nearest its image centre, and
# a blade passes the cameras in one order: Right -> Center -> Left.
rig = (('Center', center), ('Left', left), ('Right', right))


def mid_uv(camera, blade):
    point = blade.matrix_world @ down._stations(blade.data)[0]
    view = world_to_camera_view(scene, camera, point)
    return view.x, 1.0 - view.y, view.z


first_seen = {}
for az in range(0, 360, 3):
    t1.azimuth_deg = float(az)
    bpy.context.view_layer.update()
    for name, cam in rig:
        label = down.current_blade(cam, t1)
        visible = []
        for index in (1, 2, 3):
            u, v, z = mid_uv(cam, getattr(t1, f'blade{index}_object'))
            if z > 0 and 0 <= u <= 1 and 0 <= v <= 1:
                visible.append((abs(u - .5), index))
        if visible:
            assert label == min(visible)[1], (az, name, label, visible)
        if any(index == 1 for _d, index in visible):
            first_seen.setdefault(name, az)
assert first_seen['Right'] < first_seen['Center'] < first_seen['Left'], first_seen

# ── Camera shake ──────────────────────────────────────────────────────────────
assert down.shake_signal(0.0, 1.0, 7) == (0.0, 0.0)
peak = {rpm: max(max(abs(a) for a in down.shake_signal(rpm, f / 25, 7)) for f in range(2000))
        for rpm in (3.0, 9.0, 14.0, 30.0)}
assert peak[3.0] < peak[9.0] < peak[14.0] <= down.SHAKE_MAX_DEG + 1e-9 and peak[30.0] == peak[14.0], peak
# Quadratic in RPM: 9 RPM shakes at about (9/14)^2 = 0.41 of the 14 RPM level.
assert .3 < peak[9.0] / peak[14.0] < .5, peak

fps = 25
worst = {}


def same_pose(a, b):
    return all(abs(a[i][j] - b[i][j]) < 1e-5 for i in range(4) for j in range(4))


for model in ('nrel5mw', 'rootkink'):
    scene.wfrl_manual_blade_model = model
    t1.rpm, t1.azimuth_deg = 15.0, 0.0
    plan = down._plan(scene, fps, 3 * 4 * fps, True)  # three revolutions at 15 RPM
    job = dict(plan=plan)
    cams = [center, left, right]
    base = [cam.matrix_basis.copy() for cam in cams]
    down._start_shake(job, 'T1', cams)
    for frame in range(plan['frames']):
        down.apply_frame(scene, plan, frame / fps, labels=False)
        down.apply_shake(scene, job, frame)
        bpy.context.view_layer.update()
        for name, cam in rig:
            for index in (1, 2, 3):
                blade = getattr(t1, f'blade{index}_object')
                tip = blade.matrix_world @ down._tip_local(blade)
                view = world_to_camera_view(scene, cam, tip)
                if view.z > 0 and 0 <= view.x <= 1 and 0 <= 1 - view.y <= 1:
                    worst[(model, name)] = min(worst.get((model, name), 1.0), 1.0 - view.y)
    tilts = [abs(job['shake'][f][0]) for f in job['shake']]
    pans = [abs(job['shake'][f][1]) for f in job['shake']]
    assert max(pans) > .3 and max(tilts) > .2, (max(pans), max(tilts))
    down._end_shake(job)
    assert all(same_pose(cam.matrix_basis, b) for cam, b in zip(cams, base))
assert all(v >= .5 for v in worst.values()), worst
t1.rpm = 0.0

# Workbench export through the frame handler carries the recorded shake.
t1.rpm, t1.azimuth_deg = 12.0, 180.0
before = center.matrix_basis.copy()
video = down.export_down_video(scene, 'T1', str(OUT / 'shake.mp4'), frames=10, fps=10,
                               engine='WORKBENCH', size=(480, 270), labels=False)
sidecar = json.loads(Path(video).with_suffix('.json').read_text(encoding='utf-8'))
shakes = [row['camera_shake_deg'] for row in sidecar['samples']]
assert len(shakes) == 10 and any(any(abs(a) > 1e-3 for a in row) for row in shakes), shakes
assert 'seed' in sidecar['camera_shake'] and same_pose(center.matrix_basis, before)

# ── Result ────────────────────────────────────────────────────────────────────
result = dict(
    status='PASS',
    step_deg=step,
    center_uv=[round(cu, 4), round(cv, 4)],
    first_visible_azimuth_b1=first_seen,
    shake_peak_deg={str(k): round(v, 3) for k, v in peak.items()},
    tip_min_v_with_shake={f'{m}/{n}': round(v, 4) for (m, n), v in worst.items()},
    blender=bpy.app.version_string,
)
(OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('TRIPLE_DOWN_RIG_PASS', json.dumps(result, ensure_ascii=False), flush=True)
