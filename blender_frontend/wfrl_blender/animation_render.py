"""Owned Blender render processes with bounded polling and nonblocking cancellation."""
import atexit
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def png_color_depth(value):
    """PNG jobs retain 8-bit sources and use 16 bits for higher precision."""
    return '8' if str(value) == '8' else '16'


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
                             encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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


class RenderJob:
    def __init__(self):
        self.process = None
        self.directory = None
        self.state = 'IDLE'
        self.completed_frames = self.total_frames = 0
        self.error = ''
        self.message = '后台渲染就绪'
        self._cancel_at = None
        self._record = {}

    @property
    def active(self):
        return self.process is not None

    def start(self, directory, *, blender, addon_root, scene_name,
              frame_start, frame_end, frame_step, snapshot_writer, render_settings=None):
        self.poll()
        if self.active:
            raise ValueError('已有动画正在后台渲染，请等待完成或取消')
        frames = (frame_start, frame_end, frame_step)
        if any(type(value) is not int for value in frames) or frame_step < 1 or frame_end < frame_start:
            raise ValueError('无效的动画帧范围或步长')
        blender, addon_root = Path(blender).resolve(), Path(addon_root).resolve()
        if not blender.is_file() or not os.access(blender, os.X_OK):
            raise ValueError('Blender 可执行文件不存在')
        worker = addon_root / 'animation_render_worker.py'
        if not worker.is_file() or not (addon_root / '__init__.py').is_file():
            raise ValueError('当前扩展缺少后台动画渲染入口')
        base = Path(directory).expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex
        self.directory = base / ('animation-' + job_id[:12])
        self.directory.mkdir(mode=0o700)
        self.completed_frames = 0
        self.total_frames = len(range(frame_start, frame_end + 1, frame_step))
        self._cancel_at = None
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
            self._finish('FAILED', str(exc))
            raise
        return self.directory

    def _read_progress(self):
        try:
            record = json.loads((self.directory / 'manifest.json').read_text(encoding='utf-8'))
            count = record['completed_frames']
            if (record.get('job_id') != self._record['job_id']
                    or record.get('total_frames') != self.total_frames
                    or type(count) is not int or not 0 <= count <= self.total_frames):
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
        progress_error = self._read_progress()
        code = self.process.poll()
        if code is None:
            if self._cancel_at is not None:
                if time.monotonic() - self._cancel_at >= 2:
                    self.process.kill()
                self.state, self.message = 'CANCELLING', '正在取消后台渲染…'
            else:
                self.message = f'后台渲染 {self.completed_frames}/{self.total_frames} 帧'
            return
        self.process = None
        progress_error = self._read_progress()
        complete = (not progress_error and self._record.get('state') == 'COMPLETE'
                    and self.completed_frames == self.total_frames)
        if code == 0 and complete:
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
        if self.active and self._cancel_at is None:
            self._cancel_at = time.monotonic()
            self.state, self.message = 'CANCELLING', '正在取消后台渲染…'
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass

    def close(self):
        self.cancel()
        if self.active:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            self.poll()


job = RenderJob()
atexit.register(job.close)
