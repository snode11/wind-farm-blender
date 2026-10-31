"""Scene-owned radar lifecycle and deterministic simulation/replay sampling."""
import math
from pathlib import Path
import time
import uuid
import numpy as np
from .core import schema, simulation, ply, recording, channel, acquisition
from . import view

_SESSIONS = {}
_RESTORE_ATTEMPTED = set()
_RESET_HANDLERS = ('load_pre', 'undo_pre', 'redo_pre')


def session_for(scene):
    if scene is None:
        return None
    session = _SESSIONS.get(scene.as_pointer())
    try:
        return session if session is not None and session.scene == scene else None
    except ReferenceError:
        return None


class Session:
    def __init__(self, scene, *, restoring=False):
        from .. import farm_flex
        from .scene import SceneSampler, installation_matrix
        self.scene, self.mode = scene, scene.wfrl_x100_source
        self.key, self.frame, self.gpu_batch, self.client = None, None, None, None
        self.mount, self.fov, self.fanout = None, None, None
        self.recording_stopping = self.closing = False
        self.fov_gpu_batch = None
        self.render_color = None
        self.display_valid, self.record_start, self.record_last = False, None, None
        self.source_session = str(uuid.uuid4())
        self.replay_cursor = None
        self.owner = scene.get('wfrl_x100_owner') or str(uuid.uuid4())
        scene['wfrl_x100_owner'] = self.owner
        if not restoring:
            view.remove_objects(scene)
        if self.mode == 'SIMULATION':
            flex = farm_flex.active_for(scene)
            if flex is None or not farm_flex.is_active(scene):
                raise ValueError('几何仿真需要本场景已加载的风场柔性回放')
            self.sampler = SceneSampler(flex, mount=scene.wfrl_x100_mount,
                mount_from_sensor=installation_matrix(scene.wfrl_x100_location, scene.wfrl_x100_rotation))
            t = max(self.sampler.interval[0], min(self.sampler.interval[1], float(scene.get('wfrl_clearance_time_s', 0))))
            if restoring:
                self.mount, self.fov = view.existing_installation(scene, self.owner)
            else:
                self.mount, self.fov = view.install(scene, self.owner,
                    self.sampler.mount_world_at(t) @ self.sampler.mount_from_sensor, show_fov=scene.wfrl_x100_fov)
                from mathutils import Matrix
                yaw = scene.objects['WFRL.Turbine.T1.YawRoot']
                self.mount.parent = yaw if scene.wfrl_x100_mount == 'NACELLE' else (
                    yaw.parent if scene.wfrl_x100_mount == 'TOWER_BASE' else None)
                self.mount.matrix_parent_inverse = Matrix.Identity(4)
                self.mount.matrix_basis = Matrix(self.sampler.mount_from_sensor.tolist())
            self.fov_shown = scene.wfrl_x100_fov
            self.fov_hidden = ({layer.as_pointer(): (layer, False) for layer in scene.view_layers}
                               if not self.fov_shown else {})
        elif self.mode == 'REPLAY':
            import bpy
            path = Path(bpy.path.abspath(scene.wfrl_x100_path)).expanduser()
            self.replay = recording.Replay(path) if path.is_dir() else None
            self.static_frame = None if self.replay else ply.ply_frame(path, sensor_id='X100',
                session_id=self.source_session, sequence=0)
        else:
            self.client = channel.LocalClient(scene.wfrl_x100_port, stale_after_s=scene.wfrl_x100_stale_s)
        scene['wfrl_x100_error'] = ''

    def _display(self, frame):
        import bpy
        self.frame = frame
        self.display_valid = frame is not None and frame.completeness == 'COMPLETE'
        self.gpu_batch = view.batch(frame, self.scene.wfrl_x100_color) if self.display_valid and not bpy.app.background else None
        self.render_color = self.scene.wfrl_x100_color
        if self.fanout and not self.recording_stopping and frame is not None and frame.identity != self.record_last:
            source_time = frame.time.source_time_s
            # Unknown physical time uses a separate local recording clock.
            stamp = time.monotonic() if self.mode == 'LIVE' or source_time is None else source_time
            if self.record_start is None:
                self.record_start = stamp
            self.fanout.submit(frame, stamp - self.record_start)
            self.record_last = frame.identity

    def update(self):
        from .. import farm_flex
        scene = self.scene
        scene['wfrl_x100_error'] = ''
        if self.mode != scene.wfrl_x100_source:
            raise ValueError('来源配置已改变，请应用配置')
        if self.mode == 'SIMULATION':
            if farm_flex.active_for(scene) is not self.sampler.flex:
                raise ValueError('风场几何来源已改变，请应用 X100 配置')
            self.sampler.validate()
            yaw = scene.objects.get('WFRL.Turbine.T1.YawRoot')
            expected_parent = yaw if self.sampler.mount == 'NACELLE' else (
                yaw.parent if self.sampler.mount == 'TOWER_BASE' else None)
            actual = np.array(self.mount.matrix_parent_inverse) @ np.array(self.mount.matrix_basis)
            if self.mount.parent != expected_parent or not np.allclose(actual, self.sampler.mount_from_sensor, atol=1e-5, rtol=0):
                raise ValueError('X100 实体安装被原生编辑；请重新应用标定配置')
            from .. import custom_camera_capture
            if custom_camera_capture.active() and custom_camera_capture._ACTIVE.scene == scene:
                scene['wfrl_x100_status'] = '几何仿真 · 相机采集期间保留采集前的观测帧'
                return
            t = float(scene.get('wfrl_clearance_time_s', self.sampler.interval[0]))
            # Radar samples use a declared 30 Hz source grid independent of drawing cadence.
            sequence = max(0, int(math.floor((t - self.sampler.interval[0]) * 30 + 1e-7)))
            source_t = min(self.sampler.interval[1], self.sampler.interval[0] + sequence / 30)
            key = sequence
            if key != self.key:
                from .scene import bvh_visibility
                frame = simulation.simulate(self.sampler.sample, source_t, sensor_id='X100-T1',
                    session_id=self.source_session, sequence=sequence, interval_s=self.sampler.interval,
                    candidate_budget=scene.wfrl_x100_candidates, visibility=bvh_visibility)
                frame.provenance['calibration_id'] = self.sampler.calibration_id
                self._display(frame)
                self.key = key
            self._update_fov_visibility()
            scene['wfrl_x100_status'] = '几何仿真 · 30 Hz 瞬时采样假设'
        elif self.mode == 'REPLAY':
            if getattr(self, 'playing', False) and self.replay:
                self.replay_cursor = None
                self._advancing_replay = True
                try:
                    scene.wfrl_x100_replay_s = min(self.replay.duration_s, time.monotonic() - self.play_anchor)
                finally:
                    self._advancing_replay = False
                if scene.wfrl_x100_replay_s >= self.replay.duration_s:
                    self.playing = False
            index = self.selected_replay_index() if self.replay else 0
            key = index
            if self.key != key:
                self._display(self.replay.frame(index) if self.replay and index >= 0 else self.static_frame)
                self.key = key
            scene['wfrl_x100_status'] = ('文件回放 · 独立时间轴 · 未与风机同步' if self.replay
                                         else '单帧 PLY · 时间与坐标轴未确认')
        else:
            self.client.poll()
            state = self.client.state
            selected = scene.wfrl_x100_stream
            status = state.status(selected)
            state.take(selected)
            stream = state.streams.get(selected)
            candidate = stream.frame if stream else None
            key = (selected, candidate.identity if candidate else None, status)
            if key != self.key:
                self._display(candidate if status in {'VALID', 'VALID_EMPTY'} else None)
                self.key = key
            self.display_valid = status in {'VALID', 'VALID_EMPTY'} and self.frame is not None
            scene['wfrl_x100_status'] = '本地实时通道 · ' + status
            if not self.display_valid:
                self.gpu_batch = None
            if self.client.error:
                scene['wfrl_x100_error'] = self.client.error
        if self.frame is not None and self.render_color != scene.wfrl_x100_color:
            self._display(self.frame)

    def _update_fov_visibility(self):
        show = self.scene.wfrl_x100_fov
        layers = {layer.as_pointer(): layer for layer in self.scene.view_layers}
        if show and not self.fov_shown:
            for key, (layer, hidden) in self.fov_hidden.items():
                if key in layers and self.fov.hide_get(view_layer=layers[key]):
                    self.fov.hide_set(hidden, view_layer=layers[key])
            self.fov_hidden.clear()
        elif not show:
            for key, layer in layers.items():
                if key not in self.fov_hidden:
                    hidden = self.fov.hide_get(view_layer=layer)
                    self.fov_hidden[key] = layer, hidden
                    if not hidden:
                        self.fov.hide_set(True, view_layer=layer)
        self.fov_shown = show

    def selected_replay_index(self):
        return (self.replay.index_at(self.scene.wfrl_x100_replay_s)
                if self.replay_cursor is None else self.replay_cursor)

    def seek_frame(self, index):
        self.playing = False
        self.scene.wfrl_x100_replay_s = self.replay.times[index]
        self.replay_cursor = index
        self.update()

    def start_recording(self, path):
        if self.closing:
            raise ValueError('关闭尚未确认，请先重试关闭')
        if self.fanout is not None:
            raise ValueError('记录已启动')
        if self.mode == 'LIVE':
            raise ValueError('实时完整记录由外部采集端执行；Blender 最新帧不作为完整采集记录')
        self.record_start = self.record_last = None
        self.fanout = acquisition.Fanout(recording.Recorder(path, description=dict(
            source_mode=self.mode, timing='source time when available; otherwise local recording monotonic')))
        if self.frame is not None:
            self._display(self.frame)

    def stop_recording(self):
        if self.fanout:
            fanout = self.fanout
            self.recording_stopping = True
            fanout.close()
            self.fanout = None
            self.recording_stopping = False
            if fanout.recorder.state == 'FAILED':
                raise RuntimeError(fanout.recorder.error)

    def close(self, *, remove=True):
        self.closing = True
        if self.client:
            self.client.close()
        try:
            self.stop_recording()
        finally:
            self.frame, self.gpu_batch, self.fov_gpu_batch, self.display_valid = None, None, None, False
            if remove:
                try:
                    view.remove_objects(self.scene)
                except ReferenceError:
                    pass


