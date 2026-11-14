"""Scene and landscape construction reject invalid input and retain owned IDs."""

from dataclasses import replace
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from wfrl_blender import landscape, scene_builder
from wfrl_blender.scene_model import SceneDTO, TurbineDTO


class _ID:
    def __init__(self, name, pool, **attributes):
        self.name = name
        self.pool = pool
        self.library = None
        self.use_fake_user = False
        self.shape_keys = None
        self.refs = set()
        self.properties = {}
        self.__dict__.update(attributes)

    def __getitem__(self, key):
        return self.properties[key]

    def __setitem__(self, key, value):
        self.properties[key] = value

    def __delitem__(self, key):
        del self.properties[key]

    def __contains__(self, key):
        return key in self.properties

    def get(self, key, default=None):
        return self.properties.get(key, default)

    def as_pointer(self):
        return id(self)

    @property
    def users(self):
        return len(self.pool.data.user_map().get(self, ())) + int(self.use_fake_user)

    @property
    def users_collection(self):
        return tuple(item for item in self.pool.data.collections if self in item.objects)

    def copy(self):
        return self.pool.new(self.name + '.Copy')

    def hide_set(self, hidden):
        self.hidden = hidden


class _Links(list):
    def __contains__(self, item):
        if isinstance(item, str):
            return self.get(item) is not None
        return super().__contains__(item)

    def link(self, item):
        self.append(item)

    def unlink(self, item):
        self.remove(item)

    def get(self, name):
        return next((item for item in self if item.name == name), None)


class _Pool(list):
    def __init__(self, data, kind):
        super().__init__()
        self.data = data
        self.kind = kind

    def new(self, name, data=None, **attributes):
        block = _ID(name, self, **attributes)
        if self.kind == 'objects':
            block.data = data
            block.animation_data = None
        elif self.kind == 'collections':
            block.objects = _Links()
            block.children = _Links()
            block.children_recursive = ()
        elif self.kind == 'images':
            block.size = (8, 8)
            block.colorspace_settings = SimpleNamespace(name='sRGB')
        self.append(block)
        return block

    def get(self, name):
        return next((item for item in self if item.name == name), None)

    def remove(self, item, **_kwargs):
        self.data.batch_remove(ids=(item,))


class _Data:
    kinds = ('objects', 'meshes', 'materials', 'collections', 'images', 'curves',
             'lights', 'worlds', 'node_groups', 'cameras', 'actions', 'scenes')

    def __init__(self):
        for kind in self.kinds:
            setattr(self, kind, _Pool(self, kind))

    def inventory(self):
        return {item for kind in self.kinds for item in getattr(self, kind)}

    def user_map(self, *, subset=None):
        blocks = self.inventory()
        users = {item: set() for item in blocks}
        for owner in blocks:
            refs = set(owner.refs)
            for key in ('data', 'world', 'collection', 'parent'):
                item = getattr(owner, key, None)
                if isinstance(item, _ID):
                    refs.add(item)
            for key in ('objects', 'children'):
                refs.update(getattr(owner, key, ()))
            for item in refs & blocks:
                users[item].add(owner)
        return users if subset is None else {item: users[item] for item in subset}

    def batch_remove(self, *, ids):
        ids = set(ids)
        for owner in self.inventory() - ids:
            owner.refs.difference_update(ids)
            for key in ('objects', 'children'):
                links = getattr(owner, key, None)
                if links is not None:
                    links[:] = [item for item in links if item not in ids]
            for key in ('data', 'world', 'parent'):
                if getattr(owner, key, None) in ids:
                    setattr(owner, key, None)
        for item in ids:
            list.remove(item.pool, item)


@pytest.fixture
def host(monkeypatch):
    data = _Data()
    scene = data.scenes.new('User.Scene')
    scene.collection = data.collections.new('User.Master')
    scene.objects = _Links()
    scene.world = data.worlds.new('User.World')
    bpy = SimpleNamespace(data=data, context=SimpleNamespace(scene=scene))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    return bpy


@pytest.fixture
def dto():
    return SceneDTO.from_mapping({'layout': [{'id': 'T1', 'x': 0, 'y': 0}]})


