"""Down cameras, blade labels, blade shape and Down MP4 export for the manual turbine scene.

The export renders a fixed-rate rotor and rate-limited blade pitch directly from
frame numbers, so every frame is reproducible and independent of the viewport
timer. Turbine motion is rigid; with structural motion enabled, manual_dynamics
adds an illustrative blade bend and tower lean to each exported frame.
"""
import json
import math
import os
from pathlib import Path
import queue
import random
import shutil
import subprocess
import tempfile
import threading

import bpy
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix, Vector
import numpy as np

from . import cameras, manual_control, manual_dynamics, video_output
from .preferences import get_preferences
from .manual_motion import pitch_at
from .turbine_geometry import DEFAULT_MODEL, MODELS, blade_mesh

DOWN_CAMERA_REVISION = cameras.DOWN_MOUNT_REVISION
# Manual-scene Down preset: same mount, lens, yaw and roll as the recorded-scene
# preset; the pitch depends on the blade shape. With rootkink the optical axis
# is raised 4.5 deg toward the tower: in 1920x1080 the tower base sits near
# (930, 303) px (0.28 of the height), the tip of the lowest blade near 573 px,
# below the midline, and the trailing-edge root kink is visible as soon as the
# blade has fully entered the image. A tower base at 0.2 of the height with the
# tip below the midline would need about 20 deg between them; this lens and
# mount give about 16 deg. The NREL blade has no upwind cant and passes about
# 5 deg closer to the tower in the image, so it keeps the recorded-scene pitch
# to stay below the midline.
MANUAL_DOWN_PITCH_DEG = {'rootkink': -78.0, 'nrel5mw': cameras.DOWN_PITCH_DEG}
MANUAL_DOWN_REVISION = 1


def manual_down_pitch(scene):
    """Preset Down pitch for the scene's blade shape."""
    return MANUAL_DOWN_PITCH_DEG.get(scene.wfrl_manual_blade_model, cameras.DOWN_PITCH_DEG)
VIDEO_SIZE = (1920, 1080)
VIDEO_SIZES = {'1080': (1920, 1080), '720': (1280, 720), '540': (960, 540)}
_TURBINES = (('T1', 'T1', 'T1 风机'), ('T2', 'T2', 'T2 风机'), ('T3', 'T3', 'T3 风机'))

# Three-camera rig: two side cameras flanking the main Down camera. Each side
# camera has the Down lens and is the Down pose turned by +/-TRIPLE_STEP_DEG
# about the rotor shaft, so the blade posture in the side views is identical to
# the center view; only the background (tilted ground, horizon, no tower)
# differs. A common optical centre at the Down mount cannot give that view: the
# blade passes about 1.5 m from the lens and fills any frame aimed sideways.
#
# The user specifies each camera as 59 deg with 12 deg pairwise overlap for a
# combined 153 deg (3*59 - 2*12). The Down camera's quoted "59 deg" is its
# vertical FOV (DOWN_VFOV_DEG = 60 deg); the horizontal FOV is wider
# (~91.5 deg at 16:9) because the 8 mm lens spans 60 deg across the image
# height. TRIPLE_STEP_DEG is set so that, under the user's 59 deg model,
# adjacent cameras overlap by 12 deg and the rig covers 153 deg. With the
# actual 91.5 deg HFOV the real overlap is larger; see 风机手动控制.md for the
# FOV note.
TRIPLE_CAMERA_FOV_DEG = 59.0
TRIPLE_OVERLAP_DEG = 12.0
TRIPLE_STEP_DEG = TRIPLE_CAMERA_FOV_DEG - TRIPLE_OVERLAP_DEG
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
VIEWPORT_ENGINES = {'VIEWPORT_MATERIAL': 'MATERIAL', 'VIEWPORT_SOLID': 'SOLID'}
ENGINES = {'SCENE', 'WORKBENCH', *VIEWPORT_ENGINES}
_EXPORT = {}

# Labels are camera-parented text placed on the ray through a blade station,
# LABEL_DEPTH metres in front of the optical centre: closer than any blade
# (about 0.83 m at the nearest) and beyond the 0.05 m near clip.
LABEL_DEPTH = .2
LABEL_SPAN = .5
BLADE_COLORS = ((1.0, .82, .05), (.15, .9, 1.0), (1.0, .3, .85))
_STATIONS = {}


def _entry(scene, turbine):
    entry = next((item for item in scene.wfrl_manual_turbines if item.turbine_id == turbine), None)
    if entry is None:
        raise ValueError(f'手动场景中没有 {turbine}')
    return entry


def _camera_name(scene, turbine):
    return f"{scene['wfrl_manual_owner']}.Turbine.{turbine}.DownCamera"


def ensure_down_camera(scene, turbine, *, reset=False):
    """Create or restore the turbine's fixed Down camera under its YawRoot."""
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    root = _entry(scene, turbine).yaw_object
    name = _camera_name(scene, turbine)
    camera = scene.objects.get(name)
    if camera is None:
        camera = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
        root.users_collection[0].objects.link(camera)
        camera['wfrl_manual_owner'] = scene['wfrl_manual_owner']
    camera['wfrl_down_camera'] = True
    camera['wfrl_turbine'] = turbine
    camera['wfrl_down_pitch_deg'] = manual_down_pitch(scene)
    if (reset or camera.get('wfrl_gimbal_mount_revision', 0) < DOWN_CAMERA_REVISION
            or camera.get('wfrl_manual_down_revision', 0) < MANUAL_DOWN_REVISION):
        camera.parent = root
        camera.matrix_parent_inverse.identity()
        camera.data.clip_start, camera.data.clip_end = .05, 30000
        camera.data.display_size = .5
        cameras.down_gimbal(camera)
        camera['wfrl_gimbal_mount_revision'] = DOWN_CAMERA_REVISION
        camera['wfrl_manual_down_revision'] = MANUAL_DOWN_REVISION
    camera.data.show_passepartout = True
    camera.data.passepartout_alpha = 1
    _ensure_labels(scene, camera)
    update_labels(scene)
    return camera


def _side_camera_name(scene, turbine, side):
    """Name for a side camera: side is 'Left' or 'Right'."""
    return f"{scene['wfrl_manual_owner']}.Turbine.{turbine}.DownCamera.{side}"


def _shaft_frame(entry):
    """Hub point and unit shaft axis in YawRoot-local coordinates."""
    local = _world(entry.yaw_object).inverted() @ _world(entry.rotor_object)
    return local.translation.copy(), local.to_3x3().col[0].normalized()


def side_camera_matrix(center, entry, side):
    """YawRoot-local pose of a side camera: the center Down pose turned about the shaft.

    The rotor is rotationally symmetric about the shaft, so a blade at azimuth
    a ± TRIPLE_STEP_DEG looks in the side camera exactly as a blade at a looks
    in the center camera; only the ground, horizon and tower move."""
    hub, axis = _shaft_frame(entry)
    rest = rest_matrix(center)
    right = rest.to_3x3().col[0]
    forward = -rest.to_3x3().col[2]
    pose = {}
    for sign in (-1, 1):
        turn = (Matrix.Translation(hub) @ Matrix.Rotation(math.radians(sign * TRIPLE_STEP_DEG), 4, axis)
                @ Matrix.Translation(-hub))
        turned = turn.to_3x3() @ forward
        pose['Left' if turned.dot(right) < 0 else 'Right'] = turn @ rest
    return pose[side]


def ensure_side_cameras(scene, turbine):
    """Create or restore the Left and Right cameras of the turbine's three-camera rig.

    Each side camera copies the Down camera's lens and is the Down pose turned
    by ±TRIPLE_STEP_DEG about the rotor shaft, so all three see the same blade
    silhouette sweeping across the lower half of the image. The side views show
    tilted ground and the distant horizon instead of the tower. Poses are
    recomputed on every call and follow the nacelle yaw through the YawRoot."""
    center = ensure_down_camera(scene, turbine)
    entry = _entry(scene, turbine)
    root = entry.yaw_object
    result = {}
    for side in ('Left', 'Right'):
        name = _side_camera_name(scene, turbine, side)
        cam = scene.objects.get(name)
        if cam is None:
            cam = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
            root.users_collection[0].objects.link(cam)
            cam['wfrl_manual_owner'] = scene['wfrl_manual_owner']
        cam['wfrl_down_camera'] = True
        cam['wfrl_turbine'] = turbine
        cam['wfrl_triple_side'] = side
        cam['mount'] = f'Down pose turned {TRIPLE_STEP_DEG:g} deg about the rotor shaft / {side}'
        cam.parent = root
        cam.matrix_parent_inverse.identity()
        data, source = cam.data, center.data
        data.type, data.sensor_fit = source.type, source.sensor_fit
        data.sensor_width, data.lens = source.sensor_width, source.lens
        data.shift_x, data.shift_y = source.shift_x, source.shift_y
        data.clip_start, data.clip_end = source.clip_start, source.clip_end
        data.display_size = source.display_size
        data.show_passepartout = True
        data.passepartout_alpha = 1
        cam.matrix_basis = side_camera_matrix(center, entry, side)
        if cam.get('wfrl_label_aspect') is None:
            _ensure_labels(scene, cam)
        result[side] = cam
    update_labels(scene)
    return result


