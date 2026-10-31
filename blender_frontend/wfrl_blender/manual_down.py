"""Owned manual Down views, blade-number overlays and verified MP4 exports.

Export motion is rigid and sampled at k/fps. Only the selected turbine follows
its configured RPM and pitch transition; the other turbines retain actual poses.
"""
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import bpy
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector
import numpy as np

from . import cameras, manual_control, video_output
from .preferences import get_preferences
from .manual_motion import pitch_at
from .turbine_geometry import DEFAULT_MODEL, MODELS

DOWN_CAMERA_REVISION = cameras.DOWN_MOUNT_REVISION
VIDEO_SIZE = (1920, 1080)
VIDEO_SIZES = {'1080': (1920, 1080), '720': (1280, 720), '540': (960, 540)}
_TURBINES = (('T1', 'T1', 'T1 风机'), ('T2', 'T2', 'T2 风机'), ('T3', 'T3', 'T3 风机'))
VIEWPORT_ENGINES = {'VIEWPORT_MATERIAL': 'MATERIAL', 'VIEWPORT_SOLID': 'SOLID'}
ENGINES = {'SCENE', 'WORKBENCH', *VIEWPORT_ENGINES}
_EXPORT = {}
_LAST = {}
_MODEL_UPDATING = False

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
    return f"{scene['wfrl_manual_owner']}.Camera.{turbine}.Down"


def ensure_down_camera(scene, turbine, *, reset=False):
    """Use the local owned camera contract without changing another scene's optics."""
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    entry = _entry(scene, turbine)
    manual_control._validate_controlled_rotation(entry)
    owner = scene['wfrl_manual_owner']
    for existing in entry.yaw_object.children:
        if cameras.is_down_camera(existing):
            manual_control._validate_native_controls((existing,))
    from .manual_geometry import set_detail
    set_detail(scene, 'CLOSEUP')
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        camera = cameras.ensure_gimbal(scene, turbine, yaw_object=entry.yaw_object,
                                       name=_camera_name(scene, turbine), owner=owner)
        manual_control._validate_native_controls((camera,))
        if reset:
            cameras.down_gimbal(camera)
    camera['wfrl_turbine'] = turbine
    _ensure_labels(scene, camera)
    update_labels(scene)
    return camera


def _down_cameras(scene):
    if not manual_control.is_manual(scene):
        return []
    return [obj for obj in scene.objects if obj.type == 'CAMERA' and obj.get('wfrl_down_camera')
            and obj.get('wfrl_turbine') and obj.get('wfrl_manual_owner') == scene.get('wfrl_manual_owner')]


def _label_material(scene, key, color):
    name = f"{scene['wfrl_manual_owner']}.Label.{key}"
    candidates = {material for obj in scene.objects if obj.get('wfrl_down_label')
                  for material in getattr(obj.data, 'materials', ()) if material
                  and material.get('wfrl_down_label_material') == key
                  and material.get('wfrl_manual_owner') == scene['wfrl_manual_owner']}
    material = next(iter(candidates), None)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        nodes.clear()
        emission = nodes.new('ShaderNodeEmission')
        emission.inputs['Strength'].default_value = 1.0
        output = nodes.new('ShaderNodeOutputMaterial')
        material.node_tree.links.new(emission.outputs['Emission'], output.inputs['Surface'])
    shared = any(other != scene and any(
        getattr(obj.data, 'materials', None) and material.name in obj.data.materials
        for obj in other.objects) for other in bpy.data.scenes)
    if shared:
        consumers = [obj for obj in scene.objects if obj.get('wfrl_down_label')
                     and material.name in getattr(obj.data, 'materials', ())]
        other_objects = manual_control.other_scene_objects(scene)
        if any(obj.data.users != 1 or obj.as_pointer() in other_objects
               for obj in consumers):
            raise ValueError('编号网格或文本与其他场景共享')
        original = material
        material = original.copy()
        material.name = name
        for obj in consumers:
            for index, existing in enumerate(obj.data.materials):
                if existing == original:
                    obj.data.materials[index] = material
    material.diffuse_color = (*color, 1.0)
    material.node_tree.nodes['Emission'].inputs['Color'].default_value = (*color, 1.0)
    material['wfrl_manual_owner'] = scene['wfrl_manual_owner']
    material['wfrl_down_label_material'] = key
    material['provenance'] = 'Blade-number annotation overlay; not part of the turbine'
    return material


def _label_children(scene):
    children = {}
    for obj in bpy.data.objects:
        if obj.parent is not None:
            children.setdefault(obj.parent.as_pointer(), []).append(obj)
    return children


