"""Data retention guards for scene destruction and replay mesh replacement."""
import sys
from types import SimpleNamespace

import pytest

from wfrl_blender import farm_flex, scene_builder


@pytest.fixture
def removed(monkeypatch):
    blocks = []
    data = SimpleNamespace(
        batch_remove=lambda *, ids: blocks.extend(ids),
        meshes=SimpleNamespace(remove=blocks.append),
    )
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(data=data))
    return blocks


@pytest.mark.parametrize('retained', [
    {'name': 'User.Mesh'},
    {'users': 1},
    {'use_fake_user': True},
    {'shape_keys': object()},
    {'library': object()},
    {},
])
def test_scene_cleanup_preserves_retained_data(removed, retained):
    block = SimpleNamespace(name='WFRL.Generated.Mesh', users=0,
                            use_fake_user=False, shape_keys=None, library=None)
    block.__dict__.update(retained)
    scene_builder._release_scene_data(block)
    assert removed == ([] if retained else [block])


def test_scene_cleanup_handles_non_mesh_data_and_missing_ids(removed):
    action = SimpleNamespace(name='WFRL.Generated.Action', users=0,
                             use_fake_user=False, library=None)

    class Deleted:
        @property
        def name(self):
            raise ReferenceError('Removed data')

    scene_builder._release_scene_data(None)
    scene_builder._release_scene_data(Deleted())
    scene_builder._release_scene_data(action)
    assert removed == [action]


@pytest.mark.parametrize('retained', [
    {'owned': False},
    {'users': 1},
    {'use_fake_user': True},
    {'shape_keys': object()},
    {'library': object()},
    {},
])
def test_replay_cleanup_requires_ownership_and_no_retention(removed, retained):
    mesh = SimpleNamespace(owned=True, users=0, use_fake_user=False,
                           shape_keys=None, library=None)
    mesh.__dict__.update(retained)
    mesh.get = lambda key: mesh.owned if key == 'wfrl_farm_flex_owned' else None
    farm_flex._MeshBindings._release(mesh)
    assert removed == ([] if retained else [mesh])
