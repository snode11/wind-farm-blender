"""Two-phase whole-document transactions with monotonic analysis versions."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import uuid

from .defect_schema import new_document, validate_document


@dataclass
class PreparedState:
    document: dict
    resource: object
    base_version: int
    owner: str
    consumed: bool = False
    def __post_init__(self):
        self._document_digest = _digest(self.document)


def _digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


class DefectStore:
    """Prepare derived geometry before atomically activating one full state.

    prepare(document)->resource must leave the live state unchanged.
    activate(resource,document,version) must be atomic or restore itself on error.
    dispose(resource) releases a candidate or superseded derived state. Undo/redo
    rebuild from the healthy baseline, never mutate a damaged mesh in place.
    """
    def __init__(self, surface, document=None, prepare=None, activate=None, dispose=None):
        self.surface = surface
        self._prepare = prepare or (lambda document: None)
        self._activate = activate or (lambda resource, document, version: None)
        self._dispose = dispose or (lambda resource: None)
        self._owner = uuid.uuid4().hex
        self._document = validate_document(document if document is not None else new_document(surface), surface)
        self._version = 0; self._undo = []; self._redo = []
        self._resource = self._prepare(deepcopy(self._document))
        try: self._activate(self._resource, deepcopy(self._document), self._version)
        except Exception:
            self._dispose(self._resource)
            raise

    @property
    def document(self): return deepcopy(self._document)
    @property
    def version(self): return self._version
    @property
    def resource(self): return self._resource
    @property
    def can_undo(self): return bool(self._undo)
    @property
    def can_redo(self): return bool(self._redo)

    def prepare(self, candidate):
        document = validate_document(candidate, self.surface)
        old = {d['id']: d for d in self._document['defects']}
        for defect in document['defects']:
            previous = old.get(defect['id'])
            if previous is not None:
                before, after = deepcopy(previous), deepcopy(defect)
                before.pop('revision'); after.pop('revision')
                if before != after and defect['revision'] <= previous['revision']:
                    raise ValueError('Modified defect must retain id and increase revision')
                if defect['revision'] < previous['revision']:
                    raise ValueError('Defect revision cannot decrease')
        resource = self._prepare(deepcopy(document))
        return PreparedState(deepcopy(document), resource, self._version, self._owner)

    def _check(self, token):
        if not isinstance(token, PreparedState) or token.owner != self._owner or token.consumed:
            raise ValueError('Invalid or consumed transaction')
        if token.base_version != self._version: raise ValueError('STALE_STATE: transaction prepared against an older state')

    def _replace(self, token):
        self._check(token)
        # Revalidate guards callers modifying token.document after preparation.
        validate_document(token.document, self.surface)
        if _digest(token.document) != token._document_digest:
            raise ValueError('Candidate changed after preparation')
        old_resource = self._resource
        try:
            self._activate(token.resource, deepcopy(token.document), self._version+1)
        except Exception as error:
            # Callbacks are expected to activate atomically; also explicitly
            # restore the committed resources for callbacks with partial swaps.
            try: self._activate(old_resource, deepcopy(self._document), self._version)
            except Exception as rollback_error:
                if hasattr(error, 'add_note'): error.add_note(f'Activation rollback also failed: {rollback_error}')
            raise
        self._document = deepcopy(token.document); self._resource = token.resource
        self._version += 1; token.consumed = True
        if old_resource is not self._resource: self._dispose(old_resource)
        return self._version

    def commit(self, token):
        previous = self.document
        result = self._replace(token)
        self._undo.append(previous); self._redo.clear()
        return result

    def restore(self, document):
        """Explicitly load a saved snapshot, retaining its historical revisions.

        This differs from editing an existing instance: restored revisions need
        not exceed the live revisions.  State version still increases, derived
        resources are rebuilt, and undo returns to the entire pre-load state.
        """
        candidate = validate_document(document, self.surface)
        resource = self._prepare(deepcopy(candidate))
        token = PreparedState(candidate, resource, self._version, self._owner)
        try: return self.commit(token)
        except Exception:
            if not token.consumed: self.cancel(token)
            raise

    def cancel(self, token):
        # Stale candidates still own disposable resources and may be cancelled.
        if not isinstance(token, PreparedState) or token.owner != self._owner or token.consumed:
            raise ValueError('Invalid or consumed transaction')
        self._dispose(token.resource); token.consumed = True

    def _restore(self, source, target):
        if not source: return False
        document = validate_document(source[-1], self.surface)
        resource = self._prepare(deepcopy(document))
        token = PreparedState(document, resource, self._version, self._owner)
        previous = self.document
        try: self._replace(token)
        except Exception:
            if not token.consumed: self.cancel(token)
            raise
        source.pop(); target.append(previous)
        return True

    def undo(self): return self._restore(self._undo, self._redo)
    def redo(self): return self._restore(self._redo, self._undo)

    def accepts_version(self, version):
        """Asynchronous analysis must check this immediately before publishing."""
        return type(version) is int and version == self._version
