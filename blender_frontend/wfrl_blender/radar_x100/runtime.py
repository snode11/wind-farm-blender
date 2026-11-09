"""Scene-owned radar lifecycle and deterministic simulation/replay sampling."""
import math
from pathlib import Path
import time
import uuid
import numpy as np
from .core import schema, simulation, ply, recording, channel, acquisition
from . import clearance, configuration, display, view
from . import observations, mount_presets, session_recording, estimation

_SESSIONS = {}
_DRAWN = {}
_RESTORE_ATTEMPTED = set()
_RESET_HANDLERS = ('load_pre', 'undo_pre', 'redo_pre')
_SHUTTING_DOWN = False


def session_for(scene):
    if scene is None:
        return None
    session = _SESSIONS.get(scene.as_pointer())
    try:
        return session if session is not None and session.scene == scene else None
    except ReferenceError:
        return None


def _simulation_time(scene, sampler):
    return observations._simulation_time(scene, sampler)


def _simulation_frame(scene, sampler, source_session, sequence, source_t, *, observation_mode='GEOMETRY'):
    return observations._simulation_frame(scene, sampler, source_session, sequence, source_t, observation_mode=observation_mode)


def _validate_mount_drivers(mount):
    return mount_presets._validate_mount_drivers(mount)


class Session:
    def __init__(self, scene, *, restoring=False):
        from .. import farm_flex
        from .scene import SceneSampler, installation_matrix
        self.scene, self.mode = scene, scene.wfrl_x100_source
        self.observation_mode = scene.wfrl_x100_observation_mode
        self.key, self.frame, self.gpu_batch, self.client = None, None, None, None
        self.mount, self.fov, self.fanout = None, None, None
        self.recording_stopping = self.closing = False
        self.close_remove = True
        self.restore_after_close = False
        self._gpu_required = True
        self.gpu_retry_at = None
        self.fov_gpu_batch = None
        self.render_color = None
        self.display_valid, self.record_start, self.record_last = False, None, None
        self.source_session = str(uuid.uuid4())
        self.generation = str(uuid.uuid4())
        self.context = self.analysis_result = self.side_query = None
        self.reference_curves = ()
        self.reference_key = None
        self.reference_source_key = None
        self.reference_source = None
        self.analysis_key = None
        self.analysis_retry_key = None
        self.analysis_retry_at = 0.
        self.analysis_error = ''
        self.render_error = ''
        self.reference_error = ''
        from .core import tracking
        self.blade_tracker = tracking.BladeTracker()
        self.blade_result = None
        self.blade_key = None
        self.blade_retry_at = 0.
        self.blade_error = ''
        self.clearance_tracker = None
        self.clearance_source = None
        self.render_classification = None
        self.playing = False
        self.replay_cursor = None
        self.replay_time_s = float(scene.wfrl_x100_replay_s)
        self.frame_index = self.record_last_index = self.record_last_stamp = None
        self.owner = scene.get('wfrl_x100_owner') or str(uuid.uuid4())
        scene['wfrl_x100_owner'] = self.owner
        if not restoring:
            view.remove_objects(scene)
        if self.mode == 'SIMULATION':
            flex = farm_flex.active_for(scene)
            if flex is None or not farm_flex.is_active(scene):
                raise ValueError('仿真需要本场景已加载的风场柔性回放')
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
            self.replay_path = path
            self.replay = recording.Replay(path) if path.is_dir() else None
            if self.replay is None and scene.wfrl_x100_ply_source == 'UNDECLARED':
                raise ValueError('请选择单帧 PLY 的来源；文件本身不含来源信息')
            self.static_frame = None if self.replay else ply.ply_frame(path, sensor_id='X100',
                session_id=self.source_session, sequence=0, source_kind=scene.wfrl_x100_ply_source)
        else:
            self.client = channel.LocalClient(scene.wfrl_x100_port, stale_after_s=scene.wfrl_x100_stale_s)
        self.capture_configuration()
        scene['wfrl_x100_error'] = scene.get('wfrl_x100_record_error', '')

    def capture_configuration(self):
        return observations.capture_configuration(self)

    def pending_configuration(self):
        return observations.pending_configuration(self)

    def pair_context(self, frame):
        return estimation.pair_context(self, frame)

    def prepare_reference(self, requested):
        return estimation.prepare_reference(self, requested)

    def derive_analysis(self, roi):
        return estimation.derive_analysis(self, roi)

    def _display(self, frame, *, required_gpu=False, record=True):
        return observations._display(self, frame, required_gpu=required_gpu, record=record)

    def _record_current(self, frame):
        return session_recording._record_current(self, frame)

    def _record_frame(self, frame):
        return session_recording._record_frame(self, frame)

    def update(self):
        return observations.update(self)

    def _reset_blades(self):
        return estimation._reset_blades(self)

    def derive_blades(self):
        """Optional inference errors leave the original observation and recording intact."""
        return estimation.derive_blades(self)

    def _clearance_frame_key(self, frame):
        return estimation._clearance_frame_key(self, frame)

    def derive_clearance(self):
        """Infer from allowed same-frame inputs, or display a verified recorded result."""
        return estimation.derive_clearance(self)

    def classification(self):
        return estimation.classification(self)

    def retry_display(self):
        """Rebuild only display resources for a still-valid original frame."""
        return observations.retry_display(self)

    def _update_fov_visibility(self):
        return observations._update_fov_visibility(self)

    def selected_replay_index(self):
        return session_recording.selected_replay_index(self)

    def _set_replay_position(self, time_s, *, index=None):
        return session_recording._set_replay_position(self, time_s, index=index)

    def toggle_playback(self):
        return session_recording.toggle_playback(self)

    def seek_frame(self, index):
        return session_recording.seek_frame(self, index)

    def start_recording(self, path):
        return session_recording.start_recording(self, path)

    def recording_state(self):
        return session_recording.recording_state(self)

    def recording_health(self):
        return session_recording.recording_health(self)

    def stop_recording(self):
        return session_recording.stop_recording(self)

    def close(self, *, remove=True):
        self.closing = True
        self.close_remove = remove
        if self.client:
            self.client.close()
        try:
            return self.stop_recording()
        finally:
            self.frame, self.gpu_batch, self.fov_gpu_batch, self.display_valid = None, None, None, False
            self.context = self.analysis_result = self.side_query = None
            self._reset_blades()
            if remove:
                try:
                    view.remove_objects(self.scene)
                except ReferenceError:
                    pass


