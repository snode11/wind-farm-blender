"""Farm binding failures release handlers and retain retryable resource ownership."""
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest

from test_scene_playback_lifecycle import Scene, runtimes  # noqa: F401
from wfrl_blender import clearance_replay, farm_flex, split_turbine_context, tip_tracking
from wfrl_blender import farm_flex_scene


@pytest.fixture(autouse=True)
def pending_bindings(monkeypatch):
    monkeypatch.setattr(farm_flex_scene, '_PENDING_BINDINGS', [])


@pytest.mark.parametrize('other_scene', [False, True])
@pytest.mark.parametrize('failure', ['tip_register', 'restore_saved', 'frame_set', 'frame_latched'])
def test_attach_finalization_failure_releases_only_failed_runtime(runtimes, monkeypatch, failure, other_scene):
    bpy, Runtime = runtimes
    other = Scene('Other')
    survivor = farm_flex.attach(other, 'other') if other_scene else None
    scene, built = Scene('Candidate'), []

    def bind(target, path, *, _package):
        active = Runtime(target, path, _package=_package)
        active.comparison = SimpleNamespace(close=Mock())
        active.cache = {'geometry': object()}
        active._world_matrices = active._update_geometry = active._deformation_batches = object()
        scene['wfrl_flex_active'] = True
        scene['wfrl_flex_frame_valid'] = True
        built.append(active)
        return active

    def fail(*args):
        raise RuntimeError('finalization failed')

    monkeypatch.setattr(farm_flex, 'FarmFlex', bind)
    if failure == 'tip_register':
        monkeypatch.setattr(tip_tracking, 'register', fail)
    elif failure == 'restore_saved':
        monkeypatch.setattr(sys.modules['wfrl_blender.nrel_defects.editor'], 'restore_saved', fail)
    elif failure == 'frame_set':
        scene.frame_set = fail
    else:
        def frame_set(_frame):
            # Blender reports Python handler exceptions without raising from frame_set.
            built[-1].frame_error = 'finalization failed'
            scene['wfrl_flex_frame_valid'] = False
        scene.frame_set = frame_set

    with pytest.raises((RuntimeError, ValueError), match='finalization failed'):
        farm_flex.attach(scene, 'candidate')

    failed, = built
    assert not failed.enabled and farm_flex.active_for(scene) is None
    assert scene.as_pointer() not in farm_flex._ACTIVES
    assert scene.as_pointer() not in clearance_replay._READERS
    assert not scene['wfrl_flex_active']
    assert not scene['wfrl_flex_frame_valid']
    assert 'finalization failed' in scene['wfrl_home_load_error']
    failed.comparison.close.assert_called_once()
    assert not failed.cache
    assert failed._world_matrices is failed._update_geometry is failed._deformation_batches is None
    if other_scene:
        assert farm_flex.ready_for(other) is survivor
        assert farm_flex._ACTIVE is survivor
        assert bpy.app.handlers.frame_change_post.count(farm_flex.update) == 1
    else:
        assert farm_flex._ACTIVE is None
        assert farm_flex.update not in bpy.app.handlers.frame_change_post
        assert farm_flex.on_load_pre not in bpy.app.handlers.load_pre


