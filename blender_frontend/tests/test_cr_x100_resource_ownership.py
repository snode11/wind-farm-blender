"""X100 installation cleanup preserves external data and releases owned resources."""
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender.radar_x100 import housing_model, view


class ID(dict):
    __eq__ = object.__eq__
    __ne__ = object.__ne__
    __hash__ = object.__hash__

    def __init__(self, name):
        super().__init__()
        self.name = name
        self._users = 0
        self.use_fake_user = False

    @property
    def users(self):
        return self._users + int(self.use_fake_user)


class Links(list):
    def __init__(self, env, kind, owner):
        super().__init__()
        self.env, self.kind, self.owner = env, kind, owner

    def link(self, item):
        if self.env.failure == 'collection-link' and self.kind == 'children':
            raise RuntimeError('injected collection-link')
        if self.env.failure == 'object-link' and item.name == 'WFRL.X100.Housing':
            raise RuntimeError('injected object-link')
        self.append(item)
        item._users += 1
        if self.kind == 'objects':
            item.users_collection.append(self.owner)

    def unlink(self, item):
        self.remove(item)
        item._users -= 1
        if self.kind == 'objects':
            item.users_collection.remove(self.owner)


class Collection(ID):
    def __init__(self, name, env):
        super().__init__(name)
        self.objects = Links(env, 'objects', self)
        self.children = Links(env, 'children', self)

    @property
    def children_recursive(self):
        return [child for direct in self.children for child in (direct, *direct.children_recursive)]


class Object(ID):
    def __init__(self, name, env, data=None):
        super().__init__(name)
        self.env, self._data = env, None
        self.users_collection = []
        self.parent = None
        self.data = data

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value):
        if self._data is not None:
            self._data._users -= 1
        self._data = value
        if value is not None:
            value._users += 1

    @property
    def users_scene(self):
        return [scene for scene in self.env.scenes if self in scene.objects]

    def hide_set(self, hidden, **kwargs):
        self.hidden = hidden


class Material(ID):
    def __init__(self, name, env):
        super().__init__(name)
        self.env, self.node_tree = env, None

    def __setattr__(self, name, value):
        if name == 'metallic' and self.env.failure == 'material-config':
            raise RuntimeError('injected material-config')
        super().__setattr__(name, value)


class MaterialSlots(list):
    def __init__(self, env):
        super().__init__()
        self.env = env

    def append(self, material):
        if self.env.failure == 'material-append':
            raise RuntimeError('injected material-append')
        super().append(material)
        material._users += 1


class Mesh(ID):
    def __init__(self, name, env):
        super().__init__(name)
        self.env = env
        self.materials = MaterialSlots(env)
        self.polygons = SimpleNamespace(foreach_set=lambda *args: None)

    def from_pydata(self, vertices, edges, faces):
        if self.env.failure == 'mesh' or self.env.failure == 'fov' and self.name == 'WFRL.X100.FOV':
            raise RuntimeError('injected ' + self.env.failure)
        self.geometry = vertices, edges, faces

    def update(self):
        pass


class Pool(list):
    def __init__(self, factory, env):
        super().__init__()
        self.factory, self.env = factory, env

    def new(self, name, *args):
        value = self.factory(name, self.env, *args)
        self.append(value)
        return value

    def get(self, name):
        return next((item for item in self if item.name == name), None)

    def remove(self, value, **kwargs):
        if isinstance(value, Object):
            for collection in list(value.users_collection):
                collection.objects.unlink(value)
            value.data = None
        elif isinstance(value, Mesh):
            assert value.users == 0
            for material in value.materials:
                material._users -= 1
        elif isinstance(value, Material):
            assert value.users == 0
        elif isinstance(value, Collection):
            for parent in [scene.collection for scene in self.env.scenes] + list(self.env.collections):
                if value in parent.children:
                    parent.children.unlink(value)
            for child in list(value.children):
                value.children.unlink(child)
        super().remove(value)


