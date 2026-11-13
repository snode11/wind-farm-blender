"""Indexed replay failures preserve the displayed frame and both replay clocks."""
from bisect import bisect_right
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest


@pytest.fixture
def replay_session(frontend_module, monkeypatch, tmp_path):
    runtime = frontend_module('wfrl_blender.radar_x100.runtime')
    bpy = frontend_module('bpy')
    monkeypatch.setattr(bpy.app, 'background', False)
    monkeypatch.setattr(bpy.path, 'abspath', lambda path: path)

    class Scene(dict):
        fail_property = fail_clock = None
        clock = 0.
        session = None

        @property
        def wfrl_x100_replay_s(self):
            return self.clock

        @wfrl_x100_replay_s.setter
        def wfrl_x100_replay_s(self, value):
            self.clock = float(np.float32(value))
            if self.session is not None and not getattr(self.session, '_advancing_replay', False):
                self.session.replay_cursor = None
                self.session.replay_time_s = self.clock
                self.session.playing = False
            if self.fail_clock is not None:
                error, self.fail_clock = self.fail_clock, None
                raise error

        def __setitem__(self, key, value):
            if self.fail_property is not None and key == self.fail_property[0]:
                _, error = self.fail_property
                self.fail_property = None
                raise error
            super().__setitem__(key, value)

    scene = Scene(wfrl_x100_status='displayed frame zero', wfrl_x100_error='previous status')
    scene.wfrl_x100_source, scene.wfrl_x100_path = 'REPLAY', str(tmp_path)
    scene.wfrl_x100_color, scene.wfrl_x100_color_scale = 'RANGE', 'FIXED'
    scene.wfrl_x100_velocity_limit, scene.wfrl_x100_blade_tracking = 10., False
    frames = [runtime.schema.RadarFrame('X100', 'recording', index, 'TEST', 'NEAR',
              np.zeros(1, dtype=runtime.schema.POINT_DTYPE)) for index in range(3)]
    times = [0., .7, .7]
    session = runtime.Session.__new__(runtime.Session)
    session.__dict__.update(scene=scene, mode='REPLAY', applied_config={'path': str(tmp_path)},
        replay_path=tmp_path, static_frame=None, playing=False, play_anchor=12.,
        replay=NS(times=times, rows=frames, frame=Mock(side_effect=frames.__getitem__),
                  duration_s=.7, index_at=lambda stamp: bisect_right(times, stamp) - 1),
        replay_time_s=.123456789, replay_cursor=0, key=None, frame=None, frame_index=None,
        gpu_batch=None, display_valid=False, fanout=None, recording_stopping=False,
        blade_tracker=NS(reset=Mock()), clearance_tracker=None, gpu_retry_at=None)
    scene.wfrl_x100_replay_s = session.replay_time_s
    scene.session = session
    batch = Mock(side_effect=lambda *_args, **_kwargs: object())
    monkeypatch.setattr(runtime.view, 'batch', batch)
    session.update()
    session.update = Mock(wraps=session.update)
    return NS(session=session, scene=scene, runtime=runtime, frames=frames, batch=batch)


