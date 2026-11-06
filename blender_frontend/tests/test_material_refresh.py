"""Material reuse stays local to one refresh and respects surface ownership."""
import sys
from types import SimpleNamespace as NS

import pytest

import wfrl_blender as addon
from wfrl_blender import materials


def mesh(name, material):
    return NS(name=name, type='MESH', data=NS(materials=[material]))


@pytest.mark.parametrize('private', [False, True])
def test_surface_refresh_reloads_materials_and_preserves_unowned_slots(monkeypatch, private):
    old = object()
    objects = [mesh('WFRL.Turbine.T1.MainShaft', old),
               mesh('WFRL.Turbine.T2.RootFlange', old),
               mesh('WFRL.Turbine.T1.PitchSeal', old)]
    untouched = [mesh('Other.MainShaft', old),
                 mesh('WFRL.Turbine.T1.ClearanceRadar.MainShaft', old),
                 mesh('WFRL.Turbine.T1.Tower', old)]
    available, calls = {}, []

    def material_for(kind):
        calls.append(kind)
        return available['WFRL.' + kind]

    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(materials=available)))
    monkeypatch.setattr(materials, 'get_material', material_for)
    for _ in range(2):
        # Replacement models or rebuilt Blender data must use current materials.
        available.update({'WFRL.metal': object(), 'WFRL.rubber': object()})
        calls.clear()
        materials.refresh_turbine_surfaces(
            objects + untouched, material_factory=material_for if private else None)
        assert objects[0].data.materials == [available['WFRL.metal']]
        assert objects[1].data.materials == [available['WFRL.metal']]
        assert objects[2].data.materials == [available['WFRL.rubber']]
        assert all(obj.data.materials == [old] for obj in untouched)
        assert sorted(calls) == ['metal', 'rubber']


def test_surface_refresh_keeps_custom_factory_separate(monkeypatch):
    old, default, private = object(), object(), object()
    target = mesh('Private.Turbine.T1.MainShaft', old)
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(materials={'WFRL.metal': default})))

    def unexpected_default(_kind):
        pytest.fail('Private refresh must not configure global materials')

    monkeypatch.setattr(materials, 'get_material', unexpected_default)
    materials.refresh_turbine_surfaces(
        [target], object_prefix='Private.Turbine.', material_factory=lambda kind: private)
    assert target.data.materials == [private]
    replacement = object()
    materials.refresh_turbine_surfaces(
        [target], object_prefix='Private.Turbine.', material_factory=lambda kind: replacement)
    assert target.data.materials == [replacement]


def test_layer_refresh_reloads_materials_after_hiding_and_rebuilding(monkeypatch, stub_module):
    stub_module('wfrl_blender.cinematic', set_visibility=lambda _scene: None)
    monkeypatch.setattr(addon.wake, 'update_proxy_objects', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(addon.wake, 'set_disxy_validity', lambda *_args, **_kwargs: None)
    old = object()

    def curve(name):
        return NS(name=name, data=NS(materials=[old], bevel_depth=0.0),
                  hide_render=False, hide_set=lambda *_args, **_kwargs: None)

    line = curve('WFRL.WakeProxy.T1.Line0')
    ring = curve('WFRL.WakeProxy.T1.Ring0')
    pulse = curve('WFRL.WakeProxy.T2.Pulse0')
    volume = curve('WFRL.WakeProxy.T1.Volume')
    unrelated = curve('Other.Curve')

    class Scene(dict):
        wfrl_show_wake = True
        wfrl_wake_display = 'SCIENTIFIC'
        wfrl_show_lidar = False

    scene = Scene(wfrl_scene_kind='live')
    scene.objects = [line, ring, pulse, volume, unrelated]
    scene.view_layers = [NS(objects={obj.name: obj for obj in scene.objects})]
    available, calls = {}, []

    def material_for(kind):
        calls.append(kind)
        return available[kind]

    monkeypatch.setattr(materials, 'get_material', material_for)
    for _ in range(2):
        available.update(wake_line=object(), wake_pulse=object())
        calls.clear()
        scene.wfrl_show_wake = True
        addon._update_layers(scene)
        assert line.data.materials == ring.data.materials == [available['wake_line']]
        assert pulse.data.materials == [available['wake_pulse']]
        assert line.data.bevel_depth == ring.data.bevel_depth == .11
        assert pulse.data.bevel_depth == .48
        assert volume.data.materials == unrelated.data.materials == [old]
        assert volume.hide_render and not line.hide_render
        assert sorted(calls) == ['wake_line', 'wake_pulse']
        scene.wfrl_show_wake = False
        calls.clear()
        addon._update_layers(scene)
        assert line.hide_render and ring.hide_render and pulse.hide_render
        assert not calls
