"""Deterministic manual T1 Down video rendering in a separate Blender process."""
from __future__ import annotations

import csv
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace


def frame_count(duration_s, hold_s, fps):
    """Return the exact number of samples at k/fps over the half-open clip."""
    if type(fps) is not int or fps != 25:
        raise ValueError('手动视频当前要求 25 fps')
    if not all(math.isfinite(value) for value in (duration_s, hold_s)) or duration_s <= 0 or hold_s < 0:
        raise ValueError('变桨时长必须大于零，保持时长不能为负数')
    count = (duration_s + hold_s) * fps
    # RNA stores durations as float32; allow its rounding error at integer frames.
    if count < 1 or not math.isclose(count, round(count), rel_tol=2**-24, abs_tol=1e-5):
        raise ValueError('视频总时长必须对应完整帧数')
    return round(count)


def _write_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.status-',
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _absolute_file(value, label):
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f'{label} 必须是现有文件的绝对路径')
    return path


def _publish(source, destination, **kwargs):
    """Atomically publish complete bytes without overwriting any destination."""
    from wfrl.camera_video.publication import publish
    return publish(source, destination, **kwargs)


def _spec(job):
    from .manual_motion import PitchTrial
    from .manual_pose import pose_values
    from .turbine_geometry import MODELS
    values = json.loads(job['configuration'])
    if values['schema'] != 'wfrl.manual-pitch-trial.v1' or values['blade_model'] not in MODELS:
        raise ValueError('无法识别冻结试验或叶片模型')
    start = pose_values(values['start_pose'])
    pitches = tuple(start[f'pitch_b{i}_deg'] if start['individual_pitch'] else start['pitch_deg']
                    for i in range(1, 4))
    return PitchTrial(start['azimuth_deg'], start['rpm'], pitches, tuple(values['targets']),
                      tuple(values['selected']), values['duration_s'], values.get('interpolation', 'SMOOTH'))


def _encoding(job):
    from wfrl.camera_video import media
    return dict(fps=job['fps'], pattern='%06d.png', crf=18, preset='medium',
                ffmpeg=job['ffmpeg'], ffprobe=job['ffprobe'], encoder_source_sha256=media.sha256(media.__file__),
                executable_sha256={key: media.sha256(_absolute_file(job[key], key))
                                   for key in ('ffmpeg', 'ffprobe')})


def _sources():
    from wfrl.camera_video import media, publication
    root = Path(__file__).resolve().parent
    paths = list(root.rglob('*.py')) + [root / 'assets/rootkink_geometry.json',
                                      root / 'assets/nrel5mw_geometry.json',
                                      Path(media.__file__), Path(publication.__file__)]
    paths.extend(root.parents[1] / 'scripts/blender' / name for name in
                 ('render_manual_video.py', 'generate_manual_pitch_video.py', 'manage_manual_video.py'))
    return {str(path): media.sha256(path) for path in sorted(set(paths))}


def _regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'缺少普通文件或出现符号链接：{path}')


