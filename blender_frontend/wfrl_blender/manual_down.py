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
_PUBLISHING = {}
_LAST = {}
_MODEL_UPDATING = False

LABEL_DEPTH = .2
LABEL_SPAN = .5
BLADE_COLORS = ((1.0, .82, .05), (.15, .9, 1.0), (1.0, .3, .85))
_STATIONS = {}

from .manual_down_camera import (
    _entry as _entry,
    _camera_name as _camera_name,
    ensure_down_camera as ensure_down_camera,
    _down_cameras as _down_cameras,
    _label_material as _label_material,
    _label_children as _label_children,
    _label_objects as _label_objects,
    _overlay_object as _overlay_object,
    _text as _text,
    _plate as _plate,
    _frame_tangents as _frame_tangents,
    _ensure_labels as _ensure_labels,
    _stations as _stations,
    _world as _world,
    _set_hidden as _set_hidden,
    current_blade as current_blade,
    update_labels as update_labels,
    _labels_listener as _labels_listener,
    _labels_update as _labels_update,
    apply_blade_model as apply_blade_model,
)
from .manual_down_scene import (
    _camera_optics as _camera_optics,
    _check_export_camera as _check_export_camera,
    _clone_scene as _clone_scene,
    _drop_scene as _drop_scene,
    _configure_video as _configure_video,
)
from .manual_down_viewport import (
    _draw_offscreen as _draw_offscreen,
    _view_area as _view_area,
    _prepare_view as _prepare_view,
    _restore_view as _restore_view,
)


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
    pending = _PUBLISHING.get(key)
    return (pending is not None and pending[0] == scene
            or any(export_key == key or job.get('source_key') == key
                   for export_key, job in _EXPORT.items()))


def _frame_handler(scene, _depsgraph=None):
    job = _EXPORT.get(scene.as_pointer())
    if job is not None and not job.get('cancelled') and not job.get('failure'):
        try:
            apply_frame(scene, job['plan'], (scene.frame_current - scene.frame_start)/job['plan']['fps'],
                        labels=job['labels'])
        except Exception as exc:
            job['failure'] = str(exc)
            raise


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
    result = json.loads(subprocess.run(command, check=True, capture_output=True, text=True,
                                       encoding='utf-8', errors='replace').stdout)
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
                    '-map', '0:v:0', '-f', 'null', '-'], check=True, capture_output=True, text=True,
                   encoding='utf-8', errors='replace')
    return dict(frame_count=frames, fps=fps, resolution=list(size), full_decode=True)


def _render_camera(scene, _unused=None):
    job = _EXPORT.get(scene.as_pointer())
    if job is not None and not job.get('failure'):
        try:
            _check_export_camera(scene, job, frame=scene.frame_current)
        except Exception as exc:
            job['failure'] = str(exc)


def _sidecar(plan, turbine, camera, scene, video_name, *, render_mode=None, labels=False):
    rows = []
    for frame in range(plan['frames']):
        time_s = frame/plan['fps']
        row = dict(frame=frame, time_s=time_s)
        for item in plan['turbines']:
            azimuth, pitches = pose_at(item, time_s)
            row[item['turbine']] = dict(azimuth_deg=azimuth, pitch_deg=pitches)
        rows.append(row)
    render = scene.render
    return dict(schema='wfrl.manual-down-video.v1', video=video_name, camera=f'{turbine} Down',
                resolution=[render.resolution_x, render.resolution_y], fps=plan['fps'], frames=plan['frames'],
                duration_s=plan['frames']/plan['fps'], render_mode=render_mode,
                render_engine=render.engine, blade_model=scene.wfrl_manual_blade_shape, blade_labels=labels,
                optics=_camera_optics(camera, scene),
                turbines=plan['turbines'], samples=rows,
                scope='Rigid manual motion at k/fps; only selected turbine moves, others retain actual poses. '
                      'No aerodynamic, flexible-motion or field-validation result. Blade labels are annotations.')


def _finalize_files(temp, target, metadata, size, ffprobe, ffmpeg):
    """Verify and publish the isolated render using only plain data and files."""
    try:
        produced = sorted(Path(temp).glob('*.mp4'))
        if len(produced) != 1:
            raise RuntimeError('导出必须生成唯一的 MP4')
        verification = _verify_video(produced[0], metadata['frames'], metadata['fps'],
                                     size, ffprobe, ffmpeg)
        _publish_pair(produced[0], Path(target), dict(metadata, verification=verification))
    except Exception as exc:
        return dict(state='FAILED', error=str(exc), temporary=temp)
    try:
        shutil.rmtree(temp)
    except OSError as exc:
        return dict(state='COMPLETE', path=target, warning=str(exc), temporary=temp)
    return dict(state='COMPLETE', path=target)


