"""Exercise publication cleanup with independently invalidated Object and Camera RNA."""
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_cameras as core


class CameraData:
    def __init__(self, name):
        self._name, self._users, self.removed = name, 0, False

    def check(self):
        if self.removed:
            raise ReferenceError('Camera RNA has been removed')

    @property
    def name(self):
        self.check()
        return self._name

    @name.setter
    def name(self, value):
        self.check()
        self._name = value

    @property
    def users(self):
        self.check()
        return self._users


class Camera:
    def __init__(self, name, data, slot, installed=True):
        self._name, self._data = name, data
        self.removed, self._collections = False, []
        self.values = {'wfrl_custom_slot': slot, 'wfrl_custom_camera': installed,
                       'wfrl_custom_draft': not installed}
        data._users += 1

    def check(self):
        if self.removed:
            raise ReferenceError('Object RNA has been removed')

    @property
    def name(self):
        self.check()
        return self._name

    @name.setter
    def name(self, value):
        self.check()
        self._name = value

    @property
    def data(self):
        self.check()
        return self._data

    @property
    def type(self):
        return 'CAMERA' if self.data is not None else 'EMPTY'

    @property
    def users_collection(self):
        self.check()
        return tuple(self._collections)

    def get(self, key, default=None):
        self.check()
        return self.values.get(key, default)

    def __setitem__(self, key, value):
        self.check()
        self.values[key] = value


class Links:
    def __init__(self, owner):
        self.owner, self.items = owner, []

    def __iter__(self):
        return iter(self.items)

    def __contains__(self, name):
        return any(obj.name == name for obj in self.items)

    def get(self, name):
        return next((obj for obj in self.items if obj.name == name), None)

    def link(self, obj):
        self.items.append(obj)
        obj._collections.append(self.owner)

    def unlink(self, obj):
        self.items.remove(obj)
        obj._collections.remove(self.owner)


class Resources:
    def __init__(self, kind, objects=None):
        self.kind, self.objects = kind, objects
        self.items, self.removed, self.failures = [], [], {}

    def remove(self, item, **_kwargs):
        item.check()
        failures = self.failures.get(item, 0)
        if failures:
            self.failures[item] = failures - 1
            raise RuntimeError('injected ' + self.kind + ' release failure')
        if self.kind == 'Object':
            for collection in tuple(item.users_collection):
                collection.objects.unlink(item)
            if item.data is not None:
                item.data._users -= 1
        else:
            for obj in self.objects.items:
                if obj.data is item:
                    obj._data = None
                    item._users -= 1
        item.removed = True
        self.items.remove(item)
        self.removed.append(item)


class Scene:
    def __init__(self, name, objects):
        self.name, self.objects = name, objects
        self.camera = None
        self.timeline_markers = []

    def user_of_id(self, obj):
        return (int(self.camera is obj) + sum(item is obj for item in self.objects)
                + sum(marker.camera is obj for marker in self.timeline_markers))


@pytest.fixture
def runtime(monkeypatch):
    objects = Resources('Object')
    cameras = Resources('Camera', objects)
    collection = NS()
    collection.objects = Links(collection)
    scene = Scene('CurrentScene', collection.objects)
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(objects=objects, cameras=cameras, screens=[], scenes=[scene])))
    monkeypatch.setitem(sys.modules, 'wfrl_blender.panels.custom_cameras',
                        NS(_ACTIVE=None, _VIEWING={'WATCH', 'LAYOUT'}))
    monkeypatch.setattr(core, '_refresh_references', lambda _scene: None)
    monkeypatch.setattr(core, '_RETIRED', [])
    monkeypatch.setattr(core, '_RETIRED_DATA', [], raising=False)

    def create(slot, installed=True, data=None):
        if data is None:
            data = CameraData(f'CameraData.{len(cameras.items)}')
            cameras.items.append(data)
        obj = Camera(f'Camera.{len(objects.items)}', data, slot, installed)
        objects.items.append(obj)
        collection.objects.link(obj)
        return obj

    old = {slot: create(slot) for slot in core.SLOTS}
    replacement = {slot: create(slot, installed=False) for slot in core.SLOTS}
    scene.camera = old[1]
    return NS(scene=scene, objects=objects, cameras=cameras, create=create,
              old=old, replacement=replacement, monkeypatch=monkeypatch)


def assert_published(runtime):
    assert all(core.get_camera(runtime.scene, slot) is obj
               for slot, obj in runtime.replacement.items())
    assert runtime.scene.camera is runtime.replacement[1]
    assert all(not obj.removed and obj.data in runtime.cameras.items
               for obj in runtime.replacement.values())


