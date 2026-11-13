"""Down video orchestration, scene ownership, restoration and publication.

This module owns the export registries and native render callbacks. Other
modules query export activity and validate ownership through its functions.
"""
from contextlib import ExitStack
import math
import os
from pathlib import Path
import tempfile
import uuid

import bpy
from bpy.app.handlers import persistent

from . import atomic_json, cameras, manual_control, manual_dynamics, video_output
from . import manual_down_cameras as camera_rig
from . import manual_down_motion as motion
from . import manual_down_render as rendering


_EXPORT = {}
_JOBS = {}
_JOB_KEY = 'wfrl_manual_export_job'


def _begin_samples(job, metadata):
    motion._shake_sidecar(job, metadata)
    manual_dynamics.sidecar(job, metadata)
    stream = job['sample_stream'] = ExitStack()
    job['sample_path'] = job['temp'] / 'samples.json'
    job['append_sample'] = stream.enter_context(atomic_json.write_json_array(job['sample_path'], metadata, 'samples'))
    job['frames_written'] = 0


def _end_samples(job, *, complete):
    stream = job.get('sample_stream')
    if stream is not None:
        if complete:
            stream.close()
        else:
            stream.__exit__(GeneratorExit, GeneratorExit(), None)
        job.pop('sample_stream', None)


def _append_frame(job, frame, sample):
    if (frame != job['frames_written'] or frame >= job['plan']['frames']
            or sample is None or sample['frame'] != frame):
        raise RuntimeError(f'Down 视频缺少或乱序的帧样本：{frame + 1}')
    job['append_sample'](sample)
    job['frames_written'] += 1


def _frame_handler(scene, _depsgraph=None):
    job = _EXPORT.get(scene.as_pointer())
    if (job is None or job.get('state', 'RUNNING') != 'RUNNING'
            or not job.get('frame_events') or job.get('frame_error')):
        return
    frame = scene.frame_current - job['frame_start']
    written = job['frames_written']
    if written == job['plan']['frames'] or frame < written:
        # Native renders and render.opengl can reset the timeline before returning.
        # render_write still validates the order of frames actually written to the
        # video; the pending render.opengl sample stays for _finish to record.
        return
    try:
        pending = job.get('pending_sample')
        if job['frame_events'] == 'OPENGL' and pending is not None and pending['frame'] != frame:
            # render.opengl does not emit render_write. Advancing to the next frame
            # leaves the previous pose ready to record, with the final row on success.
            _append_frame(job, frame - 1, pending)
        if frame != job['frames_written']:
            raise RuntimeError(f'预期帧 {job["frames_written"] + 1}，收到帧 {frame + 1}')
        job['pending_sample'] = motion.apply_motion(scene, job, frame)
        if job.get('progress'):
            bpy.context.window_manager.progress_update(frame)
    except Exception as exc:
        job['frame_error'] = f'Down 视频第 {frame + 1} 帧姿态更新失败：{exc}'


def _write_frame_handler(scene, _depsgraph=None):
    job = _EXPORT.get(scene.as_pointer())
    if (job is None or job.get('state', 'RUNNING') != 'RUNNING'
            or job.get('frame_events') != 'NATIVE' or job.get('frame_error')):
        return
    frame = scene.frame_current - job['frame_start']
    try:
        _append_frame(job, frame, job.pop('pending_sample', None))
    except Exception as exc:
        job['frame_error'] = f'Down 视频第 {frame + 1} 帧样本写入失败：{exc}'


