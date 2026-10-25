"""Pure distribution rules for the Part 5 procedural grassland."""

import hashlib
import random
import struct
import sys
import unittest
from types import SimpleNamespace

import pytest

from wfrl_blender.landscape import lowland_factor, road_clearance, road_y, vegetation_factor
from wfrl_blender import landscape


class LandscapeDistributionTests(unittest.TestCase):
    def test_road_and_maintenance_pads_are_clear(self):
        self.assertEqual(road_clearance(250.0, road_y(250.0)), 0.0)
        self.assertEqual(road_clearance(504.0, -11.5), 0.0)
        self.assertEqual(vegetation_factor(250.0, road_y(250.0), -5.0), 0.0)

    def test_verge_transitions_from_sparse_to_full_density(self):
        y = road_y(250.0)
        near = vegetation_factor(250.0, y + 18.0, -5.0)
        far = vegetation_factor(250.0, y + 70.0, -5.0)
        self.assertGreater(near, 0.0)
        self.assertLess(near, far)

    def test_low_ground_is_weighted_more_heavily(self):
        self.assertGreater(lowland_factor(-5.0), lowland_factor(18.0))
        low = vegetation_factor(250.0, road_y(250.0) + 80.0, -5.0)
        high = vegetation_factor(250.0, road_y(250.0) + 80.0, 18.0)
        self.assertGreater(low, high)


def test_vegetation_placement_and_following_rng_draws():
    rng = random.Random(4207)
    shrubs, rocks = landscape._vegetation_points(
        rng,
        ((10., 70., -4., 24.), (504., -170., 13., 28.), (1100., 40., 0., 32.)),
        ((-300., 150., 16., 22.), (740., -210., 18., 36.)),
        lambda x, y: x*.006+y*.004)
    assert (len(shrubs), len(rocks)) == (149, 48)
    for points, species_offset, expected in (
            (shrubs, 0, '997680e1f08a17409dffad76cc54d53fe5305133eee6fa8e4195539fea12f893'),
            (rocks, 3, 'd8023dd5745ba94eaf3cd33b55377dadbbef31083527eb84e58516fe2ea3169b')):
        digest = hashlib.sha256()
        for location, rotation, scale, species, cluster in points:
            digest.update(struct.pack('<9f2i', *location, *rotation, *scale,
                                      species+species_offset, cluster))
        assert digest.hexdigest() == expected
    tail = struct.pack('<32d', *(rng.random() for _ in range(32)))
    assert hashlib.sha256(tail).hexdigest() == 'f7beb6b3b41386295efd35110725c7fa718f879289b147fc6daeed1a377e6c49'


class _Block(dict):
    def __init__(self, name, **properties):
        super().__init__()
        self.name = name
        self.users = 0
        self.use_fake_user = False
        self.__dict__.update(properties)

    def as_pointer(self):
        return id(self)


class _Objects(list):
    def link(self, obj):
        self.append(obj)
        obj.users += 1

    def unlink(self, obj):
        index = next(index for index, item in enumerate(self) if item is obj)
        del self[index]
        obj.users -= 1


class _Indices(list):
    def foreach_set(self, field, values):
        assert field == 'value'
        assert len(self) == len(values)
        for item, value in zip(self, values):
            item.value = value