def _down_cameras(scene):
    if not manual_control.is_manual(scene):
        return []
    return [obj for obj in scene.objects if obj.type == 'CAMERA' and obj.get('wfrl_down_camera')
            and obj.get('wfrl_turbine') and obj.get('wfrl_manual_owner') == scene.get('wfrl_manual_owner')]


def _label_material(scene, key, color):
    name = f"{scene['wfrl_manual_owner']}.Label.{key}"
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        nodes.clear()
        emission = nodes.new('ShaderNodeEmission')
        emission.inputs['Strength'].default_value = 1.0
        output = nodes.new('ShaderNodeOutputMaterial')
        material.node_tree.links.new(emission.outputs['Emission'], output.inputs['Surface'])
    material.diffuse_color = (*color, 1.0)
    material.node_tree.nodes['Emission'].inputs['Color'].default_value = (*color, 1.0)
    material['provenance'] = 'Blade-number annotation overlay; not part of the turbine'
    return material


def _overlay_object(scene, camera, suffix, data):
    name = f'{camera.name}.{suffix}'
    obj = scene.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, data)
        camera.users_collection[0].objects.link(obj)
    obj.parent = camera
    obj.matrix_parent_inverse.identity()
    obj.rotation_euler = (0, 0, 0)
    obj.visible_shadow = False
    obj.hide_select = True
    obj['wfrl_manual_owner'] = scene['wfrl_manual_owner']
    obj['wfrl_down_label'] = True
    return obj


def _text(scene, camera, suffix, body, size, align_x, align_y, material):
    name = f'{camera.name}.{suffix}'
    curve = bpy.data.curves.get(name)
    if curve is None:
        curve = bpy.data.curves.new(name, 'FONT')
    curve.body, curve.size = body, size
    curve.align_x, curve.align_y = align_x, align_y
    curve.materials.clear()
    curve.materials.append(material)
    return _overlay_object(scene, camera, suffix, curve)


def _plate(scene, camera, suffix, vertices, material, parent):
    name = f'{camera.name}.{suffix}'
    mesh = bpy.data.meshes.get(name)
    if mesh is None:
        mesh = bpy.data.meshes.new(name)
    mesh.clear_geometry()
    mesh.from_pydata(vertices, [], [tuple(range(len(vertices)))])
    mesh.materials.clear()
    mesh.materials.append(material)
    obj = _overlay_object(scene, camera, suffix, mesh)
    obj.parent = parent
    return obj


def _frame_tangents(camera, scene):
    tan_w = camera.data.sensor_width / 2 / camera.data.lens
    render = scene.render
    return tan_w, tan_w * render.resolution_y / render.resolution_x


def _ensure_labels(scene, camera):
    """Top-right box showing the number of the blade currently in view."""
    tan_w, tan_h = _frame_tangents(camera, scene)
    height = 2 * LABEL_DEPTH * tan_h
    for suffix in ('Legend.Title', *(f'Label.B{i}{part}' for i in range(1, 4) for part in ('', '.Back'))):
        stale = scene.objects.get(f'{camera.name}.{suffix}')
        if stale is not None:
            bpy.data.objects.remove(stale)
    dark = _label_material(scene, 'Back', (.02, .02, .025))
    line = .09 * height
    margin = .03 * height
    right, top = LABEL_DEPTH * tan_w - margin, LABEL_DEPTH * tan_h - margin
    pad = .3 * line
    width, depth = 2.2 * line, 1.0 * line
    rows = []
    for index, color in enumerate(BLADE_COLORS, 1):
        row = _text(scene, camera, f'Legend.B{index}', f'B{index}', line, 'CENTER', 'CENTER',
                    _label_material(scene, f'B{index}', color))
        row.location = (right - pad - width / 2, top - pad - depth / 2, -LABEL_DEPTH)
        rows.append(row)
    back = _plate(scene, camera, 'Legend.Back',
                  [(right, top, -LABEL_DEPTH * 1.004), (right - width - 2 * pad, top, -LABEL_DEPTH * 1.004),
                   (right - width - 2 * pad, top - depth - 2 * pad, -LABEL_DEPTH * 1.004),
                   (right, top - depth - 2 * pad, -LABEL_DEPTH * 1.004)], dark, camera)
    back.location = (0, 0, 0)
    camera['wfrl_label_aspect'] = scene.render.resolution_y / scene.render.resolution_x


def _stations(mesh):
    """Blade-local centroids of span stations from 15 % to 95 % of the blade length,
    nearest to ``LABEL_SPAN`` first."""
    key = (mesh.name, mesh.get('wfrl_blade_model', DEFAULT_MODEL))
    if key not in _STATIONS:
        co = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        low, high = float(co[:, 2].min()), float(co[:, 2].max())
        rows = []
        for fraction in np.linspace(.15, .95, 17):
            ring = co[np.abs(co[:, 2] - (low + (high - low) * fraction)) < (high - low) / 60]
            if len(ring):
                rows.append((abs(fraction - LABEL_SPAN), Vector(ring.mean(0).tolist())))
        _STATIONS[key] = [point for _distance, point in sorted(rows, key=lambda row: row[0])]
    return _STATIONS[key]


def _world(obj):
    """World matrix from current transform values, valid before the depsgraph updates."""
    matrix = obj.matrix_basis.copy()
    while obj.parent is not None:
        matrix = obj.matrix_parent_inverse @ matrix
        obj = obj.parent
        matrix = obj.matrix_basis @ matrix
    return matrix


def _set_hidden(obj, hidden):
    if obj.hide_viewport != hidden:
        obj.hide_viewport = hidden
    if obj.hide_render != hidden:
        obj.hide_render = hidden


def current_blade(camera, entry):
    """Number of the blade whose mid-span lies closest to the camera's line of sight.

    Each blade owns the rotor sector around its passage through the view, so
    the number stays defined while no blade is inside the frame. The three
    cameras in the rig compute their labels independently: the same blade can
    appear in two cameras simultaneously when it passes through the overlap
    zone near the bottom (physically correct — the blade is genuinely nearest
    to both side cameras at that moment)."""
    inverse = _world(camera).inverted()
    best = None
    for index in range(1, 4):
        blade = getattr(entry, f'blade{index}_object')
        local = inverse @ _world(blade) @ _stations(blade.data)[0]
        angle = local.angle(Vector((0.0, 0.0, -1.0)), math.pi)
        if best is None or angle < best[0]:
            best = (angle, index)
    return best[1]


_LABEL_PARTS = frozenset(('Legend.Back', 'Legend.B1', 'Legend.B2', 'Legend.B3'))


def update_labels(scene, *, visible=None):
    """Show the number of the blade currently in each Down view in its top-right box.

    Visibility is written only when it changes, since every change rebuilds
    depsgraph relations."""
    if not manual_control.is_manual(scene):
        return
    shown = scene.wfrl_manual_blade_labels if visible is None else visible
    for camera in _down_cameras(scene):
        objects = {obj.name[len(camera.name) + 1:]: obj for obj in camera.children_recursive
                   if obj.get('wfrl_down_label')}
        if not objects:
            continue
        # A label object deleted by hand is rebuilt rather than failing every later update.
        if not _LABEL_PARTS <= objects.keys() or (shown and ('Legend.Title' in objects or abs(
                camera.get('wfrl_label_aspect', 0) - scene.render.resolution_y / scene.render.resolution_x) > 1e-6)):
            _ensure_labels(scene, camera)
            objects = {obj.name[len(camera.name) + 1:]: obj for obj in camera.children_recursive
                       if obj.get('wfrl_down_label')}
        try:
            current = current_blade(camera, _entry(scene, camera['wfrl_turbine'])) if shown else None
        except ValueError:
            current = None
        _set_hidden(objects['Legend.Back'], not shown)
        for index in range(1, 4):
            _set_hidden(objects[f'Legend.B{index}'], current != index)


