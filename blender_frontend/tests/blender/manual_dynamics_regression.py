"""Manual Down export structural motion: per-pass tip pixel y and clearance spread.

Run: WFRL_TEST_OUTPUT=<new dir> blender --background --factory-startup --python <this file>
"""
import importlib
import json
import math
import os
import random
from pathlib import Path
import sys
import time

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
dyn = importlib.import_module(MODULE + '.manual_dynamics')
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)

# Signal model: load scaling and calibration statistics.
assert dyn.load_factor(0.0, 0.0) == 0.0 and dyn.load_factor(12.0, 90.0) == 0.0
assert math.isclose(dyn.load_factor(dyn.REFERENCE_RPM, dyn.REFERENCE_PITCH_DEG), 1.0)
assert dyn.load_factor(6.0, 0.0) < dyn.load_factor(12.0, 0.0) < dyn.load_factor(12.1, 0.0) == dyn.load_factor(20.0, 0.0)
signal = dyn.make_signals(5, ['T1'])['T1']
rows = np.array([dyn.sample(signal, dyn.REFERENCE_RPM, dyn.REFERENCE_RPM * 6 * t, [dyn.REFERENCE_PITCH_DEG] * 3, t)[0]
                 for t in np.arange(0, 600, .05)])
stats = dict(mean=float(rows.mean()), std=float(rows.std()), spread=float(np.ptp(rows, axis=1).mean()),
             blade_means=float(np.ptp(rows.mean(axis=0))))
assert abs(stats['mean']) < .1 and 0.6 < stats['std'] < 1.1 and 1.0 < stats['spread'] < 2.8, stats
# Blade imbalance: the three blades keep different mean deviations (about 0.8 m at the reference load).
assert 0.6 < stats['blade_means'] < 1.1, stats

addon.register()
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.wfrl_manual_pitch_rate_deg_s = 0.0
t1 = next(e for e in scene.wfrl_manual_turbines if e.turbine_id == 'T1')
t1.rpm, t1.azimuth_deg = 12.0, 0.0
t1.pitch_deg = 0.0
center = down.ensure_down_camera(scene, 'T1', reset=True)
sides = down.ensure_side_cameras(scene, 'T1')
cams = [center, sides['Left'], sides['Right']]
root = t1.yaw_object.parent
root_before = root.matrix_basis.copy()
cam_before = [c.matrix_basis.copy() for c in cams]


def tip_world(blade, depsgraph):
    evaluated = blade.evaluated_get(depsgraph)
    mesh = evaluated.data
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    return evaluated.matrix_world @ Vector(co[co[:, 2].argmax()].tolist())