def _resources(data, collection):
    image = data.images.new('terrain.jpg')
    material = data.materials.new('WFRL.terrain')
    material.refs.add(image)
    mesh = data.meshes.new('WFRL.Terrain.Data')
    mesh.refs.add(material)
    obj = data.objects.new('WFRL.Terrain', mesh)
    collection.objects.link(obj)
    prototypes = data.collections.new('WFRL.Prototypes')
    prototype_mesh = data.meshes.new('WFRL.Prototype.Data')
    prototypes.objects.link(data.objects.new('WFRL.Prototype', prototype_mesh))
    group = data.node_groups.new('WFRL.Instances')
    group.refs.add(prototypes)
    obj.refs.add(group)
    data.meshes.new('Unlinked interrupted allocation')
    for kind in ('curves', 'lights', 'cameras', 'actions'):
        data_block = getattr(data, kind).new('WFRL.' + kind)
        collection.objects.link(data.objects.new('WFRL.' + kind, data_block))
    return obj


def _stub_scene_build(monkeypatch, stub_module, host):
    monkeypatch.setattr(scene_builder, '_make_turbine', lambda *_: None)
    monkeypatch.setattr(scene_builder, '_cone', lambda *_: SimpleNamespace())
    monkeypatch.setattr(scene_builder, '_finish', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scene_builder, '_make_label', lambda *_: None)
    monkeypatch.setattr(scene_builder, '_make_terrain_surface',
                        lambda collection: _resources(host.data, collection))
    for name in ('_make_ground_grid', '_make_wake', '_make_sensor_fixtures',
                 '_configure_environment', '_configure_render'):
        monkeypatch.setattr(scene_builder, name, lambda *_: None)
    monkeypatch.setattr(scene_builder, '_primitive', lambda *_: SimpleNamespace(rotation_euler=[0, 0, 0]))
    monkeypatch.setattr(scene_builder, 'get_material', lambda *_: None)
    stub_module('wfrl_blender.repair_marks', ensure=lambda *_: None)


@pytest.mark.parametrize('stage', ['collection_link', 'turbine', 'terrain', 'environment', 'repair'])
def test_scene_failure_releases_new_ids_without_deleting_existing(host, dto, monkeypatch, stub_module, stage):
    _stub_scene_build(monkeypatch, stub_module, host)
    host.data.images.new('Preexisting unused image')
    host.data.meshes.new('WFRL.Preexisting unused mesh')
    before = host.data.inventory()

    def fail(*args):
        if stage == 'turbine':
            _resources(host.data, args[0])
        if stage == 'environment':
            host.context.scene.world = host.data.worlds.new('WFRL.NewWorld')
        raise RuntimeError('injected construction failure')

    if stage == 'collection_link':
        monkeypatch.setattr(host.context.scene.collection.children, 'link', fail)
    elif stage == 'repair':
        stub_module('wfrl_blender.repair_marks', ensure=fail)
    else:
        name = {'turbine': '_make_turbine', 'terrain': '_make_ground_grid',
                'environment': '_configure_environment'}[stage]
        monkeypatch.setattr(scene_builder, name, fail)
    with pytest.raises(RuntimeError, match='injected construction failure'):
        scene_builder.build_scene(dto)
    assert host.data.inventory() == before
    assert host.context.scene.world.name == 'User.World'


@pytest.mark.parametrize('retained', ['shared_object', 'shared_mesh', 'fake_user'])
def test_scene_rollback_preserves_external_users_and_their_dependencies(host, dto, monkeypatch, stub_module, retained):
    _stub_scene_build(monkeypatch, stub_module, host)
    external = host.data.collections.new('User.OtherCollection')
    kept = []

    def fail(collection):
        obj = collection.objects.get('WFRL.Terrain')
        if retained == 'shared_object':
            external.objects.link(obj)
            kept.append(obj)
        elif retained == 'shared_mesh':
            reference = host.data.objects.new('User.MeshReference', obj.data)
            external.objects.link(reference)
            kept.append(reference)
        else:
            obj.use_fake_user = True
            kept.append(obj)
        raise RuntimeError('injected shared-resource failure')

    monkeypatch.setattr(scene_builder, '_make_ground_grid', fail)
    with pytest.raises(RuntimeError, match='injected shared-resource failure'):
        scene_builder.build_scene(dto)
    retained_ids = host.data.inventory()
    assert kept[0] in retained_ids
    assert kept[0].data in retained_ids
    assert host.data.materials.get('WFRL.terrain') in retained_ids
    assert host.data.images.get('terrain.jpg') in retained_ids
    assert host.data.meshes.get('Unlinked interrupted allocation') is None
    assert host.data.collections.get(scene_builder.COLLECTION_NAME) is None


