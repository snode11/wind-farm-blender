"""Owned preview resources survive failures without damaging shared Blender IDs."""
import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from wfrl_blender import blade_flex_preview as flex, cinematic, repair_marks as repair


class IDs(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)

    def __contains__(self, item):
        return self.get(item) is not None if isinstance(item, str) else super().__contains__(item)

    def __getitem__(self, key):
        if isinstance(key, str):
            found = self.get(key)
            if found is None:
                raise KeyError(key)
            return found
        return super().__getitem__(key)


class Block(dict):
    __eq__ = object.__eq__
    __ne__ = object.__ne__
    __hash__ = object.__hash__

    def __init__(self, env, name, kind):
        super().__init__()
        self.env, self.name, self.kind = env, name, kind
        self.users = 0
        self.use_fake_user = False
        self.library = self.shape_keys = None
        self.deleted = False
        self.node_tree = MagicMock()
        self.nodes, self.links, self.interface = MagicMock(), MagicMock(), MagicMock()
        if kind == 'materials' and env.failure == 'material_nodes':
            self.node_tree.nodes.new.side_effect = RuntimeError('material_nodes')
            self.node_tree.nodes.get.side_effect = RuntimeError('material_nodes')
        if kind == 'node_groups' and env.failure == 'attachment_socket':
            self.interface.new_socket.side_effect = RuntimeError('attachment_socket')

    def __bool__(self):
        return True

    @property
    def name(self):
        if getattr(self, 'deleted', False):
            raise ReferenceError('Removed ID')
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    def as_pointer(self):
        return id(self)


class Materials(list):
    def append(self, value):
        super().append(value)
        value.users += 1

    def clear(self):
        for value in self:
            value.users -= 1
        super().clear()


class Vertices:
    def __init__(self, values):
        self.coords = np.asarray(values, dtype=np.float32).reshape(-1, 3)

    def __len__(self):
        return len(self.coords)

    def foreach_get(self, _name, target):
        target[:] = self.coords.ravel()

    def foreach_set(self, _name, values):
        self.coords[:] = np.asarray(values).reshape(-1, 3)


class Splines(list):
    def __init__(self, env):
        super().__init__()
        self.env = env

    def new(self, _kind):
        if self.env.failure == 'spline':
            raise RuntimeError('spline')
        class Points(list):
            def add(self, count):
                self.extend(SimpleNamespace(co=None) for _ in range(count))
        spline = SimpleNamespace(type='POLY', points=Points([SimpleNamespace(co=None)]))
        self.append(spline)
        return spline


class Geometry(Block):
    def __init__(self, env, name, kind):
        super().__init__(env, name, kind)
        self.materials = Materials()
        self.vertices = Vertices(((0, 0, 1.5), (1, 0, 2.), (0, 0, 3.)))
        self.polygons = []
        self.uv_layers, self.attributes = MagicMock(), MagicMock()
        self.splines = Splines(env)

    def from_pydata(self, vertices, _edges, _faces):
        if self.env.failure == 'mesh_data':
            raise RuntimeError('mesh_data')
        self.vertices = Vertices(vertices)

    def update(self):
        pass

    def copy(self):
        if self.env.failure == 'mesh_copy' and self.env.copy_count == 1:
            raise RuntimeError('mesh_copy')
        self.env.copy_count += 1
        result = self.env.data.meshes.new(self.name)
        result.vertices = Vertices(self.vertices.coords.copy())
        return result


class Modifier:
    type = 'NODES'

    def __init__(self):
        self._node_group = None

    @property
    def node_group(self):
        return self._node_group

    @node_group.setter
    def node_group(self, value):
        if self._node_group is not None:
            self._node_group.users -= 1
        self._node_group = value
        if value is not None:
            value.users += 1


class Modifiers(list):
    def new(self, *_args):
        value = Modifier()
        self.append(value)
        return value


class Matrix:
    def __array__(self, dtype=None):
        return np.eye(4, dtype=dtype)

    def inverted(self):
        return self


