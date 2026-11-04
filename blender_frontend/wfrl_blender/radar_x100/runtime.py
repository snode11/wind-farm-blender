"""Scene-owned radar lifecycle and deterministic simulation/replay sampling."""
import math
from pathlib import Path
import time
import uuid
import numpy as np
from .core import schema, simulation, ply, recording, channel, acquisition
from . import configuration, display, view

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
    t = float(scene.get('wfrl_clearance_time_s', sampler.interval[0]))
    sequence = max(0, int(math.floor((t - sampler.interval[0]) * 30 + 1e-7)))
    return sequence, min(sampler.interval[1], sampler.interval[0] + sequence / 30)


def _simulation_frame(scene, sampler, source_session, sequence, source_t):
    with sampler.sampling_transaction():
        frame = simulation.simulate(sampler.sample, source_t, sensor_id='X100-T1',
            session_id=source_session, sequence=sequence, interval_s=sampler.interval,
            candidate_budget=scene.wfrl_x100_candidates, visibility=sampler.visibility,
            material_at=sampler.material_sample,
            sampling_plan=sampler.sampling_plan(scene.wfrl_x100_candidates))
    frame.provenance['calibration_id'] = sampler.calibration_id
    return frame


def _validate_mount_drivers(mount):
    node = mount
    while node is not None:
        if node.constraints or node.animation_data or node.parent_type != 'OBJECT':
            raise ValueError('X100 安装或父级存在约束或动画驱动；请检查后重新应用标定配置')
        node = node.parent


