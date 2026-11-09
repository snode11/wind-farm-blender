"""Source sampling, applied observation modes and current-frame display updates."""
import math
from pathlib import Path
import time
import numpy as np
from .core import calibration, simulation
from . import configuration, display, view


def _simulation_time(scene, sampler):
    t = float(scene.get('wfrl_clearance_time_s', sampler.interval[0]))
    sequence = max(0, int(math.floor((t - sampler.interval[0]) * configuration.SAMPLE_RATE_HZ + 1e-7)))
    return sequence, min(sampler.interval[1], sampler.interval[0] + sequence / configuration.SAMPLE_RATE_HZ)


def _simulation_frame(scene, sampler, source_session, sequence, source_t, *, observation_mode='GEOMETRY'):
    with sampler.sampling_transaction():
        arguments = dict(sensor_id='X100-T1',
            session_id=source_session, sequence=sequence, interval_s=sampler.interval,
            visibility=sampler.visibility, material_at=sampler.material_sample)
        if observation_mode == 'DETECTIONS':
            budget = configuration.DETECTION_INTEGRATION_BUDGET
            azimuth, elevation = view.field_of_view(sampler.field_of_view)
            frame = simulation.simulate_detections(sampler.sample, source_t, **arguments,
                integration_budget=budget, sampling_plan=sampler.sampling_plan(budget),
                config=calibration.calibrated_config(azimuth_limit_deg=azimuth, elevation_limit_deg=elevation))
        elif observation_mode == 'GEOMETRY':
            budget = scene.wfrl_x100_candidates
            frame = simulation.simulate(sampler.sample, source_t, **arguments,
                candidate_budget=budget, sampling_plan=sampler.sampling_plan(budget),
                fov_deg=view.field_of_view(sampler.field_of_view))
        else:
            raise ValueError('未知 X100 观测模式：' + str(observation_mode))
    if frame is not None:
        frame.provenance['calibration_id'] = sampler.calibration_id
    return frame


def capture_configuration(self):
    import bpy
    self.applied_config = configuration.capture(self.scene, abspath=bpy.path.abspath,
        single_ply=self.mode == 'REPLAY' and self.static_frame is not None)
    if self.mode == 'SIMULATION':
        self.applied_config['observation_mode'] = self.observation_mode


def pending_configuration(self):
    import bpy
    return configuration.pending(self.scene, self.applied_config, abspath=bpy.path.abspath)


def _display(self, frame, *, required_gpu=False, record=True):
    import bpy
    if frame is not self.frame:
        self.pair_context(frame)
    self.frame = frame
    if frame is None:
        self._reset_blades()
    self.display_valid = frame is not None and frame.completeness == 'COMPLETE'
    if frame is not None and display.observation_mode(frame) == 'DETECTIONS':
        self.derive_blades()
    style = display.style_key(self.scene, frame)
    classification = self.classification()
    try:
        self.gpu_batch = view.batch(frame, style[0], color_scale=style[1], velocity_limit=style[2],
            classification=classification) if self.display_valid and not bpy.app.background else None
        self.gpu_retry_at = None
        self.render_error = ''
    except Exception as exc:
        self.gpu_batch = None
        self.gpu_retry_at = time.monotonic() + .25
        self.render_error = str(exc)
        if required_gpu:
            raise
    self.render_color = style
    self.render_classification = classification
    if record:
        self._record_current(frame)


def update(self):
    from .. import farm_flex
    from .runtime import _simulation_time, _simulation_frame, _validate_mount_drivers
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
            scene['wfrl_x100_status'] = configuration.OBSERVATION_LABELS[self.observation_mode] + ' · 相机采集期间保留采集前的观测帧'
            return
        # Radar samples use a declared X100-rate source grid independent of drawing cadence.
        key = sequence
        if key != self.key:
            frame = _simulation_frame(scene, self.sampler, self.source_session, sequence, source_t,
                observation_mode=self.observation_mode)
            self._display(frame, required_gpu=self._gpu_required)
            self.key = key
        self._update_fov_visibility()
        scene['wfrl_x100_status'] = configuration.OBSERVATION_LABELS[self.observation_mode] + ' · ' + configuration.RATE_LABEL
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
    self.derive_blades()
    self.retry_display()


def retry_display(self):
    """Rebuild only display resources for a still-valid original frame."""
    import bpy
    if self.frame is not None and self.display_valid and (
            self.render_color != display.style_key(self.scene, self.frame)
            or (display.style_key(self.scene, self.frame)[0] == 'ECHO_CLASS'
                and getattr(self, 'render_classification', None) is not self.classification())
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
