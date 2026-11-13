"""Construction failures release only the display resources they allocate."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import sys

import pytest

from wfrl_blender import deflection, mechanical_details, stacked_camera_rig as rig


class Block(dict):
    __eq__ = object.__eq__
    __ne__ = object.__ne__
    __hash__ = object.__hash__

    def __init__(self, name):
        self.name = name
        self.users = 0
        self.library = self.shape_keys = None
        self.use_fake_user = False

    def as_pointer(self):
        return id(self)

    def __bool__(self):
        return True


class Mesh(Block):
    def __init__(self, name):
        super().__init__(name)
        self.materials, self.polygons, self.vertices = [], [], []

    def from_pydata(self, vertices, edges, faces):
        self.vertices = [NS(co=tuple(v)) for v in vertices]
        self.polygons = [NS(vertices=f, use_smooth=False) for f in faces]

    def update(self):
        pass


class Object(Block):
    def __init__(self, name, data=None):
        super().__init__(name)
        self.data = data
        if data is not None:
            data.users += 1
        self.users_scene, self.users_collection = [], []
        self.rotation_euler = NS(x=0., y=0., z=0.)


class Material(Block):
    def __init__(self, name):
        super().__init__(name)
        self.diffuse_color = (.2, .3, .4, 1.)
        node = NS(type='BSDF_PRINCIPLED', inputs={name: NS(default_value=0.) for name in (
            'Base Color', 'Emission Color', 'Emission Strength', 'Roughness', 'Alpha', 'Metallic')})
        class Nodes(list):
            def get(self, name):
                return self[0]
        self.node_tree = NS(nodes=Nodes([node]))


class Curve(Block):
    def __init__(self, name, *args):
        super().__init__(name)
        self.materials = []
        class Splines(list):
            def new(self, kind):
                points = NS(add=lambda count: None)
                spline = NS(points=points)
                self.append(spline)
                return spline
        self.splines = Splines()


class Pool(list):
    def __init__(self, factory):
        super().__init__()
        self.factory = factory

    def new(self, name, *args):
        names = {item.name for item in self}
        candidate, i = name, 1
        while candidate in names:
            candidate, i = f'{name}.{i:03}', i + 1
        item = self.factory(candidate, *args)
        self.append(item)
        return item

    def get(self, name):
        return next((item for item in self if item.name == name), None)

    def __getitem__(self, key):
        if isinstance(key, str):
            value = self.get(key)
            if value is None:
                raise KeyError(key)
            return value
        return super().__getitem__(key)

    def remove(self, item, **kwargs):
        if isinstance(item, Object):
            for collection in tuple(item.users_collection):
                collection.objects.unlink(item)
            if item.data is not None:
                item.data.users -= 1
        super().remove(item)


@pytest.fixture
def resources(monkeypatch, stub_module):
    pools = NS(objects=Pool(Object), meshes=Pool(Mesh), curves=Pool(Curve),
               materials=Pool(Material), cameras=Pool(Block), collections=Pool(Block))
    scene = Block('owner')
    scene.objects = Pool(Object)
    scene.frame_current = 17
    collection = NS()

    def link(obj):
        scene.objects.append(obj)
        obj.users_scene.append(scene)
        obj.users_collection.append(collection)

    def unlink(obj):
        list.remove(scene.objects, obj)
        obj.users_scene.remove(scene)
        obj.users_collection.remove(collection)

    collection.objects = NS(link=link, unlink=unlink)
    root = pools.objects.new(rig.core.ROOT_NAME, None)
    link(root)
    bpy = NS(data=pools, context=NS(view_layer=NS(update=Mock())),
             types=NS(Mesh=Mesh, Curve=Curve, SpaceView3D=NS(
                 draw_handler_add=Mock(return_value=object()), draw_handler_remove=Mock())))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)

    def mesh_object(collection, name, vertices, faces, material):
        mesh = pools.meshes.new(name + '.Mesh')
        mesh.from_pydata(vertices, [], faces)
        obj = pools.objects.new(name, mesh)
        collection.objects.link(obj)
        mesh.materials.append(material)
        return obj

    builder = stub_module('wfrl_blender.scene_builder', _mesh_object=mesh_object,
                          _finish=lambda obj, **kw: obj,
                          _primitive_mesh=lambda name, *a, **kw: pools.meshes.new(name))
    builder._primitive = lambda c, k, n, l, s, m: mesh_object(c, n, [], [], m)

    def material(name, color):
        item = pools.materials.get(name) or pools.materials.new(name)
        item.diffuse_color = color
        return item
    stub_module('wfrl_blender.tip_tracking', _material=material)
    return NS(pools=pools, scene=scene, root=root, collection=collection, bpy=bpy, builder=builder)


def inventory(state):
    return {name: tuple(map(id, pool)) for name, pool in vars(state.pools).items()}


@pytest.mark.parametrize('role,model', [('RootFairing', 'nrel5mw'), ('PitchSeal', 'rootkink')])
def test_root_fitting_failure_releases_candidate_mesh(resources, monkeypatch, role, model):
    prior = resources.pools.meshes.new('user mesh')
    prior.use_fake_user = True
    before = inventory(resources)
    if role == 'RootFairing':
        def primitive(*args, **kwargs):
            mesh = resources.pools.meshes.new('candidate')
            mesh.polygons = [NS(vertices=None)]
            return mesh
        monkeypatch.setattr(resources.builder, '_primitive_mesh', primitive)
    else:
        monkeypatch.setattr(Mesh, 'update', Mock(side_effect=RuntimeError('mesh update failed')))
    with pytest.raises((RuntimeError, TypeError)):
        mechanical_details.blade_root_fitting_mesh(role, model)
    assert inventory(resources) == before


def test_mechanical_details_failure_releases_parts_and_preserves_shared_material(resources, monkeypatch):
    material = resources.pools.materials.new('shared material')
    material.users = 1
    before = inventory(resources)
    monkeypatch.setattr(resources.builder, '_finish', Mock(side_effect=RuntimeError('modifier failed')))
    with pytest.raises(RuntimeError, match='modifier failed'):
        mechanical_details.add_mechanical_details(resources.collection, resources.root, resources.root,
                                                  resources.root, 'test', {}, {},
                                                  material_factory=lambda key: material)
    assert inventory(resources) == before


def test_housing_failure_releases_partial_object_and_material(resources, monkeypatch):
    before = inventory(resources)
    # The RNA node tree can fail after the material ID has been allocated.
    resources.pools.materials.factory = Block
    with pytest.raises(AttributeError):
        rig._housing(resources.scene, [], rig.RigParameters())
    assert inventory(resources) == before


@pytest.mark.parametrize('stage', ['camera', 'housing', 'preset'])
def test_rig_build_failure_restores_resources_scene_and_history(resources, monkeypatch, stage):
    from wfrl_blender import custom_camera_history as history
    monkeypatch.setattr(history, 'HISTORY', history.History())
    history.HISTORY.notice = 'keep prior history'
    monkeypatch.setattr(rig.core, 'get_camera', lambda *args: None)
    monkeypatch.setattr(rig, 'blade_geometry', lambda scene: (None, [], [], []))
    monkeypatch.setattr(rig, 'fit_segment', lambda *a: NS(location=(0, 0, 0)))
    monkeypatch.setattr(rig.core, 'validate_research_position', lambda *a, **kw: None)

    class Sliceable:
        def __getitem__(self, key):
            return []
    monkeypatch.setattr(rig, 'blade_geometry', lambda scene: (None, Sliceable(), Sliceable(), []))

    def begin(scene, slot):
        camera = resources.pools.objects.new('camera', resources.pools.cameras.new('camera.Data'))
        resources.collection.objects.link(camera)
        camera['slot'] = slot
        return camera

    def apply(scene, camera, *args, **kwargs):
        if stage == 'camera' and camera['slot'] == 2:
            raise RuntimeError('camera failed')

    def commit(*args):
        history.HISTORY.notice = 'partial history'

    def housing(*args):
        obj = resources.pools.objects.new(rig.PREFIX, resources.pools.meshes.new('box'))
        resources.collection.objects.link(obj)
        if stage == 'housing':
            raise RuntimeError('housing failed')
        return obj

    monkeypatch.setattr(rig.core, 'begin_draft', begin)
    monkeypatch.setattr(rig.core, 'apply_research', apply)
    monkeypatch.setattr(rig.core, 'commit_draft', commit)
    monkeypatch.setattr(rig, '_housing', housing)
    monkeypatch.setattr(rig.core, 'restore_layout', Mock(side_effect=RuntimeError('preset failed')))
    before = inventory(resources)
    with pytest.raises(RuntimeError, match=stage + ' failed'):
        (rig.build_default if stage == 'preset' else rig.build)(resources.scene)
    assert inventory(resources) == before
    assert 'wfrl_stacked_camera_rig' not in resources.scene
    assert 'wfrl_stacked_reference_frame' not in resources.scene
    assert history.HISTORY.notice == 'keep prior history'


@pytest.mark.parametrize('owned,shared', [(False, False), (True, True)])
def test_comparison_construction_preserves_foreign_or_shared_objects(resources, monkeypatch, stub_module, owned, shared):
    obj = resources.pools.objects.new('WFRL.Deflection.T1.User', resources.pools.meshes.new('retained'))
    obj['wfrl_deflection_owned'] = owned
    resources.collection.objects.link(obj)
    if shared:
        obj.users_scene.append(Block('other scene'))
    monkeypatch.setattr(resources.builder, '_mesh_object', Mock(side_effect=RuntimeError('stop construction')))
    owner = NS(scene=resources.scene, blades=[(resources.root,)])
    with pytest.raises((RuntimeError, ValueError)):
        deflection.ComparisonView(owner, {'reference': 'test'}, [], [])
    assert any(item is obj for item in resources.pools.objects)
    assert any(item is obj.data for item in resources.pools.meshes)


def test_comparison_constructor_failure_cleans_new_resources(resources, monkeypatch):
    before = inventory(resources)
    original = resources.collection.objects.link

    def fail_link(obj):
        original(obj)
        raise RuntimeError('link failed')

    monkeypatch.setattr(resources.collection.objects, 'link', fail_link)
    with pytest.raises(RuntimeError, match='link failed'):
        deflection.ComparisonView(NS(scene=resources.scene, blades=[(resources.root,)]),
                                  {'reference': 'test'}, [], [])
    assert inventory(resources) == before


def test_comparison_repeated_close_releases_owned_materials_without_editing_existing(resources):
    shared = resources.pools.materials.new('WFRL.Deflection.Ghost')
    shared.users = 2
    color = shared.diffuse_color
    before = inventory(resources)
    for _ in range(2):
        view = deflection.ComparisonView(NS(scene=resources.scene, blades=[(resources.root,)]),
                                         {'reference': 'test'}, [], [])
        assert shared.diffuse_color == color
        view.close()
        view.close()
        assert inventory(resources) == before


def test_comparison_failed_rebuild_preserves_previous_display(resources, monkeypatch):
    obj = resources.pools.objects.new('WFRL.Deflection.T1.Ghost', resources.pools.meshes.new('ghost'))
    obj['wfrl_deflection_owned'] = True
    resources.collection.objects.link(obj)
    before = inventory(resources)
    monkeypatch.setattr(resources.builder, '_mesh_object', Mock(side_effect=RuntimeError('construction failed')))
    with pytest.raises(RuntimeError, match='construction failed'):
        deflection.ComparisonView(NS(scene=resources.scene, blades=[(resources.root,)]),
                                  {'reference': 'test'}, [], [])
    assert inventory(resources) == before


def test_comparison_rebuild_reclaims_owned_materials_and_preserves_names(resources):
    owner = NS(scene=resources.scene, blades=[(resources.root,)])
    first = deflection.ComparisonView(owner, {'reference': 'test'}, [], [])
    expected_names = {obj.name for obj in first.objects}
    expected_materials = {mat.name for mat in resources.pools.materials}
    second = deflection.ComparisonView(owner, {'reference': 'test'}, [], [])
    assert {obj.name for obj in second.objects} == expected_names
    assert {mat.name for mat in resources.pools.materials} == expected_materials
    second.close()


@pytest.mark.parametrize('fault', ['shared_mesh', 'linked_mesh', 'shape_keys', 'nan_height'])
def test_yaw_seal_rejects_unsafe_write_before_modifying_vertices(resources, fault):
    mesh = resources.pools.meshes.new('seal')
    mesh.from_pydata([(0., 0., 0.)] * 512, [], [])
    obj = resources.pools.objects.new('seal', mesh)
    resources.collection.objects.link(obj)
    height = 87.6
    if fault == 'shared_mesh':
        mesh.users = 2
    elif fault == 'linked_mesh':
        mesh.library = object()
    elif fault == 'shape_keys':
        mesh.shape_keys = object()
    else:
        height = float('nan')
    with pytest.raises(ValueError):
        mechanical_details.recess_yaw_seal(obj, height)
    assert all(vertex.co == (0., 0., 0.) for vertex in mesh.vertices)
    assert 'wfrl_seal_geometry_revision' not in obj


def test_yaw_seal_update_failure_restores_coordinates(resources, monkeypatch):
    mesh = resources.pools.meshes.new('seal')
    mesh.from_pydata([(0., 0., 0.)] * 512, [], [])
    obj = resources.pools.objects.new('seal', mesh)
    resources.collection.objects.link(obj)
    monkeypatch.setattr(mesh, 'update', Mock(side_effect=[RuntimeError('update failed'), None]))
    with pytest.raises(RuntimeError, match='update failed'):
        mechanical_details.recess_yaw_seal(obj, 87.6)
    assert all(vertex.co == (0., 0., 0.) for vertex in mesh.vertices)
    assert 'wfrl_seal_geometry_revision' not in obj


def test_comparison_close_preserves_shared_object_and_cleans_after_stale_handler(resources):
    owned = resources.pools.objects.new('own', resources.pools.meshes.new('own mesh'))
    shared = resources.pools.objects.new('shared', resources.pools.meshes.new('shared mesh'))
    for obj in (owned, shared):
        resources.collection.objects.link(obj)
    shared.users_scene.append(Block('other scene'))
    resources.bpy.types.SpaceView3D.draw_handler_remove.side_effect = ValueError('already removed')
    view = deflection.ComparisonView.__new__(deflection.ComparisonView)
    view.owner = NS(scene=resources.scene)
    view.handle, view.objects, view.labels, view.rows = object(), [owned, shared], ['old'], {1: {}}
    view.materials = {}
    view.close()
    assert all(item is not owned for item in resources.pools.objects)
    assert any(item is shared for item in resources.pools.objects)
    assert view.handle is None and not view.labels and not view.rows
    view.close()