@pytest.mark.parametrize('change,field', [
    ({'turbines': ()}, 'turbines'),
    ({'turbines': (TurbineDTO('T1', float('nan'), 0),)}, 'x_m'),
    ({'turbines': (TurbineDTO('T1', 0, float('inf')), )}, 'y_m'),
    ({'turbines': (TurbineDTO('T1', '0', 0),)}, 'x_m'),
    ({'turbines': (TurbineDTO('T1', False, 0),)}, 'x_m'),
    ({'turbines': (TurbineDTO('', 0, 0),)}, 'turbine_id'),
    ({'turbines': (TurbineDTO('T1', 0, 0), TurbineDTO('T1', 504, 0))}, 'turbine_id'),
    ({'turbines': (SimpleNamespace(turbine_id='T1', x_m=0),)}, 'y_m'),
    ({'wind_speed_mps': -1}, 'wind_speed_mps'),
    ({'wind_speed_mps': float('inf')}, 'wind_speed_mps'),
    ({'wind_direction_deg': float('nan')}, 'wind_direction_deg'),
    ({'dt_s': 0}, 'dt_s'),
    ({'dt_s': -1}, 'dt_s'),
    ({'dt_s': float('nan')}, 'dt_s'),
    ({'name': None}, 'name'),
    ({'backend': None}, 'backend'),
])
def test_invalid_scene_input_is_rejected_before_clearing_or_allocating(host, dto, monkeypatch, stub_module, change, field):
    _stub_scene_build(monkeypatch, stub_module, host)
    existing = host.data.collections.new(scene_builder.COLLECTION_NAME)
    host.context.scene.collection.children.link(existing)
    cleared = []
    monkeypatch.setattr(scene_builder, 'clear_scene', cleared.append)
    before = host.data.inventory()
    with pytest.raises(ValueError, match=field):
        scene_builder.build_scene(replace(dto, **change))
    assert not cleared
    assert host.data.inventory() == before


def test_valid_scene_keeps_signed_meter_coordinates_and_degree_metadata(host, dto, monkeypatch, stub_module):
    _stub_scene_build(monkeypatch, stub_module, host)
    turbine = TurbineDTO('WT01', -504.25, 7.5)
    visited = []
    monkeypatch.setattr(scene_builder, '_make_turbine', lambda collection, item: visited.append(item))
    built = scene_builder.build_scene(replace(dto, turbines=(turbine,), wind_speed_mps=0,
                                              wind_direction_deg=-90))
    assert visited == [turbine]
    assert built['wind_direction_deg'] == -90
    assert built['wind_speed_mps'] == 0
    assert built.name == scene_builder.COLLECTION_NAME


def _stub_scene_cleanup(stub_module):
    callbacks = {}
    for module, method in [('split_turbine_context', 'clear_runtime'), ('farm_flex', 'detach'),
                           ('tip_tracking', 'discard'), ('clearance_replay', 'clear'),
                           ('repair_marks', 'remove')]:
        callbacks[module] = MagicMock()
        attributes = {method: callbacks[module]}
        if module == 'repair_marks':
            attributes['ensure'] = lambda *_: None
        stub_module('wfrl_blender.' + module, **attributes)
    stub_module('wfrl_blender.panels.custom_cameras', cancel_on_load=MagicMock())
    return callbacks


@pytest.mark.parametrize('placement', ['object', 'collection'])
def test_scene_rebuild_rejects_resources_owned_by_another_scene(host, dto, monkeypatch, stub_module, placement):
    _stub_scene_build(monkeypatch, stub_module, host)
    collection = host.data.collections.new(scene_builder.COLLECTION_NAME)
    host.context.scene.collection.children.link(collection)
    mesh = host.data.meshes.new('WFRL.Turbine.T1.Mesh')
    obj = host.data.objects.new('WFRL.Turbine.T1', mesh)
    collection.objects.link(obj)
    external = host.data.collections.new('User.OtherCollection')
    other = host.data.scenes.new('User.OtherScene')
    other.collection = external
    other.objects = _Links([obj])
    if placement == 'object':
        external.objects.link(obj)
    else:
        external.children.link(collection)
        external.children_recursive = (collection,)

    with pytest.raises(ValueError, match='User.OtherScene'):
        scene_builder.build_scene(dto)

    assert collection in host.data.collections
    assert obj in host.data.objects
    assert mesh in host.data.meshes