def _sidecar(plan, turbine, camera, scene, video_name, *, render_mode=None, labels=False, dynamics=False):
    data = camera.data
    render = scene.render
    return dict(
        video=video_name, camera=f'{turbine} Down', resolution=[render.resolution_x, render.resolution_y],
        fps=plan['fps'], frames=plan['frames'], duration_s=plan['frames'] / plan['fps'],
        render_mode=render_mode or render.engine, render_engine=render.engine, gpu=rendering.gpu_renderer(),
        encoder='blender ffmpeg',
        blade_model=scene.wfrl_manual_blade_model, blade_labels=labels,
        optics=dict(lens_mm=data.lens, sensor_width_mm=data.sensor_width,
                    vertical_fov_deg=cameras.DOWN_VFOV_DEG, horizontal_fov_deg=math.degrees(data.angle_x),
                    yaw_deg=camera['gimbal_yaw'], pitch_deg=camera['gimbal_pitch'], roll_deg=camera['gimbal_roll'],
                    lens_distortion='none'),
        azimuth_convention='Rotor rotation about the shaft; blade 1 points up at 0 deg',
        pitch_convention='Blade rotation about its pitch axis, same sign as the 风机控制 panel',
        turbines=plan['turbines'],
        scope='Manual rigid scene: constant rotor speed and rate-limited pitch; no prebend, '
              'aerodynamics, motion blur or lens distortion. '
              + ('Blade flapwise bend and tower lean follow the illustrative structural_motion signal, '
                 'not a structural solution. ' if dynamics else 'No blade flexure or tower motion. ')
              + 'The camera shake imitates nacelle vibration and is not a structural model.'
              + (' The top-right blade number is an annotation overlay.' if labels else ''))


_SAVED_RENDER = ('engine', 'resolution_x', 'resolution_y', 'resolution_percentage', 'pixel_aspect_x',
                 'pixel_aspect_y', 'fps', 'fps_base', 'filepath', 'use_lock_interface', 'use_file_extension',
                 'dither_intensity', 'use_compositing', 'use_sequencer', 'use_multiview',
                 'use_border', 'use_crop_to_border')
_SAVED_IMAGE = ('media_type', 'file_format', 'color_mode', 'color_depth')
_SAVED_FFMPEG = ('format', 'codec', 'constant_rate_factor', 'ffmpeg_preset', 'gopsize', 'audio_codec')


def _snapshot(scene):
    render = scene.render
    return dict(
        camera=scene.camera, frame=(scene.frame_start, scene.frame_end, scene.frame_step,
                                    scene.frame_current, scene.frame_subframe),
        render={name: getattr(render, name) for name in _SAVED_RENDER},
        image={name: getattr(render.image_settings, name) for name in _SAVED_IMAGE},
        ffmpeg={name: getattr(render.ffmpeg, name) for name in _SAVED_FFMPEG},
        view=(scene.view_settings.view_transform, scene.view_settings.look),
        eevee=(scene.eevee.taa_samples, scene.eevee.taa_render_samples), render_aa=scene.display.render_aa,
        markers=[(marker.name, marker.frame, marker.camera) for marker in scene.timeline_markers])


def _restore_marker_cameras(scene, saved, job=None):
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
                        _check_owner(job)
                        marker.camera = camera
                    except ReferenceError:
                        pass  # The bound camera was deleted during the export.
                    del pending[index]
                    markers.remove(marker)
                    break


def _restore(scene, saved, job=None):
    render = scene.render
    for name, value in saved['image'].items():
        _check_owner(job)
        setattr(render.image_settings, name, value)
    for name, value in saved['ffmpeg'].items():
        try:
            _check_owner(job)
            setattr(render.ffmpeg, name, value)
        except TypeError:
            pass  # Unset enums read back as '' and keep their defaults.
    for name, value in saved['render'].items():
        _check_owner(job)
        setattr(render, name, value)
    _check_owner(job)
    scene.view_settings.view_transform, scene.view_settings.look = saved['view']
    _check_owner(job)
    scene.eevee.taa_samples, scene.eevee.taa_render_samples = saved['eevee']
    _check_owner(job)
    scene.display.render_aa = saved['render_aa']
    _restore_marker_cameras(scene, saved['markers'], job)
    start, end, step, frame, subframe = saved['frame']
    _check_owner(job)
    scene.frame_start, scene.frame_end, scene.frame_step = start, end, step
    _check_owner(job)
    scene.frame_set(frame, subframe=subframe)
    # Frame changes evaluate marker cameras; restore the active camera after evaluating the frame.
    _check_owner(job)
    scene.camera = saved['camera']


