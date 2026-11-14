"""Camera undo retains failed entries and rejects divergent scene state."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_camera_history as history, custom_cameras as core


@pytest.fixture
def timeline(monkeypatch, stub_module):
    scene = NS(objects=[], layout={'cameras': []}, identity=('scene', 'model'))
    state = NS(busy=False, block='', restore_error=None)
    monkeypatch.setattr(history, 'HISTORY', history.History())
    monkeypatch.setattr(history, 'identity', lambda owner: owner.identity)
    monkeypatch.setattr(history, 'state', lambda owner: (deepcopy(owner.layout), core.layout_hash(owner.layout)))
    stub_module('wfrl_blender.custom_camera_capture', active=lambda: state.busy)
    stub_module('wfrl_blender.panels.custom_cameras', installation_block_reason=lambda _scene: state.block)

    def restore(owner, layout):
        if state.restore_error:
            raise state.restore_error
        owner.layout = deepcopy(layout)

    monkeypatch.setattr(core, 'restore_layout', restore)

    def change(label):
        before = history.before_change(scene)
        scene.layout['cameras'] = [{'label': label}]
        history.committed(scene, before, label)

    return NS(scene=scene, state=state, change=change)


@pytest.mark.parametrize('error', [ValueError('invalid layout'), RuntimeError('publication unavailable')])
def test_failed_history_restore_keeps_entry_and_current_state(timeline, error):
    timeline.change('first')
    timeline.change('second')
    entries = tuple(history.HISTORY.entries)
    current = deepcopy(timeline.scene.layout)
    timeline.state.restore_error = error
    with pytest.raises(type(error), match=str(error)):
        history.undo(timeline.scene)
    assert tuple(history.HISTORY.entries) == entries and timeline.scene.layout == current
    timeline.state.restore_error = None
    assert history.undo(timeline.scene) == 'second'
    assert history.status(timeline.scene) == '撤销：first'
    assert history.undo(timeline.scene) == 'first'
    assert not history.HISTORY.entries and timeline.scene.layout == {'cameras': []}


@pytest.mark.parametrize('guard', ['capture', 'draft', 'installation'])
def test_blocked_undo_neither_mutates_layout_nor_consumes_history(timeline, guard):
    timeline.change('first')
    current = deepcopy(timeline.scene.layout)
    if guard == 'capture':
        timeline.state.busy = True
    elif guard == 'draft':
        timeline.scene.objects.append({'wfrl_custom_draft': True})
    else:
        timeline.state.block = 'installation busy'
    with pytest.raises(ValueError):
        history.undo(timeline.scene)
    assert len(history.HISTORY.entries) == 1 and timeline.scene.layout == current


@pytest.mark.parametrize('change', ['native_undo', 'native_redo', 'scene'])
def test_external_history_or_scene_change_invalidates_local_undo(timeline, change):
    timeline.change('first')
    if change == 'scene':
        timeline.scene.identity = ('another scene', 'model')
    else:
        timeline.scene.layout = {'cameras': [] if change == 'native_undo' else [{'label': 'external redo'}]}
    current = deepcopy(timeline.scene.layout)
    with pytest.raises(ValueError, match='历史已清理'):
        history.undo(timeline.scene)
    assert not history.HISTORY.entries and timeline.scene.layout == current


def test_noop_commit_does_not_add_history_and_snapshot_is_independent(timeline):
    timeline.change('first')
    before = history.before_change(timeline.scene)
    history.committed(timeline.scene, before, 'unchanged')
    assert len(history.HISTORY.entries) == 1
    timeline.change('second')
    timeline.scene.layout['cameras'][0]['label'] = 'external edit'
    assert history.HISTORY.entries[-1].before == {'cameras': [{'label': 'first'}]}