def _guard_installation():
    return mount_presets._guard_installation()


def apply_mount_preset(scene, preset, *, search_report=None):
    """Switch one sensor's calibration and resample at the existing scene time."""
    return mount_presets.apply_mount_preset(scene, preset, search_report=search_report)


def stop(scene, *, remove=True):
    _guard_installation()
    key = scene.as_pointer()
    session = _SESSIONS.get(key)
    if session:
        try:
            if not session.close(remove=remove):
                scene['wfrl_x100_status'] = '正在关闭，等待记录写盘结束'
                return False
        except Exception as exc:
            scene['wfrl_x100_status'] = '关闭未完成，请重试关闭'
            scene['wfrl_x100_error'] = str(exc)
            raise
        _release_session(key, scene)
    elif remove:
        view.remove_objects(scene)
    scene.wfrl_x100_enabled = False
    scene['wfrl_x100_status'] = '已关闭'
    return True


def _release_session(key, scene=None):
    _SESSIONS.pop(key, None)
    _RESTORE_ATTEMPTED.discard(key)
    if scene is not None:
        try:
            if hasattr(scene, 'wfrl_x100_enabled'):
                scene.wfrl_x100_enabled = False
            scene['wfrl_x100_status'] = '已关闭'
        except ReferenceError:
            pass


