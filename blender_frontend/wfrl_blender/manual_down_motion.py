"""Frame-sampled manual poses, export shake and live camera motion."""
import math
import random

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector
import numpy as np

from . import manual_control, manual_dynamics
from . import manual_down_cameras as camera_rig
from .manual_motion import pitch_at


# Exported videos carry a nacelle-vibration camera shake: a smooth tilt/pan of
# the rig in each camera's own frame, shared by all cameras of the turbine.
# Its amplitude grows with the square of RPM up to SHAKE_MAX_DEG at SHAKE_RPM_REF and
# combines slow sway, a blade-passing (3P) term and a light high-frequency jitter.
# Tilt is clamped per frame so no blade tip near the frame crosses into the
# upper image half (SHAKE_TIP_MARGIN_DEG below the midline at the closest).
SHAKE_MAX_DEG = 0.9
SHAKE_RPM_REF = 14.0
SHAKE_TIP_MARGIN_DEG = 0.1
_SHAKE_TERMS = ((0.45, 0.83), (0.25, 2.1), (0.10, 5.3), (0.08, 7.3))  # (weight, Hz)
_SHAKE_3P_WEIGHT = 0.12


def shake_signal(rpm, time_s, seed):
    """Unclamped (tilt_deg, pan_deg) of the camera shake at ``time_s``.

    Sums of sinusoids at incommensurate frequencies with seeded phases plus a
    blade-passing (3P) term, so the motion is smooth and does not repeat with
    the rotor period. Peak magnitude of each component is
    shake_amplitude(rpm)."""
    amp = shake_amplitude(rpm)
    if amp <= 0:
        return 0.0, 0.0
    tilt, pan = _shake_shape(rpm, time_s, seed)
    return amp * tilt, amp * pan


def shake_amplitude(rpm):
    """SHAKE_MAX_DEG * (min(rpm, SHAKE_RPM_REF) / SHAKE_RPM_REF) ** 2."""
    speed = min(max(float(rpm), 0.0), SHAKE_RPM_REF)
    return SHAKE_MAX_DEG * (speed / SHAKE_RPM_REF) ** 2


def _shake_shape(rpm, time_s, seed):
    """Unit-amplitude (tilt, pan) shake shape; ``rpm`` sets the 3P frequency."""
    speed = min(max(float(rpm), 0.0), SHAKE_RPM_REF)
    rng = random.Random(seed)
    values = []
    for _axis in range(2):
        total = 0.0
        for weight, hz in (*_SHAKE_TERMS, (_SHAKE_3P_WEIGHT, None)):
            jitter, phase = rng.uniform(.8, 1.25), rng.uniform(0, 2 * math.pi)
            frequency = 3 * speed / 60 if hz is None else hz * jitter
            total += weight * math.sin(2 * math.pi * frequency * time_s + phase)
        values.append(total)
    return values[0], values[1]


_TIPS = {}


def _tip_local(blade):
    """Blade-local tip point: the vertex farthest along the blade axis."""
    mesh = blade.data
    key = (mesh.as_pointer(), mesh.get('wfrl_blade_model'), len(mesh.vertices))
    if key not in _TIPS:
        co = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        _TIPS[key] = Vector(co[co[:, 2].argmax()].tolist())
    return _TIPS[key]


def min_shake_tilt(camera, entry, *, camera_world=None):
    """Smallest tilt (deg) that keeps every tip near the frame below the image midline.

    Tilting a camera about its local X by t shifts the vertical view angle
    psi = atan2(y, -z) of every point by exactly -t, and a pan about local Y
    leaves the sign of y unchanged, so the bound is max(psi_tip) + margin."""
    inverse = (camera_rig._world(camera) if camera_world is None else camera_world).inverted()
    half = math.tan(camera.data.angle_x / 2) * 1.15
    bound = -math.inf
    for index in range(1, 4):
        blade = getattr(entry, f'blade{index}_object')
        local = inverse @ camera_rig._world(blade) @ (_tip_local(blade) + manual_dynamics.tip_offset(blade))
        if local.z >= 0 or abs(local.x / -local.z) > half:
            continue
        bound = max(bound, math.degrees(math.atan2(local.y, -local.z)) + SHAKE_TIP_MARGIN_DEG)
    return bound


def apply_shake(scene, job, frame, rpm):
    """Pose the export cameras for ``frame``: base pose turned by the clamped shake."""
    rig = job.get('rig')
    if not rig:
        return 0., 0.
    entry = camera_rig._entry(scene, job['turbine'])
    tilt, pan = shake_signal(rpm, frame / job['plan']['fps'], job['shake_seed'])
    for camera, base, _transform in rig:
        camera.matrix_basis = base
    center = rig[0][0]
    yaw_world = camera_rig._world(entry.yaw_object)
    tilt = max(tilt, min_shake_tilt(center, entry),
               *(min_shake_tilt(center, entry, camera_world=yaw_world @ side_pose)
                 for side_pose in job['shake_side_poses']))
    turn = Matrix.Rotation(math.radians(tilt), 4, 'X') @ Matrix.Rotation(math.radians(pan), 4, 'Y')
    for camera, base, _transform in rig:
        camera.matrix_basis = base @ turn
    return round(tilt, 6), round(pan, 6)


