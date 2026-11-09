"""Session recording ownership and independent indexed replay controls."""
import time
from .core import acquisition
from . import clearance, display


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
    queued = frame
    if display.observation_mode(frame) == 'DETECTIONS':
        enabled = getattr(self.scene, 'wfrl_x100_blade_tracking', False)
        matched = getattr(self, 'blade_key', None) == self._clearance_frame_key(frame)
        result = getattr(self, 'blade_result', None) if enabled and matched else None
        error = (getattr(self, 'blade_error', '') if self.frame is frame else '无对应帧估计') if enabled else '估计未启用'
        queued = clearance.RecordedFrame(frame, result, error)
    self.fanout.submit(queued, stamp - self.record_start)
    self.record_last = key
    self.record_last_index = self.frame_index if indexed else None
    self.record_last_stamp = stamp


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
    self.fanout = acquisition.Fanout(clearance.Recorder(path, description=dict(
        source_mode=self.mode, timing=('original indexed presentation time' if self.mode == 'REPLAY' and self.replay
            else 'source time when available; otherwise local recording monotonic'))))
    self.scene['wfrl_x100_record_error'] = ''
    self.recording_health()
    # The displayed frame is already current; record it without rebuilding display resources.
    self._record_current(self.frame)


def recording_state(self):
    """The recording state the scene and panel show; results wait for the writer to exit."""
    if self.fanout is None:
        return None
    state = self.fanout.recorder.state
    if state != 'FAILED' and (state == 'FINALIZING' or self.recording_stopping and self.fanout.worker.is_alive()):
        return 'FINALIZING'
    return state


def recording_health(self):
    if self.fanout is not None:
        try:
            if not self.scene.as_pointer():
                return
        except ReferenceError:
            return
        recorder = self.fanout.recorder
        self.scene['wfrl_x100_record_state'] = recording_state(self)
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
