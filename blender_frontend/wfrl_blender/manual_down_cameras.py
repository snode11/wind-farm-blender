"""Manual Down and side cameras, blade-number overlays and blade shapes."""
import math

import bpy
from mathutils import Matrix, Vector
import numpy as np

from . import cameras, manual_control, manual_dynamics
from .turbine_geometry import DEFAULT_MODEL, blade_mesh


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
    rotor = entry.rotor_object
    local = rotor.matrix_parent_inverse @ rotor.matrix_basis
    return local.translation.copy(), local.to_3x3().col[0].normalized()


def side_camera_matrix(center, entry, side):
    """YawRoot-local pose of a side camera: the center Down pose turned about the shaft.

    The rotor is rotationally symmetric about the shaft, so a blade at azimuth
    a ± TRIPLE_STEP_DEG looks in the side camera exactly as a blade at a looks
    in the center camera; only the ground, horizon and tower move."""
    from . import manual_down_motion as motion
    hub, axis = _shaft_frame(entry)
    rest = motion.rest_matrix(center)
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
    from . import manual_down_motion as motion
    pitch = manual_down_pitch(scene)
    for camera in _down_cameras(scene):
        if camera.get('wfrl_triple_side') or camera.get('wfrl_down_pitch_deg') == pitch:
            continue
        motion._unshake(camera)
        camera['wfrl_down_pitch_deg'] = pitch
        cameras.down_gimbal(camera)
        sides = [c for c in _down_cameras(scene)
                 if c.get('wfrl_triple_side') and c['wfrl_turbine'] == camera['wfrl_turbine']]
        if sides:
            ensure_side_cameras(scene, camera['wfrl_turbine'])


def _apply_pending_blade_model(scene, *, strict=False):
    """Apply a blade shape chosen while an export was rendering."""
    try:
        mesh = scene.wfrl_manual_turbines[0].blade1_object.data
        if mesh.get('wfrl_blade_model', DEFAULT_MODEL) != scene.wfrl_manual_blade_model:
            apply_blade_model(scene)
    except (IndexError, ValueError, ReferenceError, AttributeError) as exc:
        if strict:
            raise
        print('WFRL manual blade model:', exc)


def _blade_model_update(scene, context):
    from . import manual_down_jobs as jobs
    if jobs.is_exporting(scene):
        # The running export keeps the shape it started with; the switch applies when it ends.
        return
    try:
        apply_blade_model(scene)
    except (ValueError, ReferenceError, AttributeError) as exc:
        print('WFRL manual blade model:', exc)