def _labels_listener(scene):
    if scene.get('wfrl_manual_exporting'):
        return
    update_labels(scene)


def _labels_update(scene, context):
    update_labels(scene)


def apply_blade_model(scene):
    """Rewrite the shared manual blade mesh in place; topology is identical across models."""
    if not manual_control.is_manual(scene) or not scene.wfrl_manual_turbines:
        return
    from .materials import ensure_blade_tip_markings
    model = scene.wfrl_manual_blade_model
    manual_dynamics.live_detach(scene)  # the live bend shows a copy of the shared mesh
    blade = scene.wfrl_manual_turbines[0].blade1_object
    mesh = blade.data
    vertices, _faces = blade_mesh(subdiv=8, ring_points=96, model=model)
    if len(vertices) != len(mesh.vertices):
        raise ValueError('叶片网格拓扑不一致，无法切换外形')
    mesh.vertices.foreach_set('co', [value for vertex in vertices for value in vertex])
    mesh.update()
    for face in mesh.polygons:
        face.material_index = 0
    mesh['wfrl_tip_marking_revision'] = 0
    prefix = scene['wfrl_manual_owner'] + '.Turbine.'
    ensure_blade_tip_markings([blade], object_prefix=prefix, material_name=mesh.name + '.TipRed')
    mesh['wfrl_blade_model'] = model
    _reaim_down_cameras(scene)
    update_labels(scene)


def _reaim_down_cameras(scene):
    """Move existing Down cameras to the preset pitch of the current blade shape."""
    pitch = manual_down_pitch(scene)
    for camera in _down_cameras(scene):
        if camera.get('wfrl_triple_side') or camera.get('wfrl_down_pitch_deg') == pitch:
            continue
        _unshake(camera)
        camera['wfrl_down_pitch_deg'] = pitch
        cameras.down_gimbal(camera)
        sides = [c for c in _down_cameras(scene)
                 if c.get('wfrl_triple_side') and c['wfrl_turbine'] == camera['wfrl_turbine']]
        if sides:
            ensure_side_cameras(scene, camera['wfrl_turbine'])


def _apply_pending_blade_model(scene):
    """Apply a blade shape chosen while an export was rendering."""
    try:
        mesh = scene.wfrl_manual_turbines[0].blade1_object.data
        if mesh.get('wfrl_blade_model', DEFAULT_MODEL) != scene.wfrl_manual_blade_model:
            apply_blade_model(scene)
    except (IndexError, ValueError, ReferenceError, AttributeError) as exc:
        print('WFRL manual blade model:', exc)


def _blade_model_update(scene, context):
    if scene.as_pointer() in _EXPORT:
        # The running export keeps the shape it started with; the switch applies when it ends.
        return
    try:
        apply_blade_model(scene)
    except (ValueError, ReferenceError, AttributeError) as exc:
        print('WFRL manual blade model:', exc)


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


def min_shake_tilt(camera, entry):
    """Smallest tilt (deg) that keeps every tip near the frame below the image midline.

    Tilting a camera about its local X by t shifts the vertical view angle
    psi = atan2(y, -z) of every point by exactly -t, and a pan about local Y
    leaves the sign of y unchanged, so the bound is max(psi_tip) + margin."""
    inverse = _world(camera).inverted()
    half = math.tan(camera.data.angle_x / 2) * 1.15
    bound = -math.inf
    for index in range(1, 4):
        blade = getattr(entry, f'blade{index}_object')
        local = inverse @ _world(blade) @ (_tip_local(blade) + manual_dynamics.tip_offset(blade))
        if local.z >= 0 or abs(local.x / -local.z) > half:
            continue
        bound = max(bound, math.degrees(math.atan2(local.y, -local.z)) + SHAKE_TIP_MARGIN_DEG)
    return bound


def apply_shake(scene, job, frame):
    """Pose the export cameras for ``frame``: base pose turned by the clamped shake."""
    rig = job.get('rig')
    if not rig:
        return
    entry = _entry(scene, job['turbine'])
    tilt, pan = shake_signal(entry.rpm, frame / job['plan']['fps'], job['shake_seed'])
    for camera, base in rig:
        camera.matrix_basis = base
    tilt = max([tilt, *(min_shake_tilt(camera, entry) for camera, _base in rig)])
    turn = Matrix.Rotation(math.radians(tilt), 4, 'X') @ Matrix.Rotation(math.radians(pan), 4, 'Y')
    for camera, base in rig:
        camera.matrix_basis = base @ turn
    job['shake'][frame] = (round(tilt, 6), round(pan, 6))


def _start_shake(job, turbine, cameras_list, seed=None):
    job['turbine'] = turbine
    job['rig'] = [(camera, camera.matrix_basis.copy()) for camera in cameras_list]
    job['shake_seed'] = random.randrange(1 << 30) if seed is None else int(seed)
    job['shake'] = {}


def _end_shake(job):
    for camera, base in job.pop('rig', None) or ():
        try:
            camera.matrix_basis = base
        except ReferenceError:
            pass


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
    for camera in _down_cameras(scene):
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
        for camera in _down_cameras(scene):
            _unshake(camera)
    except (ReferenceError, AttributeError):
        pass


@persistent
def _shake_before_save(_unused):
    """Saved files keep the camera rest poses; the shake resumes on the next update."""
    for scene in bpy.data.scenes:
        if scene.as_pointer() in _LIVE_SHAKE:
            for camera in _down_cameras(scene):
                _unshake(camera)


@persistent
def _shake_after_restore(_unused):
    _LIVE_SHAKE.clear()
    for scene in bpy.data.scenes:
        if manual_control.is_manual(scene):
            for camera in _down_cameras(scene):
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
    for row in sidecar['samples']:
        row['camera_shake_deg'] = list(job['shake'].get(row['frame'], (0.0, 0.0)))


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
    for item in plan['turbines']:
        entry = _entry(scene, item['turbine'])
        azimuth, pitches = pose_at(item, time_s)
        entry.rotor_object.rotation_mode = 'XYZ'
        entry.rotor_object.rotation_euler.x = math.radians(azimuth)
        manual_control.apply_pitch(entry, pitches)
    update_labels(scene, visible=labels)


def apply_motion(scene, job, frame):
    """Rotor and pitch, optional structural motion, then camera shake for ``frame``."""
    plan = job['plan']
    time_s = frame / plan['fps']
    apply_frame(scene, plan, time_s, labels=job['labels'])
    if job.get('dynamics_rig'):
        poses = {item['turbine']: (item['rpm'], *pose_at(item, time_s)) for item in plan['turbines']}
        manual_dynamics.apply(scene, job, frame, poses)
    apply_shake(scene, job, frame)


# Mesh deformation changes the blade geometry every frame. Blender releases the
# superseded GPU buffers only when a window draws, which a blocking offscreen
# loop never does, so the loop redraws the window every _GPU_RELEASE_FRAMES.
_GPU_RELEASE_FRAMES = 15


def _release_gpu(job, frame):
    if job.get('dynamics_rig') and frame % _GPU_RELEASE_FRAMES == _GPU_RELEASE_FRAMES - 1:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def _start_motion(scene, job, turbine, cameras_list, dynamics, seed=None):
    """``seed`` fixes the camera shake and structural motion phases; None draws new ones."""
    # The live bend would freeze while exporting; exports start from the rigid pose.
    manual_dynamics.live_detach(scene)
    live_shake_stop(scene)
    _start_shake(job, turbine, cameras_list, seed)
    if dynamics:
        manual_dynamics.start(scene, job, turbine, seed)


def _end_motion(job):
    _end_shake(job)
    manual_dynamics.end(job)


def _frame_handler(scene, _depsgraph=None):
    job = _EXPORT.get(scene.as_pointer())
    if job is None:
        return
    frame = scene.frame_current - scene.frame_start
    apply_motion(scene, job, frame)
    if job.get('progress'):
        bpy.context.window_manager.progress_update(frame)


def gpu_renderer():
    try:
        import gpu
        return gpu.platform.renderer_get()
    except Exception:  # The gpu module is unavailable in background mode.
        return 'unknown'


