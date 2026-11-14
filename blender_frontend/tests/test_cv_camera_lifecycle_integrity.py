"""Draft allocation, edit reentry and evaluated metre-frame validation."""
from types import SimpleNamespace as NS
import sys

import numpy as np
import pytest

from wfrl_blender import custom_cameras as core
from blender_frontend.tests.test_custom_camera_retirement import runtime


@pytest.fixture
def editing(frontend_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.custom_cameras')
    monkeypatch.setattr(panel, 'session_identity', lambda _scene: ('current',))
    monkeypatch.setattr(panel, 'installation_block_reason', lambda _scene: '')
    monkeypatch.setattr(panel.core, 'get_camera', lambda *_args: None)
    scene = NS(frame_current=1, frame_subframe=0.)
    draft = NS(data=object())
    created, cancelled, playback = [], [], []

    def begin(*_args):
        if created and not cancelled:
            raise ValueError('existing draft')
        created.append(draft)
        return draft

    monkeypatch.setattr(panel.core, 'begin_draft', begin)
    monkeypatch.setattr(panel.core, 'cancel_draft', lambda *_args, **_kwargs: cancelled.append(draft))
    session = panel.EditSession(scene, 1)
    session.playing, session._play = True, playback.append
    return session, created, cancelled, playback


def test_edit_begin_reentry_does_not_resume_playback_under_a_draft(editing):
    session, created, _cancelled, playback = editing
    draft = session.begin()
    with pytest.raises(ValueError):
        session.begin()
    assert session.draft is draft and len(created) == 1 and not session.closed
    assert playback == [False]


def test_closed_edit_session_cannot_allocate_an_orphan_draft(editing):
    session, created, cancelled, playback = editing
    session.begin()
    session.cancel()
    with pytest.raises(ValueError):
        session.begin()
    assert session.closed and session.draft is None
    assert len(created) == len(cancelled) == 1 and playback == [False, True]


@pytest.mark.parametrize('failure', ['object_allocation', 'link', 'data_copy'])
def test_failed_draft_construction_releases_only_its_allocations(monkeypatch, failure):
    objects, lenses = [], []

    def fail():
        raise RuntimeError('injected draft allocation failure')

    def lens_new(_name):
        lens = NS(users=0, copy=fail)
        lenses.append(lens)
        return lens

    class Camera(dict):
        def __init__(self, lens):
            super().__init__()
            self.data = lens
            lens.users += 1
            objects.append(self)

        def copy(self):
            return Camera(self.data)

    def object_new(_name, lens):
        if failure == 'object_allocation':
            fail()
        return Camera(lens)

    def object_remove(camera, **_kwargs):
        camera.data.users -= 1
        objects[:] = [obj for obj in objects if obj is not camera]

    original = Camera(lens_new('existing')) if failure == 'data_copy' else None
    before_objects, before_lenses = tuple(objects), tuple(lenses)
    root = NS(users_collection=[])
    scene = NS(objects=[], collection=NS(objects=NS(link=lambda _camera: fail())))
    monkeypatch.setattr(core, '_root', lambda _scene: root)
    monkeypatch.setattr(core, 'get_camera', lambda *_args: original)
    monkeypatch.setattr(core, 'migrate_camera', lambda *_args: None)
    monkeypatch.setattr(core, '_RETIRED', [])
    monkeypatch.setattr(core, '_RETIRED_DATA', [])
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(
        objects=NS(new=object_new, remove=object_remove),
        cameras=NS(new=lens_new, remove=lambda lens: lenses.remove(lens)))))
    with pytest.raises(RuntimeError, match='draft allocation failure'):
        core.begin_draft(scene, 1)
    assert tuple(objects) == before_objects and tuple(lenses) == before_lenses
    if original is not None:
        assert original.data.users == 1


def test_failed_draft_release_remains_retryable_while_linked(runtime):
    draft = runtime.create(1, installed=False)
    draft['wfrl_custom_camera'] = False
    runtime.objects.failures[draft] = 1
    data = draft.data
    try:
        core._remove(draft)
    except RuntimeError:
        core._remember_retired(draft, data)
    assert not draft.removed and draft.users_collection
    core.cleanup_retired()
    assert draft.removed and data.removed
    assert not core._RETIRED and not core._RETIRED_DATA


