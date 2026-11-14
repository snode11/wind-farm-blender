"""Failed palette construction cannot publish an incomplete new material."""
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import materials


class Material(dict):
    users = 0
    use_fake_user = False
    library = None

    def __bool__(self):
        return True

    def __init__(self, name):
        self.name = name
        self.node_tree = NS(nodes=self, links=NS())

    def __iter__(self):
        raise RuntimeError('node construction failed')

    def clear(self):
        raise RuntimeError('node construction failed')

    def get(self, name, default=None):
        raise RuntimeError('node construction failed')


@pytest.fixture
def material_store(monkeypatch, stub_module):
    class Store(dict):
        def new(self, name):
            value = Material(name)
            self[name] = value
            return value

        def remove(self, value):
            del self[value.name]

    store = Store()
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(materials=store)))

    def terrain_shader(_):
        raise RuntimeError('node construction failed')

    stub_module('wfrl_blender.landscape', terrain_shader=terrain_shader)
    return store


@pytest.mark.parametrize('kind', ['tower', 'wake_line', 'terrain', 'volume', 'tip'])
def test_failed_new_material_initialization_releases_the_datablock(material_store, kind):
    with pytest.raises(RuntimeError, match='construction failed'):
        if kind == 'volume':
            materials.wake_volume_material()
        elif kind == 'tip':
            materials.ensure_blade_tip_markings([])
        else:
            materials.get_material(kind)
    assert material_store == {}


def test_failed_existing_material_initialization_never_deletes_user_data(material_store):
    existing = material_store.new('WFRL.tower')
    with pytest.raises(RuntimeError, match='construction failed'):
        materials.get_material('tower')
    assert material_store['WFRL.tower'] is existing
