from types import SimpleNamespace
from wfrl_blender.deflection import ComparisonView

import numpy as np
import pytest

from wfrl_blender import split_turbine_context as context


class VisibilityObject:
    def __init__(self):
        self.hidden = False
        self.hide_render = False
        self.writes = 0

    def hide_get(self):
        return self.hidden

    def hide_set(self, value):
        self.hidden = value
        self.writes += 1


def test_hidden_comparison_recovers_external_change_without_repeated_writes():
    obj = VisibilityObject()
    view = SimpleNamespace(objects=[obj], labels=['old label'])
    ComparisonView.visible(view, False)
    ComparisonView.visible(view, False)
    assert obj.writes == 1 and obj.hide_render and not view.labels
    obj.hidden = False  # External UI edit must not defeat the next update.
    ComparisonView.visible(view, False)
    assert obj.writes == 2
    ComparisonView.visible(view, True)
    assert obj.writes == 3 and not obj.hidden and not obj.hide_render


class ContextMatrix:
    def __init__(self, value=None):
        self.value = np.eye(4) if value is None else np.array(value, dtype=float)

    @classmethod
    def Translation(cls, values):
        result = cls()
        result.value[:3, 3] = values
        return result

    def copy(self):
        return type(self)(self.value)

    def __matmul__(self, other):
        return type(self)(self.value @ other.value)

    def __eq__(self, other):
        return isinstance(other, type(self)) and np.array_equal(self.value, other.value)


class ContextObject(dict):
    def __init__(self, name, kind='MESH'):
        super().__init__()
        self.name, self.type, self.data = name, kind, object()
        self.parent, self.parent_type = None, 'OBJECT'
        self.matrix_basis = ContextMatrix()
        self._matrix_world = ContextMatrix()
        self._hide_viewport = self._hide_render = self.hidden = False
        self.writes = 0
        self.constraints = []

    def as_pointer(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    def copy(self):
        obj = type(self)(self.name, self.type)
        obj.data = self.data
        obj.update(self)
        return obj

    def animation_data_clear(self):
        pass

    def hide_set(self, value):
        self.hidden = value

    def hide_get(self):
        return self.hidden

    @property
    def matrix_world(self):
        return self._matrix_world

    @matrix_world.setter
    def matrix_world(self, value):
        self._matrix_world = value
        self.writes += 1

    @property
    def hide_viewport(self):
        return self._hide_viewport

    @hide_viewport.setter
    def hide_viewport(self, value):
        self._hide_viewport = value
        self.writes += 1

    @property
    def hide_render(self):
        return self._hide_render

    @hide_render.setter
    def hide_render(self, value):
        self._hide_render = value
        self.writes += 1


class ContextObjects(list):
    scans = 0

    def __iter__(self):
        self.scans += 1
        return super().__iter__()

    def get(self, name):
        return next((obj for obj in list.__iter__(self) if obj.name == name), None)


class ContextScene(dict):
    def as_pointer(self):
        return id(self)


@pytest.fixture
def context_fixture(monkeypatch):
    import sys
    scene = ContextScene()
    scene.objects = ContextObjects(ContextObject(context.SOURCE_PREFIX + part)
                                   for part in context._CORE)
    yaw = ContextObject(context.SOURCE_PREFIX + 'YawRoot', 'EMPTY')
    scene.objects.append(yaw)
    scene.collection = SimpleNamespace(objects=SimpleNamespace(link=scene.objects.append))
    monkeypatch.setitem(sys.modules, 'mathutils', SimpleNamespace(Matrix=ContextMatrix))
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(data=SimpleNamespace(objects=SimpleNamespace(
        get=scene.objects.get, remove=lambda obj, **_kw: scene.objects.remove(obj)))))
    monkeypatch.setattr(context, '_model_inverse', lambda *_a: ContextMatrix())
    context.clear_runtime()
    spec = dict(prefix='SplitRecon.Reference.', offset=(0., 10000., 0.),
                names_key='split_reconstruction_structure_names')
    objects = context.ensure(scene, **spec)
    yield scene, spec, objects
    context.clear_runtime()


def test_stable_context_refresh_and_sync_do_not_scan_or_write(context_fixture):
    scene, spec, objects = context_fixture
    scans = scene.objects.scans
    writes = [obj.writes for obj in objects]
    for _ in range(20):
        assert context.refresh(scene, **spec) == objects
        context.sync(scene)
    assert scene.objects.scans == scans
    assert [obj.writes for obj in objects] == writes
    # New source channels still synchronize and source selection stays separate.
    source = scene.objects.get(context.SOURCE_PREFIX + 'Tower')
    source.matrix_basis = ContextMatrix.Translation((1., 2., 3.))
    source.hide_viewport = source.hide_render = True
    objects[0].hide_set(True)
    context.sync(scene)
    assert objects[0].matrix_world == ContextMatrix.Translation((1., 10002., 3.))
    assert objects[0].hide_get() and not objects[0].hide_viewport and not objects[0].hide_render


def test_context_mesh_and_rig_replacements_rebind_without_replacing_display(context_fixture):
    scene, spec, objects = context_fixture
    source = scene.objects.get(context.SOURCE_PREFIX + 'Tower')
    source.data = object()
    context.sync(scene)
    assert objects[0].data is source.data
    replacement = ContextObject(source.name)
    scene.objects.remove(source)
    scene.objects.append(replacement)
    assert context.refresh(scene, **spec) == objects
    assert objects[0].data is replacement.data
    context.clear_runtime(scene)
    assert context.refresh(scene, **spec) == objects


@pytest.mark.parametrize('damage', ['source_delete', 'target_unlink', 'duplicate_list', 'wrong_classification'])
def test_context_cache_never_hides_deleted_ids_or_invalid_saved_provenance(context_fixture, damage):
    scene, spec, objects = context_fixture
    if damage == 'source_delete':
        scene.objects.remove(scene.objects.get(context.SOURCE_PREFIX + 'Tower'))
    elif damage == 'target_unlink':
        scene.objects.remove(objects[0])
    elif damage == 'duplicate_list':
        scene[spec['names_key']].append(objects[0].name)
    else:
        objects[0]['split_turbine_context_classification'] = 'RECONSTRUCTED'
    with pytest.raises(ValueError):
        context.refresh(scene, **spec)


def test_new_source_is_not_missed_when_object_count_is_unchanged(context_fixture):
    scene, spec, _objects = context_fixture
    unrelated = ContextObject('unrelated')
    scene.objects.append(unrelated)
    context.refresh(scene, **spec)
    scene.objects.remove(unrelated)
    source = ContextObject(context.SOURCE_PREFIX + 'YawSeal')
    scene.objects.append(source)
    context.notice_updates(scene, SimpleNamespace(updates=[SimpleNamespace(id=source)]))
    objects = context.refresh(scene, **spec)
    assert len(objects) == 5 and objects[-1].data is source.data


def test_deleted_depsgraph_update_invalidates_safely(context_fixture):
    scene, spec, objects = context_fixture
    class DeletedUpdate:
        @property
        def id(self):
            raise ReferenceError('removed ID')
    context.notice_updates(scene, SimpleNamespace(updates=[DeletedUpdate()]))
    scans = scene.objects.scans
    assert context.refresh(scene, **spec) == objects
    assert scene.objects.scans > scans
