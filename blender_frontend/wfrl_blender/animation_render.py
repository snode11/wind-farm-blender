"""Owned Blender render processes with bounded polling and nonblocking cancellation."""
import atexit
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from .atomic_json import write_json


def _pid_identity(pid):
    """Return process creation identity, '' for exited, or None if inaccessible."""
    if type(pid) is not int or pid <= 0:
        return ''
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00101000, False, pid)
        if not handle:
            return '' if ctypes.get_last_error() == 87 else None
        try:
            if kernel.WaitForSingleObject(handle, 0) == 0:
                return ''
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                return None
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return ''
    except PermissionError:
        return None
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            env=dict(os.environ, LC_ALL='C'), timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode in {0, 1} else None


def _pid_alive(pid, identity=None):
    """Query the recorded process without signalling or adopting its PID."""
    current = _pid_identity(pid)
    return current != '' and (identity is None or current is None or current == identity)


def png_color_depth(value):
    """PNG jobs retain 8-bit sources and use 16 bits for higher precision."""
    return '8' if str(value) == '8' else '16'


def validate_output_nodes(scene):
    """PNG jobs own their files; compositor side outputs require explicit disabling."""
    if not scene.render.use_compositing:
        return
    seen = set()

    def visit(tree):
        if tree is None:
            return
        key = tree.as_pointer() if hasattr(tree, 'as_pointer') else id(tree)
        if key in seen:
            return
        seen.add(key)
        for node in tree.nodes:
            if node.mute:
                continue
            if node.type == 'OUTPUT_FILE':
                raise ValueError('场景含启用的合成器文件输出节点；请先禁用这些节点，再使用后台 PNG 渲染')
            if node.type == 'GROUP':
                visit(node.node_tree)

    visit(scene.compositing_node_group)


def png_output_views(scene):
    """Return the PNG packing mode and any individually written views."""
    render = scene.render
    if not render.use_multiview:
        return 'INDIVIDUAL', ()
    stereo = render.views_format == 'STEREO_3D'
    views = tuple(view for view in render.views
                  if view.use and (not stereo or view.name in {'left', 'right'}))
    if not views:
        raise ValueError('多视图渲染至少需要一个启用的视图')
    if render.image_settings.views_format == 'STEREO_3D':
        if not stereo or {view.name for view in views} != {'left', 'right'}:
            raise ValueError('合成立体 PNG 需要启用左右双眼的立体渲染')
        return 'STEREO_3D', ()
    suffixes = [view.file_suffix for view in views]
    if (len({suffix.casefold() for suffix in suffixes}) != len(suffixes)
            or any('/' in suffix or '\\' in suffix for suffix in suffixes)):
        raise ValueError('每个启用的渲染视图需要唯一的文件名后缀，且后缀不能含路径分隔符')
    return 'INDIVIDUAL', tuple(view.name for view in views)


