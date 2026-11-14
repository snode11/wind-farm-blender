"""NREL geometry rejects invalid surfaces and releases failed Blender candidates."""
from contextlib import nullcontext
from copy import deepcopy
import math
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from blender_frontend.tests.test_nrel_defects import geometry, reference, schema


@pytest.mark.parametrize('damage', ['repeated_index', 'collapsed_edge'])
def test_reference_rejects_zero_area_faces(damage):
    vertices, faces = geometry.blade_mesh(4, 48)
    vertices, faces = list(vertices), list(faces)
    if damage == 'repeated_index':
        faces.append((0, 0, 1))
    else:
        vertices[48 * 30 + 7] = vertices[48 * 30 + 8]
    with pytest.raises(ValueError, match='DEGENERATE_SURFACE'):
        reference.ReferenceSurface(vertices, faces)


@pytest.mark.parametrize('bad', [math.nan, math.inf, -math.inf])
def test_support_normal_rejects_nonfinite_coordinates(frontend_module, bad):
    module = frontend_module('wfrl_blender.nrel_defects.defect_geometry')
    with pytest.raises(ValueError, match='finite|Degenerate'):
        module._normal(((0., 0., 0.), (1., 0., 0.), (0., bad, 0.)))


@pytest.mark.parametrize('bad', [math.nan, math.inf, -math.inf])
def test_depth_measurement_rejects_nonfinite_raycast(frontend_module, bad):
    module = frontend_module('wfrl_blender.nrel_defects.defect_geometry')
    manager = module.DefectGeometry.__new__(module.DefectGeometry)
    shape = dict(centerline=[(0., 0., 0.)] * 7, normals=[(0., 0., 1.)] * 7)
    tree = NS(ray_cast=lambda *_args: ((0., 0., 0.), None, None, bad))
    with pytest.raises(ValueError):
        manager._measure_depth(dict(id='D1', morphology='open_crack', shape={'depth_m': .015}), shape, tree)


class Vector:
    def __init__(self, values):
        self.values = tuple(values)

    def __iter__(self):
        return iter(self.values)

    def __sub__(self, other):
        return Vector(a - b for a, b in zip(self, other))

    @property
    def length_squared(self):
        return sum(v * v for v in self)

    @property
    def length(self):
        return math.sqrt(self.length_squared)