class Object(Block):
    def __init__(self, env, name, data):
        super().__init__(env, name, 'objects')
        self._data = None
        self.data = data
        self.type = 'MESH' if data is not None and data.kind == 'meshes' else 'CURVE'
        self.parent = None
        self.users_collection = []
        self.modifiers = Modifiers()
        self.rotation_euler = SimpleNamespace(x=.7, y=0., z=0.)
        self.matrix_world = Matrix()

    @property
    def data(self):
        if self.deleted:
            raise ReferenceError('Removed Object')
        return self._data

    @data.setter
    def data(self, value):
        if self._data is not None:
            self._data.users -= 1
        self._data = value
        if value is not None:
            value.users += 1

    @property
    def users_scene(self):
        return tuple(scene for scene in self.env.scenes if self in scene.objects)

    def hide_set(self, *_args, **_kwargs):
        pass


class Links(IDs):
    def __init__(self, owner, objects=False):
        super().__init__()
        self.owner, self.objects = owner, objects

    def link(self, value):
        if self.objects and self.owner.env.failure == 'link':
            raise RuntimeError('link')
        self.append(value)
        value.users += 1
        if self.objects:
            value.users_collection.append(self.owner)

    def unlink(self, value):
        self.remove(value)
        value.users -= 1
        if self.objects:
            value.users_collection.remove(self.owner)


class Collection(Block):
    def __init__(self, env, name):
        super().__init__(env, name, 'collections')
        self.objects, self.children = Links(self, True), Links(self)


class Scene(Block):
    def __init__(self, env, name='Scene'):
        super().__init__(env, name, 'scenes')
        self.collection = Collection(env, name + '.Root')
        self.view_layers = [SimpleNamespace()]
        self.render = SimpleNamespace(fps=25, fps_base=1.)
        self.frame_start = self.frame_current = 1
        self.frame_end = 26

    @property
    def objects(self):
        return IDs(self.collection.objects + [obj for child in self.collection.children
                                              for obj in child.objects])


class Store(IDs):
    def __init__(self, env, kind):
        super().__init__()
        self.env, self.kind = env, kind

    def new(self, name, data=None):
        base, index = name, 1
        while name in self:
            name = f'{base}.{index:03d}'
            index += 1
        if self.kind == 'objects':
            block = Object(self.env, name, data)
        elif self.kind in ('meshes', 'curves'):
            block = Geometry(self.env, name, self.kind)
        elif self.kind == 'collections':
            block = Collection(self.env, name)
        else:
            block = Block(self.env, name, self.kind)
        self.append(block)
        return block

    def remove(self, block, **_kwargs):
        super().remove(block)
        if self.kind == 'objects':
            block.data = None
            for modifier in block.modifiers:
                modifier.node_group = None
            for collection in list(block.users_collection):
                collection.objects.unlink(block)
        if self.kind in ('meshes', 'curves'):
            block.materials.clear()
        if self.kind == 'collections':
            for scene in self.env.scenes:
                if block in scene.collection.children:
                    scene.collection.children.unlink(block)
        block.deleted = True


@pytest.fixture
def env(monkeypatch, stub_module):
    value = SimpleNamespace(failure=None, copy_count=0, scenes=[])
    kinds = ('objects', 'meshes', 'curves', 'materials', 'node_groups', 'collections')
    value.data = SimpleNamespace(**{kind: Store(value, kind) for kind in kinds})
    value.data.batch_remove = lambda *, ids: [getattr(value.data, item.kind).remove(item) for item in ids]
    scene = Scene(value)
    value.scene = scene
    value.scenes.append(scene)
    value.data.scenes = value.scenes
    value.handlers = SimpleNamespace(**{name: [] for name in
        ('frame_change_post', 'load_pre', 'undo_pre', 'redo_pre')})
    value.handlers.persistent = lambda callback: callback
    bpy = SimpleNamespace(data=value.data, app=SimpleNamespace(handlers=value.handlers),
        context=SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'bpy.app.handlers', value.handlers)
    monkeypatch.setitem(sys.modules, 'mathutils', SimpleNamespace(
        Matrix=SimpleNamespace(Identity=lambda _: Matrix())))
    monkeypatch.setattr(flex, '_ACTIVE', None)
    value.counts = lambda: tuple(len(getattr(value.data, kind)) for kind in kinds)
    value.object = lambda name, data=None: value.data.objects.new(name, data)

    def blade(name=repair.TARGET_NAME):
        mesh = value.data.meshes.new(name + '.Mesh')
        obj = value.object(name, mesh)
        obj['geometry_source'] = 'Packaged OpenFAST AeroDyn'
        scene.collection.objects.link(obj)
        return obj
    value.blade = blade
    monkeypatch.setattr(repair, '_footprint', lambda _: (
        [(0., 0., 0.), (0., 1., 0.), (0., 0., 1.)], [(0, 1, 2)], [],
        [((0, 1, 2), (1., 0., 0.))] * 3))
    stub_module('wfrl_blender.turbine_geometry', geometry_data=lambda: {
        'scalars': {'HubRad': 1.5}, 'blade_stations': [(i,) for i in range(19)]})
    return value