def _master_contract(job_path):
    from wfrl.camera_video.media import sha256
    from .manual_video_job import load_job
    job_path, job = load_job(job_path)
    root = job_path.parent
    spec = _spec(job)
    count = frame_count(spec.duration_s, job['hold_s'], job['fps'])
    files = {name: root / name for name in ('frames.csv', 'camera.json', 'experiment.json')}
    files['snapshot'] = _absolute_file(job['snapshot'], '场景快照')
    for path in files.values():
        _regular(path)
    experiment = json.loads(files['experiment.json'].read_text())
    if (experiment['configuration'] != json.loads(job['configuration'])
            or experiment['frame_count'] != count or experiment['fps'] != job['fps']
            or experiment['resolution'] != [job['width'], job['height']]
            or experiment['snapshot_sha256'] != sha256(files['snapshot'])
            or experiment['source']['render_source'] != _sources()):
        raise ValueError('母版来源、快照、配置或源码与作业不匹配')
    resources = experiment['source']['resources_sha256']
    if any(sha256(_absolute_file(path, '场景外部资源')) != digest for path, digest in resources.items()):
        raise ValueError('场景外部资源已改变')
    master = root / 'master_frames'
    expected = [f'{index:06d}.png' for index in range(count)]
    if master.is_symlink() or not master.is_dir() or sorted(p.name for p in master.iterdir()) != expected:
        raise ValueError('完整母版必须逐帧连续且数量正确；不补帧、不接受额外文件')
    for name in expected:
        _regular(master / name)
        if (master / name).stat().st_size == 0:
            raise ValueError('母版帧不能为空')
    with files['frames.csv'].open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != count:
        raise ValueError('姿态表帧数与作业不匹配')
    for index, row in enumerate(rows):
        pose = spec.pose_at(index / job['fps'])
        values = dict(time_s=pose.time_s, rpm=spec.rpm, azimuth_deg=pose.azimuth_deg,
                      **{f'pitch_b{i}_deg': value for i, value in enumerate(pose.pitches_deg, 1)})
        if (int(row['frame_id']) != index or int(row['video_pts']) != index
                or row['segment'] != ('transition' if pose.time_s < spec.duration_s else 'hold')
                or any(not math.isclose(float(row[key]), value, rel_tol=0, abs_tol=1e-9)
                       for key, value in values.items())):
            raise ValueError(f'姿态表不符合冻结 pose_at(t)：帧 {index}')
    return dict(schema='wfrl.manual-video-master.v1', job_id=job['job_id'], complete=True,
                job_sha256=sha256(job_path), frame_count=count, encoding=_encoding(job),
                files_sha256={name: sha256(path) for name, path in files.items()},
                master_sha256={name: sha256(master / name) for name in expected})


def seal_master(job_path):
    path = Path(job_path).parent / 'master.json'
    if os.path.lexists(path):
        raise FileExistsError(path)
    _write_json(path, _master_contract(job_path))


def validate_master(job_path):
    """Accept only a complete, identical owned master and deterministic pose table."""
    path = Path(job_path).parent / 'master.json'
    _regular(path)
    record = json.loads(path.read_text())
    if record != _master_contract(job_path):
        raise ValueError('母版哈希、来源或编码参数不匹配；拒绝编码重试')
    return record


def _owned_publication(root, job, digest):
    from wfrl.camera_video.media import sha256
    output = Path(job['output_mp4'])
    if not os.path.lexists(output):
        return False
    receipt_path = root / 'publication.json'
    _regular(receipt_path)
    receipt = json.loads(receipt_path.read_text())
    _regular(output)
    identity = output.stat()
    if (receipt != dict(job_id=job['job_id'], output_mp4=str(output), video_sha256=digest,
                        master_sha256=sha256(root / 'master.json'), device=identity.st_dev, inode=identity.st_ino,
                        staging_path=receipt.get('staging_path'))
            or sha256(output) != digest):
        raise FileExistsError(f'输出不是此作业已提交的完整文件：{output}')
    return True


def cleanup_publication(job_path):
    """Remove only an explicitly recorded staging inode belonging to this job."""
    from .manual_video_job import load_job
    from wfrl.camera_video.media import sha256
    path, job = load_job(job_path)
    receipt_path = path.parent / 'publication.json'
    if not receipt_path.exists():
        return
    _regular(receipt_path)
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('job_id') != job['job_id'] or receipt.get('output_mp4') != job['output_mp4']
            or receipt.get('master_sha256') != sha256(path.parent / 'master.json')):
        raise ValueError('发布暂存所有权不匹配；保留文件')
    staging = Path(receipt['staging_path'])
    if staging.parent != Path(job['output_mp4']).parent or not staging.name.startswith('.wfrl-video-'):
        raise ValueError('发布暂存路径不匹配；保留文件')
    if os.path.lexists(staging):
        _regular(staging)
        identity = staging.stat()
        if (identity.st_dev, identity.st_ino) != (receipt['device'], receipt['inode']):
            raise ValueError('发布暂存文件身份不匹配；保留文件')
        staging.unlink()


