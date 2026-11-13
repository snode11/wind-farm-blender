"""Camera participation commits its state and undo entry only after refresh."""
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_camera_history as history, custom_cameras as core


@pytest.fixture
def participation(monkeypatch):
    camera = {'custom_enabled': True}
    scene = NS(objects=[camera])
    log = history.History(limit=2)
    monkeypatch.setattr(history, 'HISTORY', log)
    monkeypatch.setattr(core, 'get_camera', lambda _scene, _slot: camera)
    monkeypatch.setattr(history, 'identity', lambda _scene: 'scene identity')
    monkeypatch.setattr(history, 'state', lambda _scene: (dict(camera), repr(camera)))
    return NS(camera=camera, scene=scene, log=log)


@pytest.mark.parametrize('initial', [True, False, 'missing'])
@pytest.mark.parametrize('stale_history', [False, True])
@pytest.mark.parametrize('rollback_fails', [False, True])
def test_refresh_failure_restores_enabled_property_and_history(
        participation, monkeypatch, initial, stale_history, rollback_fails):
    r = participation
    if initial == 'missing':
        r.camera.clear()
    else:
        r.camera['custom_enabled'] = initial
    target = not bool(r.camera.get('custom_enabled', True))
    r.log.entries.extend([history.Entry({'older': True}, 'older', 'first'),
                          history.Entry({'before': True}, repr(r.camera), 'second')])
    r.log.identity = 'stale scene' if stale_history else 'scene identity'
    r.log.notice, r.log._watchdog_signature = 'existing notice', object()
    entries = tuple(r.log.entries)
    log_state = r.log.identity, r.log.notice, r.log._watchdog_signature
    before = dict(r.camera)
    failure = ValueError('reference refresh failed')
    refreshed = []

    def refresh(_scene):
        refreshed.append(bool(r.camera.get('custom_enabled', True)))
        if len(refreshed) == 1:
            raise failure
        if rollback_fails:
            raise RuntimeError('reference rollback failed')

    monkeypatch.setattr(core, '_refresh_references', refresh)
    with pytest.raises(ValueError) as caught:
        core.set_enabled(r.scene, 1, target)
    assert caught.value is failure
    assert r.camera == before
    assert tuple(r.log.entries) == entries
    assert (r.log.identity, r.log.notice, r.log._watchdog_signature) == log_state
    assert refreshed == [target, not target]
    if rollback_fails:
        assert any('reference rollback failed' in note for note in caught.value.__notes__)


def test_successful_participation_refresh_precedes_history_and_noop_does_not_refresh(participation, monkeypatch):
    r = participation
    refreshed = []

    def refresh(_scene):
        refreshed.append(r.camera['custom_enabled'])
        assert not r.log.entries

    monkeypatch.setattr(core, '_refresh_references', refresh)
    core.set_enabled(r.scene, 1, False)
    assert refreshed == [False] and r.camera['custom_enabled'] is False
    assert len(r.log.entries) == 1
    assert r.log.entries[0].before == {'custom_enabled': True}
    assert r.log.entries[0].after == repr(r.camera)
    core.set_enabled(r.scene, 1, False)
    assert refreshed == [False] and len(r.log.entries) == 1