@pytest.mark.parametrize('retained', ['shared_object', 'shared_collection',
                                     'referenced_object', 'referenced_collection',
                                     'fake_object', 'fake_collection',
                                     'linked_object', 'linked_collection'])
def test_scene_rebuild_clears_shared_or_retained_ids_and_owner_runtime(host, dto, monkeypatch, stub_module, retained):
    _stub_scene_build(monkeypatch, stub_module, host)
    callbacks = _stub_scene_cleanup(stub_module)
    collection = host.data.collections.new(scene_builder.COLLECTION_NAME)
    host.context.scene.collection.children.link(collection)
    mesh = host.data.meshes.new('WFRL.Turbine.T1.Mesh')
    action = host.data.actions.new('WFRL.Turbine.T1.Action')
    obj = host.data.objects.new('WFRL.Turbine.T1', mesh)
    obj.animation_data = SimpleNamespace(action=action)
    collection.objects.link(obj)
    host.context.scene.objects.link(obj)
    owners = [host.context.scene]
    external = host.data.collections.new('User.OtherCollection')
    unrelated = host.data.scenes.new('User.UnrelatedScene')
    unrelated.collection = host.data.collections.new('User.UnrelatedMaster')
    unrelated.objects = _Links()
    if retained.startswith('other_scene'):
        other = host.data.scenes.new('User.OtherScene')
        other.collection = external
        other.objects = _Links([obj])
        owners.append(other)
        if retained == 'other_scene_object':
            external.objects.link(obj)
        else:
            external.children.link(collection)
            external.children_recursive = (collection,)
    elif retained.startswith('shared'):
        if retained == 'shared_object':
            external.objects.link(obj)
        else:
            external.children.link(collection)
    else:
        block = obj if retained.endswith('object') else collection
        if retained.startswith('referenced'):
            external.refs.add(block)
            assert block.users > 1
        elif retained.startswith('linked'):
            block.library = object()
        else:
            block.use_fake_user = True
    for owner in (*owners, unrelated):
        owner['wfrl_stacked_camera_rig'] = True
        owner['wfrl_stacked_reference_frame'] = 12

    built = scene_builder.build_scene(dto)

    assert built is not collection
    assert built.name == scene_builder.COLLECTION_NAME
    assert built in host.context.scene.collection.children
    assert collection not in host.data.collections
    assert obj not in host.data.objects
    assert mesh not in host.data.meshes
    assert action not in host.data.actions
    assert obj not in external.objects and obj not in external.refs
    assert collection not in external.children and collection not in external.refs
    for callback in callbacks.values():
        assert [call.args[0] for call in callback.call_args_list] == owners
    for owner in owners:
        assert 'wfrl_stacked_camera_rig' not in owner
        assert 'wfrl_stacked_reference_frame' not in owner
    assert unrelated['wfrl_stacked_camera_rig'] is True
    assert unrelated['wfrl_stacked_reference_frame'] == 12


@pytest.mark.parametrize('stage', ['roads', 'shrubs', 'optimize'])
def test_landscape_failure_releases_new_ids_and_keeps_collection_contents(host, monkeypatch, stage):
    collection = host.data.collections.new('User.Landscape')
    user_obj = host.data.objects.new('User.Existing', host.data.meshes.new('User.Mesh'))
    collection.objects.link(user_obj)
    before = host.data.inventory()
    monkeypatch.setattr(landscape, '_build_terrain', lambda collection, detail: _resources(host.data, collection))
    monkeypatch.setattr(landscape, '_build_roads_and_pads', lambda *_: lambda x, y: 0)
    monkeypatch.setattr(landscape, '_cluster_centers', lambda *_args, **_kwargs: [])
    for name in ('_build_ground_cover', '_build_shrubs_and_rocks', '_build_grass_tufts',
                 '_build_distant_ground', 'optimize_shrubs'):
        monkeypatch.setattr(landscape, name, lambda *_: None)

    def fail(*_):
        raise RuntimeError('injected landscape failure')

    monkeypatch.setattr(landscape, {'roads': '_build_roads_and_pads', 'shrubs': '_build_shrubs_and_rocks',
                                  'optimize': 'optimize_shrubs'}[stage], fail)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='injected landscape failure'):
            landscape.build_landscape(collection)
        assert host.data.inventory() == before
        assert collection.objects == [user_obj]