@pytest.fixture
def allocations(frontend_module, monkeypatch):
    module = frontend_module('wfrl_blender.nrel_defects.defect_geometry')
    state = NS(boundary=None, release_failure=False, replace_mesh=False, mesh_release_once=False,
               primary=RuntimeError('candidate failed'))

    def fail(boundary):
        if state.boundary == boundary:
            raise state.primary

    class Pool(list):
        def new(self, name, *args):
            value = self.factory(name, *args)
            self.append(value)
            return value

        def remove(self, value, **_kwargs):
            if not any(item is value for item in self):
                raise ReferenceError('datablock removed')
            if state.release_failure and self is objects:
                raise RuntimeError('object release unavailable')
            if self is meshes and state.mesh_release_once and value.name == 'NREL.Editor.Blade1':
                state.mesh_release_once = False
                raise state.primary
            if self is objects:
                value.data = None
            self[:] = [item for item in self if item is not value]

    meshes, objects, materials, groups = Pool(), Pool(), Pool(), Pool()

    class Mesh(dict):
        __eq__ = object.__eq__

        def __init__(self, name):
            self.name, self.users = name, 0
            self.materials = []
            self.uv_layers = []
            self.vertices = [NS(index=i, co=Vector(v)) for i, v in enumerate(
                [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)])]
            self.polygons = [NS(vertices=(0, 1, 2), material_index=0, use_smooth=False)]

        def from_pydata(self, vertices, edges, faces):
            if self.name.endswith('.StableTips'):
                fail('stable_tips_data')
            if self.name == 'NREL.Editor.Cutter':
                fail('cutter_data')
            self.vertices = [NS(index=i, co=Vector(v)) for i, v in enumerate(vertices)]
            self.polygons = [NS(vertices=face, material_index=0, use_smooth=False) for face in faces]

        def copy(self):
            mesh = meshes.new(self.name + '.copy')
            mesh.materials[:] = self.materials
            return mesh

        def update(self):
            if self.name.endswith('.StableTips'):
                fail('stable_tips_update')

    class Object(dict):
        __eq__ = object.__eq__

        def __init__(self, name, mesh):
            self.name, self._data = name, None
            self.data = mesh
            self.parent = None
            matrix = NS()
            matrix.copy = lambda: matrix
            self.matrix_basis = self.matrix_parent_inverse = matrix
            self.modifiers = Pool()
            self.modifiers.factory = lambda name, _kind: NS(name=name)

        @property
        def data(self):
            return self._data

        @data.setter
        def data(self, value):
            if self._data is not None:
                self._data.users -= 1
            self._data = value
            if value is not None:
                value.users += 1

        def as_pointer(self):
            return id(self)

    class Material(dict):
        def __init__(self, name):
            self.name, self.users = name, 0
            self.node_tree = NS(nodes=NS(get=lambda _key: NS(inputs={
                'Base Color': NS(default_value=None), 'Roughness': NS(default_value=None)})))

        @property
        def use_nodes(self):
            return True

        @use_nodes.setter
        def use_nodes(self, _value):
            fail('material_nodes')

    meshes.factory, objects.factory, materials.factory = Mesh, Object, Material
    healthy = {i: meshes.new('Healthy' + str(i)) for i in (1, 2, 3)}
    blades = [objects.new('Blade' + str(i), meshes.new('Original' + str(i))) for i in (1, 2, 3)]

    def link(obj):
        fail('cutter_link' if obj.name == 'NREL.Editor.Cutter' else 'staging_link')

    def apply(**_kwargs):
        if state.boundary == 'boolean_cancelled':
            return {'CANCELLED'}
        fail('boolean_apply')
        if state.replace_mesh:
            blade = next(obj for obj in objects if obj.name == 'NREL.Editor.Staging1')
            generated = meshes.new('BooleanResult')
            generated.materials[:] = blade.data.materials
            generated.polygons[0].material_index = len(generated.materials) - 1
            blade.data = generated
        return {'FINISHED'}

    bpy = NS(data=NS(meshes=meshes, objects=objects, materials=materials, node_groups=groups),
        context=NS(view_layer=NS(update=lambda: None), temp_override=lambda **_kw: nullcontext()),
        ops=NS(object=NS(modifier_apply=apply)))
    monkeypatch.setattr(module, 'bpy', bpy)
    monkeypatch.setattr(module, 'Vector', Vector)
    bm = NS(from_mesh=lambda _mesh: fail('bmesh_load'), to_mesh=lambda _mesh: None, faces=[], free=Mock())
    monkeypatch.setattr(module, 'bmesh', NS(new=lambda: bm, ops=NS(recalc_face_normals=lambda *_a, **_kw: None)))
    monkeypatch.setattr(module, '_tree', lambda _mesh: object())
    monkeypatch.setattr(module, '_support', lambda *_a, **_kw: {})
    manager = module.DefectGeometry.__new__(module.DefectGeometry)
    manager._closed, manager._healthy, manager._baseline = False, healthy, {}
    manager._active = dict(document={}, meshes={i: b.data for i, b in enumerate(blades, 1)}, materials=[])
    manager._apex, manager._mechanical_tip = Vector((0., 1., 0.)), None
    manager.boolean_calls, manager.turbine_id = 0, 'T1'
    manager.rig = dict(blades=blades, collection=NS(objects=NS(link=link)))
    manager.surface = NS(sample_shape=lambda _d: dict(measured_length_m=.6, measured_max_width_m=.08, widths_m=[.08]))
    manager._footprint = lambda _shape: []
    manager._cut = lambda blade, defect, shape, material, owned_meshes: manager._apply_cutter(
        blade, defect, [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)], [(0, 1, 2)], material, owned_meshes)
    initial = tuple(tuple(id(item) for item in pool) for pool in (meshes, objects, materials, groups))
    return NS(module=module, manager=manager, state=state, meshes=meshes, objects=objects,
              materials=materials, groups=groups, initial=initial, bm=bm)


def candidate_document(with_cut=False):
    return {'defects': [dict(id='D1', revision=1, turbine_id='T1', blade_id=1, morphology='open_crack',
                            shape=dict(length_m=.6, max_width_m=.08, depth_m=.015))] if with_cut else []}