def _configure_video(scene, camera, fps, frames, engine, filepath, *, size=rendering.VIDEO_SIZE, samples=None):
    render = scene.render
    # Camera-bound timeline markers must not switch the render to another camera. Each
    # marker edit re-runs Blender's camera switch, so all bindings go before the camera is set.
    for marker in scene.timeline_markers:
        marker.camera = None
    scene.camera = camera
    # Every engine writes the camera image itself, never a sequencer or compositor result.
    render.use_compositing = render.use_sequencer = render.use_multiview = False
    render.use_border = render.use_crop_to_border = False
    if engine == 'WORKBENCH':
        render.engine = 'BLENDER_WORKBENCH'
    if engine == 'SCENE':
        render.engine = 'BLENDER_EEVEE'
        if samples:
            scene.eevee.taa_render_samples = int(samples)
    if engine in rendering.VIEWPORT_ENGINES:
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
    scene.frame_start, scene.frame_end, scene.frame_step = 1, frames, 1
    render.image_settings.media_type = 'VIDEO'
    render.image_settings.file_format = 'FFMPEG'
    render.image_settings.color_mode = 'RGB'
    render.ffmpeg.format = 'MPEG4'
    render.ffmpeg.codec = 'H264'
    render.ffmpeg.constant_rate_factor = 'HIGH'
    render.ffmpeg.ffmpeg_preset = 'REALTIME' if engine in rendering.VIEWPORT_ENGINES else 'GOOD'
    render.ffmpeg.gopsize = fps
    render.ffmpeg.audio_codec = 'NONE'
    render.use_file_extension = True
    render.use_lock_interface = True
    render.filepath = filepath


class _ExportOwnershipLost(RuntimeError):
    pass


def _owned_scene(job):
    if job.get('detached') or _EXPORT.get(job.get('scene_key')) is not job:
        return None
    scene = job.get('scene')
    try:
        if (scene is not None and scene.as_pointer() == job['scene_key']
                and scene.get(_JOB_KEY) == job['identity']):
            return scene
    except ReferenceError:
        pass
    return None


def _check_owner(job):
    if job is not None and 'identity' in job and _owned_scene(job) is None:
        raise _ExportOwnershipLost('Down 视频作业已失去场景归属')


def _ensure_cleanup_timer():
    if not bpy.app.timers.is_registered(_retry_exports):
        bpy.app.timers.register(_retry_exports, first_interval=.2, persistent=True)


def _own_job(scene, job):
    key = scene.as_pointer()
    if _JOBS or key in _EXPORT:
        raise RuntimeError('Down 视频仍在导出或收尾，请等待回收完成')
    identity = uuid.uuid4().hex
    job.update(scene=scene, scene_key=key, identity=identity, state='RUNNING',
               cleanup={job['temp']: job['temp'].lstat()}, encoders=[])
    _JOBS[identity] = _EXPORT[key] = job
    scene[_JOB_KEY] = identity
    _check_owner(job)
    scene['wfrl_manual_error'] = ''
    _check_owner(job)
    scene['wfrl_manual_exporting'] = True
    _ensure_cleanup_timer()


def _restore_manual_pose(scene, job=None):
    _check_owner(job)
    manual_control.hold_pitch_clock(scene)
    for entry in scene.wfrl_manual_turbines:
        _check_owner(job)
        manual_control._apply_entry(entry)
    _check_owner(job)
    camera_rig._apply_pending_blade_model(scene, strict=True)
    _check_owner(job)
    camera_rig.update_labels(scene)


def _job_error(job, message):
    job['error'] = message
    scene = _owned_scene(job)
    if scene is not None:
        scene['wfrl_manual_error'] = message


def _restore_job(job):
    scene = _owned_scene(job)
    if scene is None:
        return
    restored = job.setdefault('restored', set())
    steps = [('motion', lambda: motion._end_motion(job)),
             ('scene', lambda: _restore(scene, job['saved'], job)),
             ('view', lambda: rendering._restore_view(*job['view'], job=job) if job.get('view') else None),
             ('pose', lambda: _restore_manual_pose(scene, job)),
             ('progress', lambda: bpy.context.window_manager.progress_end() if job.get('progress') else None)]
    for name, restore in steps:
        if name not in restored:
            _check_owner(job)
            restore()
            _check_owner(job)
            restored.add(name)