@pytest.mark.parametrize('failure', ['link', 'mesh_data', 'material_nodes', 'attachment_socket'])
def test_repair_creation_failure_releases_only_new_ids(env, failure):
    env.blade()
    before = env.counts()
    env.failure = failure
    with pytest.raises(RuntimeError, match=failure):
        repair.ensure(env.scene)
    assert env.counts() == before


def test_repair_failed_rebuild_preserves_existing_attachment(env, monkeypatch):
    blade = env.blade()
    original = repair.ensure(env.scene)
    source = env.scene.objects[repair.SOURCE_NAME]
    before = env.counts()
    monkeypatch.setattr(repair, '_attribute', lambda *_: (_ for _ in ()).throw(RuntimeError('attribute')))
    with pytest.raises(RuntimeError, match='attribute'):
        repair.ensure(env.scene, force=True)
    assert env.scene.objects[repair.NAME] is original
    assert env.scene.objects[repair.SOURCE_NAME] is source and source.data is blade.data
    assert env.counts() == before


def test_repair_rejects_unmanaged_reference_name_before_allocating(env):
    env.blade()
    foreign = env.object(repair.SOURCE_NAME)
    env.scene.collection.objects.link(foreign)
    before = env.counts()
    with pytest.raises(ValueError, match='unmanaged'):
        repair.ensure(env.scene)
    assert env.counts() == before


@pytest.mark.parametrize('retained', ['use_fake_user', 'shape_keys', 'library'])
def test_repair_cleanup_preserves_retained_mesh(env, retained):
    mesh = env.data.meshes.new('Repair mesh')
    mesh[repair._OWNER] = True
    setattr(mesh, retained, True)
    patch = env.object(repair.NAME, mesh)
    patch[repair._OWNER] = True
    repair._remove([patch])
    assert mesh in env.data.meshes


def test_repair_legacy_object_does_not_own_user_material(env):
    mesh = env.data.meshes.new('User mesh')
    material = env.data.materials.new('User paint')
    mesh.materials.append(material)
    patch = env.object(repair.NAME, mesh)
    patch['repair_example'] = True
    repair._remove([patch])
    assert mesh in env.data.meshes and material in env.data.materials


def test_repair_rebuild_refuses_objects_shared_with_another_scene(env):
    env.blade()
    patch = repair.ensure(env.scene)
    other = Scene(env, 'Other')
    env.scenes.append(other)
    other.collection.objects.link(patch)
    before = env.counts()
    with pytest.raises(ValueError, match='shared'):
        repair.ensure(env.scene, force=True)
    assert patch in other.objects and patch in env.scene.objects
    assert env.counts() == before


def test_repair_repeated_rebuild_and_remove_leave_source_mesh(env):
    blade = env.blade()
    baseline = env.counts()
    patch = repair.ensure(env.scene)
    expected = env.counts()
    assert repair.ensure(env.scene) is patch
    for _ in range(3):
        repair.ensure(env.scene, force=True)
        assert env.counts() == expected
    repair.remove(env.scene)
    assert env.counts() == baseline and blade.data in env.data.meshes