class Session:
    def __init__(self, scene, *, restoring=False):
        from .. import farm_flex
        from .scene import SceneSampler, installation_matrix
        self.scene, self.mode = scene, scene.wfrl_x100_source
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
        import bpy
        self.applied_config = configuration.capture(self.scene, abspath=bpy.path.abspath,
            single_ply=self.mode == 'REPLAY' and self.static_frame is not None)

    def pending_configuration(self):
        import bpy
        return configuration.pending(self.scene, self.applied_config, abspath=bpy.path.abspath)

    def pair_context(self, frame):
        self.context = self.analysis_result = self.side_query = None
        self.blade_result = self.blade_key = None
        self.blade_retry_at = 0.
        self.blade_error = ''
        self.reference_curves = ()
        self.reference_key = None
        self.analysis_key = None
        self.analysis_retry_key = None
        self.analysis_retry_at = 0.
        self.analysis_error = ''
        self.reference_error = ''
        if frame is None or self.mode != 'SIMULATION':
            self.analysis_error = '无可靠同帧分析上下文'
            return
        identity = (self.generation, self.source_session, frame.identity, frame.source_kind,
            frame.time.clock_domain, frame.time.source_time_s, self.frame_index,
            self.sampler.source_id, self.sampler.geometry_id, self.sampler.calibration_id)
        try:
            self.context = self.sampler.analysis_context(frame, identity)
            self.sampler.latest_sample = None
        except Exception as exc:
            self.analysis_error = str(exc)

    def prepare_reference(self, requested):
        if not requested or self.context is None:
            self.reference_error = ''
            self.reference_curves = ()
            self.reference_key = None
            return
        if self.reference_key == self.context.identity:
            return
        self.reference_key = self.context.identity
        self.reference_curves = ()
        try:
            from .core import analysis
            from .. import farm_flex, prebend
            from ..deflection import interpolated_beamdyn_root_frame
            from ..turbine_geometry import geometry_data
            sampler, flex = self.sampler, self.sampler.flex
            sampler.validate()
            source_key = (id(flex), schema.digest(flex.reference), sampler.source_id,
                          sampler.geometry_id, str(farm_flex.saved_package(self.scene)))
            if source_key != self.reference_source_key:
                source, _ = farm_flex.dual_beam_api().resolve_package(farm_flex.saved_package(self.scene))
                reference = prebend.read_reference(source, flex.manifest)
                if reference is None or schema.digest(reference) != schema.digest(flex.reference):
                    self.reference_source_key = self.reference_source = None
                    raise ValueError('当前来源无核对通过的独立零载荷参考')
                from copy import deepcopy
                self.reference_source = deepcopy(reference)
                self.reference_source_key = source_key
            reference = self.reference_source
            t = self.frame.time.source_time_s
            i = int(np.clip(np.searchsorted(flex.times,t,side='right')-1,0,len(flex.times)-2))
            alpha = (t-flex.times[i])/(flex.times[i+1]-flex.times[i])
            curve = np.array(reference['curve'],float)[:,:3]
            curve[:,2] += geometry_data()['scalars']['HubRad']
            curves=[]
            for b in range(3):
                hub, axes = interpolated_beamdyn_root_frame(geometry_data()['scalars'],b+1,
                    flex.transforms[i,sampler.k,b,0],flex.transforms[i+1,sampler.k,b,0],alpha)
                curves.append(analysis.readonly(curve @ axes.T+hub+sampler.root_world[:3,3]))
            self.reference_curves = tuple(curves)
            self.reference_error = '独立零载荷49点参考曲线，非观测'
        except Exception as exc:
            self.reference_error = str(exc)

    def derive_analysis(self, roi):
        if self.context is None:
            return
        key = self.context.identity, tuple(map(tuple, roi))
        if key == self.analysis_key:
            return
        if (key == self.analysis_retry_key
                and time.monotonic() < self.analysis_retry_at):
            return
        self.analysis_key = None
        self.analysis_result = None
        try:
            from .scene import SideWallQuery
            if self.side_query is None:
                self.side_query = SideWallQuery(self.context)
            self.analysis_result = self.side_query.query(self.frame.xyz, roi, frame_key=self.frame.identity)
            self.analysis_key = key
            self.analysis_retry_key = None
            self.analysis_error = ''
        except Exception as exc:
            self.side_query = None
            self.analysis_retry_key = key
            self.analysis_retry_at = time.monotonic() + .25
            self.analysis_error = str(exc)

    def _display(self, frame, *, required_gpu=False, record=True):
        import bpy
        if frame is not self.frame:
            self.pair_context(frame)
        self.frame = frame
        if frame is None:
            self._reset_blades()
        self.display_valid = frame is not None and frame.completeness == 'COMPLETE'
        style = display.style_key(self.scene)
        try:
            self.gpu_batch = view.batch(frame, style[0], color_scale=style[1], velocity_limit=style[2]) if self.display_valid and not bpy.app.background else None
            self.gpu_retry_at = None
            self.render_error = ''
        except Exception as exc:
            self.gpu_batch = None
            self.gpu_retry_at = time.monotonic() + .25
            self.render_error = str(exc)
            if required_gpu:
                raise
        self.render_color = style
        if record:
            self._record_current(frame)

    def _record_current(self, frame):
        if self.fanout and not self.recording_stopping and frame is not None:
            try:
                self._record_frame(frame)
            except Exception as exc:
                self.scene['wfrl_x100_record_error'] = str(exc)
                self.fanout.failure = str(exc)
                self.fanout.stopping.set()

    def _record_frame(self, frame):
        indexed = self.mode == 'REPLAY' and self.replay is not None
        key = self.frame_index if indexed else frame.identity
        if key == self.record_last:
            return
        if indexed:
            if self.record_last_index is not None and self.frame_index < self.record_last_index:
                self.fanout.failure = 'Replay moved backward during recording; start a new recording'
                self.fanout.stopping.set()
                return
            stamp = self.replay.times[self.frame_index]
        else:
            source_time = frame.time.source_time_s
            # Unknown physical time uses a separate local recording clock.
            stamp = time.monotonic() if self.mode == 'LIVE' or source_time is None else source_time
            # The scene timeline can loop or be scrubbed while recorded times must not decrease.
            if self.record_last_stamp is not None and stamp < self.record_last_stamp:
                self.fanout.failure = '仿真时间在记录期间回退（时间轴循环或回拖）；请开始新的记录'
                self.fanout.stopping.set()
                return
        if self.record_start is None:
            self.record_start = stamp
        self.fanout.submit(frame, stamp - self.record_start)
        self.record_last = key
        self.record_last_index = self.frame_index if indexed else None
        self.record_last_stamp = stamp

    def update(self):
        from .. import farm_flex
        scene = self.scene
        scene['wfrl_x100_error'] = scene.get('wfrl_x100_record_error', '')
        if self.mode != scene.wfrl_x100_source:
            raise ValueError('来源配置已改变，请应用配置')
        if self.mode == 'SIMULATION':
            if farm_flex.active_for(scene) is not self.sampler.flex:
                raise ValueError('风场几何来源已改变，请应用 X100 配置')
            from .. import custom_camera_capture
            capturing = custom_camera_capture.active() and custom_camera_capture._ACTIVE.scene == scene
            sequence, source_t = _simulation_time(scene, self.sampler)
            # A new sampling transaction validates once; frozen frames still validate every tick.
            if capturing or sequence == self.key:
                self.sampler.validate()
            _validate_mount_drivers(self.mount)
            mount, fov = view.existing_installation(scene, self.owner)
            yaw = scene.objects.get('WFRL.Turbine.T1.YawRoot')
            expected_parent = yaw if self.sampler.mount == 'NACELLE' else (
                yaw.parent if self.sampler.mount == 'TOWER_BASE' else None)
            actual = np.array(self.mount.matrix_parent_inverse) @ np.array(self.mount.matrix_basis)
            if (mount != self.mount or fov != self.fov or self.mount.parent != expected_parent
                    or not np.array_equal(np.array(self.mount.matrix_parent_inverse), np.eye(4))
                    or not np.allclose(actual, self.sampler.mount_from_sensor, atol=1e-5, rtol=0)):
                raise ValueError('X100 实体安装被原生编辑；请重新应用标定配置')
            if capturing:
                scene['wfrl_x100_status'] = '几何仿真 · 相机采集期间保留采集前的观测帧'
                return
            # Radar samples use a declared 30 Hz source grid independent of drawing cadence.
            key = sequence
            if key != self.key:
                frame = _simulation_frame(scene, self.sampler, self.source_session, sequence, source_t)
                self._display(frame, required_gpu=self._gpu_required)
                self.key = key
            self._update_fov_visibility()
            scene['wfrl_x100_status'] = '几何仿真 · 30 Hz 瞬时采样假设'
        elif self.mode == 'REPLAY':
            import bpy
            if Path(bpy.path.abspath(scene.wfrl_x100_path)).expanduser() != self.replay_path:
                raise ValueError('回放路径已改变，请应用 X100 配置')
            if self.static_frame is not None and scene.wfrl_x100_ply_source != self.static_frame.source_kind:
                raise ValueError('单帧 PLY 来源声明已改变，请应用 X100 配置')
            if self.playing and self.replay:
                elapsed = time.monotonic() - self.play_anchor
                ended = elapsed >= self.replay.duration_s
                self._set_replay_position(min(self.replay.duration_s, elapsed),
                    index=len(self.replay.rows) - 1 if ended else None)
                if ended:
                    self.playing = False
            index = self.selected_replay_index() if self.replay else 0
            key = index
            if self.key != key:
                self.frame_index = index if self.replay and index >= 0 else None
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
        self.retry_display()
        self.derive_blades()

    def _reset_blades(self):
        self.blade_result = self.blade_key = None
        self.blade_retry_at = 0.
        self.blade_error = ''
        self.blade_tracker.reset()

    def derive_blades(self):
        """Optional inference errors leave the original observation and recording intact."""
        if not getattr(self.scene, 'wfrl_x100_blade_tracking', False):
            self._reset_blades()
            return
        if self.mode != 'SIMULATION' or self.context is None or self.frame is None or not self.display_valid:
            self._reset_blades()
            self.blade_error = '需要同帧塔面、根部姿态和已对齐点云'
            return
        if self.blade_key == self.context.identity or time.monotonic() < self.blade_retry_at:
            return
        self.blade_result = None
        try:
            from .core import analysis
            from .scene import SideWallQuery
            if self.side_query is None:
                self.side_query = SideWallQuery(self.context)
            background = self.side_query.query(self.frame.xyz, ((-1e6,)*3, (1e6,)*3),
                                               frame_key=self.frame.identity)
            world = analysis.transform(self.frame.xyz, self.context.world_from_sensor)
            priors = self.sampler.blade_priors(self.frame.time.source_time_s)
            result = self.blade_tracker.update(world, priors, background['distance'],
                time_s=self.frame.time.source_time_s,
                identity=(self.generation, self.source_session, self.sampler.calibration_id),
                frame_key=self.frame.identity)
            for row in result['blades']:
                row['estimated_clearance_m'] = None
                if row['tip_world'] is not None:
                    point = analysis.local([row['tip_world']], self.context.world_from_sensor)
                    distance = self.side_query.query(point, ((-1e6,)*3, (1e6,)*3))['distance'][0]
                    if np.isfinite(distance):
                        row['estimated_clearance_m'] = float(distance)
            self.blade_result, self.blade_error = result, ''
            self.blade_key = self.context.identity
            self.blade_retry_at = 0.
        except Exception as exc:
            self.blade_tracker.reset()
            self.side_query = None
            self.blade_retry_at = time.monotonic() + .25
            self.blade_error = str(exc)

    def retry_display(self):
        """Rebuild only display resources for a still-valid original frame."""
        import bpy
        if self.frame is not None and self.display_valid and (
                self.render_color != display.style_key(self.scene)
                or (self.gpu_retry_at is not None and not bpy.app.background
                    and time.monotonic() >= self.gpu_retry_at)):
            self._display(self.frame, record=False)

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
        return (self.replay.index_at(self.replay_time_s)
                if self.replay_cursor is None else self.replay_cursor)

    def _set_replay_position(self, time_s, *, index=None):
        self.replay_time_s, self.replay_cursor = time_s, index
        self._advancing_replay = True
        try:
            self.scene.wfrl_x100_replay_s = time_s
        finally:
            self._advancing_replay = False

    def toggle_playback(self):
        if self.playing:
            # Pausing snaps to the elapsed position; a failed update must still pause.
            try:
                self.update()
            finally:
                self.playing = False
            return
        if self.replay_time_s >= self.replay.duration_s:
            self._set_replay_position(self.replay.times[0])
        self.replay_cursor = None
        self.play_anchor = time.monotonic() - self.replay_time_s
        self.playing = True

    def seek_frame(self, index):
        self.playing = False
        self._set_replay_position(self.replay.times[index], index=index)
        self.update()

    def start_recording(self, path):
        if self.closing:
            raise ValueError('关闭尚未确认，请先重试关闭')
        if self.fanout is not None:
            raise ValueError('记录已启动')
        if self.mode == 'LIVE':
            raise ValueError('实时完整记录由外部采集端执行；Blender 最新帧不作为完整采集记录')
        self.record_start = self.record_last = self.record_last_index = self.record_last_stamp = None
        self.fanout = acquisition.Fanout(recording.Recorder(path, description=dict(
            source_mode=self.mode, timing=('original indexed presentation time' if self.mode == 'REPLAY' and self.replay
                else 'source time when available; otherwise local recording monotonic'))))
        self.scene['wfrl_x100_record_error'] = ''
        self.recording_health()
        # The displayed frame is already current; record it without rebuilding display resources.
        self._record_current(self.frame)

    def recording_health(self):
        if self.fanout is not None:
            try:
                if not self.scene.as_pointer():
                    return
            except ReferenceError:
                return
            recorder = self.fanout.recorder
            self.scene['wfrl_x100_record_state'] = recorder.state
            self.scene['wfrl_x100_record_directory'] = str(recorder.path)
            self.scene['wfrl_x100_record_frames'] = recorder.rows
            if recorder.state == 'FAILED':
                self.scene['wfrl_x100_record_error'] = '记录失败：' + str(recorder.error)

    def stop_recording(self):
        if self.fanout:
            fanout = self.fanout
            self.recording_stopping = True
            fanout.request_close()
            self.recording_health()
            if fanout.worker.is_alive():
                return False
            self.recording_health()
            self.fanout = None
            self.recording_stopping = False
            if fanout.recorder.state == 'FAILED':
                raise RuntimeError(fanout.recorder.error)
        return True

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
    from .. import custom_camera_capture
    if custom_camera_capture.active():
        raise ValueError('请先结束或取消相机采集，再应用或关闭 X100 安装')