def _finish(scene=None, *, completed, job=None):
    """Keep ownership until native rendering, restoration and private file cleanup settle."""
    if job is None:
        try:
            job = _EXPORT.get(scene.as_pointer())
        except (ReferenceError, AttributeError):
            return None
    if job is None or job.get('state') == 'FINISHED':
        return None if job is None else job.get('report')
    if job.get('finishing'):
        return None
    job.setdefault('completed', completed)
    if not completed or job.get('stop_requested') or _owned_scene(job) is None:
        job['completed'] = False
    job['state'] = 'FINALIZING'
    job['finishing'] = True
    try:
        if (job.get('producer_active') or job.get('render_call_active')
                or (job.get('native_render') and bpy.app.is_job_running('RENDER'))):
            job['state'] = 'STOPPING'
            _job_error(job, 'Down 视频停止中：等待原生渲染退出')
            return None
        if not rendering._reap_encoders(job):
            job['state'] = 'STOPPING'
            _job_error(job, 'Down 视频停止中：' + job['encoder_error'])
            return None
        try:
            _restore_job(job)
        except _ExportOwnershipLost:
            job['completed'] = False
        if not job.get('publication_attempted'):
            job['publication_attempted'] = True
            try:
                if job.get('frame_error'):
                    raise RuntimeError(job['frame_error'])
                if job['completed']:
                    expected = job['plan']['frames']
                    if job.get('frame_events') == 'OPENGL':
                        _append_frame(job, expected - 1, job.pop('pending_sample', None))
                    if job['frames_written'] != expected:
                        raise RuntimeError(f'Down 视频帧样本不完整：{job["frames_written"]}/{expected}')
                    targets, sidecar = job['publish_targets'][:-1], job['publish_targets'][-1]
                    produced = job.get('movies') or sorted(Path(job['temp']).glob('*.mp4'))
                    if (len(produced) != len(targets)
                            or any(not path.is_file() or path.stat().st_size == 0 for path in produced)):
                        raise RuntimeError('未生成完整的 Down MP4 文件')
                    _end_samples(job, complete=True)
                    label = 'Down 视频' if len(targets) == 1 else '三路 Down 视频'
                    video_output.publish_staged(
                        [(source, target.requested) for source, target in zip(produced, targets)],
                        sidecar.requested, job['sample_path'], label, preflight=job['publish_targets'],
                        cleanup=job.setdefault('publish_cleanup', {}))
                    job['report'] = str(targets[0].path)
            except Exception as exc:
                job['failure'] = str(exc)
        _end_samples(job, complete=False)
        cleanup_errors = []
        for owned in (job.get('publish_cleanup', {}), job.get('cleanup', {})):
            try:
                video_output.cleanup_owned_directories(owned)
            except RuntimeError as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            raise RuntimeError('；'.join(cleanup_errors))
        owner = _owned_scene(job)
        if owner is not None:
            _check_owner(job)
            owner['wfrl_manual_last_video'] = job.get('report') or ''
            _check_owner(job)
            owner['wfrl_manual_error'] = job.get('failure', '')
            _check_owner(job)
            owner['wfrl_manual_exporting'] = False
            _check_owner(job)
            del owner[_JOB_KEY]
        if _EXPORT.get(job['scene_key']) is job:
            del _EXPORT[job['scene_key']]
        _JOBS.pop(job['identity'], None)
        job['state'] = 'FINISHED'
        if not _JOBS:
            for handlers, function in ((bpy.app.handlers.frame_change_pre, _frame_handler),
                                       (bpy.app.handlers.render_write, _write_frame_handler),
                                       (bpy.app.handlers.render_complete, _render_complete),
                                       (bpy.app.handlers.render_cancel, _render_cancel)):
                if function in handlers:
                    handlers.remove(function)
        if job.get('failure'):
            raise RuntimeError(job['failure'])
        return job.get('report')
    except Exception as exc:
        _job_error(job, f'Down 视频收尾失败：{exc}')
        raise
    finally:
        job['finishing'] = False
        if job.get('state') != 'FINISHED':
            _ensure_cleanup_timer()


def _retry_exports():
    for job in tuple(_JOBS.values()):
        if _owned_scene(job) is None:
            job['stop_requested'] = True
        if job['state'] != 'RUNNING' or job.get('stop_requested'):
            try:
                _finish(completed=job.get('completed', False), job=job)
            except Exception as exc:
                message = str(exc)
                if job.get('reported_error') != message:
                    print('WFRL Down video cleanup:', message)
                    job['reported_error'] = message
    return .2 if _JOBS else None


