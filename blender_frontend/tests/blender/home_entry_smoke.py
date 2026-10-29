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
    def __init__(self, labels=None):
        self.labels = [] if labels is None else labels

    def row(self, **_kwargs): return Layout(self.labels)
    def column(self, **_kwargs): return Layout(self.labels)
    def box(self): return Layout(self.labels)
    def separator(self, **_kwargs): pass

    def panel(self, _identifier, **kwargs):
        return self.row(), None if kwargs.get('default_closed') else self.row()

    def label(self, *, text='', **_kwargs):
        self.labels.append(text)

    def prop(self, data, name, **_kwargs):
        if name.startswith('["'):
            data[name[2:-2]]
        else:
            getattr(data, name)

    def operator(self, identifier, **_kwargs):
        category, name = identifier.split('.')
        getattr(getattr(bpy.ops, category), name).get_rna_type()
        return SimpleNamespace()


def draw_pages():
    for page in ('REPLAY', 'CAMERA', 'MANUAL', 'EXPORT', 'BACKEND'):
        bpy.context.window_manager.wfrl_home_page = page
        home.WFRL_PT_Home.draw(SimpleNamespace(layout=Layout()), bpy.context)


addon.register()
assert bpy.types.WFRL_PT_home.bl_category == 'WFRL'
assert home.snapshot(bpy.context)[0].title == '未加载 WFRL 场景'
draw_pages()
addon.load_demo_scene()
scene = bpy.context.scene
assert home.snapshot(bpy.context)[0].title == '离线回放 · 就绪'
draw_pages()
before = (scene.frame_current, scene.frame_subframe)
transaction = custom_cameras.EditSession(scene, 1)
transaction.begin()
custom_cameras._ACTIVE = SimpleNamespace(scene=scene, stage='AIM', error='')
assert home.snapshot(bpy.context)[1] == '相机草稿编辑中'
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
