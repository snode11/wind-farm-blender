"""Strict NREL documents and callback-safe store transactions."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from blender_frontend.tests.test_defect_store_cleanup import nrel_contract


@pytest.mark.parametrize('location', ['document', 'defect', 'anchor', 'shape', 'provenance'])
def test_unknown_fields_are_rejected(nrel_contract, location):
    module, surface, _initial, document = nrel_contract
    target = document if location == 'document' else document['defects'][0]
    if location not in ('document', 'defect'):
        target = target[location]
    target['unrecognized_field'] = None
    with pytest.raises(ValueError, match='[Uu]nknown'):
        module.validate_document(document, surface)


@pytest.mark.parametrize('value', [[], {}])
def test_morphology_type_errors_are_explicit_validation_errors(nrel_contract, value):
    module, surface, _initial, document = nrel_contract
    document['defects'][0]['morphology'] = value
    with pytest.raises(ValueError, match='morphology'):
        module.validate_document(document, surface)


@pytest.mark.parametrize('operation', ['restore', 'undo', 'redo'])
def test_interrupted_history_restores_live_resource_and_releases_candidate(nrel_contract, operation):
    module, surface, initial, changed = nrel_contract
    active, disposed = [], []
    store = module.DefectStore(surface, initial, prepare=lambda _doc: object(),
        activate=lambda resource, *_args: active.append(resource), dispose=disposed.append)
    if operation in ('undo', 'redo'):
        store.restore(changed)
    if operation == 'redo':
        store.undo()
    before = (store.document, store.version, store.resource, store.can_undo, store.can_redo)
    disposed.clear()
    candidate = []

    def activate(resource, *_args):
        active.append(resource)
        if resource is not before[2]:
            candidate.append(resource)
            raise KeyboardInterrupt('interrupted activation')

    store._activate = activate
    with pytest.raises(KeyboardInterrupt, match='interrupted activation'):
        store.restore(changed) if operation == 'restore' else getattr(store, operation)()
    assert before == (store.document, store.version, store.resource, store.can_undo, store.can_redo)
    assert active[-1] is before[2]
    assert len(candidate) == 1 and disposed == candidate


def test_interrupted_initial_activation_releases_resource(nrel_contract):
    module, surface, initial, _changed = nrel_contract
    resource, disposed = object(), []
    with pytest.raises(KeyboardInterrupt):
        module.DefectStore(surface, initial, prepare=lambda _doc: resource,
            activate=Mock(side_effect=KeyboardInterrupt()), dispose=disposed.append)
    assert disposed == [resource]


@pytest.mark.parametrize('callback', ['prepare', 'activate', 'dispose'])
def test_callbacks_cannot_reenter_store_mutations(nrel_contract, callback):
    module, surface, initial, changed = nrel_contract
    store = module.DefectStore(surface, initial, prepare=lambda _doc: object())
    token = store.prepare(changed)
    disposed = []
    store._dispose = disposed.append

    def reenter(*_args):
        store.cancel(token)
        return object()

    setattr(store, '_' + callback, reenter)
    if callback == 'dispose':
        store.commit(token)
        assert store.pending_disposals and 'Reentrant' in store.cleanup_errors[0]
        assert store.document == changed and store.version == 1
    else:
        with pytest.raises(RuntimeError, match='Reentrant'):
            store.prepare(changed) if callback == 'prepare' else store.commit(token)
        assert store.document == initial and store.version == 0
        assert not token.consumed and not disposed
        setattr(store, '_' + callback, lambda *_args: object())
        store.cancel(token)
        assert token.consumed


@pytest.mark.parametrize('mutation', ['missing', 'nan', 'duplicate', 'version', 'type'])
def test_invalid_documents_keep_committed_state(nrel_contract, mutation):
    module, surface, initial, changed = nrel_contract
    bad = deepcopy(changed)
    defect = bad['defects'][0]
    if mutation == 'missing':
        del defect['shape']['depth_m']
    elif mutation == 'nan':
        defect['anchor']['s_m'] = float('nan')
    elif mutation == 'duplicate':
        bad['defects'].append(deepcopy(defect))
    elif mutation == 'version':
        bad['schema'] = 'wfrl.blade_defects.v999'
    else:
        defect['enabled'] = 0
    store = module.DefectStore(surface, initial)
    with pytest.raises(ValueError):
        store.restore(bad)
    assert store.document == initial and store.version == 0


def test_optional_anchor_hint_stays_missing(nrel_contract):
    module, surface, _initial, document = nrel_contract
    del document['defects'][0]['anchor']['region_hint']
    assert module.validate_document(document, surface) == document