def delivery_result(job_path):
    """Recognize an owned verified commit even if the worker exited before status."""
    from .manual_video_job import load_job
    from wfrl.camera_video.media import sha256
    path, job = load_job(job_path)
    root = path.parent
    if not (root / 'publication.json').is_file() or not os.path.lexists(job['output_mp4']):
        return {}
    try:
        _regular(root / 'master.json')
        _regular(root / 'checks/manual-video.json')
        _regular(root / 'video.mp4')
        master = json.loads((root / 'master.json').read_text())
        checks = json.loads((root / 'checks/manual-video.json').read_text())
        count = frame_count(_spec(job).duration_s, job['hold_s'], job['fps'])
        digest = sha256(root / 'video.mp4')
        if (master.get('job_id') != job['job_id'] or master.get('job_sha256') != sha256(path)
                or checks != dict(valid=True, frame_count=count, resolution=[job['width'], job['height']],
                                  duration_s=count / job['fps'], fps=job['fps'], full_decode=True,
                                  video_sha256=digest)):
            return {}
        if _owned_publication(root, job, digest):
            return dict(output_mp4=job['output_mp4'], video_sha256=digest, duration_s=count / job['fps'],
                        completed_frames=count, total_frames=count)
    except (OSError, ValueError, KeyError):
        return {}
    return {}


def _finish(job_path, status):
    from wfrl.camera_video.media import encode_video, sha256, owned_process_options
    from wfrl.camera_video.publication import preflight
    from .manual_video_job import load_job
    path, job = load_job(job_path)
    root, output = path.parent, Path(job['output_mp4'])
    master = validate_master(path)
    cleanup_publication(path)
    count = master['frame_count']
    if not os.path.lexists(output):
        preflight(output)
    else:
        _regular(root / 'video.mp4')
        _owned_publication(root, job, sha256(root / 'video.mp4'))
    if not os.path.lexists(root / 'video.mp4'):
        preflight(root / 'video.mp4')
    status('ENCODING', completed_frames=count, total_frames=count)
    report = encode_video(root, job['fps'], ffmpeg=job['ffmpeg'], ffprobe=job['ffprobe'], resume=True)
    if (report['width'], report['height'], report['frame_count']) != (job['width'], job['height'], count):
        raise ValueError('编码视频的尺寸或帧数不符合请求')
    status('VERIFYING', completed_frames=count, total_frames=count)
    subprocess.run([job['ffmpeg'], '-v', 'error', '-xerror', '-nostdin', '-i', str(root / 'video.mp4'),
                    '-f', 'null', '-'], check=True, capture_output=True, text=True, **owned_process_options())
    probe = json.loads(subprocess.run([job['ffprobe'], '-v', 'error', '-show_entries',
            'format=duration:stream=duration', '-of', 'json', str(root / 'video.mp4')],
            check=True, capture_output=True, text=True, **owned_process_options()).stdout)
    duration = count / job['fps']
    if (abs(float(probe['format']['duration']) - duration) > 1e-5
            or abs(float(probe['streams'][0]['duration']) - duration) > 1e-5):
        raise ValueError('编码视频时长不符合请求')
    _write_json(root / 'checks/manual-video.json', dict(valid=True, frame_count=count,
                resolution=[report['width'], report['height']], duration_s=duration,
                fps=job['fps'], full_decode=True, video_sha256=report['video_sha256']))

    def receipt(staging, identity):
        _write_json(root / 'publication.json', dict(job_id=job['job_id'], output_mp4=str(output),
                    video_sha256=report['video_sha256'], master_sha256=sha256(root / 'master.json'),
                    device=identity.st_dev, inode=identity.st_ino, staging_path=str(staging)))

    def check_staging(staging, identity):
        if sha256(staging) != report['video_sha256']:
            raise ValueError('发布暂存文件哈希不匹配')

    status('PUBLISHING', completed_frames=count, total_frames=count)
    if not _owned_publication(root, job, report['video_sha256']):
        _publish(root / 'video.mp4', output, on_stage=receipt, before_commit=check_staging)
    status('COMPLETE', output_mp4=str(output), video_sha256=report['video_sha256'],
           duration_s=duration, completed_frames=count, total_frames=count)
    return report