class Scene(ID):
    def __init__(self, name, env):
        super().__init__(name)
        self.collection = Collection(name + ' root', env)
        self.view_layers = []

    @property
    def objects(self):
        return list(dict.fromkeys(obj for collection in (self.collection, *self.collection.children_recursive)
                                  for obj in collection.objects))


@pytest.fixture
def resources(monkeypatch):
    env = SimpleNamespace(failure=None, scenes=[])
    for name, factory in [('objects', Object), ('meshes', Mesh), ('materials', Material), ('collections', Collection)]:
        setattr(env, name, Pool(factory, env))
    env.scene = Scene('source', env)
    env.scenes.append(env.scene)
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(data=env, types=SimpleNamespace(Mesh=Mesh)))
    monkeypatch.setitem(sys.modules, 'mathutils', SimpleNamespace(Matrix=np.array))
    return env


def install(env, owner='radar'):
    env.scene['wfrl_x100_owner'] = owner
    return view.install(env.scene, owner, np.eye(4))


@pytest.mark.parametrize('failure', ['collection-link', 'object-link', 'mesh', 'material-config', 'material-append', 'fov'])
def test_install_failure_releases_only_resources_created_by_the_attempt(resources, failure):
    env = resources
    external = env.materials.new(housing_model.MATERIALS[0][0])
    env.failure = failure
    with pytest.raises(RuntimeError, match='injected'):
        install(env)
    assert not env.objects and not env.meshes and not env.collections
    assert list(env.materials) == [external]


def test_stop_preserves_user_replacement_mesh_and_releases_detached_owned_mesh(resources):
    env = resources
    _, fov = install(env)
    original = fov.data
    external = env.meshes.new('user mesh')
    fov.data = external
    view.remove_objects(env.scene)
    assert external in env.meshes
    assert original not in env.meshes
    assert not env.objects and not env.collections and not env.materials


@pytest.mark.parametrize('preserve', ['existing', 'shared', 'fake-user'])
def test_stop_reclaims_only_unused_generated_materials(resources, preserve):
    env = resources
    if preserve == 'existing':
        kept = env.materials.new(housing_model.MATERIALS[0][0])
    install(env)
    kept = env.materials[0]
    if preserve == 'shared':
        external = env.meshes.new('external mesh')
        external.materials.append(kept)
    elif preserve == 'fake-user':
        kept.use_fake_user = True
    view.remove_objects(env.scene)
    assert list(env.materials) == [kept]


def test_stop_preserves_user_collection_nested_under_installation(resources):
    env = resources
    install(env)
    parent = env.scene.collection.children[0]
    user = env.collections.new('user children')
    parent.children.link(user)
    obj = env.objects.new('user object')
    user.objects.link(obj)
    view.remove_objects(env.scene)
    assert obj in env.scene.objects
    assert user in env.scene.collection.children_recursive


def test_shared_scene_and_mesh_survive_until_the_last_owner_releases_them(resources):
    env = resources
    _, fov = install(env)
    mesh = fov.data
    other = Scene('linked scene', env)
    env.scenes.append(other)
    other['wfrl_x100_owner'] = 'radar'
    other.collection.children.link(env.scene.collection.children[0])
    view.remove_objects(env.scene)
    assert not env.scene.objects and fov in other.objects and mesh in env.meshes
    view.remove_objects(other)
    assert not env.objects and not env.meshes and not env.materials and not env.collections


def test_deleted_scene_cleanup_releases_owned_orphans_and_preserves_external_mesh(resources):
    env = resources
    _, fov = install(env)
    external = env.meshes.new('user mesh')
    fov.data = external
    env.scenes.clear()
    env.scene.collection.children.unlink(env.collections[0])
    view.remove_orphans('radar')
    assert list(env.meshes) == [external]
    assert not env.objects and not env.collections and not env.materials


