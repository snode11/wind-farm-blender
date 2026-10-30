"""Manual ownership checks retain identity without traversing unrelated objects."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'wfrl_blender/manual_control.py'


class Object:
    def __init__(self, name):
        self.name = name
        self.users_collection = ()
        self.reference_users = ()

    def as_pointer(self):
        return id(self)


class Objects(dict):
    def __init__(self, objects, *, iterable=True):
        super().__init__((obj.name, obj) for obj in objects)
        self.iterable = iterable

    def __iter__(self):
        assert self.iterable, 'Ownership validation traversed unrelated scene objects'
        return iter(self.values())


class Collection:
    children_recursive = ()

    def as_pointer(self):
        return id(self)


class Scene:
    def __init__(self, *, name, objects=None, collection=None):
        self.name = name
        self.objects = objects
        self.collection = collection or Collection()


@pytest.fixture
def ownership():
    tree = ast.parse(SOURCE.read_text())
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
            and node.name == 'other_scene_objects']
    scenes = []
    def user_map(*, subset, value_types):
        return {obj: set(obj.users_collection) | set(obj.reference_users) for obj in subset}

    namespace = {'bpy': SimpleNamespace(data=SimpleNamespace(scenes=scenes, user_map=user_map))}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['other_scene_objects'], scenes


def test_filtered_query_avoids_foreign_object_iteration(ownership):
    shared, scenes = ownership
    owner = Scene(name='owner')
    controlled, unrelated = Object('controlled'), Object('unrelated')
    collection = Collection()
    controlled.users_collection = (collection,)
    scenes.extend([owner, Scene(name='other', objects=Objects(
        [controlled, unrelated], iterable=False), collection=collection)])
    assert shared(owner, objects=[controlled]) == {controlled.as_pointer()}


def test_same_name_requires_the_same_object_identity(ownership):
    shared, scenes = ownership
    controlled, namesake = Object('controlled'), Object('controlled')
    owner = Scene(name='owner', objects=Objects([controlled]))
    collection = Collection()
    namesake.users_collection = (collection,)
    scenes.extend([owner, Scene(name='other', objects=Objects([namesake], iterable=False),
                               collection=collection)])
    assert shared(owner, objects=[controlled]) == set()
    controlled.users_collection = (collection,)
    assert shared(owner, objects=[controlled]) == set()
    scenes[1].objects[controlled.name] = controlled
    assert shared(owner, objects=[controlled]) == {controlled.as_pointer()}


def test_foreign_metadata_reference_is_not_scene_membership(ownership):
    shared, scenes = ownership
    controlled = Object('controlled')
    owner = Scene(name='owner', objects=Objects([controlled]))
    other = Scene(name='metadata', objects=Objects([], iterable=False))
    scenes.extend([owner, other])
    controlled.reference_users = (other, other.collection)
    assert shared(owner, objects=[controlled]) == set()


def test_unfiltered_query_retains_all_foreign_objects(ownership):
    shared, scenes = ownership
    controlled, unrelated = Object('controlled'), Object('unrelated')
    owner = Scene(name='owner', objects=Objects([controlled]))
    scenes.extend([owner, Scene(name='other', objects=Objects([unrelated]))])
    assert shared(owner) == {unrelated.as_pointer()}