@pytest.mark.parametrize('reference_kind', ['camera', 'marker', 'collection'])
def test_foreign_scene_reference_rejects_publication_before_cleanup(runtime, reference_kind):
    old = runtime.old[2]
    collection = NS()
    collection.objects = Links(collection)
    other = Scene('ExternalCameraOwner', collection.objects)
    if reference_kind == 'camera':
        other.camera = old
    elif reference_kind == 'marker':
        other.timeline_markers.append(NS(camera=old))
    else:
        collection.objects.link(old)
    sys.modules['bpy'].data.scenes.append(other)
    cleanup = []
    checkpoints = []
    runtime.monkeypatch.setattr(core, 'cleanup_retired', lambda: cleanup.append(True))
    runtime.monkeypatch.setattr(core, '_publication_checkpoint', lambda *args: checkpoints.append(args))
    names = [(obj, obj.name, obj.data.name) for obj in runtime.objects.items]

    with pytest.raises(ValueError, match='ExternalCameraOwner'):
        core._publish(runtime.scene, runtime.replacement)

    assert not cleanup and not checkpoints
    assert all(not obj.removed and obj.name == name and obj.data.name == data_name
               for obj, name, data_name in names)
    assert runtime.scene.camera is runtime.old[1]
    assert all(core.get_camera(runtime.scene, slot) is obj for slot, obj in runtime.old.items())
    assert other.user_of_id(old) == 1
    assert all(obj.get('wfrl_custom_draft') for obj in runtime.replacement.values())


def test_camera_release_failure_after_object_removal_remains_retryable(runtime):
    old, data = runtime.old[1], runtime.old[1].data
    runtime.cameras.failures[data] = 1
    core._publish(runtime.scene, runtime.replacement)
    assert_published(runtime)
    assert old.removed and data.users == 0 and data in runtime.cameras.items
    assert len(core._RETIRED) == 1
    core.cleanup_retired()
    assert data.removed
    assert not core._RETIRED and not core._RETIRED_DATA
    removed = list(runtime.cameras.removed)
    core.cleanup_retired()
    assert runtime.cameras.removed == removed
    assert_published(runtime)


@pytest.mark.parametrize('missing', ['object', 'data', 'both'])
def test_external_deletion_of_pending_resource_does_not_lose_the_other(runtime, missing):
    old, data = runtime.old[1], runtime.old[1].data
    runtime.objects.failures[old] = 1
    core._publish(runtime.scene, runtime.replacement)
    assert_published(runtime)
    if missing in {'object', 'both'}:
        runtime.objects.remove(old)
    if missing in {'data', 'both'}:
        runtime.cameras.remove(data)
    core.cleanup_retired()
    assert old.removed and data.removed
    assert not core._RETIRED and not core._RETIRED_DATA
    assert_published(runtime)


def test_repeated_object_and_data_failures_keep_independent_pending_resources(runtime):
    old, data = runtime.old[1], runtime.old[1].data
    runtime.objects.failures[old] = 2
    runtime.cameras.failures[data] = 3
    core._publish(runtime.scene, runtime.replacement)
    core.cleanup_retired()
    assert not old.removed and data.users == 1
    assert_published(runtime)
    core.cleanup_retired()
    assert old.removed and not data.removed
    assert len(core._RETIRED_DATA) == 1
    for _ in range(3):
        core.cleanup_retired()
    assert data.removed
    assert not core._RETIRED and not core._RETIRED_DATA
    assert_published(runtime)


def test_pending_data_is_preserved_while_shared_and_retried_when_unused(runtime):
    old, data = runtime.old[1], runtime.old[1].data
    runtime.cameras.failures[data] = 1
    core._publish(runtime.scene, runtime.replacement)
    shared = runtime.create(0, installed=False, data=data)
    core.cleanup_retired()
    core.cleanup_retired()
    assert not data.removed and data.users == 1 and not shared.removed
    runtime.objects.remove(shared)
    core.cleanup_retired()
    assert data.removed
    assert not core._RETIRED and not core._RETIRED_DATA
    assert_published(runtime)


def test_successful_removal_does_not_claim_shared_user_data(runtime):
    data = runtime.old[1].data
    shared = runtime.create(0, installed=False, data=data)
    core._publish(runtime.scene, runtime.replacement)
    core.cleanup_retired()
    assert data.users == 1 and not data.removed
    assert not core._RETIRED and not core._RETIRED_DATA
    runtime.objects.remove(shared)
    core.cleanup_retired()
    assert data.users == 0 and not data.removed
    assert_published(runtime)


def test_failed_objects_sharing_data_have_one_data_retry_record(runtime):
    first, second = runtime.old[1], runtime.old[2]
    data = first.data
    second._data._users -= 1
    second._data = data
    data._users += 1
    runtime.objects.failures[first] = 1
    runtime.objects.failures[second] = 1
    core._publish(runtime.scene, runtime.replacement)
    assert len(core._RETIRED) == 2 and len(core._RETIRED_DATA) == 1
    core.cleanup_retired()
    assert first.removed and second.removed and data.removed
    assert runtime.cameras.removed.count(data) == 1
    assert not core._RETIRED and not core._RETIRED_DATA
    assert_published(runtime)