def test_deleted_scene_cleanup_preserves_an_instanced_radar_collection(resources):
    env = resources
    mount, fov = install(env)
    collection = env.scene.collection.children[0]
    collection._users += 1  # A collection-instance object owns a separate collection reference.
    env.scene.collection.children.unlink(collection)
    env.scenes.clear()
    view.remove_orphans('radar')
    assert mount in env.objects and fov in env.objects
    assert len(collection.objects) == 3 and len(env.materials) == len(housing_model.MATERIALS)
    collection._users -= 1
    view.remove_orphans('radar')
    assert not env.objects and not env.meshes and not env.materials and not env.collections


def test_mount_preset_preserves_external_fov_mesh(resources, monkeypatch, stub_module):
    from wfrl_blender.radar_x100 import mount_presets, runtime
    env = resources
    mount, fov = install(env)
    external = env.meshes.new('user fov mesh')
    external['wfrl_x100_field_of_view_deg'] = [30., 10.]
    fov.data = external
    flex, yaw = object(), object()
    sampler = SimpleNamespace(flex=flex, validate=lambda: None, geometry_id='geometry',
                              calibration_id='old', field_of_view=(30., 10.), field_of_view_shape='ANGULAR')
    sampler.with_installation = lambda *args: SimpleNamespace(calibration_id='new', geometry_id='geometry',
        mount_from_sensor=np.eye(4))
    mount.matrix_parent_inverse = mount.matrix_basis = np.eye(4)
    current = SimpleNamespace(owner='radar', fanout=None, closing=False, mode='SIMULATION',
        mount=mount, fov=fov, sampler=sampler, display_valid=True, frame=object(), fov_shown=True,
        observation_mode='GEOMETRY', pair_context=lambda frame: None, derive_blades=lambda: None,
        capture_configuration=lambda: None)
    scene = env.scene
    for name, value in dict(source='SIMULATION', mount='NACELLE', location=(0.,)*3, rotation=(0.,)*3,
        field_of_view=(30., 10.), field_of_view_shape='ANGULAR', color='RANGE', color_scale='FIXED',
        velocity_limit=10.).items():
        setattr(scene, 'wfrl_x100_' + name, value)
    real_objects = Scene.objects
    class SceneObjects(list):
        def __getitem__(self, key):
            return yaw if key == 'WFRL.Turbine.T1.YawRoot' else super().__getitem__(key)
    monkeypatch.setattr(Scene, 'objects', property(lambda scene: SceneObjects(real_objects.fget(scene))))
    stub_module('wfrl_blender.farm_flex', active_for=lambda scene: flex, is_active=lambda scene: True)
    monkeypatch.setattr(runtime, 'session_for', lambda scene: current)
    monkeypatch.setattr(runtime, '_guard_installation', lambda: None)
    monkeypatch.setattr(runtime, '_validate_mount_drivers', lambda mount: None)
    monkeypatch.setattr(runtime, '_simulation_time', lambda *args: (0, 0.))
    monkeypatch.setattr(runtime, '_simulation_frame', lambda *args, **kwargs: SimpleNamespace(
        completeness='COMPLETE', stream='GEOMETRY', source_kind='GEOMETRY_SIMULATION'))
    monkeypatch.setattr(view, 'existing_installation', lambda *args: (mount, fov))
    monkeypatch.setattr(view, 'housing', lambda mount: SimpleNamespace(hide_viewport=False, hide_render=False,
                                                                    get=lambda key: None))
    monkeypatch.setattr(view, 'sync_housing_visibility', lambda *args: None)
    class Matrix:
        def __new__(cls, value):
            return np.array(value)
        Identity = staticmethod(np.eye)
    monkeypatch.setitem(sys.modules, 'mathutils', SimpleNamespace(Matrix=Matrix, Euler=lambda *args: SimpleNamespace(to_matrix=lambda: np.eye(3))))
    sys.modules['bpy'].app = SimpleNamespace(background=True)
    mount_presets.apply_mount_preset(scene, 'ORIGINAL')
    assert external in env.meshes
    assert fov.data is not external