def _start_shake(job, entry, cameras_list, seed=None):
    job['turbine'] = entry.turbine_id
    rig = job['rig'] = []
    for camera in cameras_list:
        rotation = {'QUATERNION': 'rotation_quaternion', 'AXIS_ANGLE': 'rotation_axis_angle'}.get(
            camera.rotation_mode, 'rotation_euler')
        # Native transform components allow exact restoration after posing the camera.
        transform = {name: tuple(getattr(camera, name)) for name in ('location', rotation, 'scale')}
        rig.append((camera, camera.matrix_basis.copy(), transform))
    # All export layouts use the same three reference poses for the shared tilt bound.
    center = cameras_list[0]
    job['shake_side_poses'] = tuple(camera_rig.side_camera_matrix(center, entry, side) for side in ('Left', 'Right'))
    job['shake_seed'] = random.randrange(1 << 30) if seed is None else int(seed)


def _end_shake(job):
    from . import manual_down_jobs as jobs
    rig = job.get('rig', [])
    for record in tuple(rig):
        camera, _base, transform = record
        try:
            for name, value in transform.items():
                jobs._check_owner(job)
                setattr(camera, name, value)
        except ReferenceError:
            pass
        rig.remove(record)
    job.pop('rig', None)


# Live viewport shake: the Down cameras of running turbines on the manual-control clock.
# The rest pose is kept on the camera; a pose written by anything else (reset,
# side-camera recompute, view navigation) becomes the new rest pose.
_SHAKE_BASE = 'wfrl_shake_base'
_SHAKE_POSE = 'wfrl_shake_pose'
_LIVE_SHAKE = {}


def _flat(matrix):
    return [value for row in matrix for value in row]


def rest_matrix(camera):
    """Camera pose without the live shake."""
    base, pose = camera.get(_SHAKE_BASE), camera.get(_SHAKE_POSE)
    if base is not None and pose is not None and all(
            abs(a - b) < 1e-5 for a, b in zip(_flat(camera.matrix_basis), pose)):
        return manual_dynamics._matrix(base)
    return camera.matrix_basis.copy()


def _unshake(camera):
    if _SHAKE_BASE not in camera:
        return
    camera.matrix_basis = rest_matrix(camera)
    for key in (_SHAKE_BASE, _SHAKE_POSE):
        if key in camera:
            del camera[key]


def _scene_cameras(scene):
    result = {}
    for camera in camera_rig._down_cameras(scene):
        result.setdefault(camera['wfrl_turbine'], []).append(camera)
    return result


def live_shake_update(scene, now):
    """Shake the Down cameras of running turbines; True while any camera is shaken."""
    if not scene.wfrl_manual_camera_shake:
        live_shake_stop(scene)
        return False
    key = scene.as_pointer()
    state = _LIVE_SHAKE.get(key)
    if state is None:
        state = _LIVE_SHAKE[key] = dict(seed=random.randrange(1 << 30), start=now, last=now, amp={}, rpm={})
    alpha = 1.0 - math.exp(-max(0.0, now - state['last']) / manual_dynamics.LOAD_TAU_S)
    state['last'] = now
    groups = _scene_cameras(scene)
    active = False
    for entry in scene.wfrl_manual_turbines:
        tid = entry.turbine_id
        target = shake_amplitude(entry.rpm if entry.running else 0.0)
        amp = state['amp'].get(tid, 0.0)
        amp += (target - amp) * alpha
        if target == 0.0 and amp < 1e-3:
            amp = 0.0
        state['amp'][tid] = amp
        if entry.running and entry.rpm > 0:
            state['rpm'][tid] = entry.rpm
        cams = groups.get(tid, [])
        if amp <= 0.0:
            for camera in cams:
                if _SHAKE_BASE in camera:
                    _unshake(camera)
                    active = True
            continue
        active = True
        if not cams:
            continue
        shape = _shake_shape(state['rpm'].get(tid, SHAKE_RPM_REF), now - state['start'], hash((state['seed'], tid)))
        tilt, pan = amp * shape[0], amp * shape[1]
        rests = []
        for camera in cams:
            rest = rest_matrix(camera)
            camera[_SHAKE_BASE] = _flat(rest)
            camera.matrix_basis = rest
            rests.append((camera, rest))
        tilt = max([tilt, *(min_shake_tilt(camera, entry) for camera in cams)])
        turn = Matrix.Rotation(math.radians(tilt), 4, 'X') @ Matrix.Rotation(math.radians(pan), 4, 'Y')
        for camera, rest in rests:
            camera.matrix_basis = rest @ turn
            camera[_SHAKE_POSE] = _flat(camera.matrix_basis)
    return active


def live_shake_stop(scene):
    """Return the scene's Down cameras to their rest poses and forget the live amplitudes."""
    _LIVE_SHAKE.pop(scene.as_pointer(), None)
    try:
        for camera in camera_rig._down_cameras(scene):
            _unshake(camera)
    except (ReferenceError, AttributeError):
        pass