def test_shrub_lod_counts_instances_and_preserves_near_meshes(monkeypatch):
    sources = [_Block(f'Prototype{index:03d}', data=_Block(f'Species{index}',
                vertices=[None]*size)) for index, size in enumerate((100, 200, 300))]
    prototypes = SimpleNamespace(objects=_Objects())
    for source in sources:
        source['wfrl_landscape_prototype'] = True
        prototypes.objects.link(source)
    locations = ((0, 179.99998, 0), (0, 180, 0), (504, 0, 0),
                 (504, -180, 0), (1008, 181, 0), (250, 300, 0))
    indices = _Indices(SimpleNamespace(value=index) for index in (0, 0, 1, 1, 1, 2))
    untouched = {'rotation': [(0, 0, .4)]*6, 'scale': [(2, 1, .5)]*6, 'cluster': list(range(6))}
    attributes = dict(untouched, prototype_index=SimpleNamespace(data=indices))
    data = SimpleNamespace(vertices=[SimpleNamespace(co=point) for point in locations],
                           attributes=attributes, update=lambda: None)
    instancer = _Block('WFRL.Landscape.Shrubs', data=data)
    instancer['wfrl_landscape_instances'] = 'SHRUB'
    rock = _Block('WFRL.Landscape.Rocks')
    rock['wfrl_landscape_instances'] = 'ROCK'
    collection = SimpleNamespace(objects=[instancer, rock])
    monkeypatch.setattr(landscape, '_instance_collection', lambda obj: prototypes)
    reductions, removed = [], []

    def reduce_mesh(collection, data):
        reductions.append(data)
        return _Block(data.name+'.Distant', vertices=[None]*(len(data.vertices)*18//100))

    def prototype(collection, name, data):
        obj = _Block(name, data=data)
        obj['wfrl_landscape_prototype'] = True
        collection.objects.link(obj)
        return obj

    monkeypatch.setattr(landscape, '_distant_shrub_mesh', reduce_mesh)
    monkeypatch.setattr(landscape, '_new_landscape_prototype', prototype)
    monkeypatch.setattr(landscape, '_remove_landscape_prototype', removed.append)
    assert landscape.optimize_shrubs(collection) == {'before': 1100, 'after': 444, 'objects': 6}
    ordered = sorted(prototypes.objects, key=lambda source: source.name)
    assigned = [ordered[index.value] for index in indices]
    assert assigned[0] is sources[0]
    assert assigned[2] is sources[1]
    assert assigned[3] is assigned[4]
    assert [len(source.data.vertices) for source in assigned] == [100, 18, 200, 36, 36, 54]
    assert {item.as_pointer() for item in reductions} == {source.data.as_pointer() for source in sources}
    assert [source.as_pointer() for source in removed] == [sources[2].as_pointer()]
    assert all(attributes[key] == value for key, value in untouched.items())
    assert landscape.optimize_shrubs(collection) == {'before': 444, 'after': 444, 'objects': 6}
    assert len(reductions) == 3


@pytest.mark.parametrize('shared_group,shared_mesh', [(False, False), (True, False), (False, True)])
def test_instancer_release_preserves_shared_resources_and_user_orphans(monkeypatch, shared_group, shared_mesh):
    point_data = _Block('Points', users=1, shape_keys=None)
    point_data['wfrl_landscape_points'] = True
    source_data = _Block('Species', users=1+shared_mesh, shape_keys=None)
    source_data['wfrl_landscape_prototype'] = True
    source = _Block('Prototype000', data=source_data, users=1)
    source['wfrl_landscape_prototype'] = True
    prototypes = _Block('Prototypes', users=1, objects=_Objects([source]))
    prototypes['wfrl_landscape_prototypes'] = True
    node = SimpleNamespace(inputs={'Collection': SimpleNamespace(default_value=prototypes)})
    group = _Block('Instances', users=1+shared_group, nodes={'WFRL.Prototypes': node})
    group['wfrl_landscape_instances'] = True
    modifier = SimpleNamespace(type='NODES', node_group=group)
    obj = _Block('Shrubs', data=point_data, modifiers=[modifier])
    user_orphan = _Block('User mesh', shape_keys=None)
    removed_objects, removed_meshes, removed_groups, removed_collections = [], [], [], []

    def remove_object(item, **kwargs):
        removed_objects.append(item.as_pointer())
        item.data.users -= 1
        if item is obj:
            group.users -= 1

    def remove_group(item):
        removed_groups.append(item.as_pointer())
        prototypes.users -= 1

    def remove_collection(item):
        removed_collections.append(item.as_pointer())
        for source in list(item.objects):
            item.objects.unlink(source)

    fake_bpy = SimpleNamespace(data=SimpleNamespace(
        objects=SimpleNamespace(remove=remove_object),
        meshes=SimpleNamespace(remove=lambda data: removed_meshes.append(data.as_pointer())),
        node_groups=SimpleNamespace(remove=remove_group),
        collections=SimpleNamespace(remove=remove_collection)))
    monkeypatch.setitem(sys.modules, 'bpy', fake_bpy)
    landscape.remove_landscape_instancer(obj)
    assert point_data.as_pointer() in removed_meshes
    assert user_orphan.as_pointer() not in removed_meshes
    assert (group.as_pointer() in removed_groups) == (not shared_group)
    assert (prototypes.as_pointer() in removed_collections) == (not shared_group)
    assert (source.as_pointer() in removed_objects) == (not shared_group)
    assert (source_data.as_pointer() in removed_meshes) == (not shared_group and not shared_mesh)


@pytest.mark.parametrize('ownership,users,fake_user,shape_keys', [
    (False, 0, False, None), (True, 1, False, None),
    (True, 0, True, None), (True, 0, False, object())])
def test_mesh_release_preserves_unowned_retained_and_shape_key_data(monkeypatch, ownership, users, fake_user, shape_keys):
    removed = []
    data = _Block('Mesh', users=users, use_fake_user=fake_user, shape_keys=shape_keys)
    data['wfrl_landscape_prototype'] = ownership
    fake_bpy = SimpleNamespace(data=SimpleNamespace(meshes=SimpleNamespace(remove=removed.append)))
    monkeypatch.setitem(sys.modules, 'bpy', fake_bpy)
    landscape._remove_landscape_mesh(data, 'wfrl_landscape_prototype')
    assert not removed


if __name__ == "__main__":
    unittest.main()
