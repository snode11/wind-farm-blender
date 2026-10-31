"""Committed defect history remains consistent while disposal is retryable."""
from copy import deepcopy
import importlib
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