@persistent
def _before_scene_change(_unused):
    for job in tuple(_JOBS.values()):
        job['stop_requested'] = True
        try:
            _finish(completed=False, job=job)
        except Exception as exc:
            print('WFRL Down video cleanup:', exc)
        finally:
            # RNA addresses and saved ID properties can be reused by load and undo.
            job['detached'] = True
    if _JOBS:
        _ensure_cleanup_timer()


def _end_render(scene, *, completed):
    job = _EXPORT.get(scene.as_pointer())
    if job is None or _owned_scene(job) is None:
        return
    try:
        _finish(completed=completed, job=job)
    except Exception as exc:
        _job_error(job, f'Down 视频导出失败：{exc}')
        print('WFRL Down video export failed:', exc)


@persistent
def _render_complete(scene, _unused=None):
    _end_render(scene, completed=True)


@persistent
def _render_cancel(scene, _unused=None):
    _end_render(scene, completed=False)


def _mp4_target(filepath):
    path = Path(bpy.path.abspath(str(filepath)))
    return (path if path.suffix.lower() == '.mp4' else path.with_suffix('.mp4')).absolute()


def _has_animation(data):
    animation = data.animation_data
    return animation is not None and bool(animation.action or animation.drivers or animation.nla_tracks)


def _validate_export(scene, turbine, targets):
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    if _JOBS or scene.as_pointer() in _EXPORT:
        raise ValueError('Down 视频仍在导出或收尾，请等待回收完成')
    publish_targets = video_output.validate_publish_targets((*targets, targets[0].with_suffix('.json')))
    if bpy.app.is_job_running('RENDER'):
        raise ValueError('请等待当前 Blender 渲染结束后再导出 Down 视频')

    camera_rig._entry(scene, turbine)
    controlled = [getattr(entry, field) for entry in scene.wfrl_manual_turbines
                  for field in manual_control._POINTER_FIELDS]
    names = [camera_rig._camera_name(scene, turbine)]
    if len(targets) == 3:
        names.extend(camera_rig._side_camera_name(scene, turbine, side) for side in ('Left', 'Right'))
    for name in names:
        camera = scene.objects.get(name)
        if camera is None:
            continue
        if camera.type != 'CAMERA':
            raise ValueError(f'{name} 不是有效的 Down 相机')
        if _has_animation(camera.data):
            raise ValueError(f'{camera.data.name} 含动画或驱动，无法保证导出镜头参数一致')
        controlled.append(camera)
    checked = set()
    for obj in controlled:
        while obj is not None and obj not in checked:
            if obj.constraints or _has_animation(obj):
                raise ValueError(f'{obj.name} 含约束、动画或驱动，无法保证 Down 视频与逐帧姿态一致')
            checked.add(obj)
            obj = obj.parent
    return publish_targets