def is_integrated_gpu(renderer):
    name = renderer.lower()
    return any(key in name for key in ('intel', 'uhd graphics', 'iris', 'radeon(tm) graphics', 'llvmpipe'))


def _sidecar(plan, turbine, camera, scene, video_name, *, render_mode=None, labels=False, dynamics=False):
    rows = []
    for frame in range(plan['frames']):
        time_s = frame / plan['fps']
        row = dict(frame=frame, time_s=round(time_s, 6))
        for item in plan['turbines']:
            azimuth, pitches = pose_at(item, time_s)
            row[item['turbine']] = dict(azimuth_deg=round(azimuth, 6), pitch_deg=[round(p, 6) for p in pitches])
        rows.append(row)
    data = camera.data
    render = scene.render
    return dict(
        video=video_name, camera=f'{turbine} Down', resolution=[render.resolution_x, render.resolution_y],
        fps=plan['fps'], frames=plan['frames'], duration_s=plan['frames'] / plan['fps'],
        render_mode=render_mode or render.engine, render_engine=render.engine, gpu=gpu_renderer(),
        encoder='blender ffmpeg',
        blade_model=scene.wfrl_manual_blade_model, blade_labels=labels,
        optics=dict(lens_mm=data.lens, sensor_width_mm=data.sensor_width,
                    vertical_fov_deg=cameras.DOWN_VFOV_DEG, horizontal_fov_deg=math.degrees(data.angle_x),
                    yaw_deg=camera['gimbal_yaw'], pitch_deg=camera['gimbal_pitch'], roll_deg=camera['gimbal_roll'],
                    lens_distortion='none'),
        azimuth_convention='Rotor rotation about the shaft; blade 1 points up at 0 deg',
        pitch_convention='Blade rotation about its pitch axis, same sign as the 风机控制 panel',
        turbines=plan['turbines'], samples=rows,
        scope='Manual rigid scene: constant rotor speed and rate-limited pitch; no prebend, '
              'aerodynamics, motion blur or lens distortion. '
              + ('Blade flapwise bend and tower lean follow the illustrative structural_motion signal, '
                 'not a structural solution. ' if dynamics else 'No blade flexure or tower motion. ')
              + 'The camera shake imitates nacelle vibration and is not a structural model.'
              + (' The top-right blade number is an annotation overlay.' if labels else ''))


_SAVED_RENDER = ('engine', 'resolution_x', 'resolution_y', 'resolution_percentage', 'pixel_aspect_x',
                 'pixel_aspect_y', 'fps', 'fps_base', 'filepath', 'use_lock_interface', 'use_file_extension',
                 'dither_intensity', 'use_compositing', 'use_sequencer')
_SAVED_FFMPEG = ('format', 'codec', 'constant_rate_factor', 'ffmpeg_preset', 'gopsize', 'audio_codec')


def _snapshot(scene):
    render = scene.render
    return dict(
        camera=scene.camera, frame=(scene.frame_start, scene.frame_end, scene.frame_current),
        render={name: getattr(render, name) for name in _SAVED_RENDER},
        image=(render.image_settings.media_type, render.image_settings.file_format,
               render.image_settings.color_mode),
        ffmpeg={name: getattr(render.ffmpeg, name) for name in _SAVED_FFMPEG},
        view=(scene.view_settings.view_transform, scene.view_settings.look),
        eevee=(scene.eevee.taa_samples, scene.eevee.taa_render_samples), render_aa=scene.display.render_aa,
        markers=[(marker.name, marker.frame, marker.camera) for marker in scene.timeline_markers])


def _restore_marker_cameras(scene, saved):
    """Rebind marker cameras by marker name and frame, then by name for a moved marker.

    Markers may be added, removed or moved while an interactive render runs. Each saved binding
    goes back to at most one marker; a marker without a saved name keeps no camera."""
    pending = list(saved)
    markers = list(scene.timeline_markers)
    for exact in (True, False):
        for marker in tuple(markers):
            for index, (name, frame, camera) in enumerate(pending):
                if name == marker.name and (frame == marker.frame or not exact):
                    try:
                        marker.camera = camera
                    except ReferenceError:
                        pass  # The bound camera was deleted during the export.
                    del pending[index]
                    markers.remove(marker)
                    break


def _restore(scene, saved):
    render = scene.render
    image = render.image_settings
    image.media_type = saved['image'][0]
    image.file_format = saved['image'][1]
    image.color_mode = saved['image'][2]
    for name, value in saved['ffmpeg'].items():
        try:
            setattr(render.ffmpeg, name, value)
        except TypeError:
            pass  # Unset enums read back as '' and keep their defaults.
    for name, value in saved['render'].items():
        setattr(render, name, value)
    scene.view_settings.view_transform, scene.view_settings.look = saved['view']
    scene.eevee.taa_samples, scene.eevee.taa_render_samples = saved['eevee']
    scene.display.render_aa = saved['render_aa']
    _restore_marker_cameras(scene, saved['markers'])
    scene.camera = saved['camera']
    scene.frame_start, scene.frame_end = saved['frame'][:2]
    scene.frame_set(saved['frame'][2])


def _configure_video(scene, camera, fps, frames, engine, filepath, *, size=VIDEO_SIZE, samples=None):
    render = scene.render
    # Camera-bound timeline markers must not switch the render to another camera. Each
    # marker edit re-runs Blender's camera switch, so all bindings go before the camera is set.
    for marker in scene.timeline_markers:
        marker.camera = None
    scene.camera = camera
    # Every engine writes the camera image itself, never a sequencer or compositor result.
    render.use_compositing = render.use_sequencer = False
    if engine == 'WORKBENCH':
        render.engine = 'BLENDER_WORKBENCH'
    if engine == 'SCENE' and samples:
        scene.eevee.taa_render_samples = int(samples)
    if engine in VIEWPORT_ENGINES:
        # The viewport path is bounded by per-frame colour processing, so the
        # export uses the plain sRGB transform without dithering.
        scene.view_settings.view_transform, scene.view_settings.look = 'Standard', 'None'
        render.dither_intensity = 0
        scene.eevee.taa_samples = 1
        scene.display.render_aa = 'FXAA'
    render.resolution_x, render.resolution_y = size
    render.resolution_percentage = 100
    render.pixel_aspect_x = render.pixel_aspect_y = 1
    render.fps, render.fps_base = fps, 1.0
    scene.frame_start, scene.frame_end = 1, frames
    render.image_settings.media_type = 'VIDEO'
    render.image_settings.file_format = 'FFMPEG'
    render.image_settings.color_mode = 'RGB'
    render.ffmpeg.format = 'MPEG4'
    render.ffmpeg.codec = 'H264'
    render.ffmpeg.constant_rate_factor = 'HIGH'
    render.ffmpeg.ffmpeg_preset = 'REALTIME' if engine in VIEWPORT_ENGINES else 'GOOD'
    render.ffmpeg.gopsize = fps
    render.ffmpeg.audio_codec = 'NONE'
    render.use_file_extension = True
    render.use_lock_interface = True
    render.filepath = filepath


def _finish(scene, *, completed):
    """Move the rendered movie into place and restore the scene; returns the report."""
    job = _EXPORT.pop(scene.as_pointer(), None)
    if job is None:
        return None
    report = None
    try:
        if not _EXPORT:
            for handlers, function in ((bpy.app.handlers.frame_change_pre, _frame_handler),
                                       (bpy.app.handlers.render_complete, _render_complete),
                                       (bpy.app.handlers.render_cancel, _render_cancel)):
                if function in handlers:
                    handlers.remove(function)
        if job.get('progress'):
            bpy.context.window_manager.progress_end()
        try:
            _end_motion(job)
            _restore(scene, job['saved'])
            if job.get('view') is not None:
                _restore_view(*job['view'])
        finally:
            scene['wfrl_manual_exporting'] = False
            manual_control.hold_pitch_clock(scene)
            for entry in scene.wfrl_manual_turbines:
                manual_control._apply_entry(entry)
            _apply_pending_blade_model(scene)
            update_labels(scene)
        if completed:
            produced = sorted(Path(job['temp']).glob('*.mp4'))
            if not produced:
                raise RuntimeError('未生成 MP4 文件')
            target = Path(job['target'])
            _shake_sidecar(job, job['sidecar'])
            manual_dynamics.sidecar(job, job['sidecar'])
            video_output.publish_staged([(produced[0], target)], target.with_suffix('.json'), job['sidecar'],
                                        'Down 视频')
            report = str(target)
    finally:
        shutil.rmtree(job['temp'], ignore_errors=True)
        scene['wfrl_manual_last_video'] = report or ''
    return report


