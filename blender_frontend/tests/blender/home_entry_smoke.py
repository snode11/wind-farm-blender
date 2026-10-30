"""Source-only entry-panel integration in an isolated factory-startup process.

The layout adapter checks registered buttons and actual RNA properties without
rendering, exporting files, connecting a backend or measuring viewport speed.
"""
from pathlib import Path
from types import SimpleNamespace
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import clearance_replay, farm_flex, charts
from wfrl_blender.panels import home, custom_cameras


class Layout:
    def __init__(self, parent=None, *, open_panels=()):
        self.parent = parent
        self.labels = [] if parent is None else parent.labels
        self.actions = [] if parent is None else parent.actions
        self.properties = [] if parent is None else parent.properties
        self.open_panels = set(open_panels) if parent is None else parent.open_panels
        self._enabled = True

    @property
    def enabled(self):
        return self._enabled and (self.parent is None or self.parent.enabled)

    @enabled.setter
    def enabled(self, value):
        self._enabled = value

    def row(self, **_kwargs): return Layout(self)
    def column(self, **_kwargs): return Layout(self)
    def box(self): return Layout(self)
    def separator(self, **_kwargs): pass

    def panel(self, identifier, **kwargs):
        closed = kwargs.get('default_closed') and identifier not in self.open_panels
        return self.row(), None if closed else self.row()

    def label(self, *, text='', **_kwargs):
        self.labels.append(text)

    def prop(self, data, name, **_kwargs):
        self.properties.append((name, self.enabled))
        if name.startswith('["'):
            data[name[2:-2]]
        else:
            getattr(data, name)

    def prop_enum(self, data, name, value, **_kwargs):
        assert value in data.bl_rna.properties[name].enum_items

    def operator(self, identifier, **_kwargs):
        category, name = identifier.split('.')
        getattr(getattr(bpy.ops, category), name).get_rna_type()
        action = SimpleNamespace()
        self.actions.append((identifier, self.enabled, action))
        return action


def draw_pages(*, ready=False, busy=False):
    scene = bpy.context.scene
    before = (scene.frame_current, scene.frame_subframe)
    pages = {}
    for page in ('REPLAY', 'CAMERA', 'MANUAL', 'EXPORT', 'BACKEND'):
        bpy.context.window_manager.wfrl_home_page = page
        layout = pages[page] = Layout()
        home.WFRL_PT_Home.draw(SimpleNamespace(layout=layout), bpy.context)
        play = [enabled for name, enabled, _ in layout.actions if name == 'wfrl.clearance_playback']
        seek = [enabled for name, enabled in layout.properties if name == 'wfrl_clearance_progress']
        expected = [not busy] if ready and page != 'MANUAL' else []
        assert play == expected, (page, play, expected)
        assert seek == expected, (page, seek, expected)
    assert before == (scene.frame_current, scene.frame_subframe)
    return pages


addon.register()
assert bpy.types.WFRL_PT_home.bl_category == 'WFRL'
assert home.snapshot(bpy.context)[0].title == '未加载 WFRL 场景'
draw_pages()
addon.load_demo_scene()
scene = bpy.context.scene
assert home.snapshot(bpy.context)[0].title == '离线回放 · 就绪'
pages = draw_pages(ready=True)
assert not any(label.startswith('镜头 X：') for label in pages['CAMERA'].labels)
assert '工程参数与显示' in pages['CAMERA'].labels
assert any(label.startswith('仿真时刻 117.000') for label in pages['REPLAY'].labels)
assert '片段进度 0.0 / 60.0 秒' in pages['REPLAY'].labels
bpy.context.window_manager.wfrl_home_page = 'CAMERA'
expanded = Layout(open_panels={'wfrl_camera_engineering_parameters'})
home.WFRL_PT_Home.draw(SimpleNamespace(layout=expanded), bpy.context)
assert any(label.startswith('镜头 X：') for label in expanded.labels)
assert any(name == 'wfrl.custom_camera_coordinates' for name, _, _ in expanded.actions)
scene.frame_set(901, subframe=.5)
draw_pages(ready=True)
before = (scene.frame_current, scene.frame_subframe)
transaction = custom_cameras.EditSession(scene, 1)
draft = transaction.begin()
custom_cameras._ACTIVE = SimpleNamespace(scene=scene, stage='AIM', error='',
                                        camera=draft, ready=True, frame_fits=True)
assert home.snapshot(bpy.context)[1] == '相机草稿编辑中'
pages = draw_pages(ready=True, busy=True)
assert ('wfrl_custom_pitch', True) in pages['CAMERA'].properties
assert ('wfrl_custom_fov', True) in pages['CAMERA'].properties
custom_cameras._ACTIVE = None
transaction.cancel()
assert before == (scene.frame_current, scene.frame_subframe)
assert home.snapshot(bpy.context)[0].title == '离线回放 · 就绪'
scene.frame_set(scene.frame_current + 1)
assert farm_flex.is_active(scene)
assert clearance_replay.sample(scene) is not None
assert charts.history_for(bpy.context.scene).points('T1', 'yaw')
bpy.context.window_manager.wfrl_home_page = 'MANUAL'
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
assert bpy.context.window_manager.wfrl_home_page == 'MANUAL'
draw_pages()
assert home.snapshot(bpy.context)[0].title.startswith('手动场景')
assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
assert bpy.context.scene == scene
assert home.snapshot(bpy.context)[0].title == '离线回放 · 就绪'
addon.unregister()
assert not hasattr(bpy.types.WindowManager, 'wfrl_home_page')
assert not hasattr(bpy.types, 'WFRL_PT_home')
print('HOME_ENTRY_SMOKE_PASS', flush=True)