def _guard_installation():
    from .. import custom_camera_capture
    if custom_camera_capture.active():
        raise ValueError('请先结束或取消相机采集，再应用或关闭 X100 安装')


def stop(scene, *, remove=True):
    _guard_installation()
    key = scene.as_pointer()
    session = _SESSIONS.get(key)
    if session:
        try:
            session.close(remove=remove)
        except Exception as exc:
            scene['wfrl_x100_status'] = '关闭未完成，请重试关闭'
            scene['wfrl_x100_error'] = str(exc)
            raise
        _SESSIONS.pop(key, None)
    elif remove:
        view.remove_objects(scene)
    scene.wfrl_x100_enabled = False
    scene['wfrl_x100_status'] = '已关闭'


def start(scene, *, restoring=False):
    _guard_installation()
    stop(scene, remove=not restoring)
    session = None
    try:
        session = Session(scene, restoring=restoring)
        session.update()
        _SESSIONS[scene.as_pointer()] = session
        scene.wfrl_x100_enabled = True
        _RESTORE_ATTEMPTED.add(scene.as_pointer())
        return session
    except Exception:
        if session:
            session.close(remove=not restoring)
        elif not restoring:
            view.remove_objects(scene)
        raise


def tick():
    import bpy
    alive = {scene.as_pointer(): scene for scene in bpy.data.scenes}
    for key, session in list(_SESSIONS.items()):
        if key not in alive:
            worker = getattr(session.fanout, 'worker', None)
            if session.closing and worker is not None and worker.is_alive():
                continue
            try:
                session.close(remove=False)
                _SESSIONS.pop(key, None)
                _RESTORE_ATTEMPTED.discard(key)
            except Exception as exc:
                print('X100 deleted scene record close failed:', str(exc))
            finally:
                view.remove_orphans(session.owner)
    for key, scene in alive.items():
        if key not in _SESSIONS and scene.wfrl_x100_enabled and key not in _RESTORE_ATTEMPTED:
            _RESTORE_ATTEMPTED.add(key)
            try:
                start(scene, restoring=True)
            except Exception as exc:
                scene['wfrl_x100_error'] = '恢复失败：' + str(exc)
        session = _SESSIONS.get(key)
        if session is None:
            continue
        if session.closing:
            continue
        try:
            session.update()
            if session.fanout and session.fanout.recorder.state == 'FAILED':
                scene['wfrl_x100_error'] = '记录失败：' + str(session.fanout.recorder.error)
        except Exception as exc:
            session._display(None)
            session.key = None
            scene['wfrl_x100_error'] = str(exc)
            if session.fanout:
                session.fanout.failure = 'Radar source failed: ' + str(exc)
                session.fanout.stopping.set()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D' and session_for(window.scene) is not None:
                area.tag_redraw()
    return 1 / 30