def _label_objects(scene, camera, *, children=None, shared=None):
    result = {}
    children = _label_children(scene) if children is None else children
    pending = list(children.get(camera.as_pointer(), ()))
    while pending:
        obj = pending.pop()
        pending.extend(children.get(obj.as_pointer(), ()))
        if not obj.get('wfrl_down_label'):
            continue
        if obj.get('wfrl_manual_owner') != scene['wfrl_manual_owner']:
            raise ValueError('编号对象不属于此场景')
        if shared is None:
            shared = manual_control.other_scene_objects(scene)
        if obj.as_pointer() in shared:
            raise ValueError('编号对象不属于此场景')
        role = obj.get('wfrl_down_role')
        if not role:
            match = re.search(r'(Legend\.(?:B[123]|Back|Title)|Label\.B[123](?:\.Back)?)(?:\.\d+)*$', obj.name)
            role = match.group(1) if match else None
        if role:
            if role in result:
                raise ValueError('同一相机包含重复编号角色')
            result[role] = obj
    return result


def _overlay_object(scene, camera, suffix, data):
    name = f'{camera.name}.{suffix}'
    obj = _label_objects(scene, camera).get(suffix)
    if obj is None:
        obj = bpy.data.objects.new(name, data)
        camera.users_collection[0].objects.link(obj)
    if obj.data != data or obj.as_pointer() in manual_control.other_scene_objects(scene):
        raise ValueError('编号对象或数据与其他场景共享')
    data['wfrl_manual_owner'] = scene['wfrl_manual_owner']
    if obj.get('wfrl_manual_owner', scene['wfrl_manual_owner']) != scene['wfrl_manual_owner']:
        raise ValueError('编号对象归属不匹配')
    obj.parent = camera
    obj.matrix_parent_inverse.identity()
    obj.rotation_euler = (0, 0, 0)
    obj.visible_shadow = False
    obj.hide_select = True
    obj['wfrl_manual_owner'] = scene['wfrl_manual_owner']
    obj['wfrl_down_label'] = True
    obj['wfrl_down_role'] = suffix
    return obj


def _text(scene, camera, suffix, body, size, align_x, align_y, material):
    name = f'{camera.name}.{suffix}'
    existing = _label_objects(scene, camera).get(suffix)
    curve = existing.data if existing else None
    if curve is not None and (curve.users != 1 or curve.get('wfrl_manual_owner') != scene['wfrl_manual_owner']):
        raise ValueError('编号文本数据不属于此场景')
    if curve is None:
        curve = bpy.data.curves.new(name, 'FONT')
    curve.body, curve.size = body, size
    curve.align_x, curve.align_y = align_x, align_y
    curve.materials.clear()
    curve.materials.append(material)
    return _overlay_object(scene, camera, suffix, curve)


def _plate(scene, camera, suffix, vertices, material, parent):
    name = f'{camera.name}.{suffix}'
    existing = _label_objects(scene, camera).get(suffix)
    mesh = existing.data if existing else None
    if mesh is not None and (mesh.users != 1 or mesh.get('wfrl_manual_owner') != scene['wfrl_manual_owner']):
        raise ValueError('编号背景数据不属于此场景')
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
    return tan_w, tan_w * render.resolution_y * render.pixel_aspect_y / (render.resolution_x * render.pixel_aspect_x)


