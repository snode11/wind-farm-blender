"""Owned display resources and scene-local visibility under injected faults."""
import math
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wfrl_blender import atmosphere, blade_root_display, clearance_visual


class ID:
    def __init__(self, name, **attributes):
        self.name = name
        self.tags = {}
        self.users = 0
        self.library = None
        self.use_fake_user = False
        self.shape_keys = None
        self.animation_data = None
        self.materials = []
        self.__dict__.update(attributes)

    def as_pointer(self):
        return id(self)

    def get(self, key, default=None):
        return self.tags.get(key, default)

    def __getitem__(self, key):
        return self.tags[key]

    def __setitem__(self, key, value):
        self.tags[key] = value

    def pop(self, key, default=None):
        return self.tags.pop(key, default)


class MeshObject(ID):
    def __init__(self, name, mesh, **attributes):
        super().__init__(name, type='MESH', **attributes)
        self.deleted = False
        self._data = None
        self.data = mesh

    @property
    def data(self):
        if self.deleted:
            raise ReferenceError('Object was removed')
        return self._data

    @data.setter
    def data(self, mesh):
        if self._data is not None:
            self._data.users -= 1
        self._data = mesh
        if mesh is not None:
            mesh.users += 1


class Pool(list):
    def get(self, name):
        return next((block for block in self if block.name == name), None)

    def new(self, name, data=None):
        block = MeshObject(name, data) if data is not None else ID(name)
        self.append(block)
        return block

    def remove(self, block, **kwargs):
        super().remove(block)
        if isinstance(block, MeshObject):
            block.data = None
            block.deleted = True


def fittings(monkeypatch, stub_module, *, material_failure=False):
    meshes = Pool()
    source = meshes.new('Original fitting')
    source.materials = [object()]
    source['wfrl_root_fitting_digest'] = 'reference'
    scene = ID('Scene', objects=[])
    parent = ID('T1.PitchRoot')
    scene.objects = [MeshObject(f'WFRL.Turbine.T{i}.RootFairing', source,
                               users_scene=(scene,), parent=parent) for i in (1, 2)]

    def create(role, model):
        mesh = meshes.new(role)
        if material_failure:
            mesh.materials = NS(append=Mock(side_effect=RuntimeError('material copy failed')))
        return mesh

    stub_module('wfrl_blender.mechanical_details', blade_root_fitting_mesh=create)
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(meshes=meshes)))
    monkeypatch.setattr(blade_root_display, 'mesh_fingerprint', lambda mesh: 'reference')
    return scene, source, meshes


def test_root_fitting_material_failure_releases_unmarked_owned_mesh(monkeypatch, stub_module):
    scene, source, meshes = fittings(monkeypatch, stub_module, material_failure=True)
    with pytest.raises(RuntimeError, match='material copy failed'):
        blade_root_display.RootFittingBindings(scene, 'rootkink')
    assert meshes == [source]
    assert all(obj.data is source for obj in scene.objects)


def test_root_fitting_rollback_survives_a_deleted_object(monkeypatch, stub_module):
    scene, source, meshes = fittings(monkeypatch, stub_module)
    bindings = blade_root_display.RootFittingBindings(scene, 'rootkink')
    bindings.apply()
    scene.objects[-1].data = None
    scene.objects[-1].deleted = True
    bindings.rollback()
    bindings.rollback()
    assert scene.objects[0].data is source
    assert meshes == [source]


@pytest.mark.parametrize('fault', ['bmesh', 'geometry', 'link'])
def test_radar_primitive_failure_reclaims_only_its_allocations(monkeypatch, fault):
    meshes, objects = Pool(), Pool()
    preserved = meshes.new('User mesh')
    bm = NS(free=Mock(), to_mesh=Mock())
    create = Mock(side_effect=RuntimeError('geometry failed') if fault == 'geometry' else None)
    new = Mock(side_effect=RuntimeError('bmesh failed') if fault == 'bmesh' else None,
               return_value=bm)
    monkeypatch.setitem(sys.modules, 'bmesh', NS(new=new, ops=NS(create_cube=create)))
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(meshes=meshes, objects=objects)))
    collection = NS(objects=NS(link=Mock(side_effect=RuntimeError('link failed'))))
    with pytest.raises(RuntimeError, match='failed'):
        clearance_visual._radar_primitive(collection, 'cube', 'Radar', object())
    assert meshes == [preserved]
    assert objects == []
    if fault != 'bmesh':
        bm.free.assert_called_once()