@bpy.app.handlers.persistent
def _render_complete(scene, _unused=None):
    for key in list(_EXPORT):
        job = _EXPORT.get(key)
        if job and job['scene'] == scene:
            try:
                _finish(scene, completed=True)
            except Exception as exc:
                # No operator is waiting on an interactive render; the panels show this error.
                scene['wfrl_manual_error'] = f'Down 视频导出失败：{exc}'
                print('WFRL Down video export failed:', exc)


@bpy.app.handlers.persistent
def _render_cancel(scene, _unused=None):
    for key in list(_EXPORT):
        job = _EXPORT.get(key)
        if job and job['scene'] == scene:
            _finish(scene, completed=False)


def ffmpeg_executable(context=None):
    """FFmpeg from the add-on video settings or PATH; None when unavailable."""
    prefs = get_preferences(context)
    configured = bpy.path.abspath(prefs.video_ffmpeg) if prefs and prefs.video_ffmpeg else ''
    try:
        return video_output.executable(configured, 'ffmpeg')
    except ValueError:
        return None


def _draw_offscreen(scene, job, view, camera, ffmpeg, output):
    """Draw each frame of the 3D view offscreen and pipe raw RGBA to FFmpeg.

    Skips Blender's render pipeline and movie writer; drawing, read-back and
    encoding overlap, which keeps 1080p near 0.02 s per frame on a discrete GPU."""
    import gpu
    window, area, region = view
    render, plan = scene.render, job['plan']
    width, height = render.resolution_x, render.resolution_y
    command = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{width}x{height}', '-r', str(plan['fps']), '-i', '-',
               '-vf', 'vflip', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
               '-g', str(plan['fps']), '-an', '-movflags', '+faststart', output]
    log = tempfile.TemporaryFile()
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log, **options)
    frames, failure = queue.Queue(maxsize=4), []

    def write():
        try:
            while (data := frames.get()) is not None:
                process.stdin.write(data)
        except OSError as exc:
            failure.append(exc)
            while frames.get() is not None:
                pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    offscreen = gpu.types.GPUOffScreen(width, height, format='RGBA8')
    try:
        with bpy.context.temp_override(window=window, area=area, region=region, scene=scene):
            space, layer = area.spaces.active, bpy.context.view_layer
            depsgraph = bpy.context.evaluated_depsgraph_get()
            projection = camera.calc_matrix_camera(depsgraph, x=width, y=height)
            for frame in range(plan['frames']):
                if failure:
                    break
                apply_motion(scene, job, frame)
                depsgraph.update()
                view_matrix = camera.evaluated_get(depsgraph).matrix_world.inverted()
                offscreen.draw_view3d(scene, layer, space, region, view_matrix, projection,
                                      do_color_management=True)
                pixels = offscreen.texture_color.read()
                pixels.dimensions = width * height * 4
                frames.put(np.asarray(pixels, dtype=np.uint8).tobytes())
                _release_gpu(job, frame)
                bpy.context.window_manager.progress_update(frame)
    finally:
        frames.put(None)
        writer.join()
        offscreen.free()
        code = process.wait()
        log.seek(0)
        message = log.read().decode('utf-8', errors='replace')[-600:]
        log.close()
    if failure or code:
        raise RuntimeError(f'FFmpeg 编码失败（{code}）：{message or failure}')


def _draw_offscreen_multi(scene, job, view, cameras_list, ffmpeg, outputs):
    """Draw each frame from multiple cameras and pipe raw RGBA to parallel FFmpeg processes.

    All cameras render in lock-step so the resulting MP4 files share identical
    frame timestamps.  The offscreen buffer is re-used across cameras each frame."""
    import gpu
    window, area, region = view
    render, plan = scene.render, job['plan']
    width, height = render.resolution_x, render.resolution_y
    fps = plan['fps']

    def _make_pipe(output):
        command = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{width}x{height}', '-r', str(fps), '-i', '-',
                   '-vf', 'vflip', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
                   '-pix_fmt', 'yuv420p', '-g', str(fps), '-an', '-movflags', '+faststart', output]
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        log = tempfile.TemporaryFile()
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log, **options)
        q = queue.Queue(maxsize=4)
        failure = []

        def write():
            try:
                while (data := q.get()) is not None:
                    proc.stdin.write(data)
            except OSError as exc:
                failure.append(exc)
                while q.get() is not None:
                    pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        thread = threading.Thread(target=write, daemon=True)
        thread.start()
        return proc, q, thread, log, failure

    pipes = [_make_pipe(out) for out in outputs]
    offscreen = gpu.types.GPUOffScreen(width, height, format='RGBA8')
    try:
        with bpy.context.temp_override(window=window, area=area, region=region, scene=scene):
            space, layer = area.spaces.active, bpy.context.view_layer
            depsgraph = bpy.context.evaluated_depsgraph_get()
            # Pre-compute projection matrices (identical for all cameras: same FOV and aspect).
            projections = [cam.calc_matrix_camera(depsgraph, x=width, y=height) for cam in cameras_list]
            for frame in range(plan['frames']):
                if any(p[4] for p in pipes):  # any failure list non-empty
                    break
                apply_motion(scene, job, frame)
                depsgraph.update()
                for cam_idx, cam in enumerate(cameras_list):
                    proc, q, thread, log, failure = pipes[cam_idx]
                    if failure:
                        continue
                    view_matrix = cam.evaluated_get(depsgraph).matrix_world.inverted()
                    offscreen.draw_view3d(scene, layer, space, region, view_matrix, projections[cam_idx],
                                         do_color_management=True)
                    pixels = offscreen.texture_color.read()
                    pixels.dimensions = width * height * 4
                    q.put(np.asarray(pixels, dtype=np.uint8).tobytes())
                _release_gpu(job, frame)
                bpy.context.window_manager.progress_update(frame)
    finally:
        for _proc, q, thread, _log, _failure in pipes:
            q.put(None)
        for _proc, _q, thread, _log, _failure in pipes:
            thread.join()
        offscreen.free()
        errors = []
        for i, (proc, _q, _thread, log, failure) in enumerate(pipes):
            code = proc.wait()
            log.seek(0)
            message = log.read().decode('utf-8', errors='replace')[-400:]
            log.close()
            if failure or code:
                errors.append(f'camera {i}: FFmpeg({code}) {message or failure}')
        if errors:
            raise RuntimeError('FFmpeg 编码失败：' + '; '.join(errors))