@pytest.mark.parametrize('failure', ['comparison', 'support', 'split', 'editor'])
def test_detach_failure_invalidates_all_owners_and_keeps_cleanup_retryable(runtimes, monkeypatch, failure):
    bpy, _ = runtimes
    scenes = [Scene('A'), Scene('B')]
    actives = [farm_flex.attach(scene, 'package') for scene in scenes]
    for active in actives:
        active.blades, active.towers = [], []
        active.comparison = SimpleNamespace(close=Mock())
        active.restore_support = Mock()
        active.cache = {'geometry': object()}
        active._world_matrices = active._update_geometry = active._deformation_batches = object()
    reject_once = Mock(side_effect=[RuntimeError('cleanup blocked'), None])
    if failure == 'comparison':
        actives[0].comparison.close = reject_once
    elif failure == 'support':
        actives[0].restore_support = reject_once
    elif failure == 'split':
        monkeypatch.setattr(split_turbine_context, 'clear_runtime', reject_once)
    else:
        monkeypatch.setattr(sys.modules['wfrl_blender.nrel_defects.editor'], 'detach', reject_once)

    with pytest.raises(RuntimeError, match='cleanup blocked'):
        farm_flex.detach()

    assert all(not active.enabled for active in actives)
    assert all(farm_flex.active_for(scene) is None for scene in scenes)
    assert not clearance_replay._READERS
    assert farm_flex._ACTIVE is None
    assert farm_flex.update not in bpy.app.handlers.frame_change_post
    assert (farm_flex.on_load_pre in bpy.app.handlers.load_pre) == (failure in ('comparison', 'support'))
    for active in actives:
        assert not active.cache
        assert active._world_matrices is active._update_geometry is active._deformation_batches is None
    actives[1].comparison.close.assert_called_once()
    actives[1].restore_support.assert_called_once()
    if failure in ('comparison', 'support'):
        assert farm_flex._ACTIVES == {scenes[0].as_pointer(): actives[0]}
    farm_flex.detach()
    assert not farm_flex._ACTIVES
    assert reject_once.call_count == 2
    assert farm_flex.on_load_pre not in bpy.app.handlers.load_pre


def test_load_pre_invalidates_runtime_even_if_trail_cleanup_fails(runtimes, monkeypatch):
    bpy, _ = runtimes
    scene = Scene('Loading')
    active = farm_flex.attach(scene, 'package')
    monkeypatch.setattr(tip_tracking, 'on_load_pre', Mock(side_effect=RuntimeError('trail cleanup failed')))
    clear_cache = Mock()
    monkeypatch.setattr(farm_flex, 'clear_package_cache', clear_cache)

    with pytest.raises(RuntimeError, match='trail cleanup failed'):
        farm_flex.on_load_pre(None)

    assert not active.enabled and not farm_flex._ACTIVES
    assert farm_flex._ACTIVE is None and not clearance_replay._READERS
    assert farm_flex.update not in bpy.app.handlers.frame_change_post
    assert farm_flex.on_load_pre not in bpy.app.handlers.load_pre
    clear_cache.assert_called_once()


def test_attach_keeps_primary_error_when_comparison_cleanup_also_fails(runtimes, monkeypatch):
    bpy, Runtime = runtimes
    scene, built = Scene('Candidate'), []
    close = Mock(side_effect=[RuntimeError('cleanup blocked'), None])

    def bind(target, path, *, _package):
        active = Runtime(target, path, _package=_package)
        active.blades = []
        active.comparison = SimpleNamespace(close=close)
        built.append(active)
        return active

    monkeypatch.setattr(farm_flex, 'FarmFlex', bind)
    monkeypatch.setattr(tip_tracking, 'register', Mock(side_effect=ValueError('activation failed')))
    with pytest.raises(ValueError, match='activation failed') as error:
        farm_flex.attach(scene, 'candidate')

    assert 'cleanup blocked' in '\n'.join(error.value.__notes__)
    assert 'activation failed' in scene['wfrl_home_load_error']
    assert 'cleanup blocked' in scene['wfrl_home_load_error']
    assert not built[0].enabled and not clearance_replay._READERS
    assert farm_flex.on_load_pre in bpy.app.handlers.load_pre
    farm_flex.detach(scene, restore=False)
    assert not farm_flex._ACTIVES and close.call_count == 2


def test_failed_attach_preserves_existing_tracking_handler_order(runtimes, monkeypatch):
    bpy, _Runtime = runtimes
    other = Scene('Other')
    survivor = farm_flex.attach(other, 'other')
    handlers = bpy.app.handlers
    editor = sys.modules['wfrl_blender.nrel_defects.editor']
    handlers.frame_change_post[:] = [farm_flex.update, tip_tracking.update, editor.frame_changed]
    handlers.load_pre[:] = [tip_tracking.on_load_pre, farm_flex.on_load_pre]
    before = list(handlers.frame_change_post), list(handlers.load_pre)
    monkeypatch.setattr(tip_tracking, 'register', Mock(side_effect=RuntimeError('registration failed')))

    with pytest.raises(RuntimeError, match='registration failed'):
        farm_flex.attach(Scene('Candidate'), 'candidate')

    assert farm_flex.ready_for(other) is survivor
    assert (handlers.frame_change_post, handlers.load_pre) == before


