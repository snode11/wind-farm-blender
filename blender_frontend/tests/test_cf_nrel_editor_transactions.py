"""Failed NREL previews and abandoned candidates preserve display and cleanup ownership."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from blender_frontend.tests.test_nrel_editor_playback import editor_runtime
from blender_frontend.tests.test_defect_store_cleanup import nrel_contract


@pytest.fixture
def preview_runtime(editor_runtime, nrel_contract):
    r = editor_runtime
    module, surface, initial, changed = nrel_contract
    s = r.session
    s.surface = surface
    s.geometry = NS(active=None, prepare=Mock(side_effect=lambda doc: {'document': deepcopy(doc)}))

    def activate(payload, *_args):
        s.geometry.active = payload

    s.geometry.activate = Mock(side_effect=activate)
    s.geometry.dispose = Mock()
    s.store = module.DefectStore(surface, initial, prepare=s.geometry.prepare,
                                activate=s.geometry.activate, dispose=s.geometry.dispose)
    s.payload, s.draft, s.draft_index = s.store.resource, changed, 0
    s.update_helpers = Mock()
    del s.discard_preview
    s.preview_draft()
    s.draft['defects'][0]['shape']['length_m'] *= 1.1
    return r


@pytest.mark.parametrize('boundary', ['prepare', 'activate', 'helpers'])
def test_failed_preview_keeps_displayed_candidate(preview_runtime, boundary):
    s = preview_runtime.session
    old = s.preview
    primary = RuntimeError('preview failed')
    if boundary == 'prepare':
        s.geometry.prepare.side_effect = primary
    elif boundary == 'activate':
        def activate(payload, *_args):
            if payload not in (s.payload, old.resource):
                raise primary
            s.geometry.active = payload
        s.geometry.activate.side_effect = activate
    else:
        s.update_helpers.side_effect = [primary, None]
    with pytest.raises(RuntimeError) as caught:
        s.preview_draft()
    assert caught.value is primary
    assert s.preview is old and not old.consumed
    assert s.geometry.active is old.resource
    assert s.store.resource is s.payload and s.store.version == 0
    if boundary != 'prepare':
        disposed = [call.args[0] for call in s.geometry.dispose.call_args_list]
        assert len(disposed) == 1 and disposed[0] is not old.resource


def test_preview_replacement_cleanup_failure_is_retryable(preview_runtime):
    s = preview_runtime.session
    old = s.preview
    s.geometry.dispose.side_effect = RuntimeError('old preview release unavailable')
    s.preview_draft()
    assert s.preview is not old and s.geometry.active is s.preview.resource
    assert old.consumed and s.store.pending_disposals == (old.resource,)
    s.geometry.dispose.side_effect = None
    assert s.store.retry_disposals(strict=True)
    assert s.store.pending_disposals == ()


def test_discard_preview_cleanup_failure_does_not_describe_hidden_preview(preview_runtime):
    s = preview_runtime.session
    old = s.preview
    s.geometry.dispose.side_effect = RuntimeError('preview release unavailable')
    s.discard_preview()
    assert s.preview is None and s.geometry.active is s.payload
    assert old.consumed and s.store.pending_disposals == (old.resource,)
    s.update_helpers.assert_called_with(s.store.document)


def test_detach_retries_abandoned_geometry_candidates(editor_runtime, stub_module):
    r, s = editor_runtime, editor_runtime.session
    candidate = object()
    pending = [candidate]
    released = []

    def dispose(payload):
        released.append(payload)
        pending[:] = [item for item in pending if item is not payload]

    s.geometry = NS(active=None, pending=pending, managers={}, dispose=dispose)
    s.payload = None
    s.flex = NS(blades=[], update=Mock())
    stub_module('wfrl_blender.repair_marks', ensure=Mock())
    r.namespace.detach(r.scene)
    assert released == [candidate] and not pending
    assert r.namespace.session() is None


def test_geometry_disposal_attempts_all_turbines_and_retains_failed_candidate(editor_runtime):
    cls = editor_runtime.namespace.GeometrySet
    geometry = cls.__new__(cls)
    geometry.active = None
    payload = {'turbines': {'T1': object(), 'T2': object()}}
    geometry.pending = [payload]
    primary = RuntimeError('T1 cleanup failed')
    first, second = Mock(side_effect=primary), Mock()
    geometry.managers = {'T1': NS(dispose=first), 'T2': NS(dispose=second)}
    with pytest.raises(RuntimeError, match='T1 cleanup failed'):
        geometry.dispose(payload)
    second.assert_called_once_with(payload['turbines']['T2'])
    assert geometry.pending == [payload]
    first.side_effect = None
    geometry.dispose(payload)
    assert not geometry.pending


def test_set_time_preserves_outer_frame_update_guard(editor_runtime, monkeypatch):
    r, s = editor_runtime, editor_runtime.session
    monkeypatch.setattr(r.namespace, '_UPDATING', True)
    s.flex = NS(times=[117., 177.], visible_turbines={0}, readers=['T1'], update=Mock())
    s.scene.frame_set = Mock()
    type(s).set_time(s, 120.)
    assert r.namespace._UPDATING is True


def test_editor_handler_registration_is_idempotent_and_paired(editor_runtime, monkeypatch):
    r = editor_runtime
    handlers = NS(save_pre=[], save_post=[], save_post_fail=[], frame_change_post=[])
    r.bpy.app.handlers = handlers
    r.bpy.types = NS(Scene=type('Scene', (), {}))
    r.bpy.props = NS(PointerProperty=Mock(return_value=object()))
    monkeypatch.setattr(r.namespace, 'detach', Mock())
    r.namespace.register_properties()
    r.namespace.register_properties()
    assert all(len(callbacks) == 1 for callbacks in vars(handlers).values())
    r.namespace.unregister_properties()
    r.namespace.unregister_properties()
    assert all(not callbacks for callbacks in vars(handlers).values())
    assert not hasattr(r.bpy.types.Scene, 'wfrl_nrel_defects')


def test_config_replace_failure_preserves_file_and_removes_temporary(nrel_contract, monkeypatch):
    from wfrl_blender import atomic_json
    from wfrl_blender.nrel_defects import defect_schema
    _module, surface, initial, changed = nrel_contract
    with tempfile.TemporaryDirectory(prefix='cf-nrel-config-') as directory:
        path = Path(directory) / 'defects.json'
        defect_schema.save_document(path, initial, surface)
        original = path.read_bytes()
        monkeypatch.setattr(atomic_json.os, 'replace', Mock(side_effect=OSError('replace unavailable')))
        with pytest.raises(OSError, match='replace unavailable'):
            defect_schema.save_document(path, changed, surface)
        assert path.read_bytes() == original
        assert list(Path(directory).iterdir()) == [path]