@persistent
def _shake_before_save(_unused):
    """Saved files keep the camera rest poses; the shake resumes on the next update."""
    for scene in bpy.data.scenes:
        if scene.as_pointer() in _LIVE_SHAKE:
            for camera in camera_rig._down_cameras(scene):
                _unshake(camera)


@persistent
def _shake_after_restore(_unused):
    _LIVE_SHAKE.clear()
    for scene in bpy.data.scenes:
        if manual_control.is_manual(scene):
            for camera in camera_rig._down_cameras(scene):
                _unshake(camera)


def _shake_toggle(scene, context):
    if not scene.wfrl_manual_camera_shake:
        live_shake_stop(scene)
    manual_control._wake_timer()


_SHAKE_HANDLERS = (('save_pre', _shake_before_save), ('load_post', _shake_after_restore),
                   ('undo_post', _shake_after_restore), ('redo_post', _shake_after_restore))


def _shake_sidecar(job, sidecar):
    sidecar['camera_shake'] = dict(
        seed=job['shake_seed'], max_deg=SHAKE_MAX_DEG, rpm_ref=SHAKE_RPM_REF,
        tip_margin_deg=SHAKE_TIP_MARGIN_DEG,
        convention='tilt about camera local X, then pan about camera local Y, applied to every '
                   'camera of the rig; tilt clamped so blade tips stay below the image midline')


def _plan(scene, fps, frames, from_start):
    """Per-turbine start state, sampled per frame by pose_at()."""
    rate = max(0.0, float(scene.wfrl_manual_pitch_rate_deg_s))
    plan = []
    for entry in scene.wfrl_manual_turbines:
        source = 'pitch_start' if from_start else 'pitch_actual'
        plan.append(dict(
            turbine=entry.turbine_id, azimuth_deg=float(entry.azimuth_deg), rpm=float(entry.rpm),
            pitch_start_deg=[float(getattr(entry, f'{source}_b{i}_deg')) for i in range(1, 4)],
            pitch_target_deg=[float(manual_control.target_pitch(entry, i)) for i in range(1, 4)],
            pitch_rate_deg_s=rate))
    return dict(fps=fps, frames=frames, turbines=plan)


def pose_at(item, time_s):
    azimuth = (item['azimuth_deg'] + item['rpm'] * 6.0 * time_s) % 360.0
    pitches = [pitch_at(start, target, item['pitch_rate_deg_s'], time_s)
               for start, target in zip(item['pitch_start_deg'], item['pitch_target_deg'])]
    return azimuth, pitches


def apply_frame(scene, plan, time_s, *, labels=True):
    """Pose every turbine at ``time_s`` of ``plan`` and refresh the blade-number box."""
    poses = {}
    for item in plan['turbines']:
        turbine = item['turbine']
        entry = camera_rig._entry(scene, turbine)
        azimuth, pitches = pose_at(item, time_s)
        entry.rotor_object.rotation_mode = 'XYZ'
        entry.rotor_object.rotation_euler.x = math.radians(azimuth)
        manual_control.apply_pitch(entry, pitches)
        poses[turbine] = item['rpm'], azimuth, pitches
    camera_rig.update_labels(scene, visible=labels)
    return poses


def apply_motion(scene, job, frame):
    """Apply the export pose and return the corresponding sidecar sample."""
    plan = job['plan']
    time_s = frame / plan['fps']
    poses = apply_frame(scene, plan, time_s, labels=job['labels'])
    structure = manual_dynamics.apply(scene, job, frame, poses)
    shake = apply_shake(scene, job, frame, poses[job['turbine']][0])
    sample = dict(frame=frame, time_s=round(time_s, 6))
    for turbine, (_, azimuth, pitches) in poses.items():
        sample[turbine] = dict(azimuth_deg=round(azimuth, 6), pitch_deg=[round(p, 6) for p in pitches])
    sample['camera_shake_deg'] = shake
    if structure is not None:
        sample['structural_motion'] = structure
    return sample


def _start_motion(scene, job, turbine, cameras_list, dynamics, seed=None):
    """``seed`` fixes the camera shake and structural motion phases; None draws new ones."""
    # The live bend would freeze while exporting; exports start from the rigid pose.
    manual_dynamics.live_detach(scene)
    live_shake_stop(scene)
    _start_shake(job, camera_rig._entry(scene, turbine), cameras_list, seed)
    if dynamics:
        manual_dynamics.start(scene, job, turbine, seed)


def _end_motion(job):
    from . import manual_down_jobs as jobs
    _end_shake(job)
    jobs._check_owner(job)
    manual_dynamics.end(job)


def stop_live_shakes():
    """Restore live camera poses and release the live-shake registry."""
    for scene in getattr(bpy.data, 'scenes', ()):
        if scene.as_pointer() in _LIVE_SHAKE:
            live_shake_stop(scene)
    _LIVE_SHAKE.clear()