def on_load_pre(_unused):
    from . import output
    output.close_all()
    for key, session in list(_SESSIONS.items()):
        try:
            session.close(remove=False)
            _SESSIONS.pop(key, None)
        except Exception as exc:
            print('X100 record close failed:', str(exc))
    _RESTORE_ATTEMPTED.clear()


def register():
    import bpy
    from bpy.app.handlers import persistent
    from ..panels import radar_x100
    from . import output
    radar_x100.register_properties()
    handler = persistent(on_load_pre)
    for name in _RESET_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)
    loaded = persistent(output.on_load_post)
    if loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(loaded)
    bpy.app.timers.register(tick, persistent=True)
    view.register()


def unregister():
    import bpy
    from ..panels import radar_x100
    from . import output
    output.close_all()
    errors = []
    try:
        for key, session in list(_SESSIONS.items()):
            try:
                session.close()
                _SESSIONS.pop(key, None)
            except Exception as exc:
                errors.append(exc)
                print('X100 cleanup failed:', str(exc))
    finally:
        _RESTORE_ATTEMPTED.clear()
        if bpy.app.timers.is_registered(tick):
            bpy.app.timers.unregister(tick)
        for name in _RESET_HANDLERS:
            handlers = getattr(bpy.app.handlers, name)
            while on_load_pre in handlers:
                handlers.remove(on_load_pre)
        while output.on_load_post in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(output.on_load_post)
        view.unregister()
        radar_x100.unregister_properties()
    if errors:
        raise RuntimeError('X100 close unconfirmed: ' + '; '.join(map(str, errors)))
