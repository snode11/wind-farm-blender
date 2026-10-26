"""Live scene presentation preserves unrelated and independently owned views."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace as NS

from wfrl_blender import atmosphere, cameras, live_scene, playback, scene_builder
import wfrl_blender as addon
from wfrl_blender.scene_model import SceneDTO


class Scene(dict):
    pass


def area():
    space = NS(clip_start=.01, clip_end=500, overlay=NS(show_overlays=True),
               shading=NS(type='SOLID'))
    return NS(type='VIEW_3D', spaces=NS(active=space), as_pointer=lambda: id(space))


def test_backend_build_only_configures_its_main_viewports(monkeypatch):
    scene, unrelated = Scene(name='owned'), Scene(name='unrelated')
    own, independent, other = area(), area(), area()
    screen = NS(areas=[own, independent])
    other_screen = NS(areas=[other])
    windows = [NS(scene=scene, screen=screen), NS(scene=unrelated, screen=other_screen)]
    bpy = NS(context=NS(scene=scene, window_manager=NS(windows=windows)),
             data=NS(screens=[screen, other_screen], objects=[]))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(addon, '_cancel_playback', lambda *_args: None)
    monkeypatch.setattr(addon, '_update_layers', lambda *_args: None)
    monkeypatch.setattr(scene_builder, 'build_scene', lambda *_args: None)
    monkeypatch.setattr(cameras, 'build_cameras', lambda *_args: None)
    monkeypatch.setattr(atmosphere, 'apply_preset', lambda *_args, **_kwargs: None)
    for name, module in {
        'native_camera_views': NS(owns_view=lambda _w, a: a is independent),
        'split_reconstruction': NS(is_reconstruction_view=lambda *_args: False),
    }.items():
        monkeypatch.setitem(sys.modules, 'wfrl_blender.' + name, module)
        monkeypatch.setattr(addon, name, module, raising=False)
    dto = SceneDTO.from_mapping(dict(name='test', backend='demo', turbine='nrel5mw', dt=1,
        layout=[dict(id='T1', x=0, y=0)], inflow=dict(speed=8, direction=270)))
    live_scene.build_live_scene(dto)
    assert own.spaces.active.shading.type == 'RENDERED'
    for preserved in (independent, other):
        assert preserved.spaces.active.shading.type == 'SOLID'
        assert preserved.spaces.active.overlay.show_overlays
        assert preserved.spaces.active.clip_end == 500


def playback_control():
    source = Path(__file__).parents[1] / 'wfrl_blender/panels/clearance.py'
    node = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'playback_control')
    namespace = {'__package__': 'wfrl_blender.panels'}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['playback_control']


def test_playback_label_uses_scene_owner_instead_of_global_screen_flag(monkeypatch):
    scene = NS(frame_current=10, frame_end=100)
    monkeypatch.setattr(playback, 'is_playing', lambda _scene: False)
    assert playback_control()(scene, NS(is_animation_playing=True)) == ('播放', 'PLAY', '已暂停')
    monkeypatch.setattr(playback, 'is_playing', lambda _scene: True)
    assert playback_control()(scene, NS(is_animation_playing=True)) == ('暂停', 'PAUSE', '正在播放')
