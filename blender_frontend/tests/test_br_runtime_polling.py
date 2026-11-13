"""Detached scene ownership and one display observation per runtime poll."""
from unittest.mock import Mock

import pytest

from wfrl_blender import runtime
from test_live_scene_ownership import live_owner
from test_runtime_redraw import local_runtime, backend


@pytest.mark.parametrize('invalid', ['missing', 'rna', 'zero_pointer'])
@pytest.mark.parametrize('close_fails', [False, True])
def test_invalid_backend_scene_detaches_once_and_retains_session(live_owner, monkeypatch, invalid, close_fails):
    r = live_owner
    r.client.next_sequence = 37
    runtime._state.run_id = 'owned-run'
    if invalid == 'missing':
        r.bpy.data.scenes.remove(r.owner)
    else:
        r.owner.as_pointer = Mock(return_value=0)
        if invalid == 'rna':
            r.owner.as_pointer.side_effect = ReferenceError('Scene RNA has been removed')
    close = Mock(side_effect=RuntimeError('close failed') if close_fails else None)
    poll = Mock(return_value=r.messages)
    monkeypatch.setattr(r.client, 'close', close)
    monkeypatch.setattr(r.client, 'poll', poll)
    other_before = dict(r.other)
    for _ in range(5):
        assert runtime.tick() == .05
    assert runtime._backend_scene is runtime._DETACHED_BACKEND_SCENE
    assert runtime._live_scene is None
    assert runtime._state.connection == 'DISCONNECTED' and not runtime._state.confirmed
    assert 'reconnect' in runtime._state.error
    assert runtime._state.session_id == r.client.session_id == 's'
    assert runtime._state.run_id == 'owned-run' and r.client.next_sequence == 37
    assert r.other == other_before
    close.assert_called_once_with()
    poll.assert_not_called()
    if invalid != 'missing':
        r.owner.as_pointer.assert_called_once_with()
    with pytest.raises(ValueError, match='detached'):
        runtime._build_backend_scene(object())
    close.assert_called_once_with()


def test_external_scene_check_detaches_before_timer_and_explicit_connect_rebinds(live_owner, monkeypatch):
    r = live_owner
    close = Mock()
    monkeypatch.setattr(r.client, 'close', close)
    r.bpy.data.scenes.remove(r.owner)
    with pytest.raises(ValueError, match='reconnect'):
        runtime.backend_scene()
    assert runtime.tick() == .05
    close.assert_called_once_with()
    runtime.connect(8765)
    assert runtime.backend_scene() is r.other
    assert runtime._state.session_id == r.client.session_id == 's'
    assert r.client.progress == 'Connecting'


@pytest.mark.parametrize('connection', ['LOCAL DEMO', 'OFFLINE RESULTS', 'CONNECTED'])
def test_timer_observes_display_once_and_reuses_it_for_object_update_and_redraw(
        local_runtime, monkeypatch, connection):
    r = local_runtime
    runtime._state.connection = connection
    if connection == 'CONNECTED':
        backend(r, monkeypatch)
    observation = Mock(side_effect=[('fresh',), ('fresh',), ('stale',), ('stale',)])
    update = Mock()
    monkeypatch.setattr(runtime, '_display_state', observation)
    monkeypatch.setattr(runtime.kinematics, 'apply_objects', update)
    for index in range(4):
        assert runtime.tick() == .05
        assert observation.call_count == index + 1
        assert runtime._last_display_state == (('fresh',) if index < 2 else ('stale',))
    assert len(r.draws) == (2 if connection == 'CONNECTED' else 1)
    assert update.call_count == {'CONNECTED': 2, 'OFFLINE RESULTS': 1, 'LOCAL DEMO': 0}[connection]