def _ensure_labels(scene, camera):
    """Top-right box showing the number of the blade currently in view."""
    tan_w, tan_h = _frame_tangents(camera, scene)
    height = 2 * LABEL_DEPTH * tan_h
    for suffix in ('Legend.Title', *(f'Label.B{i}{part}' for i in range(1, 4) for part in ('', '.Back'))):
        stale = _label_objects(scene, camera).get(suffix)
        if stale is not None:
            if (stale.get('wfrl_manual_owner') != scene['wfrl_manual_owner']
                    or stale.as_pointer() in manual_control.other_scene_objects(scene)):
                raise ValueError('编号对象不属于此场景')
            bpy.data.objects.remove(stale, do_unlink=True)
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
    key = (mesh.as_pointer(), len(mesh.vertices), mesh.get('wfrl_manual_geometry', mesh.get('wfrl_blade_model', DEFAULT_MODEL)))
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
        if not _STATIONS[key]:
            raise ValueError('叶片缺少编号定位截面')
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
    the number stays defined while no blade is inside the frame."""
    inverse = _world(camera).inverted()
    best = None
    for index in range(1, 4):
        blade = getattr(entry, f'blade{index}_object')
        local = inverse @ _world(blade) @ _stations(blade.data)[0]
        angle = local.angle(Vector((0.0, 0.0, -1.0)), math.pi)
        if best is None or angle < best[0]:
            best = (angle, index)
    return best[1]


def update_labels(scene, *, visible=None):
    """Show the number of the blade currently in each Down view in its top-right box.

    Visibility is written only when it changes, since every change rebuilds
    depsgraph relations."""
    if not manual_control.is_manual(scene):
        return
    shown = scene.wfrl_manual_blade_labels if visible is None else visible
    children = shared = None
    for camera in _down_cameras(scene):
        if children is None:
            children = _label_children(scene)
            shared = manual_control.other_scene_objects(scene)
        objects = _label_objects(scene, camera, children=children, shared=shared)
        if not objects:
            continue
        if shown and ('Legend.Title' in objects or abs(
                camera.get('wfrl_label_aspect', 0) - scene.render.resolution_y / scene.render.resolution_x) > 1e-6):
            _ensure_labels(scene, camera)
            children = _label_children(scene)
            shared = manual_control.other_scene_objects(scene)
            objects = _label_objects(scene, camera, children=children, shared=shared)
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
    """Select owned display meshes while retaining poses and motion clocks."""
    if not manual_control.is_manual(scene) or not scene.wfrl_manual_turbines:
        return
    from . import manual_scene
    manual_scene._ensure_blade_model(scene)
    _STATIONS.clear()
    update_labels(scene)


def _blade_model_update(scene, context):
    global _MODEL_UPDATING
    if _MODEL_UPDATING:
        return
    previous = scene.get('wfrl_manual_blade_model', 'rootkink')
    try:
        apply_blade_model(scene)
        scene.pop('wfrl_manual_geometry_error', None)
    except (ValueError, RuntimeError, ReferenceError, AttributeError) as exc:
        _MODEL_UPDATING = True
        try:
            scene.wfrl_manual_blade_shape = previous
        finally:
            _MODEL_UPDATING = False
        scene['wfrl_manual_geometry_error'] = str(exc)
        print('WFRL manual blade model:', exc)


def _plan(scene, fps, frames, from_start, turbine, poses):
    """Freeze actual poses, with motion enabled only for the selected turbine."""
    rate = float(scene.wfrl_manual_pitch_rate_deg_s)
    if not math.isfinite(rate) or rate < 0:
        raise ValueError('变桨速率必须为非负有限值')
    plan = []
    for entry in scene.wfrl_manual_turbines:
        pose = poses[entry.turbine_id]
        actual = [float(pose[f'pitch_b{i}_deg'] if pose['individual_pitch'] else pose['pitch_deg'])
                  for i in range(1, 4)]
        selected = entry.turbine_id == turbine
        start = [float(getattr(entry, f'pitch_start_b{i}_deg')) for i in range(1, 4)] \
            if selected and from_start else actual
        target = [float(manual_control.target_pitch(entry, i)) for i in range(1, 4)] if selected else actual
        item = dict(turbine=entry.turbine_id, azimuth_deg=float(pose['azimuth_deg']),
                    rpm=float(entry.rpm) if selected else 0.0, pitch_start_deg=start,
                    pitch_target_deg=target, pitch_rate_deg_s=rate if selected else 0.0)
        if not all(math.isfinite(value) for value in (item['azimuth_deg'], item['rpm'], *start, *target)):
            raise ValueError('导出姿态必须为有限值')
        plan.append(item)
    return dict(fps=fps, frames=frames, selected_turbine=turbine, turbines=plan)


def pose_at(item, time_s):
    azimuth = (item['azimuth_deg'] + item['rpm'] * 6.0 * time_s) % 360.0
    pitches = [pitch_at(start, target, item['pitch_rate_deg_s'], time_s)
               for start, target in zip(item['pitch_start_deg'], item['pitch_target_deg'])]
    return azimuth, pitches


def apply_frame(scene, plan, time_s, *, labels=True):
    """Write the isolated render scene directly, without creating wall-clock motion."""
    previous = manual_control._updating
    manual_control._updating = True
    try:
        for item in plan['turbines']:
            entry = _entry(scene, item['turbine'])
            azimuth, pitches = pose_at(item, time_s)
            entry.azimuth_deg = azimuth
            entry.rotor_object.rotation_mode = 'XYZ'
            entry.rotor_object.rotation_euler.x = math.radians(azimuth)
            for index, pitch in enumerate(pitches, 1):
                setattr(entry, f'pitch_actual_b{index}_deg', pitch)
            entry.pitch_state_ready = True
            manual_control.apply_pitch(entry, pitches)
        update_labels(scene, visible=labels)
    finally:
        manual_control._updating = previous


def active(scene):
    key = scene.as_pointer()
    return any(export_key == key or job.get('source_key') == key
               for export_key, job in _EXPORT.items())


def _frame_handler(scene, _depsgraph=None):
    job = _EXPORT.get(scene.as_pointer())
    if job is not None and not job.get('cancelled') and not job.get('failure'):
        try:
            apply_frame(scene, job['plan'], (scene.frame_current - scene.frame_start)/job['plan']['fps'],
                        labels=job['labels'])
        except Exception as exc:
            job['failure'] = str(exc)
            raise


def gpu_renderer():
    if bpy.app.background:
        return 'background'
    try:
        import gpu
        return gpu.platform.renderer_get()
    except (RuntimeError, AttributeError):
        return 'unknown'


def ffmpeg_executable(context=None):
    prefs = get_preferences(context)
    configured = bpy.path.abspath(prefs.video_ffmpeg) if prefs and prefs.video_ffmpeg else ''
    try:
        return video_output.executable(configured, 'ffmpeg')
    except ValueError:
        return None


def _tools(context=None):
    prefs = get_preferences(context)
    configured = bpy.path.abspath(getattr(prefs, 'video_ffprobe', '')) if prefs else ''
    ffmpeg = ffmpeg_executable(context)
    if not ffmpeg:
        raise ValueError('导出需要 FFmpeg，请在相机视频输出偏好设置中配置')
    if not configured:
        sibling = Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
        configured = str(sibling) if sibling.is_file() else ''
    return ffmpeg, video_output.executable(configured, 'ffprobe')


def _output_paths(filepath):
    target = Path(os.path.abspath(os.path.expanduser(filepath)))
    if target.suffix.lower() != '.mp4':
        target = target.with_suffix('.mp4')
    sidecar = target.with_suffix('.json')
    if not target.parent.is_dir():
        raise ValueError('输出目录不存在')
    for path in (target, sidecar):
        if os.path.lexists(path):
            raise FileExistsError(f'目标已存在，不能覆盖：{path}')
    return target, sidecar


def _publish_pair(encoded, target, metadata):
    """Commit complete sidecar and video entries exclusively, undoing only owned links."""
    target = Path(target)
    sidecar = target.with_suffix('.json')
    for path in (target, sidecar):
        if os.path.lexists(path):
            raise FileExistsError(path)
    published = []
    with tempfile.TemporaryDirectory(prefix='.wfrl-down-publish-', dir=target.parent) as directory:
        staged_video, staged_json = Path(directory)/'video.mp4', Path(directory)/'video.json'
        with Path(encoded).open('rb') as incoming, staged_video.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        with staged_json.open('x', encoding='utf-8') as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=1, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        try:
            for source, destination in ((staged_json, sidecar), (staged_video, target)):
                identity = source.stat()
                os.link(source, destination, follow_symlinks=False)
                published.append((destination, identity.st_dev, identity.st_ino))
        except BaseException:
            for path, device, inode in reversed(published):
                try:
                    current = path.lstat()
                    if (current.st_dev, current.st_ino) == (device, inode):
                        path.unlink()
                except FileNotFoundError:
                    pass
            raise


def _verify_video(path, frames, fps, size, ffprobe, ffmpeg):
    """Decode actual frames and enforce dimensions, timing and the requested frame count."""
    command = [ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_streams', '-show_frames',
               '-show_entries', 'stream=codec_name,width,height,avg_frame_rate:frame=best_effort_timestamp_time',
               '-of', 'json', str(path)]
    result = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    streams, decoded = result.get('streams', []), result.get('frames', [])
    if len(streams) != 1 or len(decoded) != frames:
        raise ValueError('实际解码帧数与请求不一致')
    stream = streams[0]
    if (stream.get('codec_name') != 'h264' or (stream.get('width'), stream.get('height')) != tuple(size)
            or Fraction(stream.get('avg_frame_rate', '0')) != fps):
        raise ValueError('实际编码、尺寸或帧率与请求不一致')
    for index, frame in enumerate(decoded):
        timestamp = float(frame.get('best_effort_timestamp_time', 'nan'))
        if not math.isfinite(timestamp) or abs(timestamp - index/fps) > 1e-5:
            raise ValueError('实际帧时间戳与 k/fps 不一致')
    subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-nostdin', '-i', str(path),
                    '-map', '0:v:0', '-f', 'null', '-'], check=True, capture_output=True, text=True)
    return dict(frame_count=frames, fps=fps, resolution=list(size), full_decode=True)


def _sidecar(plan, turbine, camera, scene, video_name, *, render_mode=None, labels=False):
    rows = []
    for frame in range(plan['frames']):
        time_s = frame/plan['fps']
        row = dict(frame=frame, time_s=time_s)
        for item in plan['turbines']:
            azimuth, pitches = pose_at(item, time_s)
            row[item['turbine']] = dict(azimuth_deg=azimuth, pitch_deg=pitches)
        rows.append(row)
    data, render = camera.data, scene.render
    return dict(schema='wfrl.manual-down-video.v1', video=video_name, camera=f'{turbine} Down',
                resolution=[render.resolution_x, render.resolution_y], fps=plan['fps'], frames=plan['frames'],
                duration_s=plan['frames']/plan['fps'], render_mode=render_mode,
                render_engine=render.engine, blade_model=scene.wfrl_manual_blade_shape, blade_labels=labels,
                optics=dict(lens_mm=data.lens, sensor_width_mm=data.sensor_width,
                            vertical_fov_deg=math.degrees(2*math.atan(data.sensor_width/(2*data.lens)*
                                render.resolution_y/render.resolution_x)),
                            horizontal_fov_deg=math.degrees(data.angle_x), yaw_deg=camera['gimbal_yaw'],
                            pitch_deg=camera['gimbal_pitch'], roll_deg=camera['gimbal_roll'], lens_distortion='none'),
                turbines=plan['turbines'], samples=rows,
                scope='Rigid manual motion at k/fps; only selected turbine moves, others retain actual poses. '
                      'No aerodynamic, flexible-motion or field-validation result. Blade labels are annotations.')


def _clone_scene(source):
    """Copy scene resources before any camera, overlay, material or motion writes."""
    owner = 'WFRL.DownExport.' + uuid.uuid4().hex
    clone = source.copy()
    clone.name = owner
    clone['wfrl_manual_owner'] = owner
    clone['wfrl_manual_exporting'] = True
    objects, data_copies, material_copies = {}, {}, {}
    previous = manual_control._updating
    manual_control._updating = True
    try:
        for collection in list(clone.collection.children):
            clone.collection.children.unlink(collection)
        for obj in list(clone.collection.objects):
            clone.collection.objects.unlink(obj)
        collection = bpy.data.collections.new(owner + '.Objects')
        collection['wfrl_manual_owner'] = owner
        clone.collection.children.link(collection)
        for original in source.objects:
            obj = original.copy()
            obj.name = owner + '.' + original.name
            obj['wfrl_manual_owner'] = owner
            objects[original] = obj
            collection.objects.link(obj)
            if original.data is not None:
                if original.data not in data_copies:
                    data = original.data.copy()
                    data.name = owner + '.' + original.data.name
                    data['wfrl_manual_owner'] = owner
                    if original.type == 'MESH' and data.get('wfrl_manual_mesh_owner'):
                        data['wfrl_manual_mesh_owner'] = owner
                    data_copies[original.data] = data
                    if hasattr(data, 'materials'):
                        for index, material in enumerate(data.materials):
                            if material is not None:
                                if material not in material_copies:
                                    copy = material.copy()
                                    copy.name = owner + '.' + material.name
                                    copy['wfrl_manual_owner'] = owner
                                    material_copies[material] = copy
                                data.materials[index] = material_copies[material]
                obj.data = data_copies[original.data]
        for original, obj in objects.items():
            obj.parent = objects.get(original.parent)
            for constraint in obj.constraints:
                if hasattr(constraint, 'target') and constraint.target in objects:
                    constraint.target = objects[constraint.target]
        if source.world:
            clone.world = source.world.copy()
            clone.world.name = owner + '.World'
            clone.world['wfrl_manual_owner'] = owner
        clone.camera = objects.get(source.camera)
        for entry in clone.wfrl_manual_turbines:
            for field in manual_control._POINTER_FIELDS:
                setattr(entry, field, objects[getattr(entry, field)])
            entry.running = False
            entry.pitch_paused = True
        clone.wfrl_manual_return_scene = None
        valid, reason = manual_control.validate_scene(clone)
        if not valid:
            raise ValueError(reason)
        return clone
    except BaseException:
        _drop_scene(clone)
        raise
    finally:
        manual_control._updating = previous


def _drop_scene(scene):
    """Release only export-owned, unshared resources; preserve users and fake users."""
    owner = scene.get('wfrl_manual_owner')
    shared = manual_control.other_scene_objects(scene)
    for obj in list(scene.objects):
        if obj.get('wfrl_manual_owner') == owner and obj.as_pointer() not in shared:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.scenes.remove(scene)
    for group in (bpy.data.collections, bpy.data.meshes, bpy.data.curves, bpy.data.cameras,
                  bpy.data.lights, bpy.data.worlds, bpy.data.materials):
        for data in list(group):
            if (data.get('wfrl_manual_owner') == owner and data.users == 0
                    and not data.use_fake_user and data.library is None
                    and not getattr(data, 'shape_keys', None)):
                group.remove(data)
    _STATIONS.clear()


def _configure_video(scene, camera, fps, frames, engine, filepath, *, size=VIDEO_SIZE, samples=None):
    render = scene.render
    scene.camera = camera
    render.use_compositing = render.use_sequencer = False
    if engine == 'WORKBENCH':
        render.engine = 'BLENDER_WORKBENCH'
    if engine == 'SCENE':
        render.engine = 'BLENDER_EEVEE'
    if engine == 'SCENE' and samples:
        scene.eevee.taa_render_samples = int(samples)
    if engine in VIEWPORT_ENGINES:
        # The viewport path is bounded by per-frame colour processing, so the
        # export uses the plain sRGB transform without dithering or compositing.
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



def _draw_offscreen(scene, job, view, camera, ffmpeg, output):
    """Draw each isolated frame offscreen and pipe raw RGBA to FFmpeg."""
    import gpu
    window, area, region = view
    render, plan = scene.render, job['plan']
    width, height = render.resolution_x, render.resolution_y
    command = [ffmpeg, '-n', '-hide_banner', '-loglevel', 'error', '-nostdin',
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
    offscreen = None
    try:
        offscreen = gpu.types.GPUOffScreen(width, height, format='RGBA8')
        with bpy.context.temp_override(window=window, area=area, region=region, scene=scene):
            space, layer = area.spaces.active, bpy.context.view_layer
            depsgraph = bpy.context.evaluated_depsgraph_get()
            projection = camera.calc_matrix_camera(depsgraph, x=width, y=height)
            for frame in range(plan['frames']):
                if failure:
                    break
                apply_frame(scene, plan, frame / plan['fps'], labels=job['labels'])
                depsgraph.update()
                view_matrix = camera.evaluated_get(depsgraph).matrix_world.inverted()
                offscreen.draw_view3d(scene, layer, space, region, view_matrix, projection,
                                      do_color_management=True)
                pixels = offscreen.texture_color.read()
                pixels.dimensions = width * height * 4
                frames.put(np.asarray(pixels, dtype=np.uint8).tobytes())
                bpy.context.window_manager.progress_update(frame)
    finally:
        try:
            frames.put(None, timeout=5)
        except queue.Full:
            process.terminate()
            while True:
                try:
                    frames.get_nowait()
                except queue.Empty:
                    break
            frames.put_nowait(None)
        writer.join(timeout=5)
        if writer.is_alive():
            process.terminate()
            writer.join(timeout=5)
        if offscreen is not None:
            offscreen.free()
        try:
            code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait(timeout=5)
        log.seek(0)
        message = log.read().decode('utf-8', errors='replace')[-600:]
        log.close()
    if failure or code:
        raise RuntimeError(f'FFmpeg 编码失败（{code}）：{message or failure}')


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


def _finish(scene, *, completed):
    """Verify and publish complete media, then release the isolated render scene."""
    job = _EXPORT.pop(scene.as_pointer(), None)
    if job is None:
        return None
    source_key = job.get('source_key')
    if source_key is None:
        try:
            source_key = job['source'].as_pointer()
        except ReferenceError:
            source_key = None
    try:
        if not completed:
            _LAST[source_key] = dict(state='CANCELLED', temporary=job['temp'])
            return None
        if job.get('failure'):
            raise RuntimeError('逐帧姿态应用失败：' + job['failure'])
        produced = sorted(Path(job['temp']).glob('*.mp4'))
        if len(produced) != 1:
            raise RuntimeError('导出必须生成唯一的 MP4')
        verification = _verify_video(produced[0], job['plan']['frames'], job['plan']['fps'],
                                     job['size'], job['ffprobe'], job['ffmpeg'])
        job['sidecar']['verification'] = verification
        _publish_pair(produced[0], Path(job['target']), job['sidecar'])
        _LAST[source_key] = dict(state='COMPLETE', path=job['target'])
        shutil.rmtree(job['temp'])
        return job['target']
    except BaseException as exc:
        _LAST[source_key] = dict(state='FAILED', error=str(exc), temporary=job['temp'])
        raise RuntimeError(f'{exc}；恢复文件保留于 {job["temp"]}') from exc
    finally:
        if job.get('view'):
            _restore_view(*job['view'])
        if job.get('progress'):
            bpy.context.window_manager.progress_end()
        _drop_scene(scene)
        if not _EXPORT:
            for handlers, function in ((bpy.app.handlers.frame_change_pre, _frame_handler),
                                       (bpy.app.handlers.render_complete, _render_complete),
                                       (bpy.app.handlers.render_cancel, _render_cancel)):
                if function in handlers:
                    handlers.remove(function)


def _defer_finish(scene, completed):
    key = scene.as_pointer()
    def finish():
        job = _EXPORT.get(key)
        if job is not None:
            try:
                _finish(job['scene'], completed=completed)
            except Exception as exc:
                print('WFRL Down video:', exc)
    bpy.app.timers.register(finish, first_interval=.1)


@bpy.app.handlers.persistent
def _render_complete(scene, _unused=None):
    job = _EXPORT.get(scene.as_pointer())
    if job and not job['blocking']:
        _defer_finish(scene, not job['cancelled'])


@bpy.app.handlers.persistent
def _render_cancel(scene, _unused=None):
    job = _EXPORT.get(scene.as_pointer())
    if job:
        job['cancelled'] = True
        if not job['blocking']:
            _defer_finish(scene, False)


def export_down_video(scene, turbine, filepath, *, duration_s=10.0, fps=25, frames=None,
                      engine='VIEWPORT_MATERIAL', from_start=True, blocking=None,
                      size=VIDEO_SIZE, samples=16, labels=None, context=None):
    """Render an owned scene copy to verified H.264 MP4 and per-frame JSON.

    The source is intentionally paused at its actual pose. Its camera, render
    settings, materials and geometry remain untouched. Failed or cancelled
    attempts retain their private temporary media for recovery.
    """
    from . import manual_video
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    if active(scene) or manual_video.active(scene):
        raise ValueError('该场景正在导出视频')
    if manual_control.trial_active(scene):
        raise ValueError('T1 冻结试验请使用恒速变桨后台视频导出')
    if engine not in ENGINES:
        raise ValueError('未知渲染方式')
    if type(fps) is not int or not 1 <= fps <= 120:
        raise ValueError('帧率必须是 1–120 的整数')
    if frames is None:
        if not math.isfinite(float(duration_s)) or duration_s <= 0:
            raise ValueError('时长必须为正有限值')
        frames = round(duration_s*fps)
    if type(frames) is not int or not 1 <= frames <= 432000:
        raise ValueError('帧数必须是正整数，最多 432000 帧')
    if (len(size) != 2 or any(type(value) is not int or value < 4 or value > 8192 or value % 2 for value in size)):
        raise ValueError('H.264 输出尺寸必须是 4–8192 的偶数')
    if type(samples) is not int or not 1 <= samples <= 64:
        raise ValueError('采样数必须是 1–64 的整数')
    _entry(scene, turbine)
    viewport = engine in VIEWPORT_ENGINES
    if viewport and bpy.app.background:
        raise ValueError('视口渲染需要可见 Blender 窗口；后台请选择 EEVEE 或 Workbench')
    target, _sidecar_path = _output_paths(bpy.path.abspath(filepath))
    ffmpeg, ffprobe = _tools(context)
    for entry in scene.wfrl_manual_turbines:
        manual_control._validate_controlled_rotation(entry)
    for camera in scene.objects:
        if cameras.is_down_camera(camera):
            manual_control._validate_native_controls((camera,))
    # Sample every active clock at one instant before intentionally pausing it.
    now = time.monotonic()
    poses = manual_control.pose_snapshot(scene, now=now)
    plan = _plan(scene, fps, frames, from_start, turbine, poses)
    manual_control.pause_scene(scene, now=now)
    view = _view_area(context or bpy.context) if viewport else None
    blocking = True if viewport else (bpy.app.background if blocking is None else bool(blocking))
    temp = tempfile.mkdtemp(prefix='wfrl-down-')
    clone = None
    try:
        clone = _clone_scene(scene)
        camera = ensure_down_camera(clone, turbine, reset=True)
        shown = scene.wfrl_manual_blade_labels if labels is None else bool(labels)
        _configure_video(clone, camera, fps, frames, engine, os.path.join(temp, 'down_'),
                         size=size, samples=samples)
        job = dict(source=scene, source_key=scene.as_pointer(), scene=clone, plan=plan, temp=temp, target=str(target),
                   labels=shown, view=None, blocking=blocking, size=tuple(size),
                   ffmpeg=ffmpeg, ffprobe=ffprobe, cancelled=False)
        job['sidecar'] = _sidecar(plan, turbine, camera, clone, target.name,
                                  render_mode=engine, labels=shown)
        with (Path(temp)/'down.json').open('x', encoding='utf-8') as handle:
            json.dump(job['sidecar'], handle, ensure_ascii=False, indent=1, allow_nan=False)
        _EXPORT[clone.as_pointer()] = job
        for handlers, function in ((bpy.app.handlers.frame_change_pre, _frame_handler),
                                   (bpy.app.handlers.render_complete, _render_complete),
                                   (bpy.app.handlers.render_cancel, _render_cancel)):
            if function not in handlers:
                handlers.append(function)
        clone.frame_set(1)
        if viewport:
            window, area, region = view
            job['view'] = _prepare_view(area, camera, VIEWPORT_ENGINES[engine])
            bpy.context.window_manager.progress_begin(0, frames)
            job['progress'] = True
            source_window_scene = window.scene
            try:
                window.scene = clone
                _draw_offscreen(clone, job, view, camera, ffmpeg, os.path.join(temp, 'down.mp4'))
            finally:
                window.scene = source_window_scene
            return _finish(clone, completed=True)
        if blocking:
            result = bpy.ops.render.render(animation=True, scene=clone.name)
            if result != {'FINISHED'} or job['cancelled']:
                _finish(clone, completed=False)
                raise InterruptedError(f'导出已取消；临时文件保留于 {temp}')
            return _finish(clone, completed=True)
        result = bpy.ops.render.render('INVOKE_DEFAULT', animation=True, scene=clone.name)
        if result not in ({'RUNNING_MODAL'}, {'FINISHED'}):
            raise RuntimeError('无法启动渲染')
        return None
    except BaseException:
        if clone is not None:
            try:
                if clone.as_pointer() in _EXPORT:
                    _finish(clone, completed=False)
                elif clone.name in bpy.data.scenes:
                    _drop_scene(clone)
            except (ReferenceError, RuntimeError):
                pass
        raise


def _turbine_default(context):
    target = context.scene.wfrl_manual_target
    return target if target != 'ALL' else 'T1'


class WFRL_OT_ManualDownView(bpy.types.Operator):
    bl_idname = 'wfrl.manual_down_view'
    bl_label = '观察 Down 相机'
    bl_description = '在当前视口通过所选风机的 Down 相机观察，并恢复该相机的安装位置和预设方向'

    turbine: bpy.props.EnumProperty(items=_TURBINES)

    @classmethod
    def poll(cls, context):
        return manual_control.is_manual(context.scene)

    def execute(self, context):
        scene = context.scene
        render = scene.render
        saved_render = (render.resolution_x, render.resolution_y, render.resolution_percentage)
        try:
            camera = ensure_down_camera(scene, self.turbine, reset=True)
            render.resolution_x, render.resolution_y = VIDEO_SIZE
            render.resolution_percentage = 100
            _ensure_labels(scene, camera)
            update_labels(scene)
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
        except Exception as exc:
            render.resolution_x, render.resolution_y, render.resolution_percentage = saved_render
            try:
                update_labels(scene)
            except (ValueError, ReferenceError, AttributeError, KeyError):
                pass
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


_ENGINE_ITEMS = (
    ('VIEWPORT_MATERIAL', '视口材质', '视口材质与场景光照；需要可见窗口，导出期间界面暂停响应'),
    ('VIEWPORT_SOLID', '视口实体', '视口实体着色；需要可见窗口，导出期间界面暂停响应'),
    ('SCENE', 'EEVEE 渲染', '包含多采样抗锯齿，可在渲染窗口取消'),
    ('WORKBENCH', 'Workbench', '实体着色渲染，支持后台运行'))


class WFRL_OT_ManualDownVideo(bpy.types.Operator, ExportHelper):
    bl_idname = 'wfrl.manual_down_video'
    bl_label = '导出 Down 视频'
    bl_description = ('按所选风机的转速与变桨设置渲染 Down 相机 H.264 MP4，其余风机冻结于实际姿态，'
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
        ('1080', '1920×1080', '16:9 输出'), ('720', '1280×720', ''), ('540', '960×540', '')))
    engine: bpy.props.EnumProperty(name='渲染', items=_ENGINE_ITEMS)
    eevee_samples: bpy.props.IntProperty(name='EEVEE 采样', default=16, min=1, max=64)
    labels: bpy.props.BoolProperty(name='叶片编号叠加', default=True,
                                   description='在画面右上角显示当前画面中的叶片编号')
    from_start: bpy.props.BoolProperty(
        name='从变桨起点录制', default=True,
        description='开启时视频从本次变桨开始前的桨距录起；关闭时从当前实际桨距录起')

    @classmethod
    def poll(cls, context):
        return (manual_control.is_manual(context.scene)
                and not active(context.scene)
                and not manual_control.trial_active(context.scene))

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
            layout.label(text='需要可见窗口；导出期间界面暂停响应')
        layout.label(text='需要 FFmpeg 与 FFprobe，成片逐帧解码校验')
        if self.engine == 'SCENE':
            layout.prop(self, 'eevee_samples')
        layout.prop(self, 'labels')
        layout.prop(self, 'from_start')

    def invoke(self, context, event):
        self.turbine = _turbine_default(context)
        self.labels = context.scene.wfrl_manual_blade_labels
        self.filepath = bpy.path.abspath(f'//{self.turbine}_down.mp4') if bpy.data.filepath else \
            os.path.join(os.path.expanduser('~'), f'{self.turbine}_down.mp4')
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            written = export_down_video(
                context.scene, self.turbine, self.filepath, frames=self._frames(), fps=self.fps,
                engine=self.engine, from_start=self.from_start, size=VIDEO_SIZES[self.resolution],
                samples=self.eevee_samples, labels=self.labels, context=context)
        except (ValueError, OSError, RuntimeError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'已写出 {written}' if written else '正在渲染 Down 视频，完成后写出 MP4 与 JSON')
        return {'FINISHED'}


CLASSES = (WFRL_OT_ManualDownView, WFRL_OT_ManualDownVideo)
_BLADE_ITEMS = tuple((key, label, '手动场景叶片外形', index)
                     for index, (key, (_, label)) in enumerate(MODELS.items()))


def register():
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)
    bpy.types.Scene.wfrl_manual_blade_shape = bpy.props.EnumProperty(
        name='叶片外形', items=_BLADE_ITEMS, default='rootkink', update=_blade_model_update)
    bpy.types.Scene.wfrl_manual_blade_labels = bpy.props.BoolProperty(
        name='叶片编号', default=True, update=_labels_update,
        description='在 Down 画面右上角显示当前画面中的叶片编号 B1/B2/B3')
    if _labels_listener not in manual_control.POSE_LISTENERS:
        manual_control.POSE_LISTENERS.append(_labels_listener)


def unregister():
    if _labels_listener in manual_control.POSE_LISTENERS:
        manual_control.POSE_LISTENERS.remove(_labels_listener)
    for job in list(_EXPORT.values()):
        if bpy.app.is_job_running('RENDER') and not job['blocking']:
            job['cancelled'] = True
        else:
            _finish(job['scene'], completed=False)
    _STATIONS.clear()
    for name in ('wfrl_manual_blade_shape', 'wfrl_manual_blade_labels'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
