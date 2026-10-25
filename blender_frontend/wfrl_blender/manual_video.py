"""Launch isolated manual-video jobs and display their progress."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ExportHelper

_JOBS = {}
_SCENE_JOBS = {}
_RESOLVING = {}
_DISPLAYED = {}
_PENDING = dict(state='QUERYING', active=True, final=False, completed_frames=0, total_frames=0)


def _query_record(path):
    from . import manual_video_job as jobs
    try:
        return jobs.query(path)
    except (OSError, ValueError, KeyError) as exc:
        return dict(state='LOST', active=True, final=False, error=str(exc))


class _StatusQueries:
    """One daemon queries plain paths; snapshots and acquisition times share a short lock."""

    def __init__(self):
        self._condition = threading.Condition()
        self._thread = None
        self._generation = 0
        self._due = {}
        self._records = {}
        self._revisions = {}

    def clear(self):
        with self._condition:
            self._generation += 1
            self._due.clear()
            self._records.clear()
            self._revisions.clear()
            self._condition.notify_all()

    def snapshot(self, path):
        with self._condition:
            record, obtained = self._records.get(path, (None, None))
            return (dict(record) if record is not None else None), obtained

    def track(self, path):
        with self._condition:
            record, obtained = self._records.get(path, (None, None))
            if record is not None and not record.get('active'):
                return
            self._due.setdefault(path, obtained + .5 if obtained is not None else time.monotonic())
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name='WFRL video status', daemon=True)
                self._thread.start()
            self._condition.notify_all()

    def store(self, path, record):
        """Authoritative actions invalidate an older background reply for the same path."""
        with self._condition:
            self._revisions[path] = self._revisions.get(path, 0) + 1
            obtained = time.monotonic()
            self._records[path] = (dict(record), obtained)
            if not record.get('active'):
                self._due.pop(path, None)
            elif path in self._due:
                self._due[path] = obtained + .5
            self._condition.notify_all()

    def forget(self, path):
        with self._condition:
            self._due.pop(path, None)
            self._revisions[path] = self._revisions.get(path, 0) + 1
            self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                if not self._due:
                    self._thread = None
                    return
                path = min(self._due, key=self._due.get)
                delay = self._due[path] - time.monotonic()
                if delay > 0:
                    self._condition.wait(delay)
                    continue
                generation = self._generation
                revision = self._revisions.get(path, 0)
            try:
                record = _query_record(path)
            except Exception as exc:
                record = dict(state='LOST', active=True, final=False, error=str(exc))
            with self._condition:
                if (generation != self._generation or path not in self._due
                        or revision != self._revisions.get(path, 0)):
                    continue
                obtained = time.monotonic()
                self._records[path] = (dict(record), obtained)
                if record.get('active'):
                    self._due[path] = obtained + .5
                else:
                    self._due.pop(path)
                self._condition.notify_all()


# Module reload reuses a retiring daemon, including one waiting on a supervisor.
_QUERIES = globals().get('_QUERIES') or _StatusQueries()
_QUERIES.clear()


def _ensure_poll():
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=.5, persistent=True)


def _binary(name):
    candidates = [shutil.which(name), '/opt/homebrew/bin/' + name, '/usr/local/bin/' + name]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise ValueError(f'未找到 {name}；请安装 FFmpeg 后再导出')


def _job_for(scene):
    """Resolve the saved association without reactivating completed polling entries."""
    path = _SCENE_JOBS.get(scene.session_uid)
    if not path:
        saved = scene.get('wfrl_manual_video_job', '')
        if saved:
            from . import manual_video_job as jobs
            try:
                binding = jobs.read_json(Path(saved).parent / 'scene-binding.json')
                if binding == dict(scene_name=scene.name, scene_owner=scene.get('wfrl_manual_owner')):
                    path = saved
                    _SCENE_JOBS[scene.session_uid] = path
            except (OSError, ValueError):
                pass
    if not path:
        return None
    entry = _JOBS.get(path) or _RESOLVING.get(path) or dict(directory=Path(path).parent, scene_name=scene.name)
    record, _obtained = _QUERIES.snapshot(path)
    if record is not None and not record.get('active'):
        return dict(entry, job_path=path)
    if path not in _JOBS:
        _RESOLVING[path] = entry
    _QUERIES.track(path)
    _ensure_poll()
    return dict(entry, job_path=path)


@persistent
def _restore_job_paths(_unused):
    """Task ownership follows scene identity independently of undoable properties."""
    for scene in bpy.data.scenes:
        path = _SCENE_JOBS.get(scene.session_uid)
        if path:
            scene['wfrl_manual_video_job'] = path
            if path in _JOBS:
                _JOBS[path]['scene_name'] = scene.name
            from . import manual_video_job as jobs
            try:
                jobs.write_json(Path(path).parent / 'scene-binding.json',
                                dict(scene_name=scene.name, scene_owner=scene.get('wfrl_manual_owner')))
            except OSError:
                pass


@persistent
def _before_load(_unused):
    _QUERIES.clear()
    _RESOLVING.clear()
    _DISPLAYED.clear()
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)


@persistent
def _after_load(_unused):
    _QUERIES.clear()
    _RESOLVING.clear()
    _DISPLAYED.clear()
    current = {scene.session_uid for scene in bpy.data.scenes}
    for uid in set(_SCENE_JOBS) - current:
        _SCENE_JOBS.pop(uid)
    for scene in bpy.data.scenes:
        _job_for(scene)
        for job_path, job in _JOBS.items():
            if (job.get('source_filepath') == bpy.data.filepath
                    and job.get('source_scene_name') == scene.name
                    and job.get('scene_owner') == scene.get('wfrl_manual_owner')):
                _SCENE_JOBS[scene.session_uid] = job_path
    _restore_job_paths(None)
    for scene in bpy.data.scenes:
        _job_for(scene)
    if _JOBS or _RESOLVING:
        _ensure_poll()


def status(scene):
    job = _job_for(scene)
    if not job:
        return {}
    record, _obtained = _QUERIES.snapshot(job['job_path'])
    return record if record is not None else dict(_PENDING)


def active(scene, *, refresh=False):
    if refresh:
        job = _job_for(scene)
        if not job:
            return False
        record = _refresh_status(job['job_path'])
        _job_for(scene)
        return bool(record.get('active'))
    return bool(status(scene).get('active'))


def _refresh_status(path):
    record = _query_record(path)
    _QUERIES.store(path, record)
    _QUERIES.track(path)
    return record


def _python_binary():
    """Find a standalone interpreter for the supervisor, independent of Blender."""
    candidates = [os.environ.get('WFRL_VIDEO_PYTHON'), str(Path(sys.prefix) / 'bin/python3'),
                  *map(str, sorted((Path(sys.prefix) / 'bin').glob('python3.*'))), shutil.which('python3')]
    for value in candidates:
        if not value or not Path(value).is_file() or not os.access(value, os.X_OK):
            continue
        try:
            subprocess.run([value, '-B', '-c', 'import sys; assert sys.version_info >= (3, 11)'],
                           check=True, capture_output=True, timeout=5)
            return str(Path(value).resolve())
        except (OSError, subprocess.SubprocessError):
            continue
    raise ValueError('未找到独立 Python 3.11+；可用 WFRL_VIDEO_PYTHON 指定监管解释器')


def _workspace_root():
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / 'scripts/blender/render_manual_video.py').is_file():
        return source_root
    from .preferences import get_preferences
    preferences = get_preferences()
    configured = getattr(preferences, 'project_dir', '')
    root = Path(bpy.path.abspath(configured)).expanduser().resolve() if configured else None
    required = ('scripts/blender/render_manual_video.py', 'wfrl/camera_video/publication.py',
                'blender_frontend/wfrl_blender/manual_video_render.py')
    if root is None or not all((root / name).is_file() for name in required):
        raise ValueError('请在扩展偏好的 Project directory 设置完整项目源码目录')
    return root


def _preflight_output(root, destination):
    spec = importlib.util.spec_from_file_location(
        '_wfrl_manual_video_publication', root / 'wfrl/camera_video/publication.py')
    publication = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publication)
    publication.preflight(destination)


def launch(scene, output_mp4):
    from . import manual_control as control
    from .manual_video_render import frame_count
    from . import manual_video_job as jobs
    jobs.require_supervision()
    if active(scene, refresh=True):
        raise ValueError('此场景已有视频正在生成')
    valid, reason = control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    if not scene.wfrl_manual_trial.configuration:
        raise ValueError('请先确认一组变桨试验，再导出视频')
    _start, spec = control.trial_configuration(scene)
    count = frame_count(spec.duration_s, scene.wfrl_manual_video_hold_s, 25)
    destination = jobs.destination_path(bpy.path.abspath(str(output_mp4)))
    if destination.suffix.lower() != '.mp4':
        raise ValueError('输出文件必须为 .mp4')
    if os.path.lexists(destination) or not destination.parent.is_dir():
        raise ValueError('请选择现有文件夹中的新 MP4 文件名；不会覆盖已有文件')
    root = _workspace_root()
    script = root / 'scripts/blender/render_manual_video.py'
    if not script.is_file():
        raise ValueError('当前视频入口需要完整工作区源码及导出脚本')
    ffmpeg, ffprobe = _binary('ffmpeg'), _binary('ffprobe')
    python = _python_binary()
    job_id = uuid.uuid4().hex
    with jobs.Lease(jobs.output_lock(destination), job_id) as lease:
        _preflight_output(root, destination)
        directory = destination.parent / (destination.stem + '_data_' + job_id)
        directory.mkdir(mode=0o700)
        snapshot, job_path = directory / 'scene.blend', directory / 'job.json'
        command = [bpy.app.binary_path, '--background', '--factory-startup', '--python-exit-code', '1',
                   '--python', str(script), '--', '--job', str(job_path)]
        job = dict(schema='wfrl.manual-video-job.v2', job_id=job_id, control_token=uuid.uuid4().hex + uuid.uuid4().hex,
                   snapshot=str(snapshot), scene_name=scene.name, render_command=command,
                   configuration=scene.wfrl_manual_trial.configuration,
                   output_mp4=str(destination), width=1920, height=1080, fps=25, frame_count=count,
                   hold_s=scene.wfrl_manual_video_hold_s, samples=16, ffmpeg=ffmpeg, ffprobe=ffprobe)
        result = bpy.ops.wm.save_as_mainfile(filepath=str(snapshot), copy=True, check_existing=False)
        if result != {'FINISHED'}:
            raise ValueError('无法保存视频场景副本')
        jobs.write_json(job_path, job)
        jobs.write_json(directory / 'scene-binding.json',
                        dict(scene_name=scene.name, scene_owner=scene.get('wfrl_manual_owner')))
        _JOBS[str(job_path)] = dict(directory=directory, scene_name=scene.name,
                                   source_scene_name=scene.name, source_filepath=bpy.data.filepath,
                                   scene_owner=scene['wfrl_manual_owner'])
        _SCENE_JOBS[scene.session_uid] = str(job_path)
        scene['wfrl_manual_video_job'] = str(job_path)
        if not bpy.app.timers.is_registered(_poll):
            bpy.app.timers.register(_poll, first_interval=.5, persistent=True)
        jobs.start(job_path, python=python, lease=lease, project_root=root,
                   preflight_check=lambda destination: _preflight_output(root, destination))
        _refresh_status(str(job_path))
    return job_path


def _display_record(record):
    return tuple(record.get(key) for key in (
        'state', 'active', 'final', 'completed_frames', 'total_frames', 'error', 'output_mp4'))


def _poll():
    changed = False
    for job_path, job in list((_RESOLVING | _JOBS).items()):
        record, _obtained = _QUERIES.snapshot(job_path)
        record = record if record is not None else _PENDING
        display = _display_record(record)
        previous = _DISPLAYED.get(job_path, _display_record(_PENDING))
        changed |= display != previous
        _DISPLAYED[job_path] = display
        if record.get('active'):
            if record is not _PENDING:
                _JOBS[job_path] = job
                _RESOLVING.pop(job_path, None)
            _QUERIES.track(job_path)
            continue
        _JOBS.pop(job_path, None)
        _RESOLVING.pop(job_path, None)
        _QUERIES.forget(job_path)
    if changed:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    return .5 if _JOBS or _RESOLVING else None


def cancel(scene):
    job = _job_for(scene)
    if job is None:
        return
    from . import manual_video_job as jobs
    record = jobs.cancel(job['job_path'])
    _QUERIES.store(job['job_path'], record)
    _QUERIES.track(job['job_path'])
    return record


class WFRL_OT_ManualVideo(bpy.types.Operator, ExportHelper):
    bl_idname = 'wfrl.manual_video'
    bl_label = '导出 T1 Down 视频'
    bl_description = '从最近确认的试验起点生成 1080p、25 fps MP4；当前预览保持原状态'
    filename_ext = '.mp4'
    filter_glob: bpy.props.StringProperty(default='*.mp4', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        from . import manual_control as control
        return (control.is_manual(context.scene) and bool(context.scene.wfrl_manual_trial.configuration)
                and not active(context.scene))

    def execute(self, context):
        try:
            launch(context.scene, self.filepath)
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, '视频正在后台生成，可在风机控制面板查看进度')
        return {'FINISHED'}


class WFRL_OT_ManualVideoCancel(bpy.types.Operator):
    bl_idname = 'wfrl.manual_video_cancel'
    bl_label = '取消视频生成'

    def execute(self, context):
        try:
            cancel(context.scene)
        except (OSError, ValueError, TimeoutError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


def draw(layout, scene):
    from . import manual_control as control
    box = layout.box()
    box.label(text='T1 Down 视频 · 1080p / 25 fps')
    record = status(scene)
    inputs = box.column()
    inputs.enabled = not record.get('active')
    inputs.prop(scene, 'wfrl_manual_video_hold_s')
    if scene.wfrl_manual_trial.configuration:
        try:
            _start, spec = control.trial_configuration(scene)
            box.label(text=f'{spec.rpm:g} RPM · 变桨 {spec.duration_s:g} s + 保持 {scene.wfrl_manual_video_hold_s:g} s')
        except (ValueError, KeyError, TypeError):
            box.label(text='请重新确认有效试验', icon='ERROR')
    else:
        box.label(text='先确认试验，再导出完整过程')
    inputs.operator('wfrl.manual_video', text='导出 MP4…', icon='RENDER_ANIMATION')
    state = record.get('state')
    if record.get('active'):
        if state == 'QUERYING':
            box.label(text='正在查询视频作业状态…')
        else:
            box.label(text=f"已渲染 {record.get('completed_frames', 0)} / {record.get('total_frames', 0)} 帧")
        box.operator('wfrl.manual_video_cancel')
    elif record.get('final') is True and state == 'COMPLETE':
        box.label(text='视频已完成', icon='CHECKMARK')
        box.operator('wm.path_open', text='打开视频', icon='PLAY').filepath = record['output_mp4']
    elif record.get('final') is True and state == 'FAILED':
        box.label(text='视频生成失败，见输出目录 process.log', icon='ERROR')
    elif record.get('final') is True and state == 'CANCELLED':
        box.label(text='视频生成已取消')
    if state == 'LOST':
        box.label(text='无法联系监管进程；请查询作业目录', icon='ERROR')


CLASSES = (WFRL_OT_ManualVideo, WFRL_OT_ManualVideoCancel)
_HANDLERS = (('undo_post', _restore_job_paths), ('redo_post', _restore_job_paths),
             ('save_pre', _restore_job_paths), ('load_pre', _before_load), ('load_post', _after_load))


def _restore_registered_jobs():
    if not hasattr(bpy.data, 'scenes'):
        return .1
    _after_load(None)
    return None


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.wfrl_manual_video_hold_s = bpy.props.FloatProperty(
        name='目标桨距保持 s', default=7.0, min=0.0, soft_max=60.0, precision=2)
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)
    if hasattr(bpy.data, 'scenes'):
        _after_load(None)
    elif not bpy.app.timers.is_registered(_restore_registered_jobs):
        bpy.app.timers.register(_restore_registered_jobs, first_interval=.1)
    if _JOBS and not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=.5, persistent=True)


def unregister():
    # Jobs own saved snapshots and can finish independently after add-on unload.
    _QUERIES.clear()
    _RESOLVING.clear()
    _DISPLAYED.clear()
    if bpy.app.timers.is_registered(_restore_registered_jobs):
        bpy.app.timers.unregister(_restore_registered_jobs)
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    if hasattr(bpy.types.Scene, 'wfrl_manual_video_hold_s'):
        del bpy.types.Scene.wfrl_manual_video_hold_s
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