def run(dynamics, shake=True, seconds=20.0, fps=30):  # 2.4 deg per frame at 12 rpm hits 180 deg exactly
    plan = down._plan(scene, fps, int(seconds * fps), False)
    job = dict(plan=plan, labels=False)
    down._start_motion(scene, job, 'T1', cams, dynamics)
    if not shake:
        job.pop('rig')
    passes = []
    best = {}
    try:
        for frame in range(plan['frames']):
            down.apply_motion(scene, job, frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            depsgraph.update()
            azimuth, _ = down.pose_at(plan['turbines'][0], frame / fps)
            tower = t1.yaw_object.parent.matrix_world.translation
            for index in range(1, 4):
                blade_az = (azimuth + 120.0 * (index - 1)) % 360.0
                gap = abs(blade_az - 180.0)
                tip = tip_world(getattr(t1, f'blade{index}_object'), depsgraph)
                view = world_to_camera_view(scene, center, tip)
                key = (index, int((frame / fps * 12 / 60) + (index - 1) / 3 + .5))
                if gap < 30 and (key not in best or gap < best[key][0]):
                    best[key] = (gap, (1 - view.y) * 1080, (tip.xy - tower.xy).length, frame)
        record = dict(job.get('dynamics', {}))
    finally:
        down._end_motion(job)
    for (index, _rev), (gap, y, distance, frame) in sorted(best.items(), key=lambda kv: kv[1][3]):
        if gap < .5:
            passes.append(dict(blade=index, frame=frame, tip_px_y=round(y, 1), tip_tower_xy_m=round(distance, 3)))
    return passes, record


def spread(rows, key):
    values = [row[key] for row in rows]
    return round(max(values) - min(values), 3)


measured = {}
record = {}
for model in ('nrel5mw', 'rootkink'):
    scene.wfrl_manual_blade_model = model
    cam_before = [c.matrix_basis.copy() for c in cams]  # the Down pitch follows the blade shape
    cases = {}
    for name, dynamics, shake in (('rigid', False, False), ('shake', False, True),
                                  ('bend', True, False), ('bend_shake', True, True)):
        random.seed(11)  # same shake and structural seeds in every case
        cases[name], frames = run(dynamics, shake)
        if dynamics:
            record = frames
    bent = cases['bend']
    measured[model] = dict(
        passes=len(bent),
        rigid_px=spread(cases['rigid'], 'tip_px_y'), rigid_m=spread(cases['rigid'], 'tip_tower_xy_m'),
        shake_px=spread(cases['shake'], 'tip_px_y'),
        bend_px=spread(bent, 'tip_px_y'), bend_m=spread(bent, 'tip_tower_xy_m'),
        bend_px_per_m=round(float(np.polyfit([r['tip_tower_xy_m'] for r in bent],
                                             [r['tip_px_y'] for r in bent], 1)[0]), 2),
        bend_shake_px=spread(cases['bend_shake'], 'tip_px_y'),
        blade_mean_px=round(float(np.ptp([np.mean([r['tip_px_y'] for r in bent if r['blade'] == b])
                                          for b in (1, 2, 3)])), 1),
        adjacent_px=round(float(np.mean([abs(a['tip_px_y'] - b['tip_px_y'])
                                         for a, b in zip(bent, bent[1:])])), 1),
        bend_min_px_y=min(r['tip_px_y'] for r in bent),
        bend_shake_min_px_y=min(r['tip_px_y'] for r in cases['bend_shake']))
print('MEASURED', json.dumps(measured), flush=True)
for model, m in measured.items():
    assert m['passes'] >= 9 and m['rigid_px'] < 2.0 and m['rigid_m'] < .05, (model, m)
    assert 0.7 < m['bend_m'] < 2.5 and 8.0 < m['bend_px'] < 60.0, (model, m)
    # Successive blades pass at clearly different heights in the Down image.
    assert m['blade_mean_px'] > 8.0 and m['adjacent_px'] > 5.0, (model, m)
    assert m['bend_shake_min_px_y'] > 540.0 and m['bend_shake_px'] > 10.0, (model, m)
assert len(record) > 0 and all('tower_top_m' in v['T1'] for v in record.values())
# State restored: original shared mesh and modifiers, root and camera poses as before.
shared = t1.blade1_object.data
for entry in scene.wfrl_manual_turbines:
    for index in range(1, 4):
        blade = getattr(entry, f'blade{index}_object')
        assert dyn.MODIFIER not in blade.modifiers and blade.data == shared
        assert all(m.show_viewport and m.show_render for m in blade.modifiers), blade.name
assert not [m for m in bpy.data.meshes if m.get('wfrl_flap_baked')]
assert all(abs(root.matrix_basis[i][j] - root_before[i][j]) < 1e-6 for i in range(4) for j in range(4))
assert all(all(abs(c.matrix_basis[i][j] - b[i][j]) < 1e-6 for i in range(4) for j in range(4))
           for c, b in zip(cams, cam_before))

# Workbench export writes the structural motion into the sidecar and restores the scene.
video = down.export_down_video(scene, 'T1', str(OUT / 'dyn.mp4'), frames=8, fps=8,
                               engine='WORKBENCH', size=(480, 270), labels=False)
sidecar = json.loads(Path(video).with_suffix('.json').read_text(encoding='utf-8'))
assert 'structural_motion' in sidecar and all('structural_motion' in r for r in sidecar['samples'])
assert all(dyn.MODIFIER not in getattr(t1, f'blade{i}_object').modifiers for i in range(1, 4))

# Live viewport motion on the manual-control clock (background: pretend the scene is shown).
control.visible_scenes = lambda: {scene.as_pointer()}
assert scene.wfrl_manual_structural_motion
entries = list(scene.wfrl_manual_turbines)
blades_all = [getattr(e, f'blade{i}_object') for e in entries for i in range(1, 4)]
shared_name = shared.name


def bent(entry_list=entries):
    return [dyn.MODIFIER in getattr(e, f'blade{i}_object').modifiers for e in entry_list for i in range(1, 4)]


down_cams = down._down_cameras(scene)


def same(a, b, tol=1e-5):
    return all(abs(a[i][j] - b[i][j]) < tol for i in range(4) for j in range(4))


def rigid_state(cams_too=True):
    assert not any(bent()), bent()
    if cams_too:
        assert all(down._SHAKE_BASE not in c for c in down_cams)
        assert all(same(c.matrix_basis, b) for c, b in zip(cams, cam_before))
    assert all(b.data.name == shared_name and dyn._RESTORE_KEY not in b for b in blades_all)
    assert all(m.show_viewport for b in blades_all for m in b.modifiers)
    assert all(dyn._LEAN_KEY not in e.yaw_object.parent for e in entries)


rigid_state()
for entry, rpm in zip(entries, (12.0, 9.0, 6.0)):
    entry.rpm = rpm
scene.wfrl_manual_target = 'ALL'
assert bpy.ops.wfrl.manual_action(action='START') == {'FINISHED'}
assert all(e.running for e in entries) and all(e.pitch_actual_b1_deg == 0.0 for e in entries)
clock = [time.monotonic()]


def run_live(seconds, fps=30):
    tips = []
    for _ in range(int(seconds * fps)):
        clock[0] += 1.0 / fps
        assert control.tick(now=clock[0]) == control.TICK_SECONDS
        tips.append([dyn.tip_offset(b).length for b in blades_all])
    return np.array(tips)


tips = run_live(8.0)
assert all(bent()) and all(b.data.name.endswith(dyn.BAKED_SUFFIX) for b in blades_all)
assert len({b.data.name for b in blades_all}) == 1, 'one baked copy serves every blade'
early, late = tips[:3].max(), tips[-60:].max(axis=0)
assert early < .1 < late[:3].max(), (early, late)  # amplitude eases in, no jump on start
live_t1, live_t3 = late[:3].max(), late[6:].max()
assert live_t3 < live_t1, (live_t1, live_t3)  # 6 RPM bends less than 12 RPM
assert dyn.live_active(scene)
lean = entries[0].yaw_object.parent
assert any(abs(lean.matrix_basis[i][j] - root_before[i][j]) > 1e-7 for i in range(3) for j in range(3))

# Live camera shake: T1 Down cameras turn about their rest pose, tips stay below the midline.
assert scene.wfrl_manual_camera_shake
assert all(down._SHAKE_BASE in c for c in cams)
assert all(same(down.rest_matrix(c), b) for c, b in zip(cams, cam_before))
turns = []
for _ in range(60):
    run_live(1 / 30)
    turns.append(max(math.degrees((c.matrix_basis.to_3x3() @ b.to_3x3().inverted()).to_quaternion().angle)
                     for c, b in zip(cams, cam_before)))
live_shake_deg = round(max(turns), 3)
assert .1 < live_shake_deg < 2 * down.SHAKE_MAX_DEG, live_shake_deg
# Recomputing side cameras or resetting the Down view keeps the rest pose.
down.ensure_side_cameras(scene, 'T1')
down.ensure_down_camera(scene, 'T1', reset=True)
run_live(1 / 30)
assert all(same(down.rest_matrix(c), b, 1e-5) for c, b in zip(cams, cam_before))
scene.wfrl_manual_camera_shake = False
assert all(same(c.matrix_basis, b, 1e-5) for c, b in zip(cams, cam_before))
assert all(down._SHAKE_BASE not in c for c in cams)
run_live(.2)
assert all(down._SHAKE_BASE not in c for c in cams) and all(bent())
scene.wfrl_manual_camera_shake = True
run_live(1.0)
assert all(down._SHAKE_BASE in c for c in cams)

# Switching it off restores the rigid scene at once; switching on resumes.
scene.wfrl_manual_structural_motion = False
rigid_state(cams_too=False)
assert control.tick(now=clock[0] + .01) == control.TICK_SECONDS  # rotors still turn
clock[0] += .01
rigid_state(cams_too=False)
scene.wfrl_manual_structural_motion = True
run_live(1.0)
assert all(bent())

# Export detaches the live bend; it reattaches on the next tick.
video = down.export_down_video(scene, 'T1', str(OUT / 'live.mp4'), frames=6, fps=6,
                               engine='WORKBENCH', size=(480, 270), labels=False)
assert 'structural_motion' in json.loads(Path(video).with_suffix('.json').read_text(encoding='utf-8'))
assert not dyn.live_active(scene)
rigid_state()
run_live(.5)
assert all(bent())

# Blade shape switch rewrites the shared mesh; the live copy follows.
scene.wfrl_manual_blade_model = 'nrel5mw'
assert bpy.data.meshes[shared_name]['wfrl_blade_model'] == 'nrel5mw'
# The Down view returns to the NREL preset pitch at its rest pose.
assert cams[0]['gimbal_pitch'] == down.manual_down_pitch(scene) and down._SHAKE_BASE not in cams[0]
assert not same(cams[0].matrix_basis, cam_before[0])
cam_before = [c.matrix_basis.copy() for c in cams]
run_live(.5)
assert all(bent()) and blades_all[0].data.get('wfrl_blade_model') == 'nrel5mw'

# Saving writes the rigid model; live motion continues afterwards.
saved = OUT / 'live.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(saved), copy=True)
rigid_state()
run_live(.5)
assert all(bent())

# Stopping feathers the blades; the motion decays and the rigid scene returns.
scene.wfrl_manual_pitch_rate_deg_s = 0.0
assert bpy.ops.wfrl.manual_action(action='STOP') == {'FINISHED'}
assert not any(e.running for e in entries)
for _ in range(30 * 30):
    clock[0] += 1.0 / 30
    if control.tick(now=clock[0]) == control.IDLE_SECONDS:
        break
else:
    raise AssertionError('live motion did not settle')
rigid_state()
assert not [m for m in bpy.data.meshes if m.get('wfrl_flap_baked')]

# A scene no longer shown stops ticking and restores at once.
for entry in entries:
    entry.rpm = 12.0
assert bpy.ops.wfrl.manual_action(action='START') == {'FINISHED'}
run_live(1.0)
assert all(bent())
control.visible_scenes = lambda: set()
control.tick(now=clock[0] + .03)
rigid_state()
assert not dyn._LIVE

# The saved file holds the rigid model.
bpy.ops.wm.open_mainfile(filepath=str(saved))
scene = bpy.context.scene
assert not [m for m in bpy.data.meshes if m.get('wfrl_flap_baked')]
assert not [o for o in bpy.data.objects if dyn._RESTORE_KEY in o or dyn._LEAN_KEY in o]
assert not [o for o in bpy.data.objects if dyn.MODIFIER in getattr(o, 'modifiers', ())]

result = dict(status='PASS', signal_stats=stats, measured=measured,
              live_shake_deg=live_shake_deg, live_tip_m=dict(t1_12rpm=round(float(live_t1), 3), t3_6rpm=round(float(live_t3), 3)),
              blender=bpy.app.version_string)
addon.unregister()
(OUT / 'result.json').write_text(json.dumps(result, indent=1) + '\n', encoding='utf-8')
print('MANUAL_DYNAMICS_PASS', json.dumps(result), flush=True)