def export_triple_down_video(scene, turbine, filepaths, *, duration_s=10.0, fps=25, frames=None,
                             engine='VIEWPORT_MATERIAL', from_start=True, size=rendering.VIDEO_SIZE, samples=16,
                             labels=None, dynamics=True, seed=None, context=None):
    """Render the main Down camera plus Left and Right side cameras as three time-synchronised MP4s.

    ``filepaths`` is a sequence of three paths: (center, left, right).  The
    three cameras share the same per-frame pose, so the videos are frame-exact
    matches.  Only the VIEWPORT_MATERIAL and VIEWPORT_SOLID engines are
    supported (the single-pass offscreen path draws all three cameras per frame).
    Returns a tuple of the three written paths."""
    if engine not in rendering.VIEWPORT_ENGINES:
        raise ValueError('三路视频导出仅支持视口渲染方式（视口材质或视口实体）')
    targets = tuple(_mp4_target(path) for path in filepaths)
    if len(targets) != 3:
        raise ValueError('请选择中心、左侧和右侧三个 MP4 目标路径')
    publish_targets = _validate_export(scene, turbine, targets)
    if bpy.app.background:
        raise ValueError('视口渲染需要 Blender 窗口；后台请选择 EEVEE 或 Workbench')
    fps = int(fps)
    frames = int(frames) if frames else int(round(float(duration_s) * fps))
    if fps < 1 or frames < 1:
        raise ValueError('帧数与帧率须大于 0')
    labels_flag = scene.wfrl_manual_blade_labels if labels is None else bool(labels)
    ffmpeg = rendering.ffmpeg_executable(context)
    if not ffmpeg:
        raise ValueError('三路视频导出需要 FFmpeg；请在扩展偏好设置中配置 FFmpeg 路径')
    view = rendering._view_area(context or bpy.context)
    manual_control.pause_scene(scene)
    center_cam = camera_rig.ensure_down_camera(scene, turbine)
    side_cams = camera_rig.ensure_side_cameras(scene, turbine)
    cameras_list = [center_cam, side_cams['Left'], side_cams['Right']]
    plan = motion._plan(scene, fps, frames, from_start)
    saved = _snapshot(scene)
    temp = Path(tempfile.mkdtemp(prefix='wfrl_triple_'))
    out_paths = [temp / f'triple_{i}.mp4' for i in range(3)]
    job = dict(plan=plan, saved=saved, temp=temp, publish_targets=publish_targets,
               movies=out_paths, labels=labels_flag)
    _own_job(scene, job)
    try:
        _configure_video(scene, center_cam, fps, frames, engine, os.path.join(temp, 'triple_'),
                         size=size, samples=samples)
        sidecar = _sidecar(plan, turbine, center_cam, scene, targets[0].name, render_mode=engine,
                           labels=labels_flag, dynamics=dynamics)
        sidecar['encoder'] = 'ffmpeg libx264 (offscreen)'
        sidecar['camera'] = f'{turbine} Down rig'
        sidecar['rig'] = dict(
            videos=dict(center=targets[0].name, left=targets[1].name, right=targets[2].name),
            step_deg=camera_rig.TRIPLE_STEP_DEG, overlap_deg=camera_rig.TRIPLE_OVERLAP_DEG,
            side_pose='center Down pose turned about the rotor shaft by the step',
            yawroot_matrix={name: [list(row) for row in cam.matrix_basis]
                            for name, cam in zip(('center', 'left', 'right'), cameras_list)})
        motion._start_motion(scene, job, turbine, cameras_list, dynamics, seed)
        _begin_samples(job, sidecar)
        _, area, _ = view
        job['view'] = rendering._prepare_view(area, center_cam, rendering.VIEWPORT_ENGINES[engine])
        bpy.context.window_manager.progress_begin(0, frames)
        job['progress'] = True
        job['producer_active'] = True
        try:
            rendering._draw_offscreen_multi(scene, job, view, cameras_list, ffmpeg, out_paths)
        finally:
            job['producer_active'] = False
    except BaseException:
        _finish(completed=False, job=job)
        raise
    _finish(completed=True, job=job)
    return tuple(str(output.path) for output in publish_targets[:-1])