def test_attach_rejects_a_first_frame_that_disabled_its_runtime(runtimes):
    _bpy, _Runtime = runtimes
    scene = Scene('Unavailable')

    def frame_set(_frame):
        farm_flex.active_for(scene).enabled = False
    scene.frame_set = frame_set
    with pytest.raises(ValueError, match='initial frame unavailable'):
        farm_flex.attach(scene, 'candidate')
    assert not farm_flex._ACTIVES and not clearance_replay._READERS
    assert not scene['wfrl_flex_active']


@pytest.fixture
def mesh_data(monkeypatch):
    meshes = []

    class Mesh(dict):
        __eq__ = object.__eq__

        def __init__(self):
            super().__init__()
            self.users, self.use_fake_user = 0, False
            self.library = self.shape_keys = None
            meshes.append(self)

    class Object:
        def __init__(self, mesh):
            self._data = mesh
            mesh.users += 1
            self.blocked = None

        @property
        def data(self):
            return self._data

        @data.setter
        def data(self, mesh):
            if mesh is self.blocked:
                self.blocked = None
                raise RuntimeError('assignment blocked')
            self._data.users -= 1
            self._data = mesh
            mesh.users += 1

    def remove(mesh):
        assert mesh.users == 0
        meshes.remove(mesh)

    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(
        data=SimpleNamespace(meshes=SimpleNamespace(remove=remove))))
    return Mesh, Object, meshes


def test_mesh_rollback_continues_after_one_assignment_failure_and_can_retry(mesh_data):
    Mesh, Object, meshes = mesh_data
    bindings = farm_flex._MeshBindings()
    originals, replacements, objects = [], [], []
    for _ in range(2):
        original, replacement = Mesh(), bindings.own(Mesh())
        obj = Object(original)
        bindings.replace(obj, replacement)
        originals.append(original)
        replacements.append(replacement)
        objects.append(obj)
    objects[1].blocked = originals[1]

    with pytest.raises(RuntimeError, match='assignment blocked'):
        bindings.rollback()

    assert objects[0].data is originals[0]
    assert objects[1].data is replacements[1]
    assert replacements[0] not in meshes
    assert bindings.replaced == [(objects[1], originals[1], replacements[1])]
    assert bindings.created == [replacements[1]]
    bindings.rollback()
    assert all(obj.data is original for obj, original in zip(objects, originals))
    assert meshes == originals
    assert not bindings.created and not bindings.replaced


def test_constructor_rollback_failure_remains_owned_until_detach(runtimes, monkeypatch):
    bpy, _Runtime = runtimes
    scene = Scene('Binding')
    original = SimpleNamespace(users=0)
    candidate = SimpleNamespace(users=0)
    remove = Mock(side_effect=[RuntimeError('mesh cleanup blocked'), None])
    bpy.data = SimpleNamespace(meshes=SimpleNamespace(remove=remove))
    monkeypatch.setattr(farm_flex._MeshBindings, '_release', staticmethod(remove))

    def bind(owner, target, path, *, _package=None):
        assert target is scene
        owner._meshes.created.append(candidate)
        obj = SimpleNamespace(data=candidate)
        owner._meshes.replaced.append((obj, original, candidate))
        raise ValueError('binding failed')

    monkeypatch.setattr(farm_flex_scene._SceneBinding, '_bind', bind)
    with pytest.raises(ValueError, match='binding failed') as error:
        farm_flex_scene._SceneBinding(scene, 'candidate')

    assert 'mesh cleanup blocked' in '\n'.join(error.value.__notes__)
    assert remove.call_count == 1
    assert farm_flex.on_load_pre in bpy.app.handlers.load_pre
    farm_flex.detach(scene, restore=False)
    assert remove.call_count == 2
    farm_flex.detach(scene, restore=False)
    assert remove.call_count == 2