def export_triple_down_video(scene, turbine, filepaths, *, duration_s=10.0, fps=25, frames=None,
                             engine='VIEWPORT_MATERIAL', from_start=True, size=VIDEO_SIZE, samples=16,
                             labels=None, dynamics=True, seed=None, context=None):
    """Render the main Down camera plus Left and Right side cameras as three time-synchronised MP4s.

    ``filepaths`` is a sequence of three paths: (center, left, right).  The
    three cameras share the same per-frame pose, so the videos are frame-exact
    matches.  Only the VIEWPORT_MATERIAL and VIEWPORT_SOLID engines are
    supported (the single-pass offscreen path draws all three cameras per frame).
    Returns a tuple of the three written paths."""
    if engine not in VIEWPORT_ENGINES:
        raise ValueError('三路视频导出仅支持视口渲染方式（视口材质或视口实体）')
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    if scene.as_pointer() in _EXPORT:
        raise ValueError('该场景正在导出视频')
    if bpy.app.background:
        raise ValueError('视口渲染需要 Blender 窗口；后台请选择 EEVEE 或 Workbench')
    fps = int(fps)
    frames = int(frames) if frames else int(round(float(duration_s) * fps))
    if fps < 1 or frames < 1:
        raise ValueError('帧数与帧率须大于 0')
    labels_flag = scene.wfrl_manual_blade_labels if labels is None else bool(labels)
    targets = [Path(bpy.path.abspath(str(p))) for p in filepaths]
    targets = [t if t.suffix.lower() == '.mp4' else t.with_suffix('.mp4') for t in targets]
    view = _view_area(context or bpy.context)
    manual_control.pause_scene(scene)
    center_cam = ensure_down_camera(scene, turbine)
    side_cams = ensure_side_cameras(scene, turbine)
    cameras_list = [center_cam, side_cams['Left'], side_cams['Right']]
    plan = _plan(scene, fps, frames, from_start)
    saved = _snapshot(scene)
    temp = tempfile.mkdtemp(prefix='wfrl_triple_')
    job = dict(scene=scene, plan=plan, saved=saved, temp=temp,
               target=str(targets[0]), labels=labels_flag, view=None)
    _EXPORT[scene.as_pointer()] = job
    scene['wfrl_manual_exporting'] = True
    try:
        _configure_video(scene, center_cam, fps, frames, engine, os.path.join(temp, 'triple_'),
                         size=size, samples=samples)
        sidecar = _sidecar(plan, turbine, center_cam, scene, targets[0].name, render_mode=engine,
                           labels=labels_flag, dynamics=dynamics)
        sidecar['encoder'] = 'ffmpeg libx264 (offscreen)'
        sidecar['camera'] = f'{turbine} Down rig'
        sidecar['rig'] = dict(
            videos=dict(center=targets[0].name, left=targets[1].name, right=targets[2].name),
            step_deg=TRIPLE_STEP_DEG, overlap_deg=TRIPLE_OVERLAP_DEG,
            side_pose='center Down pose turned about the rotor shaft by the step',
            yawroot_matrix={name: [list(row) for row in cam.matrix_basis]
                            for name, cam in zip(('center', 'left', 'right'), cameras_list)})
        _start_motion(scene, job, turbine, cameras_list, dynamics, seed)
        ffmpeg = ffmpeg_executable(context)
        if not ffmpeg:
            raise ValueError('三路视频导出需要 FFmpeg；请在扩展偏好设置中配置 FFmpeg 路径')
        window, area, region = view
        job['view'] = _prepare_view(area, center_cam, VIEWPORT_ENGINES[engine])
        bpy.context.window_manager.progress_begin(0, frames)
        job['progress'] = True
        out_paths = [os.path.join(temp, f'triple_{i}.mp4') for i in range(3)]
        _draw_offscreen_multi(scene, job, view, cameras_list, ffmpeg, out_paths)
    except Exception:
        _finish(scene, completed=False)
        raise
    # Move files into place manually (bypass _finish which moves only one file).
    try:
        _end_motion(job)
        _restore(scene, saved)
        if job.get('view') is not None:
            _restore_view(*job['view'])
    finally:
        scene['wfrl_manual_exporting'] = False
        manual_control.hold_pitch_clock(scene)
        for entry in scene.wfrl_manual_turbines:
            manual_control._apply_entry(entry)
        _apply_pending_blade_model(scene)
        update_labels(scene)
    if job.get('progress'):
        bpy.context.window_manager.progress_end()
    _EXPORT.pop(scene.as_pointer(), None)
    try:
        _shake_sidecar(job, sidecar)
        manual_dynamics.sidecar(job, sidecar)
        video_output.publish_staged([(Path(src), target) for src, target in zip(out_paths, targets)],
                                    targets[0].with_suffix('.json'), sidecar, '三路视频')
    except RuntimeError:
        scene['wfrl_manual_last_video'] = ''
        raise
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    scene['wfrl_manual_last_video'] = str(targets[0])
    return tuple(str(target) for target in targets)


def _view_area(context):
    """The 3D view that renders a viewport export: the calling area, else the largest one."""
    window = getattr(context, 'window', None) or bpy.context.window_manager.windows[0]
    area = getattr(context, 'area', None)
    if area is None or area.type != 'VIEW_3D':
        areas = [item for item in window.screen.areas if item.type == 'VIEW_3D']
        if not areas:
            raise ValueError('视口渲染需要一个三维视图')
        area = max(areas, key=lambda item: item.width * item.height)
    region = next(item for item in area.regions if item.type == 'WINDOW')
    return window, area, region


def _prepare_view(area, camera, shading):
    space = area.spaces.active
    saved = (space, dict(type=space.shading.type, world=space.shading.use_scene_world,
                         lights=space.shading.use_scene_lights, overlays=space.overlay.show_overlays,
                         camera=space.camera, local=space.use_local_camera,
                         perspective=space.region_3d.view_perspective))
    space.use_local_camera = True
    space.camera = camera
    space.region_3d.view_perspective = 'CAMERA'
    space.shading.type = shading
    if shading == 'MATERIAL':
        space.shading.use_scene_world = space.shading.use_scene_lights = True
    space.overlay.show_overlays = False
    return saved


def _restore_view(space, saved):
    try:
        space.shading.type = saved['type']
        space.shading.use_scene_world = saved['world']
        space.shading.use_scene_lights = saved['lights']
        space.overlay.show_overlays = saved['overlays']
        space.camera = saved['camera']
        space.use_local_camera = saved['local']
        space.region_3d.view_perspective = saved['perspective']
    except ReferenceError:
        pass


def export_down_video(scene, turbine, filepath, *, duration_s=10.0, fps=25, frames=None, engine='VIEWPORT_MATERIAL',
                      from_start=True, blocking=None, size=VIDEO_SIZE, samples=16, labels=None, dynamics=True,
                      seed=None, context=None):
    """Render a turbine's Down camera to an H.264 MP4 plus a per-frame JSON sidecar.

    ``frames`` overrides ``duration_s``. Viewport engines draw through a 3D view
    of the current window and always block: with FFmpeg available frames are
    drawn offscreen and piped to it, otherwise ``render.opengl`` writes the
    movie. EEVEE renders in a window run as a
    cancellable render job and finish through the render completion handlers.
    Blocking renders return the written path; interactive renders return None."""
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    if scene.as_pointer() in _EXPORT:
        raise ValueError('该场景正在导出视频')
    if engine not in ENGINES:
        raise ValueError('未知渲染方式')
    viewport = engine in VIEWPORT_ENGINES
    if viewport and bpy.app.background:
        raise ValueError('视口渲染需要 Blender 窗口；后台请选择 EEVEE 或 Workbench')
    fps = int(fps)
    frames = int(frames) if frames else int(round(float(duration_s) * fps))
    if fps < 1 or frames < 1:
        raise ValueError('帧数与帧率须大于 0')
    labels = scene.wfrl_manual_blade_labels if labels is None else bool(labels)
    target = Path(bpy.path.abspath(filepath))
    if target.suffix.lower() != '.mp4':
        target = target.with_suffix('.mp4')
    blocking = True if viewport else (bpy.app.background if blocking is None else blocking)
    view = _view_area(context or bpy.context) if viewport else None
    manual_control.pause_scene(scene)
    camera = ensure_down_camera(scene, turbine)
    plan = _plan(scene, fps, frames, from_start)
    scene['wfrl_manual_error'] = ''
    temp = tempfile.mkdtemp(prefix='wfrl_down_')
    saved = _snapshot(scene)
    job = dict(scene=scene, plan=plan, saved=saved, temp=temp, target=str(target), labels=labels, view=None)
    _EXPORT[scene.as_pointer()] = job
    scene['wfrl_manual_exporting'] = True
    try:
        _configure_video(scene, camera, fps, frames, engine, os.path.join(temp, 'down_'),
                         size=size, samples=samples)
        job['sidecar'] = _sidecar(plan, turbine, camera, scene, target.name, render_mode=engine, labels=labels,
                                  dynamics=dynamics)
        _start_motion(scene, job, turbine, [camera], dynamics, seed)
        hooks = [(bpy.app.handlers.frame_change_pre, _frame_handler)]
        if not blocking:
            hooks += [(bpy.app.handlers.render_complete, _render_complete),
                      (bpy.app.handlers.render_cancel, _render_cancel)]
        for handlers, function in hooks:
            if function not in handlers:
                handlers.append(function)
        scene.frame_set(scene.frame_start)
        if viewport:
            window, area, region = view
            job['view'] = _prepare_view(area, camera, VIEWPORT_ENGINES[engine])
            bpy.context.window_manager.progress_begin(0, frames)
            job['progress'] = True
            ffmpeg = ffmpeg_executable(context)
            job['sidecar']['encoder'] = 'ffmpeg libx264 (offscreen)' if ffmpeg else 'blender ffmpeg (render.opengl)'
            if ffmpeg:
                _draw_offscreen(scene, job, view, camera, ffmpeg, os.path.join(temp, 'down.mp4'))
            else:
                with bpy.context.temp_override(window=window, area=area, region=region, scene=scene):
                    bpy.ops.render.opengl(animation=True, view_context=True)
            return _finish(scene, completed=True)
        if blocking:
            bpy.ops.render.render(animation=True, scene=scene.name)
            return _finish(scene, completed=True)
        bpy.ops.render.render('INVOKE_DEFAULT', animation=True, scene=scene.name)
        return None
    except Exception:
        _finish(scene, completed=False)
        raise


