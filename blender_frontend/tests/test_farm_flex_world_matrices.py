"""Replay readers use current rigid channels while evaluated matrices are stale."""
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from wfrl_blender import clearance_replay, deflection, farm_flex, tip_tracking


class Matrix:
    """Float32 storage with double accumulation for matrix products, like mathutils."""
    def __init__(self, values):
        self.values = np.array(values, dtype=np.float32)

    def copy(self):
        return Matrix(self.values)

    def inverted(self):
        return Matrix(np.linalg.inv(self.values))

    def __array__(self, dtype=None, copy=None):
        values = np.asarray(self.values, dtype=float if dtype is None else dtype)
        return values.copy() if copy else values

    def __matmul__(self, other):
        if isinstance(other, Matrix):
            return Matrix(self.values.astype(np.float64) @ other.values.astype(np.float64))
        return self.values[:3, :3] @ other + self.values[:3, 3]


@pytest.fixture(autouse=True)
def mathutils_module(monkeypatch):
    monkeypatch.setitem(sys.modules, 'mathutils', SimpleNamespace(
        Matrix=Matrix, Vector=lambda co: np.array(co, dtype=np.float32)))


class Object:
    def __init__(self, name, basis, parent=None, inverse=None):
        self.name, self.parent, self.parent_type = name, parent, 'OBJECT'
        self.basis = Matrix(basis)
        self.matrix_parent_inverse = Matrix(np.eye(4) if inverse is None else inverse)
        self.constraints, self.animation_data, self.rigid_body = [], None, None
        self.type, self.basis_reads = 'EMPTY', 0
        self.rotation_euler = SimpleNamespace(x=0., y=0., z=0.)
        self.matrix_world = Matrix(np.eye(4) * 7)

    def as_pointer(self):
        return id(self)

    @property
    def matrix_basis(self):
        self.basis_reads += 1
        return self.basis.copy()


def test_parent_inverse_order_float32_and_shared_parent_cache():
    rng = np.random.default_rng(11520)
    matrices = []
    for _ in range(5):
        value = rng.normal(size=(4, 4)).astype(np.float32)
        value[3] = [0, 0, 0, 1]
        matrices.append(value)
    root = Object('root', matrices[0])
    parent = Object('parent', matrices[2], root, matrices[1])
    blade = Object('blade', matrices[4], parent, matrices[3])
    cache = farm_flex._WorldMatrices(None, None)
    expected_parent = (matrices[0] @ matrices[1]).astype(np.float32) @ matrices[2]
    expected = (expected_parent @ matrices[3]).astype(np.float32) @ matrices[4]
    regrouped = expected_parent @ (matrices[3] @ matrices[4])
    assert not np.array_equal(expected, regrouped)
    double = (Matrix(matrices[0]) @ Matrix(matrices[1])) @ Matrix(matrices[2])
    double = (double @ Matrix(matrices[3])) @ Matrix(matrices[4])
    assert not np.array_equal(expected, double.values)
    actual = cache.world(blade)
    assert isinstance(actual, Matrix)
    assert actual.values.dtype == np.float32
    np.testing.assert_array_equal(actual.values, expected)
    cached = cache.matrices[blade.as_pointer()]
    assert isinstance(cached, np.ndarray) and cached.dtype == np.float32
    np.testing.assert_array_equal(cached, expected)
    sibling = Object('sibling', np.eye(4), parent)
    cache.world(sibling)
    np.testing.assert_array_equal(cache.world(blade).values, expected)
    assert cache.matrices[blade.as_pointer()] is cached
    assert root.basis_reads == parent.basis_reads == blade.basis_reads == 1


@pytest.mark.parametrize('target', ['object', 'ancestor'])
@pytest.mark.parametrize('condition', ['parent_type', 'constraint', 'driver',
                                       'rigid_body', 'curve_path'])