@pytest.mark.parametrize('failure_mode', ['cleanup', 'removed_rna'])
def test_layout_rollback_failure_still_releases_all_staged_cameras(monkeypatch, failure_mode):
    from wfrl_blender import stacked_camera_rig as rig
    scene = NS(objects=[])
    staged = [{'wfrl_custom_slot': slot} for slot in (1, 2)]
    parameters = core.CameraParameters((0., 0., 1.))
    records = [({'slot_id': slot, 'research_confirmed': True, 'enabled': True,
                 'label': f'C{slot}', 'mount_validation_status': 'research_outside'}, parameters, None)
               for slot in (1, 2)]
    monkeypatch.setattr(core, 'validate_layout', lambda *_args: records)
    monkeypatch.setattr(core, 'begin_draft', lambda _scene, slot: staged[slot - 1])
    monkeypatch.setattr(core, 'apply_research', lambda *_args: None)
    monkeypatch.setattr(rig, 'pose', lambda _scene: 'old pose')
    monkeypatch.setattr(rig, 'apply_pose', lambda *_args: None)
    removed = []

    def publish(*_args):
        if failure_mode == 'removed_rna':
            staged[0].removed = True
        raise ValueError('injected publication failure')

    def remove(camera):
        removed.append(camera['wfrl_custom_slot'])
        if camera['wfrl_custom_slot'] == 1:
            raise RuntimeError('injected cleanup failure')

    pending = []
    monkeypatch.setattr(core, '_publish', publish)
    monkeypatch.setattr(core, '_remove', remove)
    monkeypatch.setattr(core, '_remember_retired', lambda camera, _data: pending.append(camera))
    class Camera(dict):
        removed = False

        @property
        def data(self):
            if self.removed:
                raise ReferenceError('Object RNA has been removed')
            return object()

    staged[:] = [Camera(camera) for camera in staged]
    error = RuntimeError if failure_mode == 'cleanup' else ValueError
    with pytest.raises(error, match='publication failure'):
        core.restore_layout(scene, {'model_signature': 'mount'})
    assert removed == ([1, 2] if failure_mode == 'cleanup' else [2])
    assert pending == ([staged[0]] if failure_mode == 'cleanup' else [])


def test_rig_restore_failure_does_not_skip_staged_camera_cleanup(monkeypatch):
    from wfrl_blender import stacked_camera_rig as rig
    scene = NS(objects=[])
    class Camera(dict):
        data = object()
    camera = Camera(wfrl_custom_slot=1)
    record = {'slot_id': 1, 'research_confirmed': True, 'enabled': True,
              'label': 'C1', 'mount_validation_status': 'research_outside'}
    monkeypatch.setattr(core, 'validate_layout', lambda *_args: [(record, object(), None)])
    monkeypatch.setattr(core, 'begin_draft', lambda *_args: camera)
    monkeypatch.setattr(core, 'apply_research', lambda *_args: None)
    monkeypatch.setattr(rig, 'pose', lambda _scene: 'old pose')
    def fail_pose(*_args):
        raise RuntimeError('injected rig restore failure')
    monkeypatch.setattr(rig, 'apply_pose', fail_pose)
    removed = []
    monkeypatch.setattr(core, '_remove', removed.append)
    with pytest.raises(RuntimeError, match='rig restore failure'):
        core.restore_layout(scene, {'model_signature': 'mount'})
    assert removed == [camera]


def test_load_cleanup_cancels_capture_even_when_editor_cleanup_fails(
        frontend_module, monkeypatch, stub_module):
    panel = frontend_module('wfrl_blender.panels.custom_cameras')
    calls = []

    def fail_editor(**_kwargs):
        calls.append('editor')
        raise RuntimeError('injected edit cleanup failure')

    monkeypatch.setattr(panel, '_ACTIVE', NS(finish=fail_editor))
    stub_module('wfrl_blender.panels.stacked_camera_rig', shutdown=lambda **_kwargs: calls.append('box'))
    stub_module('wfrl_blender.custom_camera_capture', shutdown=lambda **_kwargs: calls.append('capture'))
    monkeypatch.setattr(panel.preview, 'shutdown', lambda: calls.append('preview'))
    monkeypatch.setattr(panel.history.HISTORY, 'clear', lambda _reason: calls.append('history'))
    try:
        panel.cancel_on_load()
    except RuntimeError:
        pass
    assert calls == ['box', 'editor', 'capture', 'preview', 'history']


class Matrix:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=float)

    def __iter__(self):
        return iter(self.values)

    def to_3x3(self):
        return Matrix(self.values[:3, :3])

    @property
    def col(self):
        return [NS(length=np.linalg.norm(v), dot=lambda other, v=v: np.dot(v, other.values), values=v)
                for v in self.values.T]

    def determinant(self):
        return np.linalg.det(self.values)


@pytest.mark.parametrize('transform', [
    [[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    [[1, .1, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    [[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
])
def test_evaluated_root_rejects_scale_shear_and_reflection(monkeypatch, transform):
    evaluated = NS(matrix_world=Matrix(transform))
    root = NS(matrix_world=Matrix(np.eye(4)), evaluated_get=lambda _graph: evaluated)
    monkeypatch.setattr(core, '_root', lambda _scene: root)
    monkeypatch.setitem(sys.modules, 'bpy', NS())
    monkeypatch.setitem(sys.modules, 'mathutils', NS(Matrix=Matrix))
    with pytest.raises(ValueError, match='米制|缩放|剪切|反射'):
        core.validate_native_state(object(), cameras=[], depsgraph=object())


def test_evaluated_root_keeps_rigid_motion_and_metre_frame(monkeypatch):
    values = [[0., -1., 0., 1000.], [1., 0., 0., -2000.], [0., 0., 1., 90.], [0., 0., 0., 1.]]
    root = NS(evaluated_get=lambda _graph: NS(matrix_world=Matrix(values)))
    monkeypatch.setattr(core, '_root', lambda _scene: root)
    monkeypatch.setitem(sys.modules, 'bpy', NS())
    monkeypatch.setitem(sys.modules, 'mathutils', NS(Matrix=Matrix))
    assert core.validate_native_state(object(), cameras=[], depsgraph=object()) is True