def _turbine_default(context):
    target = context.scene.wfrl_manual_target
    return target if target != 'ALL' else 'T1'


class WFRL_OT_ManualDownView(bpy.types.Operator):
    bl_idname = 'wfrl.manual_down_view'
    bl_label = '观察 Down 相机'
    bl_description = '在当前视口通过所选风机的 Down 相机观察，并恢复该相机的安装位置和预设方向'

    turbine: bpy.props.EnumProperty(items=_TURBINES)
    # SKIP_SAVE: a button that leaves ``side`` unset must mean CENTER, not the
    # side of the previous call.
    side: bpy.props.EnumProperty(
        name='侧向',
        items=(('CENTER', '中心', '主 Down 相机'),
               ('LEFT', '左侧', '左侧视相机'),
               ('RIGHT', '右侧', '右侧视相机')),
        default='CENTER', options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if context.scene is not None and context.scene.as_pointer() in _EXPORT:
            cls.poll_message_set('导出 Down 视频期间不能切换观察相机')
            return False
        return manual_control.is_manual(context.scene)

    def execute(self, context):
        scene = context.scene
        scene.render.resolution_x, scene.render.resolution_y = VIDEO_SIZE
        scene.render.resolution_percentage = 100
        try:
            camera = ensure_down_camera(scene, self.turbine, reset=True)
            if self.side != 'CENTER':
                camera = ensure_side_cameras(scene, self.turbine)[self.side.capitalize()]
        except (ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        for area in context.screen.areas if context.screen else ():
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            space.use_local_camera = True
            space.camera = camera
            space.region_3d.view_perspective = 'CAMERA'
            cameras.fill_camera_view(area, scene)
            area.tag_redraw()
        return {'FINISHED'}


_ENGINE_ITEMS = (
    ('VIEWPORT_MATERIAL', '实时 · 视口材质',
     '视口材质预览（场景天空、光照、阴影与材质，单采样）；独立显卡且有 FFmpeg 时 1080p 约 0.02–0.04 s/帧，'
     '导出期间界面暂停响应'),
    ('VIEWPORT_SOLID', '最快 · 视口实体', '视口纯色实体着色，速度与视口材质相近；导出期间界面暂停响应'),
    ('SCENE', '高质量 · EEVEE 渲染', '最终渲染，含多采样抗锯齿；16 采样约 0.8 s/帧，可在渲染窗口取消'),
    ('WORKBENCH', '后台 · Workbench', '最终渲染路径的纯色模式，约 0.2 s/帧'))


class WFRL_OT_ManualDownVideo(bpy.types.Operator, ExportHelper):
    bl_idname = 'wfrl.manual_down_video'
    bl_label = '导出 Down 视频'
    bl_description = ('按当前转速与变桨设置渲染所选风机 Down 相机的 H.264 MP4，'
                      '同时写出逐帧叶轮角度与三片桨距的 JSON')
    filename_ext = '.mp4'

    filter_glob: bpy.props.StringProperty(default='*.mp4', options={'HIDDEN'})
    turbine: bpy.props.EnumProperty(name='风机', items=_TURBINES)
    length_mode: bpy.props.EnumProperty(name='长度', items=(
        ('DURATION', '按时长', '按秒数确定帧数'), ('FRAMES', '按帧数', '直接指定帧数')))
    duration_s: bpy.props.FloatProperty(name='时长 s', default=10.0, min=.04, max=3600.0, precision=2)
    frame_count: bpy.props.IntProperty(name='帧数', default=250, min=1, max=90000)
    fps: bpy.props.IntProperty(name='帧率', default=25, min=1, max=120)
    resolution: bpy.props.EnumProperty(name='分辨率', items=(
        ('1080', '1920×1080', '与现场相机一致'), ('720', '1280×720', ''), ('540', '960×540', '')))
    engine: bpy.props.EnumProperty(name='渲染', items=_ENGINE_ITEMS)
    eevee_samples: bpy.props.IntProperty(name='EEVEE 采样', default=16, min=1, max=64)
    labels: bpy.props.BoolProperty(name='叶片编号叠加', default=True,
                                   description='在画面右上角显示当前画面中的叶片编号')
    from_start: bpy.props.BoolProperty(
        name='从变桨起点录制', default=True,
        description='开启时视频从本次变桨开始前的桨距录起；关闭时从当前实际桨距录起')
    dynamics: bpy.props.BoolProperty(
        name='叶片摆动与塔架运动', default=True,
        description='按转速与桨距叠加示意性的叶片挥舞弯曲和塔顶前后运动，各叶片净空随时间不同；不是结构求解')
    use_seed: bpy.props.BoolProperty(
        name='固定随机种子', default=False,
        description='相机抖动与叶片／塔架运动使用下方种子，相同设置重复导出得到相同的抖动与运动；关闭时每次随机')
    seed: bpy.props.IntProperty(name='种子', default=1, min=0, max=(1 << 30) - 1,
                                description='写入 JSON 的 camera_shake.seed 与 structural_motion.seed')

    @classmethod
    def poll(cls, context):
        return (manual_control.is_manual(context.scene)
                and context.scene.as_pointer() not in _EXPORT)

    def _frames(self):
        return self.frame_count if self.length_mode == 'FRAMES' else max(1, round(self.duration_s * self.fps))

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, 'turbine')
        layout.prop(self, 'length_mode', expand=True)
        layout.prop(self, 'duration_s' if self.length_mode == 'DURATION' else 'frame_count')
        layout.prop(self, 'fps')
        frames = self._frames()
        layout.label(text=f'共 {frames} 帧 · {frames / self.fps:.2f} s')
        layout.prop(self, 'resolution')
        layout.prop(self, 'engine')
        if self.engine in VIEWPORT_ENGINES:
            renderer = gpu_renderer()
            layout.label(text='GPU ' + renderer, icon='ERROR' if is_integrated_gpu(renderer) else 'INFO')
            if is_integrated_gpu(renderer):
                layout.label(text='当前为集成显卡，导出较慢；在 Windows 图形设置中将 Blender 设为高性能')
            if ffmpeg_executable(context):
                layout.label(text='编码 FFmpeg · 快速路径', icon='INFO')
            else:
                layout.label(text='未找到 FFmpeg，使用 Blender 内置编码（较慢）', icon='ERROR')
                layout.label(text='可在扩展偏好设置“相机视频输出”中选择 ffmpeg.exe')
        if self.engine == 'SCENE':
            layout.prop(self, 'eevee_samples')
        layout.prop(self, 'labels')
        layout.prop(self, 'from_start')
        layout.prop(self, 'dynamics')
        row = layout.row(heading='固定随机种子')
        row.prop(self, 'use_seed', text='')
        sub = row.row()
        sub.enabled = self.use_seed
        sub.prop(self, 'seed', text='')

    def invoke(self, context, event):
        self.turbine = _turbine_default(context)
        self.labels = context.scene.wfrl_manual_blade_labels
        self.dynamics = context.scene.wfrl_manual_structural_motion
        self.filepath = bpy.path.abspath(f'//{self.turbine}_down.mp4') if bpy.data.filepath else \
            os.path.join(os.path.expanduser('~'), f'{self.turbine}_down.mp4')
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            written = export_down_video(
                context.scene, self.turbine, self.filepath, frames=self._frames(), fps=self.fps,
                engine=self.engine, from_start=self.from_start, size=VIDEO_SIZES[self.resolution],
                samples=self.eevee_samples, labels=self.labels, dynamics=self.dynamics,
                seed=self.seed if self.use_seed else None, context=context)
        except (ValueError, RuntimeError, ReferenceError, AttributeError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'已写出 {written}' if written else '正在渲染 Down 视频，完成后写出 MP4 与 JSON')
        return {'FINISHED'}


class WFRL_OT_ManualTripleDownVideo(bpy.types.Operator, ExportHelper):
    """Export the main Down camera plus Left and Right side cameras as three synchronised MP4s."""
    bl_idname = 'wfrl.manual_triple_down_video'
    bl_label = '导出三路 Down 视频'
    bl_description = ('按当前转速与变桨设置渲染所选风机的中心、左、右三路 Down 相机 H.264 MP4，'
                      '三路视频帧对帧同步；相邻相机重叠 12°，合计有效视场 153°')
    filename_ext = '.mp4'

    filter_glob: bpy.props.StringProperty(default='*.mp4', options={'HIDDEN'})
    turbine: bpy.props.EnumProperty(name='风机', items=_TURBINES)
    length_mode: bpy.props.EnumProperty(name='长度', items=(
        ('DURATION', '按时长', '按秒数确定帧数'), ('FRAMES', '按帧数', '直接指定帧数')))
    duration_s: bpy.props.FloatProperty(name='时长 s', default=10.0, min=.04, max=3600.0, precision=2)
    frame_count: bpy.props.IntProperty(name='帧数', default=250, min=1, max=90000)
    fps: bpy.props.IntProperty(name='帧率', default=25, min=1, max=120)
    resolution: bpy.props.EnumProperty(name='分辨率', items=(
        ('1080', '1920×1080', '与现场相机一致'), ('720', '1280×720', ''), ('540', '960×540', '')))
    engine: bpy.props.EnumProperty(name='渲染', items=(
        ('VIEWPORT_MATERIAL', '实时 · 视口材质', '视口材质预览，单采样'),
        ('VIEWPORT_SOLID', '最快 · 视口实体', '视口纯色实体着色')))
    labels: bpy.props.BoolProperty(name='叶片编号叠加', default=True,
                                    description='在每路画面右上角显示当前画面中的叶片编号')
    from_start: bpy.props.BoolProperty(
        name='从变桨起点录制', default=True,
        description='开启时视频从本次变桨开始前的桨距录起；关闭时从当前实际桨距录起')
    dynamics: bpy.props.BoolProperty(
        name='叶片摆动与塔架运动', default=True,
        description='按转速与桨距叠加示意性的叶片挥舞弯曲和塔顶前后运动，各叶片净空随时间不同；不是结构求解')
    use_seed: bpy.props.BoolProperty(
        name='固定随机种子', default=False,
        description='相机抖动与叶片／塔架运动使用下方种子，相同设置重复导出得到相同的抖动与运动；关闭时每次随机')
    seed: bpy.props.IntProperty(name='种子', default=1, min=0, max=(1 << 30) - 1,
                                description='写入 JSON 的 camera_shake.seed 与 structural_motion.seed')

    @classmethod
    def poll(cls, context):
        return (manual_control.is_manual(context.scene)
                and context.scene.as_pointer() not in _EXPORT)

    def _frames(self):
        return self.frame_count if self.length_mode == 'FRAMES' else max(1, round(self.duration_s * self.fps))

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, 'turbine')
        layout.prop(self, 'length_mode', expand=True)
        layout.prop(self, 'duration_s' if self.length_mode == 'DURATION' else 'frame_count')
        layout.prop(self, 'fps')
        frames = self._frames()
        layout.label(text=f'共 {frames} 帧 · {frames / self.fps:.2f} s · 三路共 {3 * frames} 帧')
        layout.prop(self, 'resolution')
        layout.prop(self, 'engine')
        renderer = gpu_renderer()
        layout.label(text='GPU ' + renderer, icon='ERROR' if is_integrated_gpu(renderer) else 'INFO')
        if is_integrated_gpu(renderer):
            layout.label(text='当前为集成显卡，导出较慢；在 Windows 图形设置中将 Blender 设为高性能')
        if ffmpeg_executable(context):
            layout.label(text='编码 FFmpeg · 三路并行快速路径', icon='INFO')
        else:
            layout.label(text='三路导出需要 FFmpeg，当前未找到', icon='ERROR')
            layout.label(text='请在扩展偏好设置“相机视频输出”中选择 ffmpeg.exe')
        layout.label(text='保存路径选择中心路；左路、右路文件名加 _left、_right 后缀')
        layout.prop(self, 'labels')
        layout.prop(self, 'from_start')
        layout.prop(self, 'dynamics')
        row = layout.row(heading='固定随机种子')
        row.prop(self, 'use_seed', text='')
        sub = row.row()
        sub.enabled = self.use_seed
        sub.prop(self, 'seed', text='')

    def invoke(self, context, event):
        self.turbine = _turbine_default(context)
        self.labels = context.scene.wfrl_manual_blade_labels
        self.dynamics = context.scene.wfrl_manual_structural_motion
        self.filepath = bpy.path.abspath(f'//{self.turbine}_down.mp4') if bpy.data.filepath else \
            os.path.join(os.path.expanduser('~'), f'{self.turbine}_down.mp4')
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        center = Path(bpy.path.abspath(self.filepath))
        if center.suffix.lower() != '.mp4':
            center = center.with_suffix('.mp4')
        stem = center.stem
        # Strip an existing _center / _down suffix so the side names are clean.
        for tail in ('_down', '_center'):
            if stem.lower().endswith(tail):
                stem = stem[: -len(tail)]
                break
        left = center.with_name(f'{stem}_left.mp4')
        right = center.with_name(f'{stem}_right.mp4')
        try:
            written = export_triple_down_video(
                context.scene, self.turbine, [center, left, right], frames=self._frames(), fps=self.fps,
                engine=self.engine, from_start=self.from_start, size=VIDEO_SIZES[self.resolution],
                labels=self.labels, dynamics=self.dynamics, seed=self.seed if self.use_seed else None,
                context=context)
        except (ValueError, RuntimeError, ReferenceError, AttributeError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'已写出三路视频：{os.path.basename(written[0])} / {os.path.basename(written[1])} / {os.path.basename(written[2])}')
        return {'FINISHED'}