@contextmanager
def _worker_guard(job_path):
    from . import manual_video_job as jobs
    path, job = jobs.load_job(job_path)
    previous = path.parent / 'status.json'
    if previous.is_file() and jobs.read_json(previous).get('state') == 'COMPLETE':
        raise ValueError('已成功的作业不会重新执行或改写状态')
    with jobs.Lease(path.parent / 'render.lock', job['job_id']):
        fd = os.environ.get('WFRL_MANUAL_OUTPUT_FD')
        if os.environ.get('WFRL_MANUAL_ATTEMPT') and fd is not None:
            actual, expected = os.fstat(int(fd)), os.stat(jobs.output_lock(job['output_mp4']))
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError('输出锁身份不匹配')
            yield path, job
        else:
            with jobs.Lease(jobs.output_lock(job['output_mp4']), job['job_id']):
                yield path, job


def _status_writer(path, job):
    started = time.monotonic()
    attempt = os.environ.get('WFRL_MANUAL_ATTEMPT')
    def status(state, **extra):
        value = dict(schema='wfrl.manual-video-status.v2', job_id=job['job_id'], attempt_id=attempt,
                     state=state, elapsed_wall_s=time.monotonic() - started, **extra)
        if not attempt:
            value.update(final=state in {'COMPLETE', 'FAILED', 'CANCELLED'}, active=False)
        _write_json(path.parent / ('worker-status.json' if attempt else 'status.json'), value)
    return status


def resume_job(job_path):
    """Retry encoding, validation and publication without starting Blender or rendering."""
    with _worker_guard(job_path) as (path, job):
        validate_master(path)  # Rejected inputs must not change any existing result state.
        status = _status_writer(path, job)
        try:
            return _finish(path, status)
        except BaseException as exc:
            status('FAILED', error=f'{type(exc).__name__}: {exc}')
            raise


def _stop_clock(control):
    import bpy
    control.pause_all()
    if bpy.app.timers.is_registered(control._timer):
        bpy.app.timers.unregister(control._timer)


def _validate_frozen_controls(entry, camera):
    """Reject native evaluation that can override sampled poses or fixed optics."""
    from . import manual_control as control
    control._validate_native_controls([getattr(entry, field) for field in control._POINTER_FIELDS] + [camera])


def _apply_pose(scene, start, spec, sample):
    """Apply one shared sample; no wall-clock or frame-change callback is used."""
    from . import manual_control as control
    entry = control._t1(scene)
    previous = control._updating
    control._updating = True
    try:
        entry.yaw_deg = start['yaw_deg']
        entry.azimuth_deg = sample.azimuth_deg % 360.0
        entry.rpm, entry.running = spec.rpm, False
        entry.individual_pitch = True
        for index, pitch in enumerate(sample.pitches_deg, 1):
            setattr(entry, f'pitch_b{index}_deg', pitch)
        scene.wfrl_manual_trial.elapsed_s = sample.time_s
        control._settle_pitch(entry)
        control._apply_entry(entry)
    finally:
        control._updating = previous
    scene.view_layers[0].update()