@pytest.mark.parametrize('playing', [False, True])
@pytest.mark.parametrize('boundary', ['clock', 'read', 'style', 'retry', 'status'])
def test_seek_failure_restores_position_and_display(replay_session, monkeypatch, boundary, playing):
    r = replay_session
    s = r.session
    s.playing = playing
    failure = ValueError('requested frame update failed')
    if boundary == 'clock':
        r.scene.fail_clock = failure
    elif boundary == 'read':
        s.replay.frame.side_effect = failure
    elif boundary == 'style':
        monkeypatch.setattr(r.runtime.observations.display, 'style_key', Mock(side_effect=failure))
    elif boundary == 'retry':
        monkeypatch.setattr(s, 'retry_display', Mock(side_effect=failure))
    else:
        r.scene['wfrl_x100_status'] = 'displayed frame zero'
        r.scene['wfrl_x100_error'] = 'previous status'
        r.scene.fail_property = 'wfrl_x100_error', failure
    before = dict(s.__dict__)
    scene_before, clock_before = dict(r.scene), r.scene.wfrl_x100_replay_s

    with pytest.raises(ValueError) as caught:
        s.seek_frame(1)

    assert caught.value is failure
    assert s.replay_cursor == before['replay_cursor']
    assert s.replay_time_s == before['replay_time_s']
    assert r.scene.wfrl_x100_replay_s == clock_before
    assert s.playing is playing and s.play_anchor == before['play_anchor']
    assert s.frame is before['frame'] and s.gpu_batch is before['gpu_batch']
    for name in ('key', 'frame_index', 'display_valid', 'render_color', 'render_error',
                 'render_classification', 'context', 'analysis_error', 'blade_result', 'blade_key'):
        assert getattr(s, name) == before[name]
    assert dict(r.scene) == scene_before
    assert not getattr(s, '_advancing_replay', False)
    assert s.update.call_count == (0 if boundary == 'clock' else 1)


def test_failed_clock_restore_does_not_replace_seek_exception(replay_session, monkeypatch):
    r = replay_session
    failure = ValueError('frame failed')
    restore_failure = RuntimeError('clock restore failed')

    def fail_update():
        r.scene.fail_clock = restore_failure
        raise failure

    monkeypatch.setattr(r.session, 'update', fail_update)
    with pytest.raises(ValueError) as caught:
        r.session.seek_frame(1)
    assert caught.value is failure
    assert r.session.replay_cursor == 0
    assert r.session.replay_time_s == .123456789
    assert not getattr(r.session, '_advancing_replay', False)
    assert any('clock restore failed' in note for note in caught.value.__notes__)


def test_successful_seek_preserves_explicit_indices_at_equal_timestamps(replay_session):
    r = replay_session
    s = r.session
    s.playing = True
    for index in (1, 2):
        s.seek_frame(index)
        assert not s.playing
        assert s.replay_cursor == s.frame_index == s.key == index
        assert s.replay_time_s == .7
        assert r.scene.wfrl_x100_replay_s == float(np.float32(.7))
        assert s.frame is r.frames[index] and s.display_valid
    assert s.update.call_count == 2 and r.batch.call_count == 3


def test_failed_seek_does_not_submit_or_duplicate_uncommitted_frame(replay_session, monkeypatch):
    r = replay_session
    s = r.session
    s.record_start, s.record_last, s.record_last_index, s.record_last_stamp = 0., 0, 0, 0.
    s.fanout = NS(submit=Mock(), failure='', stopping=NS(set=Mock()))
    failure = ValueError('display refresh failed after frame preparation')
    monkeypatch.setattr(s, 'retry_display', Mock(side_effect=[failure, None]))

    with pytest.raises(ValueError) as caught:
        s.seek_frame(1)
    assert caught.value is failure
    s.fanout.submit.assert_not_called()
    assert (s.record_start, s.record_last, s.record_last_index, s.record_last_stamp) == (0., 0, 0, 0.)
    s.fanout.stopping.set.assert_not_called()
    assert s.fanout.failure == ''

    s.seek_frame(1)
    s.fanout.submit.assert_called_once_with(r.frames[1], .7)
    assert (s.record_last, s.record_last_index, s.record_last_stamp) == (1, 1, .7)


def test_successful_backward_seek_keeps_recording_failure_contract(replay_session):
    r = replay_session
    s = r.session
    s.seek_frame(2)
    s.record_start, s.record_last, s.record_last_index, s.record_last_stamp = 0., 2, 2, .7
    s.fanout = NS(submit=Mock(), failure='', stopping=NS(set=Mock()))
    s.seek_frame(1)
    assert s.replay_cursor == 1 and s.frame is r.frames[1]
    s.fanout.submit.assert_not_called()
    s.fanout.stopping.set.assert_called_once_with()
    assert 'moved backward' in s.fanout.failure