def _publish_async(source, source_key, files):
    result = queue.SimpleQueue()
    pending = (source, result)

    def poll():
        if _PUBLISHING.get(source_key) is not pending:
            return None
        try:
            status = result.get_nowait()
        except queue.Empty:
            return .2
        del _PUBLISHING[source_key]
        try:
            if source.as_pointer() == source_key:
                _LAST[source_key] = status
        except ReferenceError:
            pass
        if status['state'] == 'FAILED':
            print(f'WFRL Down video: {status["error"]}；恢复文件保留于 {status["temporary"]}')
        return None

    def publish():
        result.put(_finalize_files(*files))

    _PUBLISHING[source_key] = pending
    try:
        bpy.app.timers.register(poll, first_interval=.1, persistent=True)
        threading.Thread(target=publish, name='wfrl-down-publish', daemon=True).start()
    except Exception:
        del _PUBLISHING[source_key]
        raise


def _finish(scene, *, completed, asynchronous=False):
    """Release the isolated render scene and settle the media publication."""
    job = _EXPORT.pop(scene.as_pointer(), None)
    if job is None:
        return None
    source_key = job.get('source_key')
    if source_key is None:
        try:
            source_key = job['source'].as_pointer()
        except ReferenceError:
            pass
    try:
        if job.get('failure'):
            raise RuntimeError('逐帧导出检查失败：' + job['failure'])
        if not completed:
            _LAST[source_key] = dict(state='CANCELLED', temporary=job['temp'])
            return None
        _check_export_camera(scene, job)
        if job['camera_frames'] != set(range(1, job['plan']['frames'] + 1)):
            raise RuntimeError('Down 导出相机检查未覆盖全部输出帧')
        from copy import deepcopy
        job['sidecar'] = deepcopy(job['sidecar'])
        files = (job['temp'], job['target'], job['sidecar'], job['size'], job['ffprobe'], job['ffmpeg'])
        if asynchronous:
            _publish_async(job['source'], source_key, files)
            return None
        status = _finalize_files(*files)
        if status['state'] == 'FAILED':
            raise RuntimeError(status['error'])
        _LAST[source_key] = status
        return status['path']
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
                                       (bpy.app.handlers.render_post, _render_camera),
                                       (bpy.app.handlers.render_complete, _render_complete),
                                       (bpy.app.handlers.render_cancel, _render_cancel)):
                if function in handlers:
                    handlers.remove(function)


def _defer_finish(scene, completed):
    key = scene.as_pointer()
    job = _EXPORT.get(key)
    if job is None or job.get('finishing'):
        return
    job['finishing'] = True

    def finish():
        if _EXPORT.get(key) is job:
            try:
                _finish(job['scene'], completed=completed and not job['cancelled'], asynchronous=True)
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
    blocking = (viewport or bpy.app.background) if blocking is None else bool(blocking)
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
        job.update(camera=camera, camera_data=camera.data,
                   camera_optics=dict(job['sidecar']['optics']), camera_frames=set())
        with (Path(temp)/'down.json').open('x', encoding='utf-8') as handle:
            json.dump(job['sidecar'], handle, ensure_ascii=False, indent=1, allow_nan=False)
        _EXPORT[clone.as_pointer()] = job
        for handlers, function in ((bpy.app.handlers.frame_change_pre, _frame_handler),
                                   (bpy.app.handlers.render_post, _render_camera),
                                   (bpy.app.handlers.render_complete, _render_complete),
                                   (bpy.app.handlers.render_cancel, _render_cancel)):
            if function not in handlers:
                handlers.append(function)
        clone.frame_set(1)
        _check_export_camera(clone, job)
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
            return _finish(clone, completed=True, asynchronous=not blocking)
        if blocking:
            result = bpy.ops.render.render(animation=True, scene=clone.name)
            if result != {'FINISHED'} or job['cancelled']:
                _finish(clone, completed=False)
                raise InterruptedError(f'导出已取消；临时文件保留于 {temp}')
            return _finish(clone, completed=True)
        result = bpy.ops.render.render('INVOKE_DEFAULT', animation=True, scene=clone.name)
        if result == {'FINISHED'}:
            _defer_finish(clone, not job['cancelled'])
        elif result != {'RUNNING_MODAL'}:
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


_ENGINE_ITEMS = (
    ('VIEWPORT_MATERIAL', '视口材质', '视口材质与场景光照；需要可见窗口，导出期间界面暂停响应'),
    ('VIEWPORT_SOLID', '视口实体', '视口实体着色；需要可见窗口，导出期间界面暂停响应'),
    ('SCENE', 'EEVEE 渲染', '包含多采样抗锯齿，可在渲染窗口取消'),
    ('WORKBENCH', 'Workbench', '实体着色渲染，支持后台运行'))


from .manual_down_operators import (
    _turbine_default as _turbine_default,
    WFRL_OT_ManualDownView as WFRL_OT_ManualDownView,
    WFRL_OT_ManualDownVideo as WFRL_OT_ManualDownVideo,
)


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
            try:
                _finish(job['scene'], completed=False)
            except RuntimeError as exc:
                print('WFRL Down video:', exc)
    _STATIONS.clear()
    for name in ('wfrl_manual_blade_shape', 'wfrl_manual_blade_labels'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