def test_repair_remove_preserves_attachment_owned_by_another_blade(env):
    blade = env.blade()
    other = env.blade('WFRL.Turbine.T2.Blade1')
    patch = env.object('Other blade repair', env.data.meshes.new('Other repair mesh'))
    patch.parent = other
    patch[repair._OWNER] = True
    env.scene.collection.objects.link(patch)
    repair.ensure(env.scene)
    repair.remove(env.scene)
    assert patch in env.scene.objects and patch.parent is other
    assert blade in env.scene.objects


@pytest.fixture
def wind_scene(env):
    root = env.object('WFRL.Turbine.T1.YawRoot')
    env.scene.collection.objects.link(root)
    return env


@pytest.mark.parametrize('failure', ['material_nodes', 'spline', 'link'])
def test_cinematic_creation_failure_does_not_leave_datablocks(wind_scene, failure):
    before = wind_scene.counts()
    wind_scene.failure = failure
    with pytest.raises(RuntimeError, match=failure):
        cinematic.ensure(wind_scene.scene)
    assert wind_scene.counts() == before


def test_cinematic_name_collision_does_not_rewrite_user_curve(wind_scene):
    mesh = wind_scene.data.curves.new('User curve', 'CURVE')
    obj = wind_scene.object(cinematic.PREFIX + 'T1', mesh)
    wind_scene.scene.collection.objects.link(obj)
    before = wind_scene.counts()
    with pytest.raises(ValueError, match='unmanaged'):
        cinematic.ensure(wind_scene.scene)
    assert not mesh.splines and wind_scene.counts() == before


def test_cinematic_material_collision_does_not_rewrite_user_material(wind_scene):
    material = wind_scene.data.materials.new('WFRL.Cinematic.Silk')
    with pytest.raises(ValueError, match='unmanaged'):
        cinematic.ensure(wind_scene.scene)
    material.node_tree.nodes.clear.assert_not_called()


def test_cinematic_rejects_shared_curve_before_upgrade(wind_scene):
    curve = wind_scene.data.curves.new('Shared curve', 'CURVE')
    obj = wind_scene.object(cinematic.PREFIX + 'T1', curve)
    obj['wfrl_revision'] = 3
    wind_scene.scene.collection.objects.link(obj)
    wind_scene.object('Other consumer', curve)
    with pytest.raises(ValueError, match='shared'):
        cinematic.ensure(wind_scene.scene)
    assert not curve.splines


def test_cinematic_repeated_ensure_reuses_normal_resources(wind_scene):
    cinematic.ensure(wind_scene.scene)
    obj = wind_scene.scene.objects[cinematic.PREFIX + 'T1']
    counts = wind_scene.counts()
    cinematic.ensure(wind_scene.scene)
    assert wind_scene.counts() == counts and len(obj.data.splines) == cinematic.STRANDS


def test_cinematic_visibility_leaves_unmanaged_prefixed_objects_alone(wind_scene, monkeypatch):
    obj = wind_scene.object(cinematic.PREFIX + 'UserAnnotation')
    obj.hide_render = False
    wind_scene.scene.collection.objects.link(obj)
    wind_scene.scene.wfrl_show_wake = False
    monkeypatch.setattr(cinematic, '_hide_in_scene', lambda *_: None)
    cinematic.set_visibility(wind_scene.scene)
    assert obj.hide_render is False


