"""Committed defect history remains consistent while disposal is retryable."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest


@pytest.fixture(params=['nrel', 'gw184'])
def contract(request):
    if request.param == 'nrel':
        from wfrl_blender import turbine_geometry
        from wfrl_blender.nrel_defects import defect_schema, defect_store, reference_surface
        verts, faces = turbine_geometry.blade_mesh(4, 48)
        surface = reference_surface.ReferenceSurface(verts, faces, ring_points=48)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'projects/gw184-single'))
        from gw184 import defect_schema, defect_store, reference_surface, model
        surface = reference_surface.ReferenceSurface(model.read_config()[0])
    initial = defect_schema.new_document(surface)
    changed = deepcopy(initial)
    changed['defects'] = [defect_schema.new_defect(surface, 'fine_crack')]
    return defect_store, surface, initial, changed


@pytest.mark.parametrize('operation', ['commit', 'restore', 'undo', 'redo'])
def test_post_commit_disposal_failure_preserves_fact_history_and_retry(contract, operation):
    module, surface, initial, changed = contract
    active, failures, disposed = [], [False], []
    def dispose(resource):
        if failures[0]:
            raise RuntimeError('release unavailable')
        disposed.append(resource)
    store = module.DefectStore(surface, initial, prepare=lambda doc: object(),
        activate=lambda resource, doc, version: active.append((resource, doc, version)), dispose=dispose)
    if operation in ('undo', 'redo'):
        store.restore(changed)
    if operation == 'redo':
        store.undo()
    old_resource, old_version = store.resource, store.version
    failures[0] = True
    if operation == 'commit':
        token = store.prepare(changed)
        assert store.commit(token) == old_version + 1
        assert token.consumed
    elif operation == 'restore':
        store.restore(changed)
    else:
        assert getattr(store, operation)()
    expected = initial if operation == 'undo' else changed
    assert store.document == expected == active[-1][1]
    assert store.version == active[-1][2] == old_version + 1
    assert store.can_redo == (operation == 'undo')
    assert store.can_undo == (operation != 'undo')
    assert store.pending_disposals == (old_resource,)
    assert store.cleanup_errors == ('release unavailable',)
    with pytest.raises(RuntimeError, match='await cleanup'):
        store.retry_disposals(strict=True)
    assert store.pending_disposals == (old_resource,)
    failures[0] = False
    assert store.retry_disposals(strict=True)
    assert store.pending_disposals == () and store.cleanup_errors == ()
    assert old_resource in disposed
    assert store.document == expected and store.version == old_version + 1
    assert store.undo() if operation != 'undo' else store.redo()


@pytest.fixture
def nrel_contract():
    from wfrl_blender import turbine_geometry
    from wfrl_blender.nrel_defects import defect_schema, defect_store, reference_surface
    verts, faces = turbine_geometry.blade_mesh(4, 48)
    surface = reference_surface.ReferenceSurface(verts, faces, ring_points=48)
    initial = defect_schema.new_document(surface)
    changed = deepcopy(initial)
    changed['defects'] = [defect_schema.new_defect(surface, 'fine_crack')]
    return defect_store, surface, initial, changed


def test_initial_activation_error_survives_disposal_failure(nrel_contract, caplog):
    module, surface, initial, _ = nrel_contract
    primary = KeyError('activation field missing')
    def activate(*_args):
        raise primary
    def dispose(_resource):
        raise RuntimeError('initial dispose failed')
    with pytest.raises(KeyError) as caught:
        module.DefectStore(surface, initial, activate=activate, dispose=dispose)
    assert caught.value is primary
    assert 'initial dispose failed' in caplog.text


@pytest.mark.parametrize('operation', ['restore', 'undo', 'redo'])
def test_failed_history_activation_retains_primary_and_retryable_candidate(nrel_contract, operation, caplog):
    module, surface, initial, changed = nrel_contract
    store = module.DefectStore(surface, initial, prepare=lambda _doc: object())
    if operation in ('undo', 'redo'):
        store.restore(changed)
    if operation == 'redo':
        store.undo()
    before = (store.document, store.resource, store.version, store.can_undo, store.can_redo)
    primary = KeyError('activation field missing')
    def activate(resource, *_args):
        if resource is not store.resource:
            raise primary
    def dispose(_resource):
        raise RuntimeError('candidate dispose failed')
    store._activate, store._dispose = activate, dispose
    with pytest.raises(KeyError) as caught:
        store.restore(changed) if operation == 'restore' else getattr(store, operation)()
    assert caught.value is primary
    assert before == (store.document, store.resource, store.version, store.can_undo, store.can_redo)
    assert len(store.pending_disposals) == 1
    assert store.pending_disposals[0] is not store.resource
    assert store.cleanup_errors == ('candidate dispose failed',)
    assert 'candidate dispose failed' in caplog.text
    disposed = []
    store._dispose = disposed.append
    assert store.retry_disposals(strict=True)
    assert len(disposed) == 1 and not store.pending_disposals


def test_activation_rollback_failure_is_attached_to_primary(nrel_contract):
    module, surface, initial, changed = nrel_contract
    store = module.DefectStore(surface, initial)
    token = store.prepare(changed)
    primary = TypeError('invalid candidate')
    def activate(_resource, _document, version):
        raise primary if version else RuntimeError('rollback failed')
    store._activate = activate
    with pytest.raises(TypeError) as caught:
        store.commit(token)
    assert caught.value is primary
    assert any('rollback failed' in note for note in primary.__notes__)
    assert store.document == initial and store.version == 0 and not token.consumed