@pytest.mark.parametrize('boundary', [
    'staging_link', 'stable_tips_data', 'stable_tips_update', 'material_nodes',
    'cutter_data', 'cutter_link', 'bmesh_load', 'boolean_apply',
])
def test_prepare_failure_releases_every_candidate_datablock(allocations, boundary):
    r = allocations
    r.state.boundary = boundary
    active = r.manager.active
    with pytest.raises(RuntimeError) as caught:
        r.manager.prepare(candidate_document(boundary not in ('staging_link', 'stable_tips_data', 'stable_tips_update')))
    assert caught.value is r.state.primary
    assert r.manager.active is active
    assert all(blade.data is active['meshes'][i] for i, blade in enumerate(r.manager.rig['blades'], 1))
    assert tuple(tuple(id(item) for item in pool) for pool in (r.meshes, r.objects, r.materials, r.groups)) == r.initial
    if boundary == 'bmesh_load':
        r.bm.free.assert_called_once()


def test_cancelled_boolean_is_rejected_and_modifier_removed(allocations):
    r = allocations
    r.state.boundary = 'boolean_cancelled'
    blade = r.manager.rig['blades'][0]
    # A matching material index cannot turn a cancelled Blender operator into success.
    blade.data.polygons[0].material_index = len(blade.data.materials)
    with pytest.raises(RuntimeError, match='Boolean.*CANCELLED'):
        r.manager._apply_cutter(blade, {'id': 'D1'}, [(0, 0, 0)] * 3, [(0, 1, 2)], object())
    assert not blade.modifiers
    assert not any(obj.name == 'NREL.Editor.Cutter' for obj in r.objects)
    assert not any(mesh.name == 'NREL.Editor.Cutter' for mesh in r.meshes)


@pytest.mark.parametrize('boundary', ['staging_link', 'stable_tips_data'])
def test_prepare_cleanup_failure_keeps_primary_and_close_retries(allocations, boundary):
    r = allocations
    r.state.boundary, r.state.release_failure = boundary, True
    with pytest.raises(RuntimeError) as caught:
        r.manager.prepare(candidate_document())
    assert caught.value is r.state.primary
    r.state.boundary, r.state.release_failure = None, False
    r.manager.close(restore=False)
    assert not any(obj.name.startswith('NREL.Editor') for obj in r.objects)
    assert not any(mesh.name.startswith('NREL.Editor') or '.copy' in mesh.name for mesh in r.meshes)


@pytest.mark.parametrize('cuts', [1, 2])
def test_replaced_boolean_mesh_is_released_after_tip_rebuild_failure(allocations, cuts):
    r = allocations
    r.state.replace_mesh, r.state.boundary = True, 'stable_tips_data'
    original = r.manager._cut
    def cut(*args):
        for _ in range(cuts):
            slot = original(*args)
        return slot
    r.manager._cut = cut
    with pytest.raises(RuntimeError) as caught:
        r.manager.prepare(candidate_document(True))
    assert caught.value is r.state.primary
    assert tuple(tuple(id(item) for item in pool) for pool in (r.meshes, r.objects, r.materials, r.groups)) == r.initial


def test_failed_old_mesh_release_retains_ownership_after_mesh_swap(allocations):
    r = allocations
    r.state.mesh_release_once = True
    with pytest.raises(RuntimeError) as caught:
        r.manager.prepare(candidate_document())
    assert str(r.state.primary) in str(caught.value)
    assert tuple(tuple(id(item) for item in pool) for pool in (r.meshes, r.objects, r.materials, r.groups)) == r.initial


def test_successful_preparation_owns_meshes_until_disposal(allocations):
    r = allocations
    payload = r.manager.prepare(candidate_document())
    assert len(payload['meshes']) == 3
    assert all(any(item is mesh for item in r.meshes) for mesh in payload['meshes'].values())
    assert not any(obj.name.startswith('NREL.Editor') for obj in r.objects)
    r.manager.dispose(payload)
    r.manager.dispose(payload)
    assert tuple(tuple(id(item) for item in pool) for pool in (r.meshes, r.objects, r.materials, r.groups)) == r.initial


@pytest.mark.parametrize('path,value', [
    (('anchor', 's_m'), math.nan), (('anchor', 'u'), math.inf),
    (('shape', 'depth_m'), -math.inf), (('anchor', 's_m'), 70.),
    (('anchor', 'u'), 1.), (('shape', 'length_m'), 6.),
])
def test_invalid_defect_parameters_fail_before_preparation(path, value):
    vertices, faces = geometry.blade_mesh(4, 48)
    surface = reference.ReferenceSurface(vertices, faces)
    document = schema.new_document(surface)
    document['defects'] = [schema.new_defect(surface, 'pit')]
    invalid = deepcopy(document)
    invalid['defects'][0][path[0]][path[1]] = value
    with pytest.raises(ValueError):
        schema.validate_document(invalid, surface)