def test_unsupported_chain_evaluates_once_and_discards_analytic_cache(monkeypatch, target, condition):
    root = Object('root', np.eye(4))
    parent = Object('parent', np.eye(4), root)
    blade = Object('blade', np.eye(4), parent)
    obj = blade if target == 'object' else parent
    if condition == 'parent_type':
        obj.parent_type = 'BONE'
    elif condition == 'constraint':
        obj.constraints = [object()]
    elif condition == 'driver':
        obj.animation_data = SimpleNamespace(drivers=[object()])
    elif condition == 'rigid_body':
        obj.rigid_body = object()
    else:
        obj.parent.type = 'CURVE'
        obj.parent.data = SimpleNamespace(use_path=True)
    scene, calls = object(), []
    expected = Matrix(np.diag([2., 3., 4., 1.]))
    def evaluate(found):
        assert found is scene
        calls.append(found)
        blade.matrix_world = expected
    monkeypatch.setattr(farm_flex, '_update_scene_transforms', evaluate)
    cache = farm_flex._WorldMatrices(scene, None)
    cache.world(root)
    actual = cache.world(blade)
    np.testing.assert_array_equal(actual.values, expected.values)
    assert actual is not expected
    assert cache.matrices == {} and cache.evaluated
    cache.world(blade)
    cache.world(root)
    assert calls == [scene]


class Vertices(list):
    def __init__(self, points):
        super().__init__(SimpleNamespace(co=np.array(p, dtype=np.float32)) for p in points)

    def foreach_set(self, name, values):
        assert name == 'co'
        for vertex, co in zip(self, np.asarray(values).reshape(-1, 3), strict=True):
            vertex.co = co.astype(np.float32)

    def foreach_get(self, name, values):
        assert name == 'co'
        values[:] = np.asarray([vertex.co for vertex in self]).ravel()


class Scene(dict):
    def as_pointer(self):
        return id(self)


@pytest.fixture
def replay(monkeypatch):
    from wfrl_blender import nrel_defects, split_turbine_context
    from wfrl_blender.turbine_geometry import geometry_data

    scene = Scene(wfrl_clearance_time_s=0.)
    scene.frame_current, scene.frame_subframe = 1, 0.
    scene.wfrl_show_wake, scene.camera = False, None
    scene.wfrl_deflection_visible, scene.wfrl_deflection_blade = False, 1
    scene.objects = {}
    active = object.__new__(farm_flex.FarmFlex)
    active.scene, active.enabled, active._world_matrices = scene, True, None
    active.visible_turbines, active.readers = {0, 1, 2}, {'T1': object(), 'T2': object(), 'T3': object()}
    active.times, active.poses = np.array([0., 1.]), np.zeros((2, 3, 6))
    active.cache, active.groups, active.reference_matrices, active.blades = {}, {}, {}, []
    active.tower_motion, active.reference = None, None
    active.towers, active.fittings = [], []
    active.transforms = np.zeros((2, 3, 3, 19, 3, 4), dtype=np.float32)
    active.transforms[..., :3] = np.eye(3)
    active.transforms[1, ..., 0, 3] = .02
    rest = np.array([[0., 0., 1.5], [0., 0., 63.], [.01, 0., 63.]], dtype=np.float32)
    for k, tid in enumerate(active.readers):
        prefix = f'WFRL.Turbine.{tid}.'
        basis = np.eye(4)
        basis[:3, 3] = [k * 504., .125, 87.6]
        yaw = Object(prefix + 'YawRoot', basis)
        rotor = Object(prefix + 'Rotor', np.eye(4), yaw)
        scene.objects.update({yaw.name: yaw, rotor.name: rotor})
        for b in range(3):
            obj = Object(prefix + f'Blade{b+1}', np.eye(4), rotor)
            obj.data = SimpleNamespace(vertices=Vertices(rest), update=lambda: None)
            scene.objects[obj.name] = obj
            matrix = np.array(farm_flex._world_from_basis(obj, {}), dtype=float)
            offset = np.array([k * 504., 0., 0.])
            world = (rest @ matrix[:3, :3].T + matrix[:3, 3] - offset).astype(np.float32)
            active.blades.append((obj, rest.copy(), world, np.zeros(3, dtype=int),
                                  np.zeros((3, 1), dtype=np.float32), k, b, offset))
            active.groups[obj.name] = [(0, np.arange(3))]
            active.reference_matrices[obj.name] = matrix
    comparison = object.__new__(deflection.ComparisonView)
    comparison.owner, comparison.rows, comparison.objects = active, {}, []
    comparison.data = {'poses': np.zeros((2, 6)), 'simulation': np.zeros((2, 3, 2)),
                       'scalars': geometry_data()['scalars']}
    active.comparison = comparison
    calls = []
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(
        context=SimpleNamespace(scene=scene), app=SimpleNamespace(
            handlers=SimpleNamespace(frame_change_post=[], load_pre=[]))))
    editor = SimpleNamespace(after_update=lambda *_: None, detach=lambda *_, **_kw: None)
    monkeypatch.setitem(sys.modules, 'wfrl_blender.nrel_defects.editor', editor)
    monkeypatch.setattr(nrel_defects, 'editor', editor, raising=False)
    monkeypatch.setattr(split_turbine_context, 'sync', lambda *_: None)
    monkeypatch.setattr(farm_flex, '_update_scene_transforms', calls.append)
    monkeypatch.setattr(clearance_replay, 'reader_for', lambda _: active.readers['T1'])
    monkeypatch.setattr(clearance_replay, '_READERS', {})
    monkeypatch.setattr(farm_flex, '_ACTIVES', {scene.as_pointer(): active})
    monkeypatch.setattr(farm_flex, '_ACTIVE', None)
    return active, scene, calls