def start(scene, *, restoring=False):
    _guard_installation()
    if not stop(scene, remove=not restoring):
        raise ValueError('记录关闭中，请等待写盘完成后再应用配置')
    session = None
    try:
        session = Session(scene, restoring=restoring)
        session.update()
        session._gpu_required = False
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
        scene = alive.get(key)
        try:
            if session.scene != scene:
                scene = None
        except ReferenceError:
            scene = None
        if scene is None or session.closing:
            try:
                if session.close(remove=scene is not None and session.close_remove):
                    _release_session(key, None if session.restore_after_close else scene)
            except Exception as exc:
                if scene is not None:
                    scene['wfrl_x100_error'] = str(exc)
                else:
                    print('X100 record close failed:', str(exc))
            finally:
                if scene is None:
                    view.remove_orphans(session.owner)
    if _SHUTTING_DOWN:
        return 1 / 30 if _SESSIONS else None
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
        if session.recording_stopping:
            try:
                session.stop_recording()
            except Exception as exc:
                scene['wfrl_x100_record_error'] = '记录失败：' + str(exc)
        try:
            session.update()
            session.recording_health()
            if session.fanout and session.fanout.recorder.state == 'FAILED':
                scene['wfrl_x100_error'] = '记录失败：' + str(session.fanout.recorder.error)
        except Exception as exc:
            session._display(None)
            session.key = None
            scene['wfrl_x100_error'] = str(exc)
            if session.fanout:
                session.fanout.failure = 'Radar source failed: ' + str(exc)
                session.fanout.stopping.set()
    _redraw_changed_overlays(alive)
    return 1 / 30


def _overlay_state(scene):
    session = _SESSIONS.get(scene.as_pointer())
    if session is None or session.closing:
        return None
    return (session, session.frame, session.gpu_batch, session.display_valid, scene.wfrl_x100_fov,
            getattr(session, 'blade_result', None), getattr(session, 'blade_error', ''))


def _redraw_changed_overlays(alive):
    """Tag 3D views only when the scene's drawn radar overlay inputs changed."""
    changed = set()
    for key in set(_DRAWN) | set(_SESSIONS):
        scene = alive.get(key)
        state = None if scene is None else _overlay_state(scene)
        previous = _DRAWN.get(key)
        # Identity comparison: frames are dataclasses whose equality would compare arrays.
        if (state is None) != (previous is None) or (
                state is not None and any(a is not b for a, b in zip(state, previous))):
            changed.add(key)
        if state is None:
            _DRAWN.pop(key, None)
        else:
            _DRAWN[key] = state
    if not changed:
        return
    from .. import viewport
    viewport.tag_redraw(scene for key, scene in alive.items() if key in changed)


def on_load_pre(_unused):
    from . import output
    output.close_all()
    for key, session in list(_SESSIONS.items()):
        try:
            session.restore_after_close = True
            if session.close(remove=False):
                _SESSIONS.pop(key, None)
        except Exception as exc:
            print('X100 record close failed:', str(exc))
    _RESTORE_ATTEMPTED.clear()
    _DRAWN.clear()


def register():
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = False
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
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, persistent=True)
    view.register()


def unregister():
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    import bpy
    from ..panels import radar_x100
    from . import output
    output.close_all()
    errors = []
    try:
        for key, session in list(_SESSIONS.items()):
            try:
                session.restore_after_close = False
                if session.close():
                    _release_session(key, session.scene)
            except Exception as exc:
                errors.append(exc)
                print('X100 cleanup failed:', str(exc))
    finally:
        _RESTORE_ATTEMPTED.clear()
        _DRAWN.clear()
        if not _SESSIONS and bpy.app.timers.is_registered(tick):
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


def status_for(scene):
    from ..home_state import TaskStatus
    session = session_for(scene)
    if session is None:
        return TaskStatus('x100', error=scene.get('wfrl_x100_error', ''))
    stage = 'CLOSING' if session.closing else 'CANCELLING' if session.recording_stopping else session.mode
    progress = '' if session.frame is None else f'原帧 #{session.frame.sequence}'
    return TaskStatus('x100', True, True, stage, scene.name,
                      session.render_error or scene.get('wfrl_x100_error', ''), progress)