def export_down_video(scene, turbine, filepath, *, duration_s=10.0, fps=25, frames=None, engine='VIEWPORT_MATERIAL',
                      from_start=True, blocking=None, size=rendering.VIDEO_SIZE, samples=16, labels=None, dynamics=True,
                      seed=None, context=None):
    """Render a turbine's Down camera to an H.264 MP4 plus a per-frame JSON sidecar.

    ``frames`` overrides ``duration_s``. Viewport engines draw through a 3D view
    of the current window and always block: with FFmpeg available frames are
    drawn offscreen and piped to it, otherwise ``render.opengl`` writes the
    movie. EEVEE renders in a window run as a
    cancellable render job and finish through the render completion handlers.
    Blocking renders return the written path; interactive renders return None."""
    if engine not in rendering.ENGINES:
        raise ValueError('未知渲染方式')
    target = _mp4_target(filepath)
    publish_targets = _validate_export(scene, turbine, (target,))
    viewport = engine in rendering.VIEWPORT_ENGINES
    if viewport and bpy.app.background:
        raise ValueError('视口渲染需要 Blender 窗口；后台请选择 EEVEE 或 Workbench')
    fps = int(fps)
    frames = int(frames) if frames else int(round(float(duration_s) * fps))
    if fps < 1 or frames < 1:
        raise ValueError('帧数与帧率须大于 0')
    labels = scene.wfrl_manual_blade_labels if labels is None else bool(labels)
    blocking = True if viewport else (bpy.app.background if blocking is None else blocking)
    view = rendering._view_area(context or bpy.context) if viewport else None
    manual_control.pause_scene(scene)
    camera = camera_rig.ensure_down_camera(scene, turbine)
    plan = motion._plan(scene, fps, frames, from_start)
    saved = _snapshot(scene)
    temp = Path(tempfile.mkdtemp(prefix='wfrl_down_'))
    job = dict(plan=plan, saved=saved, temp=temp, publish_targets=publish_targets, labels=labels)
    _own_job(scene, job)
    try:
        _configure_video(scene, camera, fps, frames, engine, os.path.join(temp, 'down_'),
                         size=size, samples=samples)
        sidecar = _sidecar(plan, turbine, camera, scene, target.name, render_mode=engine, labels=labels,
                           dynamics=dynamics)
        ffmpeg = rendering.ffmpeg_executable(context) if viewport else None
        if viewport:
            sidecar['encoder'] = 'ffmpeg libx264 (offscreen)' if ffmpeg else 'blender ffmpeg (render.opengl)'
        motion._start_motion(scene, job, turbine, [camera], dynamics, seed)
        _begin_samples(job, sidecar)
        if viewport:
            window, area, region = view
            job['view'] = rendering._prepare_view(area, camera, rendering.VIEWPORT_ENGINES[engine])
            bpy.context.window_manager.progress_begin(0, frames)
            job['progress'] = True
            if ffmpeg:
                output = temp / 'down.mp4'
                job['movies'] = (output,)
                job['producer_active'] = True
                try:
                    rendering._draw_offscreen(scene, job, view, camera, ffmpeg, output)
                finally:
                    job['producer_active'] = False
                return _finish(completed=True, job=job)
        job['frame_events'] = 'OPENGL' if viewport else 'NATIVE'
        job['frame_start'] = scene.frame_start
        hooks = [(bpy.app.handlers.frame_change_pre, _frame_handler)]
        if not viewport:
            hooks.append((bpy.app.handlers.render_write, _write_frame_handler))
        if not blocking:
            hooks += [(bpy.app.handlers.render_complete, _render_complete),
                      (bpy.app.handlers.render_cancel, _render_cancel)]
        for handlers, function in hooks:
            if function not in handlers:
                handlers.append(function)
        # Evaluate the first pose and its visibility on the calling thread before
        # a native render can enter its worker. A render callback confirms the sample.
        scene.frame_set(scene.frame_start)
        if job.get('frame_error'):
            raise RuntimeError(job['frame_error'])
        job['native_render'] = True
        job['render_call_active'] = True
        try:
            if viewport:
                with bpy.context.temp_override(window=window, area=area, region=region, scene=scene):
                    result = bpy.ops.render.opengl(animation=True, view_context=True)
            elif blocking:
                result = bpy.ops.render.render(animation=True, scene=scene.name)
            else:
                result = bpy.ops.render.render('INVOKE_DEFAULT', animation=True, scene=scene.name)
        finally:
            job['render_call_active'] = False
        if not blocking and 'RUNNING_MODAL' in result:
            return None
        if 'FINISHED' not in result:
            raise RuntimeError('Blender 未能完成 Down 视频渲染')
        if _EXPORT.get(job['scene_key']) is job:
            return _finish(completed=True, job=job)
        # An interactive invocation may complete synchronously and run its handler before returning.
        if scene.get('wfrl_manual_error'):
            raise RuntimeError(scene['wfrl_manual_error'])
        return scene.get('wfrl_manual_last_video') or None
    except BaseException:
        _finish(completed=False, job=job)
        raise

_LIFECYCLE_HANDLERS = (('load_pre', _before_scene_change), ('undo_pre', _before_scene_change),
                       ('redo_pre', _before_scene_change))


def is_exporting(scene):
    """Whether this scene still owns a running or finalizing export."""
    return scene.as_pointer() in _EXPORT


def stop_exports():
    """Request finalization of every owned export, retaining unsettled resources."""
    for job in tuple(_JOBS.values()):
        job['stop_requested'] = True
        try:
            _finish(completed=False, job=job)
        except Exception as exc:
            print('WFRL Down video cleanup:', exc)