class RenderJob:
    def __init__(self):
        self.process = None
        self.directory = None
        self.state = 'IDLE'
        self.completed_frames = self.total_frames = 0
        self.error = ''
        self.message = '后台渲染就绪'
        self._cancel_at = None
        self._start_error = ''
        self._recovered_pid = None
        self._record = {}

    @property
    def active(self):
        return self.process is not None or self._recovered_pid is not None

    def recover(self, directory):
        """Restore a saved result or wait for an interrupted worker to exit."""
        self.poll()
        if self.active or self.state == 'STARTING':
            return False
        directory = Path(directory).expanduser().resolve()
        try:
            record = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            job_record = json.loads((directory / 'job.json').read_text(encoding='utf-8'))
            if not isinstance(record, dict) or not isinstance(job_record, dict):
                return False
            count, total = record['completed_frames'], record['total_frames']
            state = record['state']
            if (record.get('schema') != 'wfrl.animation-render.v1'
                    or not record.get('job_id') or record['job_id'] != job_record.get('job_id')
                    or type(count) is not int or type(total) is not int or not 0 <= count <= total
                    or total < 1 or total != job_record.get('total_frames')
                    or not isinstance(state, str)
                    or (state == 'COMPLETE' and count != total)
                    or not isinstance(record.get('error') or '', str)
                    or state not in {'COMPLETE', 'FAILED', 'CANCELLED', 'FAILING', 'RENDERED', 'RUNNING'}):
                return False
            if state == 'RUNNING' and not record.get('worker_pid'):
                return False
        except (OSError, ValueError, KeyError, TypeError):
            return False
        self.directory, self._record = directory, record
        self.completed_frames, self.total_frames = count, total
        self._cancel_at, self._start_error = None, ''
        if state in {'COMPLETE', 'FAILED', 'CANCELLED'}:
            self._finish(state, record.get('error') or '')
        elif _pid_alive(record.get('worker_pid'), record.get('worker_identity')):
            self._recovered_pid = record['worker_pid']
            self.state, self.message = 'RECOVERING', '等待先前后台渲染进程退出…'
            self.error = record.get('error') or ''
        else:
            self._finish('FAILED', record.get('error') or '后台渲染控制已中断，无法确认完整结果')
        return True

    def start(self, directory, *, blender, addon_root, scene_name,
              frame_start, frame_end, frame_step, snapshot_writer, render_settings=None):
        self.poll()
        if self.active or self.state == 'STARTING':
            raise ValueError('已有动画正在后台渲染，请等待完成或取消')
        frames = (frame_start, frame_end, frame_step)
        if any(type(value) is not int for value in frames) or frame_step < 1 or frame_end < frame_start:
            raise ValueError('无效的动画帧范围或步长')
        total_frames = len(range(frame_start, frame_end + 1, frame_step))
        blender, addon_root = Path(blender).resolve(), Path(addon_root).resolve()
        if not blender.is_file() or not os.access(blender, os.X_OK):
            raise ValueError('Blender 可执行文件不存在')
        worker = addon_root / 'animation_render_worker.py'
        if not worker.is_file() or not (addon_root / '__init__.py').is_file():
            raise ValueError('当前扩展缺少后台动画渲染入口')
        base = Path(directory).expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex
        directory = base / ('animation-' + job_id[:12])
        directory.mkdir(mode=0o700)
        self.directory = directory
        self.completed_frames = 0
        self.total_frames = total_frames
        self._cancel_at = None
        self._start_error = ''
        self.error = ''
        self.state, self.message = 'STARTING', '正在保存独立渲染场景…'
        self._record = dict(schema='wfrl.animation-render.v1', job_id=job_id,
                            state=self.state, completed_frames=0, total_frames=self.total_frames)
        try:
            snapshot = self.directory / 'scene.blend'
            snapshot_writer(snapshot)
            if not snapshot.is_file():
                raise OSError('未生成独立渲染场景')
            job = dict(self._record, snapshot=str(snapshot), addon_root=str(addon_root),
                       scene_name=scene_name, frame_start=frame_start,
                       frame_end=frame_end, frame_step=frame_step,
                       render_settings=render_settings or {})
            job_path = self.directory / 'job.json'
            write_json(job_path, job)
            write_json(self.directory / 'manifest.json', self._record)
            command = [str(blender), '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                       '--python', str(worker), '--', str(job_path)]
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith('BLENDER_USER_') and key not in {
                               'PYTHONPATH', 'WFRL_ADDON_ROOT', 'WFRL_ADDON_PATH',
                               'WFRL_ADDON_MODULE', 'WFRL_TEST_OUTPUT'}}
            for key, name in (('BLENDER_USER_CONFIG', 'config'), ('BLENDER_USER_SCRIPTS', 'scripts'),
                              ('BLENDER_USER_DATAFILES', 'datafiles')):
                profile = self.directory / 'profile' / name
                profile.mkdir(parents=True)
                environment[key] = str(profile)
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            with (self.directory / 'render.log').open('wb') as log:
                self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, env=environment, **options)
            self.state, self.message = 'RUNNING', '后台渲染启动中…'
        except Exception as exc:
            if self.active:
                self._start_error = str(exc)
                self.cancel()
                if self.active:
                    self.message = '后台渲染启动失败，正在停止进程…'
            else:
                self._finish('FAILED', str(exc))
            raise
        return self.directory

    def _read_progress(self):
        try:
            record = json.loads((self.directory / 'manifest.json').read_text(encoding='utf-8'))
            if not isinstance(record, dict):
                raise ValueError('渲染状态必须是对象')
            count = record['completed_frames']
            state = record.get('state')
            if (record.get('job_id') != self._record['job_id']
                    or record.get('total_frames') != self.total_frames
                    or type(count) is not int or not 0 <= count <= self.total_frames
                    or not isinstance(state, str)
                    or state not in {'STARTING', 'RUNNING', 'RENDERED', 'FAILING', 'COMPLETE', 'FAILED', 'CANCELLED'}
                    or (state == 'COMPLETE' and count != self.total_frames)
                    or not isinstance(record.get('error') or '', str)):
                raise ValueError('渲染状态与当前作业不匹配')
            self._record = record
            self.completed_frames = count
            return ''
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return str(exc)

    def _log_tail(self):
        try:
            with (self.directory / 'render.log').open('rb') as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 2000))
                return stream.read().decode('utf-8', errors='replace').strip()
        except OSError:
            return ''

    def poll(self):
        if not self.active:
            return
        if self.process is None:
            if _pid_alive(self._recovered_pid, self._record.get('worker_identity')):
                return
            self._recovered_pid = None
            progress_error = self._read_progress()
            state = self._record.get('state')
            if not progress_error and state in {'COMPLETE', 'FAILED', 'CANCELLED'}:
                self._finish(state, self._record.get('error') or '')
            else:
                self._finish('FAILED', progress_error or self._record.get('error')
                             or '后台渲染控制已中断，无法确认完整结果')
            return
        progress_error = self._read_progress()
        code = self.process.poll()
        if code is None:
            if self._cancel_at is not None:
                if time.monotonic() - self._cancel_at >= 2:
                    self._signal_stop(force=True)
                self.state, self.message = 'CANCELLING', '正在取消后台渲染…'
            else:
                self.message = f'后台渲染 {self.completed_frames}/{self.total_frames} 帧'
            return
        self.process = None
        progress_error = self._read_progress()
        complete = (not progress_error and self._record.get('state') in {'RENDERED', 'COMPLETE'}
                    and self.completed_frames == self.total_frames)
        if self._start_error:
            self._finish('FAILED', self._start_error)
        elif not progress_error and self._record.get('state') == 'FAILING':
            self._finish('FAILED', self._record.get('error') or '后台渲染失败')
        elif code == 0 and complete:
            self._finish('COMPLETE')
        elif self._cancel_at is not None:
            self._finish('CANCELLED', '用户取消或源场景关闭；已生成帧保留')
        else:
            reason = progress_error or self._record.get('error') or f'渲染进程退出 {code}，完整帧数未确认'
            self._finish('FAILED', reason + '\n' + self._log_tail())

    def _finish(self, state, error=''):
        self.state, self.error = state, error
        self._record.update(state=state, error=error or None,
                            completed_frames=self.completed_frames, total_frames=self.total_frames)
        self.message = {'COMPLETE': '动画渲染完成', 'CANCELLED': '后台渲染已取消',
                        'FAILED': '后台渲染失败'}[state]
        if self.directory is not None:
            try:
                write_json(self.directory / 'manifest.json', self._record)
            except OSError as exc:
                self.state = 'FAILED'
                self.error = f'{error}\n无法写入渲染清单：{exc}'.strip()
                self.message = '渲染清单写入失败'

    def cancel(self):
        self.poll()
        if self.process is not None and self._cancel_at is None:
            self._cancel_at = time.monotonic()
            self.state, self.message = 'CANCELLING', '正在取消后台渲染…'
            self._signal_stop()

    def _cleanup_error(self, exc):
        detail = f'后台渲染进程尚未回收，将重试：{exc}'
        if detail not in self.error:
            self.error = '\n'.join(filter(None, (self.error, detail)))

    def _signal_stop(self, *, force=False):
        try:
            if force:
                self.process.kill()
            else:
                self.process.terminate()
        except ProcessLookupError:
            pass
        except OSError as exc:
            self._cleanup_error(exc)

    def close(self):
        self.cancel()
        if self.process is not None:
            try:
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._signal_stop(force=True)
                    self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._cleanup_error(exc)
            self.poll()


job = RenderJob()
atexit.register(job.close)