CLASSES = (WFRL_OT_ManualDownView, WFRL_OT_ManualDownVideo, WFRL_OT_ManualTripleDownVideo)
_BLADE_ITEMS = tuple((key, label, '手动场景叶片外形', index)
                     for index, (key, (_, label)) in enumerate(MODELS.items()))


def register():
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)
    bpy.types.Scene.wfrl_manual_blade_model = bpy.props.EnumProperty(
        name='叶片外形', items=_BLADE_ITEMS, default=DEFAULT_MODEL, update=_blade_model_update)
    bpy.types.Scene.wfrl_manual_blade_labels = bpy.props.BoolProperty(
        name='叶片编号', default=True, update=_labels_update,
        description='在 Down 画面右上角显示当前画面中的叶片编号 B1/B2/B3')
    if _labels_listener not in manual_control.POSE_LISTENERS:
        manual_control.POSE_LISTENERS.append(_labels_listener)
    manual_dynamics.register()
    bpy.types.Scene.wfrl_manual_camera_shake = bpy.props.BoolProperty(
        name='画面抖动', default=True, update=_shake_toggle,
        description='运转时 Down 相机随转速做示意性的机舱振动小角度抖动（与导出视频同一模型）')
    if live_shake_update not in manual_control.MOTION_HOOKS:
        manual_control.MOTION_HOOKS.append(live_shake_update)  # after the blade bend: tips are current
    if live_shake_stop not in manual_control.STOP_HOOKS:
        manual_control.STOP_HOOKS.append(live_shake_stop)
    for name, function in _SHAKE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)


def unregister():
    for scene in getattr(bpy.data, 'scenes', ()):
        if scene.as_pointer() in _LIVE_SHAKE:
            live_shake_stop(scene)
    _LIVE_SHAKE.clear()
    for name, function in _SHAKE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    for hooks, function in ((manual_control.MOTION_HOOKS, live_shake_update),
                            (manual_control.STOP_HOOKS, live_shake_stop)):
        if function in hooks:
            hooks.remove(function)
    if hasattr(bpy.types.Scene, 'wfrl_manual_camera_shake'):
        del bpy.types.Scene.wfrl_manual_camera_shake
    manual_dynamics.unregister()
    if _labels_listener in manual_control.POSE_LISTENERS:
        manual_control.POSE_LISTENERS.remove(_labels_listener)
    for scene in getattr(bpy.data, 'scenes', ()):
        if scene.as_pointer() in _EXPORT:
            _finish(scene, completed=False)
    for name in ('wfrl_manual_blade_model', 'wfrl_manual_blade_labels'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