@pytest.fixture
def preview(env, tmp_path, stub_module):
    env.scene['wfrl_clearance_demo'] = 'near_tower'
    rotor = env.object('WFRL.Turbine.T1.Rotor')
    env.scene.collection.objects.link(rotor)
    blades = [env.blade(f'WFRL.Turbine.T1.Blade{bid}') for bid in (1, 2, 3)]
    env.reader = SimpleNamespace(start_s=0., end_s=1., at=lambda _: {
        'motion': {'yaw_deg': 0., 'pitch_deg': (0., 0., 0.)}})
    stub_module('wfrl_blender.clearance_replay', reader_for=lambda _: env.reader,
        update=lambda _: None, sample=lambda _: {'time_s': .5})
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{}', encoding='utf-8')
    meta = {'package_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'schema': 'wfrl.section-transforms.preview.v1', 'turbine_id': 'T1'}
    path = tmp_path / 'preview.npz'

    def archive(times=(0., 1.), metadata=None):
        transforms = np.tile(np.eye(4)[:3], (len(times), 3, 19, 1, 1))
        transforms[..., 0, 3] = .2
        np.savez(path, times=np.asarray(times), transforms=transforms,
                 metadata=json.dumps(meta if metadata is None else metadata))
    archive()
    return SimpleNamespace(env=env, scene=env.scene, blades=blades, rotor=rotor,
        path=path, manifest=manifest, archive=archive, meta=meta)


@pytest.mark.parametrize('times', [(np.nan, 1.), (0., np.nan, 1.), (0., np.inf)])
def test_flex_rejects_nonfinite_time_before_mutating_scene(preview, times):
    p = preview
    p.archive(times)
    before = p.env.counts()
    original = [obj.data for obj in p.blades]
    with pytest.raises(ValueError, match='preview|times'):
        flex.attach(p.scene, p.path, p.manifest)
    assert [obj.data for obj in p.blades] == original
    assert p.rotor.rotation_euler.x == .7 and p.env.counts() == before


def test_flex_constructor_failure_restores_mesh_bindings_and_rotor(preview):
    p = preview
    before = p.env.counts()
    original = [obj.data for obj in p.blades]
    p.env.failure = 'mesh_copy'
    with pytest.raises(RuntimeError, match='mesh_copy'):
        flex.attach(p.scene, p.path, p.manifest)
    assert [obj.data for obj in p.blades] == original
    assert p.rotor.rotation_euler.x == .7 and p.env.counts() == before
    assert flex._ACTIVE is None and not p.env.handlers.frame_change_post


def test_flex_initial_update_failure_restores_original_scene(preview, monkeypatch):
    p = preview
    original = [obj.data for obj in p.blades]
    state = dict(p.scene)
    before = p.env.counts()
    replay = sys.modules['wfrl_blender.clearance_replay']
    monkeypatch.setattr(replay, 'sample', lambda _: (_ for _ in ()).throw(ValueError('sample failure')))
    with pytest.raises(ValueError, match='sample failure'):
        flex.attach(p.scene, p.path, p.manifest)
    assert [obj.data for obj in p.blades] == original and p.env.counts() == before
    assert p.rotor.rotation_euler.x == .7 and dict(p.scene) == state
    assert flex._ACTIVE is None and not p.env.handlers.frame_change_post


def test_flex_reattach_uses_rest_mesh_and_releases_previous_copies(preview):
    p = preview
    flex.attach(p.scene, p.path, p.manifest)
    expected = [obj.data.vertices.coords.copy() for obj in p.blades]
    counts = p.env.counts()
    flex.attach(p.scene, p.path, p.manifest)
    assert p.env.counts() == counts
    for obj, coords in zip(p.blades, expected):
        np.testing.assert_allclose(obj.data.vertices.coords, coords)
    assert p.env.handlers.frame_change_post == [flex.update]


def test_flex_failed_reattach_keeps_previous_meshes_and_trails(preview, monkeypatch):
    p = preview
    p.scene.wfrl_flex_show_tip_trails = True
    p.scene['wfrl_flex_tip_trails'] = True
    previous = flex.attach(p.scene, p.path, p.manifest)
    meshes = [obj.data for obj in p.blades]
    trails = previous.trails.copy()
    counts = p.env.counts()
    replay = sys.modules['wfrl_blender.clearance_replay']
    monkeypatch.setattr(replay, 'sample', lambda _: (_ for _ in ()).throw(ValueError('unavailable sample')))
    with pytest.raises(ValueError, match='unavailable sample'):
        flex.attach(p.scene, p.path, p.manifest)
    assert flex._ACTIVE is previous and previous.enabled
    assert [obj.data for obj in p.blades] == meshes
    assert all(not obj.deleted for obj in trails) and p.env.counts() == counts


def test_flex_does_not_write_into_replaced_user_mesh(preview):
    p = preview
    flex.attach(p.scene, p.path, p.manifest)
    user_mesh = p.env.data.meshes.new('User replacement')
    p.blades[0].data = user_mesh
    before = user_mesh.vertices.coords.copy()
    with pytest.raises(ValueError, match='binding'):
        flex.update(p.scene)
    np.testing.assert_array_equal(user_mesh.vertices.coords, before)
    assert p.blades[0].data is user_mesh and flex._ACTIVE is None


def test_flex_refuses_to_rebind_objects_shared_by_scenes(preview):
    p = preview
    other = Scene(p.env, 'Other')
    p.env.scenes.append(other)
    other.collection.objects.link(p.blades[0])
    before = p.env.counts()
    with pytest.raises(ValueError, match='shared'):
        flex.attach(p.scene, p.path, p.manifest)
    assert p.env.counts() == before and p.rotor.rotation_euler.x == .7


@pytest.mark.parametrize('stamp', [None, np.nan, np.inf])
def test_flex_invalid_sample_does_not_write_nonfinite_coordinates(preview, monkeypatch, stamp):
    p = preview
    active = flex.attach(p.scene, p.path, p.manifest)
    before = [obj.data.vertices.coords.copy() for obj in p.blades]
    replay = sys.modules['wfrl_blender.clearance_replay']
    monkeypatch.setattr(replay, 'sample', lambda _: {'time_s': stamp})
    with pytest.raises(ValueError, match='time'):
        active.update(p.scene)
    for obj, coords in zip(p.blades, before):
        np.testing.assert_array_equal(obj.data.vertices.coords, coords)


@pytest.mark.parametrize('handler', ['load_pre', 'undo_pre', 'redo_pre'])
def test_flex_native_lifecycle_releases_handlers_and_owned_meshes(preview, handler):
    p = preview
    before = p.env.counts()
    original = [obj.data for obj in p.blades]
    flex.attach(p.scene, p.path, p.manifest)
    callbacks = list(getattr(p.env.handlers, handler))
    assert callbacks, 'Active preview must release references before Blender replaces IDs'
    for callback in callbacks:
        callback(None)
    assert flex._ACTIVE is None and not p.scene.get('wfrl_flex_active')
    assert [obj.data for obj in p.blades] == original and p.env.counts() == before
    assert all(not getattr(p.env.handlers, name) for name in
               ('frame_change_post', 'load_pre', 'undo_pre', 'redo_pre'))


def test_flex_trail_cleanup_preserves_unmanaged_collection_contents(env):
    collection = env.data.collections.new(flex._TRAIL_COLLECTION)
    env.scene.collection.children.link(collection)
    foreign = env.object('User curve', env.data.curves.new('User curve data', 'CURVE'))
    collection.objects.link(foreign)
    before = env.counts()
    flex._remove_trails(env.scene)
    assert foreign in collection.objects and env.counts() == before


def test_flex_trail_rebuild_reclaims_own_curve_material_and_collection(env):
    before = env.counts()
    flex._make_trails(env.scene)
    expected = env.counts()
    flex._make_trails(env.scene)
    assert env.counts() == expected
    flex._remove_trails(env.scene)
    assert env.counts() == before


@pytest.mark.parametrize('failure', ['material_nodes', 'spline', 'link'])
def test_flex_trail_failure_releases_owned_allocations(env, failure):
    before = env.counts()
    env.failure = failure
    with pytest.raises(RuntimeError, match=failure):
        flex._make_trails(env.scene)
    assert env.counts() == before


def test_flex_trail_cleanup_can_retry_after_partial_removal(env, monkeypatch):
    before = env.counts()
    trails = flex._make_trails(env.scene)
    real_remove = env.data.objects.remove

    def fail_second(obj, **kwargs):
        if obj is trails[1]:
            raise RuntimeError('remove failure')
        real_remove(obj, **kwargs)

    monkeypatch.setattr(env.data.objects, 'remove', fail_second)
    with pytest.raises(RuntimeError, match='remove failure'):
        flex._remove_trails(env.scene, trails)
    monkeypatch.setattr(env.data.objects, 'remove', real_remove)
    flex._remove_trails(env.scene, trails)
    assert env.counts() == before


def test_flex_deleted_object_invalidates_preview_without_stale_rna(preview):
    p = preview
    flex.attach(p.scene, p.path, p.manifest)
    p.env.data.objects.remove(p.blades[0])
    flex.update(p.scene)
    assert flex._ACTIVE is None and not p.env.handlers.frame_change_post