def apply_mount_preset(scene, preset, *, search_report=None):
    """Switch one sensor's calibration and resample at the existing scene time."""
    import bpy
    from mathutils import Matrix
    from .. import farm_flex
    from .scene import (SceneSampler, ORIGINAL_LOCATION, ORIGINAL_ROTATION,
                        nacelle_side_installation, installation_matrix)
    _guard_installation()
    if scene.wfrl_x100_source != 'SIMULATION':
        raise ValueError('安装位置切换仅用于几何仿真')
    if preset not in {'ORIGINAL', 'NACELLE_SIDE', 'SEARCH'}:
        raise ValueError('未知雷达安装位置：' + str(preset))
    current = session_for(scene)
    if current is not None and (current.fanout or current.closing):
        raise ValueError('请先结束记录或完成关闭，再切换雷达安装位置')
    if current is not None and current.mode != 'SIMULATION':
        raise ValueError('请先应用几何仿真配置，再切换雷达安装位置')
    flex = farm_flex.active_for(scene)
    if flex is None or not farm_flex.is_active(scene):
        raise ValueError('请先加载本场景的风场柔性回放')
    sampler = None
    if current is not None:
        _validate_mount_drivers(current.mount)
        mount, fov = view.existing_installation(scene, current.owner)
        if mount != current.mount or fov != current.fov:
            raise ValueError('雷达安装对象已改变，请重新应用配置')
        if any(len(obj.users_scene) > 1 for obj in scene.objects
               if obj.get('wfrl_x100_owner') == current.owner):
            raise ValueError('雷达安装与其他场景共享，请先应用配置以建立独立安装')
        if current.sampler.flex is flex:
            try:
                current.sampler.validate()
                sampler = current.sampler
            except (ValueError, ReferenceError):
                pass
    if sampler is None:
        sampler = SceneSampler(flex, mount_from_sensor=np.eye(4))
    if preset == 'SEARCH':
        from .core import installation
        location, rotation = installation.selected_installation(search_report, sampler.source_id, sampler.geometry_id)
    elif preset == 'ORIGINAL':
        location, rotation = ORIGINAL_LOCATION, ORIGINAL_ROTATION
    else:
        location, rotation = nacelle_side_installation(sampler)
    location = tuple(float(np.float32(value)) for value in location)
    rotation = tuple(float(np.float32(value)) for value in rotation)
    sampler = sampler.with_installation('NACELLE', installation_matrix(location, rotation))
    yaw = scene.objects['WFRL.Turbine.T1.YawRoot']
    if (current is not None and current.display_valid and current.frame is not None
            and current.sampler.calibration_id == sampler.calibration_id
            and current.sampler.geometry_id == sampler.geometry_id
            and current.mount.parent == yaw
            and np.allclose(np.array(current.mount.matrix_parent_inverse) @ np.array(current.mount.matrix_basis),
                            sampler.mount_from_sensor, atol=1e-5, rtol=0)):
        current.update()
        scene.wfrl_x100_mount = 'NACELLE'
        scene.wfrl_x100_location, scene.wfrl_x100_rotation = location, rotation
        current.capture_configuration()
        return current

    previous = (scene.wfrl_x100_mount, tuple(scene.wfrl_x100_location),
                tuple(scene.wfrl_x100_rotation))
    if current is None:
        # A new session samples its own first frame; only the scene settings need rollback.
        try:
            scene.wfrl_x100_mount = 'NACELLE'
            scene.wfrl_x100_location, scene.wfrl_x100_rotation = location, rotation
            return start(scene)
        except Exception:
            scene.wfrl_x100_mount, scene.wfrl_x100_location, scene.wfrl_x100_rotation = previous
            raise
    source_session = str(uuid.uuid4())
    sequence, source_t = _simulation_time(scene, sampler)
    frame = _simulation_frame(scene, sampler, source_session, sequence, source_t)
    style = display.style_key(scene)
    batch = view.batch(frame, style[0], color_scale=style[1], velocity_limit=style[2]) if not bpy.app.background else None
    previous_mount = (current.mount.parent, current.mount.matrix_parent_inverse.copy(),
                      current.mount.matrix_basis.copy())
    saved_session = dict(current.__dict__)
    try:
        scene.wfrl_x100_mount = 'NACELLE'
        scene.wfrl_x100_location, scene.wfrl_x100_rotation = location, rotation
        current.mount.parent = yaw
        current.mount.matrix_parent_inverse = Matrix.Identity(4)
        current.mount.matrix_basis = Matrix(sampler.mount_from_sensor.tolist())
        current.sampler, current.source_session, current.key = sampler, source_session, sequence
        current.frame, current.gpu_batch, current.display_valid = frame, batch, True
        current.gpu_retry_at = None
        current.render_error = ''
        current.render_color = style
        current.capture_configuration()
        current.pair_context(frame)
        current.derive_blades()
    except Exception:
        scene.wfrl_x100_mount, scene.wfrl_x100_location, scene.wfrl_x100_rotation = previous
        current.mount.parent, current.mount.matrix_parent_inverse, current.mount.matrix_basis = previous_mount
        current.__dict__.clear()
        current.__dict__.update(saved_session)
        raise
    scene['wfrl_x100_error'] = scene.get('wfrl_x100_record_error', '')
    scene['wfrl_x100_status'] = '几何仿真 · 30 Hz 瞬时采样假设'
    return current


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
    return (session, session.frame, session.gpu_batch, session.display_valid, scene.wfrl_x100_fov)


def _redraw_changed_overlays(alive):
    """Tag 3D views only when the scene's drawn radar overlay inputs changed."""
    import bpy
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
    for window in bpy.context.window_manager.windows:
        if window.scene.as_pointer() in changed:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


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
