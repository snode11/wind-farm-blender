"""Failed manual-scene transitions retain their original failure and ownership."""
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from wfrl_blender import manual_scene


class Scene(dict):
    pass


def test_failed_manual_entry_discards_resume_token_and_preserves_error(monkeypatch, stub_module, caplog):
    source, manual = Scene(), Scene(wfrl_scene_kind='manual')
    source.wfrl_manual_scene = manual
    window = NS(scene=source, as_pointer=lambda: 123)
    context = NS(scene=source, window=window)
    stub_module('wfrl_blender.manual_control', validate_scene=lambda _s: (True, ''), pause_scene=Mock())
    stub_module('wfrl_blender.playback', is_playing=lambda _s: False)
    for name in ('_remember_view', '_ensure_environment', '_ensure_blade_model', '_save_return_view'):
        monkeypatch.setattr(manual_scene, name, Mock())
    monkeypatch.setattr(manual_scene, 'entry_error', lambda _c: '')
    monkeypatch.setattr(manual_scene, '_RESUME', {})
    primary = KeyError('focus target missing')
    monkeypatch.setattr(manual_scene, 'focus', Mock(side_effect=primary))
    monkeypatch.setattr(manual_scene, '_restore_view', Mock(side_effect=RuntimeError('view restore failed')))
    with pytest.raises(KeyError) as caught:
        manual_scene.enter(context)
    assert caught.value is primary and window.scene is source
    assert not manual_scene._RESUME
    assert 'view restore failed' in caplog.text


def test_failed_manual_construction_preserves_error_when_discard_fails(monkeypatch, stub_module, caplog):
    source, scene = Scene(), Scene()
    context = NS(scene=source, window=NS(scene=source))
    primary = KeyError('collection creation failed')
    bpy = NS(data=NS(scenes=NS(new=lambda _name: scene),
                    collections=NS(new=Mock(side_effect=primary))))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    stub_module('wfrl_blender.manual_control')
    stub_module('wfrl_blender.materials', get_material=Mock())
    stub_module('wfrl_blender.scene_builder', _make_turbine=Mock(), _cone=Mock(), _finish=Mock())
    monkeypatch.setattr(manual_scene, 'entry_error', lambda _c: '')
    monkeypatch.setattr(manual_scene, '_discard', Mock(side_effect=RuntimeError('scene release failed')))
    with pytest.raises(KeyError) as caught:
        manual_scene.create(context)
    assert caught.value is primary and context.window.scene is source
    manual_scene._discard.assert_called_once()
    assert 'scene release failed' in caplog.text


@pytest.mark.parametrize('error_type', [ValueError, KeyboardInterrupt])
def test_surface_rollback_cannot_replace_primary(monkeypatch, stub_module, caplog, error_type):
    class Mesh:
        library = None
        use_fake_user = False
        users = 1
        materials = [NS(name='Owner.hub')]
    class Object:
        type = 'MESH'
        name = 'Owner.Turbine.T1.Hub'
        fail_restore = False
        _data = Mesh()
        def get(self, _key):
            return 'Owner'
        @property
        def data(self):
            return self._data
        @data.setter
        def data(self, value):
            if self.fail_restore:
                raise RuntimeError('mesh restoration failed')
            self._data = value
    class HashableScene(Scene):
        __hash__ = object.__hash__
    scene = HashableScene(wfrl_manual_owner='Owner')
    obj = Object()
    obj.users_scene = [scene]
    scene.objects = [obj]
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(objects=[obj])))
    primary = error_type('surface setup failed')
    def refresh(*_args, **_kwargs):
        obj.fail_restore = True
        raise primary
    stub_module('wfrl_blender.materials', get_material=Mock(),
                turbine_surface_kind=lambda _name: 'hub', refresh_turbine_surfaces=refresh)
    with pytest.raises(error_type) as caught:
        manual_scene._ensure_surface_roles(scene)
    assert caught.value is primary
    assert 'mesh restoration failed' in caplog.text


def test_corrupt_saved_return_view_is_reported(monkeypatch, caplog):
    monkeypatch.setattr(manual_scene, '_VIEWS', {})
    context = NS(window=NS(as_pointer=lambda: 123), screen=NS(areas=[]))
    manual_scene._restore_view(context, {'wfrl_manual_return_views': '{broken'})
    assert 'Saved manual return view is invalid' in caplog.text