def _configure_render(scene, camera, job):
    scene.camera = camera
    scene.render.engine = 'BLENDER_EEVEE'
    scene.eevee.taa_render_samples = job.get('samples', 16)
    scene.render.resolution_x, scene.render.resolution_y = job['width'], job['height']
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1.0
    scene.render.fps, scene.render.fps_base = job['fps'], 1.0
    scene.render.use_border = scene.render.use_crop_to_border = False
    scene.render.use_motion_blur = False
    scene.render.film_transparent = False
    scene.render.use_sequencer = False
    scene.render.use_compositing = False
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    scene.render.image_settings.color_depth = '8'
    scene.render.image_settings.compression = 15
    scene.render.image_settings.color_management = 'FOLLOW_SCENE'
    scene.render.use_file_extension = True
    scene.display_settings.display_device = 'sRGB'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.view_settings.use_curve_mapping = False
    camera.data.dof.use_dof = False


def render_job(job_path):
    """Render a frozen manual trial, strictly validate H.264, and publish once."""
    with _worker_guard(job_path) as (path, job):
        return _render_job(path, job)


def _render_job(job_path, job):
    import bpy
    from . import cameras, manual_control as control
    from .camera_video import camera_configuration, sha256
    from wfrl.camera_video.publication import preflight

    if not bpy.app.background:
        raise RuntimeError('视频导出只能在独立后台 Blender 中运行')
    job_path = Path(job_path).resolve()
    root = job_path.parent
    completed, count = 0, 0
    write_status = _status_writer(job_path, job)

    def status(state, **extra):
        write_status(state, **dict(dict(completed_frames=completed, total_frames=count), **extra))

    try:
        status('PREPARING')
        snapshot = _absolute_file(job['snapshot'], '场景快照')
        _regular(snapshot)
        output = Path(job['output_mp4'])
        if not output.is_absolute() or output.suffix.lower() != '.mp4' or not output.parent.is_dir():
            raise ValueError('输出必须是现有目录内 MP4 文件的绝对路径')
        if os.path.lexists(output):
            raise FileExistsError(f'输出已存在：{output}')
        preflight(output)
        preflight(root / 'video.mp4')
        for key in ('ffmpeg', 'ffprobe'):
            executable = _absolute_file(job[key], key)
            if not os.access(executable, os.X_OK):
                raise ValueError(f'{key} 不可执行')
        for key in ('width', 'height'):
            value = job[key]
            if type(value) is not int or value < 16 or value % 2:
                raise ValueError('视频宽高必须是至少 16 的偶数像素')
        samples = job.get('samples', 16)
        if type(samples) is not int or samples < 1:
            raise ValueError('渲染采样数必须是正整数')
        master = root / 'master_frames'
        for path in (master, root / 'master.json', root / 'frames.csv', root / 'experiment.json',
                     root / 'camera.json', root / 'video.mp4'):
            if os.path.lexists(path):
                raise FileExistsError(f'作业目录已有结果；完整母版请使用 retry 入口：{path}')
        control.register()
        _stop_clock(control)
        bpy.ops.wm.open_mainfile(filepath=str(snapshot), load_ui=False, use_scripts=False)
        _stop_clock(control)
        scene = bpy.data.scenes[job['scene_name']]
        if bpy.context.window is not None:
            bpy.context.window.scene = scene
        valid, reason = control.validate_scene(scene)
        if not valid:
            raise ValueError(reason)
        if scene.wfrl_manual_trial.configuration != job['configuration']:
            raise ValueError('视频作业与场景快照的已确认试验配置不一致')
        start, spec = control.trial_configuration(scene)
        frozen_model = json.loads(job['configuration'])['blade_model']
        if scene.get('wfrl_manual_blade_model') != frozen_model:
            raise ValueError('手动视频叶片模型与冻结试验不一致')
        from .manual_geometry import set_detail
        set_detail(scene, 'CLOSEUP')
        count = frame_count(spec.duration_s, job['hold_s'], job['fps'])
        scene.wfrl_manual_trial.engaged = False
        for obj in scene.objects:
            if obj.get('wfrl_down_label'):
                obj.hide_render = True
                obj.hide_viewport = True
        for entry in scene.wfrl_manual_turbines:
            entry.running = False
        entry = control._t1(scene)
        owner = scene['wfrl_manual_owner']
        camera = cameras.ensure_gimbal(scene, 'T1', yaw_object=entry.yaw_object,
                                       name=owner + '.Camera.T1.Down', owner=owner)
        _validate_frozen_controls(entry, camera)
        cameras.down_gimbal(camera)
        _configure_render(scene, camera, job)
        _apply_pose(scene, start, spec, spec.pose_at(0.0))
        camera_record = camera_configuration(scene, camera, SimpleNamespace(
            turbine='T1', exposure_s=0.0, width=job['width'], height=job['height']))
        camera_record.update(schema='wfrl.manual-video-camera.v1',
                             shutter='instantaneous_sample', exposure_s=0.0,
                             camera_mount_definition='world = manual_T1_yaw_world @ parent_mount_matrix_local',
                             motion_blur=False)
        _write_json(root / 'camera.json', camera_record)
        import importlib
        addon = importlib.import_module(__package__)
        source = Path(addon.__file__).resolve().parent
        resources = {}
        for image in bpy.data.images:
            if image.source == 'FILE' and not image.packed_file and image.filepath:
                resource = Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()
                resources[str(resource)] = sha256(_absolute_file(str(resource), '场景外部图像'))
        for library in bpy.data.libraries:
            resource = Path(bpy.path.abspath(library.filepath)).resolve()
            resources[str(resource)] = sha256(_absolute_file(str(resource), '场景链接库'))
        experiment = dict(schema='wfrl.manual-video-experiment.v1',
                          fidelity='SYNTH_RIGID', physics_coupled=False, blade_model=frozen_model,
                          configuration=json.loads(job['configuration']),
                          snapshot_sha256=sha256(snapshot), fps=job['fps'],
                          resolution=[job['width'], job['height']], frame_count=count,
                          duration_s=count / job['fps'], transition_s=spec.duration_s,
                          hold_s=job['hold_s'], sample_times='t[k] = k / fps, k = 0 .. frame_count-1',
                          shutter='instantaneous_sample', motion_blur=False,
                          renderer='BLENDER_EEVEE', samples=samples,
                          stationary_turbines=[item.turbine_id for item in scene.wfrl_manual_turbines
                                               if item.turbine_id != 'T1'],
                          original_hide_render={obj.name: obj.hide_render for obj in scene.objects},
                          source=dict(addon_file=addon.__file__, blender=bpy.app.version_string,
                                      render_source=_sources(), resources_sha256=resources,
                                      module_sha256={name: sha256(source / name) for name in
                                          ('manual_video_render.py', 'manual_motion.py', 'manual_control.py', 'cameras.py')},
                                      geometry_sha256=sha256(source / 'assets' / f'{frozen_model}_geometry.json')))
        _write_json(root / 'experiment.json', experiment)
        master.mkdir()
        fields = ('frame_id', 'video_pts', 'time_s', 'rpm', 'azimuth_deg',
                  'pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg', 'segment')
        with (root / 'frames.csv').open('x', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(fields)
            status('RENDERING')
            for index in range(count):
                sample = spec.pose_at(index / job['fps'])
                _apply_pose(scene, start, spec, sample)
                scene.render.filepath = str(master / f'{index:06d}.png')
                if bpy.ops.render.render(write_still=True, scene=scene.name) != {'FINISHED'}:
                    raise RuntimeError(f'渲染未完成：帧 {index}')
                writer.writerow((index, index, sample.time_s, spec.rpm, sample.azimuth_deg,
                                 *sample.pitches_deg, 'transition' if sample.time_s < spec.duration_s else 'hold'))
                handle.flush()
                completed = index + 1
                status('RENDERING')
            os.fsync(handle.fileno())
        seal_master(job_path)
        return _finish(job_path, status)
    except BaseException as exc:
        status('FAILED', error=f'{type(exc).__name__}: {exc}')
        raise