@pytest.mark.parametrize('failure', ['missing', 'decode', 'empty_image'])
def test_terrain_texture_failure_preserves_shader_and_releases_loaded_images(host, failure):
    material = host.data.materials.new('User.Terrain')
    material.node_tree = SimpleNamespace(nodes=MagicMock(), links=MagicMock())
    old_image = host.data.images.new('ExistingImage')
    before = host.data.inventory()

    def load(path):
        if '_diff_' in str(path):
            return host.data.images.new(str(path))
        if failure == 'missing':
            raise FileNotFoundError('missing')
        if failure == 'decode':
            raise RuntimeError('decoder failed')
        image = host.data.images.new(str(path))
        image.size = (0, 0)
        return image

    with pytest.raises((ValueError, RuntimeError), match='rocky_terrain_02_rough_2k.jpg'):
        landscape.terrain_shader(material, image_loader=load)
    material.node_tree.nodes.clear.assert_not_called()
    assert host.data.inventory() == before
    assert old_image in host.data.images


@pytest.mark.parametrize('failure', ['object', 'link'])
def test_shrub_reduction_allocation_failure_releases_temporary_mesh(host, monkeypatch, failure):
    collection = host.data.collections.new('Landscape')
    source = host.data.meshes.new('ShrubSource')
    before = host.data.inventory()

    def fail(*_):
        raise RuntimeError('injected reduction allocation failure')

    if failure == 'object':
        monkeypatch.setattr(host.data.objects, 'new', fail)
    else:
        monkeypatch.setattr(collection.objects, 'link', fail)
    with pytest.raises(RuntimeError, match='injected reduction allocation failure'):
        landscape._distant_shrub_mesh(collection, source)
    assert host.data.inventory() == before


@pytest.mark.parametrize('kind', ['mesh', 'object', 'group', 'collection'])
def test_landscape_release_preserves_linked_library_resources(host, kind):
    data = host.data
    mesh = data.meshes.new('WFRL.LibraryMesh')
    mesh['wfrl_landscape_prototype'] = True
    if kind == 'mesh':
        mesh.library = object()
        landscape._remove_landscape_mesh(mesh, 'wfrl_landscape_prototype')
        assert mesh in data.meshes
        return
    prototype = data.objects.new('WFRL.LibraryPrototype', mesh)
    prototype['wfrl_landscape_prototype'] = True
    if kind == 'object':
        prototype.library = object()
        landscape._remove_landscape_prototype(prototype)
        assert prototype in data.objects
        assert mesh in data.meshes
        return
    prototypes = data.collections.new('WFRL.LibraryPrototypes')
    prototypes['wfrl_landscape_prototypes'] = True
    prototypes.objects.link(prototype)
    group = data.node_groups.new('WFRL.LibraryInstances')
    group['wfrl_landscape_instances'] = True
    group.refs.add(prototypes)
    group.nodes = {'WFRL.Prototypes': SimpleNamespace(
        inputs={'Collection': SimpleNamespace(default_value=prototypes)})}
    (group if kind == 'group' else prototypes).library = object()
    points = data.meshes.new('WFRL.Points')
    points['wfrl_landscape_points'] = True
    instancer = data.objects.new('WFRL.Instancer', points)
    instancer.refs.add(group)
    instancer.modifiers = [SimpleNamespace(type='NODES', node_group=group)]
    landscape.remove_landscape_instancer(instancer)
    assert instancer not in data.objects
    assert points not in data.meshes
    assert (group in data.node_groups) == (kind == 'group')
    assert prototypes in data.collections
    assert prototype in data.objects
    assert mesh in data.meshes