@pytest.mark.parametrize('calibration', [
    {'origin': (math.nan, 0., 0.)}, {'origin': (0., 0.)},
    {'directions': [(0., 0., 0.)]}, {'directions': [(0., 0., math.inf)]},
    {'directions': [(0., 0.)]}, {'directions': []},
    {'directions': [(1e308, 1e308, 1e308)]},
    {'beam_origins': [(0., 0., 0.)]},
    {'beam_origins': [(0., 0., 0.), (0., math.nan, 0.), (0., 0., 0.)]},
])
def test_radar_invalid_calibration_is_rejected_before_resource_mutation(monkeypatch, stub_module, calibration):
    allocate = Mock(side_effect=AssertionError('must validate before allocating'))
    stub_module('wfrl_blender.materials', get_material=allocate)
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(**{
        name: Pool() for name in ('objects', 'meshes', 'curves', 'materials')})))
    yaw = NS(users_collection=[object()])
    scene = NS(objects={'WFRL.Turbine.T1.YawRoot': yaw})
    with pytest.raises(ValueError):
        clearance_visual.ensure_radar(scene, 'T1', **calibration)
    allocate.assert_not_called()


@pytest.mark.parametrize('fault', ['fitting', 'cable'])
def test_radar_build_failure_releases_all_new_fittings_and_curve_data(monkeypatch, stub_module, fault):
    objects, meshes, materials = Pool(), Pool(), Pool()
    preserved = materials.new('User material')
    scene = ID('Scene', objects=objects)
    collection = ID('Collection', objects=NS(link=Mock()))
    yaw = ID('WFRL.Turbine.T1.YawRoot', users_collection=[collection])
    objects.append(yaw)

    class Curves(Pool):
        def new(self, name, kind):
            curve = super().new(name)
            curve.splines = NS(new=Mock(side_effect=RuntimeError('cable failed')))
            return curve

    curves = Curves()
    calls = []

    def material(name):
        calls.append(name)
        if fault == 'fitting' and len(calls) == 2:
            raise RuntimeError('fitting failed')
        return preserved

    def primitive(collection, kind, name, material):
        mesh = meshes.new(name + '.Mesh')
        mesh.materials.append(material)
        obj = objects.new(name, mesh)
        obj.users_scene = (scene,)
        obj.users_collection = (collection,)
        obj.matrix_parent_inverse = NS(identity=Mock())
        obj.rotation_euler = NS()
        obj.modifiers = NS(get=lambda name: None, new=lambda *args: NS())
        return obj

    stub_module('wfrl_blender.materials', get_material=material)
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(
        objects=objects, meshes=meshes, curves=curves, materials=materials)))
    monkeypatch.setattr(clearance_visual, '_radar_primitive', primitive)
    with pytest.raises(RuntimeError, match=fault + ' failed'):
        clearance_visual.ensure_radar(scene, 'T1')
    assert objects == [yaw]
    assert not meshes and not curves
    assert materials == [preserved]


def test_atmosphere_visibility_uses_only_target_scene_and_its_view_layers(monkeypatch):
    scene, other = ID('Target', objects=[]), ID('Other')
    owned = ID('WFRL.Atmosphere.Owned', users_scene=(scene,), hide_render=False, hide_set=Mock())
    foreign = ID('WFRL.Atmosphere.Other', users_scene=(other,), hide_render=False, hide_set=Mock())
    unrelated = ID('User object', users_scene=(scene,), hide_render=False, hide_set=Mock())
    scene.objects = [owned, unrelated]
    scene.view_layers = [NS(objects={obj.name: obj for obj in scene.objects}) for _ in range(2)]
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(objects=[owned, foreign, unrelated])))
    atmosphere.set_feature_visibility(scene, False)
    assert owned.hide_render is True
    assert [call.kwargs for call in owned.hide_set.call_args_list] == [
        {'view_layer': layer} for layer in scene.view_layers]
    assert not foreign.hide_render and not unrelated.hide_render
    foreign.hide_set.assert_not_called()
    unrelated.hide_set.assert_not_called()


def test_atmosphere_image_failure_preserves_world_links_nodes_and_metadata(monkeypatch):
    background = NS(type='BACKGROUND', inputs={'Color': object()})

    class Nodes(list):
        def get(self, name):
            return None

        def new(self, kind):
            node = NS(type=kind)
            self.append(node)
            return node

    old_link = NS(to_node=background, to_socket=background.inputs['Color'])
    nodes, links = Nodes([background]), [old_link]
    scene = ID('Scene', world=NS(use_nodes=True, node_tree=NS(nodes=nodes, links=links)))
    scene['wfrl_atmosphere_preset'] = 'dusk'
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(scenes=[scene])))
    loader = Mock(side_effect=OSError('HDR image missing'))
    with pytest.raises(OSError, match='HDR image missing'):
        atmosphere.apply_preset(scene, 'clear', image_loader=loader)
    assert nodes == [background]
    assert links == [old_link]
    assert scene.tags == {'wfrl_atmosphere_preset': 'dusk'}
