"""Recording shutdown yields to the event loop and retains its worker ownership."""
import threading
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest
from wfrl_blender.radar_x100 import runtime
from wfrl.radar_x100 import acquisition, recording, schema, tracking


class Scene(dict):
    wfrl_x100_enabled = True
    wfrl_x100_fov = True
    def as_pointer(self): return id(self)


@pytest.mark.parametrize('mode', ['SIMULATION', 'REPLAY'])
@pytest.mark.parametrize('whole_session', [False, True])
def test_recording_stop_yields_until_worker_exits_and_prevents_restart(monkeypatch, tmp_path, mode, whole_session):
    scene = Scene()
    sink = recording.Recorder(tmp_path / 'record')
    entered, release = threading.Event(), threading.Event()
    append = sink.append
    def held_append(frame, t):
        entered.set()
        assert release.wait(15)
        append(frame, t)
    sink.append = held_append
    fanout = acquisition.Fanout(sink)
    join = fanout.worker.join
    def nonblocking_join(timeout=None):
        assert timeout == 0, 'Blender must not wait for the recording worker'
        return join(timeout=0)
    monkeypatch.setattr(fanout.worker, 'join', nonblocking_join)
    fanout.submit(schema.RadarFrame('X100', 'local', 0, 'TEST', 'NEAR',
                                  np.zeros(1, dtype=schema.POINT_DTYPE)), 0.)
    s = runtime.Session.__new__(runtime.Session)
    s.scene, s.mode, s.fanout = scene, mode, fanout
    s.closing = s.recording_stopping = False
    s.close_remove, s.restore_after_close = True, False
    s.client = s.frame = s.gpu_batch = s.fov_gpu_batch = None
    s.blade_tracker = tracking.BladeTracker()
    s.display_valid = False
    s.owner = 'recording-owner'
    s.update = lambda: None
    monkeypatch.setattr(runtime, '_SESSIONS', {scene.as_pointer(): s})
    monkeypatch.setattr(runtime, '_SHUTTING_DOWN', False, raising=False)
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(
        data=SimpleNamespace(scenes=[scene]), context=SimpleNamespace(
            window_manager=SimpleNamespace(windows=[]))))
    monkeypatch.setattr(runtime, '_guard_installation', lambda: None)
    monkeypatch.setattr(runtime.view, 'remove_objects', lambda scene: None)
    try:
        assert entered.wait(3)
        assert (runtime.stop(scene) if whole_session else s.stop_recording()) is False
        assert s.fanout is fanout and s.recording_stopping and fanout.worker.is_alive()
        assert runtime.session_for(scene) is s and scene.wfrl_x100_enabled
        if whole_session:
            assert s.closing and scene['wfrl_x100_status'] != '已关闭'
            with pytest.raises(ValueError, match='关闭'): runtime.start(scene)
        else:
            with pytest.raises(ValueError): s.start_recording(tmp_path / 'conflicting')
        assert not (tmp_path / 'conflicting').exists()
    finally:
        release.set()
        join(3)
    if whole_session:
        runtime.tick()
        assert runtime.session_for(scene) is None and not scene.wfrl_x100_enabled
        assert scene['wfrl_x100_status'] == '已关闭'
    else:
        runtime.tick()
        assert s.fanout is None and not s.recording_stopping
        runtime.stop(scene)
    assert sink.state == 'COMPLETE' and recording.Replay(sink.path).frame(0).sequence == 0


def test_simulation_recording_stops_with_reason_when_timeline_moves_backward(tmp_path):
    def frame(sequence, t):
        return schema.RadarFrame('X100-T1', 'sim', sequence, 'GEOMETRY_SIMULATION', 'GEOMETRY',
            np.zeros(1, dtype=schema.POINT_DTYPE),
            time=schema.FrameTime('SIMULATION:src', t, (t, t), 'INSTANT', 'SIMULATION'))
    s = runtime.Session.__new__(runtime.Session)
    s.mode, s.frame_index = 'SIMULATION', None
    s.record_start = s.record_last = s.record_last_index = s.record_last_stamp = None
    s.fanout = acquisition.Fanout(recording.Recorder(tmp_path / 'record'))
    for sequence, t in ((150, 5.), (151, 5. + 1 / 30)):
        s._record_frame(frame(sequence, t))
    # A failure discards queued rows that are not yet durable, so let accepted frames land first.
    deadline = time.monotonic() + 5
    while s.fanout.recorder.rows < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    s._record_frame(frame(0, 0.))  # playback wraps to the start
    s.fanout.request_close()
    s.fanout.worker.join(5)
    recorder = s.fanout.recorder
    assert recorder.state == 'FAILED' and recorder.rows == 2
    assert '仿真时间在记录期间回退' in recorder.error
    assert (tmp_path / 'record/INCOMPLETE').exists()