@pytest.mark.parametrize('time_s', [0., .5, 1.])
def test_update_comparison_hidden_tip_attachments_and_trail_use_current_matrix(replay, time_s):
    active, scene, calls = replay
    active.update(scene, sim_time_s=time_s, record_telemetry=False)
    visible = {b: {name: value.copy() for name, value in row.items()
                   if name in ('actual', 'reference', 'components', 'error')}
               for b, row in active.comparison.rows.items()}
    for row in active.blades:
        obj, rest, *_ = row
        world = active.world_matrix(obj)
        assert not np.array_equal(world.values, obj.matrix_world.values)
        np.testing.assert_array_equal(tip_tracking._tip_world(obj, scene=scene),
                                      world @ obj.data.vertices[-1].co)
        local = active.deform_points(obj, rest, time_s)
        points = active.deform_points(obj, rest, time_s, world=True)
        inverse = np.array(world.inverted())
        np.testing.assert_array_equal(local, points @ inverse[:3, :3].T + inverse[:3, 3])
    first_cache = active._world_matrices
    active.visible_turbines = {1}
    active.update(scene, sim_time_s=time_s, record_telemetry=False)
    assert active._world_matrices is not first_cache
    for b, expected in visible.items():
        for name, value in expected.items():
            np.testing.assert_array_equal(active.comparison.rows[b][name], value)
    assert calls == []


def test_same_frame_update_view_mutation_and_rebind_invalidate_world_cache(replay):
    active, scene, calls = replay
    active.update(scene, sim_time_s=0., record_telemetry=False)
    obj, rest, *_ = active.blades[0]
    previous = active.world_matrix(obj)
    scene.objects['WFRL.Turbine.T1.YawRoot'].basis.values[0, 3] += 1.
    active.update(scene, sim_time_s=.5, record_telemetry=False)
    current = active.world_matrix(obj)
    assert current is not previous
    np.testing.assert_array_equal(current.values[:3, 3] - previous.values[:3, 3], [1., 0., 0.])
    previous_cache = active._world_matrices
    active.visible_turbines.remove(0)
    np.testing.assert_array_equal(active.world_matrix(obj).values, current.values)
    assert active._world_matrices is not previous_cache
    active.rebind_blade(obj, rest)
    assert active._world_matrices is None
    assert calls == []


@pytest.mark.parametrize('mode', ['other_scene', 'export'])
def test_other_scene_and_export_keep_explicit_evaluation(replay, mode):
    active, scene, calls = replay
    active.world_matrix(active.blades[0][0])
    if mode == 'other_scene':
        sys.modules['bpy'].context.scene = object()
    else:
        active.export_turbines = {0}
    assert active._world_cache().evaluated
    assert calls == [scene]
    calls.clear()
    active.update(scene, sim_time_s=0., record_telemetry=False, update_comparison=False)
    assert active._world_matrices.evaluated
    active.world_matrix(active.blades[0][0])
    assert calls == [scene]


def test_detach_discards_world_cache(replay):
    active, scene, calls = replay
    active.update(scene, sim_time_s=0., record_telemetry=False)
    active.comparison = None
    farm_flex.detach(scene, restore=False)
    assert active._world_matrices is None and not active.enabled
    assert calls == []
