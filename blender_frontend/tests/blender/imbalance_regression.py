"""Native offline analysis, shared navigation, result-layer and seek checks."""
import importlib
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
module = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if module == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(module)
farm = importlib.import_module(module + '.farm_flex')
replay = importlib.import_module(module + '.clearance_replay')
analysis = importlib.import_module(module + '.imbalance')
panel = importlib.import_module(module + '.panels.farm_replay')
home = importlib.import_module(module + '.panels.home')
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()


class Layout:
    def __init__(self, labels=None): self.labels = [] if labels is None else labels
    def row(self, **_): return Layout(self.labels)
    def column(self, **_): return Layout(self.labels)
    def box(self): return Layout(self.labels)
    def separator(self, **_): pass
    def panel(self, *_args, **_kwargs): return self.row(), self.row()
    def label(self, *, text='', **_): self.labels.append(text)
    def prop(self, data, name, **_): getattr(data, name) if not name.startswith('[') else None
    def prop_enum(self, data, name, value, **_):
        assert value in data.bl_rna.properties[name].enum_items
    def operator(self, identifier, **_):
        category, name = identifier.split('.')
        getattr(getattr(bpy.ops, category), name).get_rna_type()
        return SimpleNamespace()


def seek(scene, time_s):
    frame = scene.frame_start + (time_s - 117.) * scene['wfrl_clearance_timebase_fps']
    scene.frame_set(math.floor(frame), subframe=frame % 1)
    farm.update(scene)
    replay.update(scene)


if module == 'wfrl_blender':
    addon.register()
addon.load_demo_scene(camera_rig=False)
scene = bpy.context.scene
scene.wfrl_farm_panel_page = 'IMBALANCE'
assert scene.bl_rna.properties['wfrl_farm_panel_page'].enum_items['VIDEO'].value == 2
assert scene.bl_rna.properties['wfrl_farm_panel_page'].enum_items['TOOLS'].value == 3
active = farm.active_for(scene)
assert not active.imbalance_error and len(active.imbalance['turbines']) == 3
for tid in ('T1', 'T2', 'T3'):
    scene.wfrl_imbalance_turbine = tid
    for metric in analysis.METRICS:
        scene.wfrl_imbalance_metric = metric
        layout = Layout()
        panel.draw(layout, bpy.context)
        assert '质量不平衡' in layout.labels and '气动不平衡' in layout.labels
        assert layout.labels.count('当前数据包未提供') == 2
        assert '三叶响应对比 · 离线统计' in layout.labels
        assert analysis.plot_data(scene).series[0][0] == 'B1'
scene.wfrl_imbalance_turbine = 'T1'
scene.wfrl_imbalance_metric = 'tip_x'
scene.wfrl_imbalance_curve = True
row = active.imbalance['turbines']['T1']
for time_s, available in ((117., False), (150., True), (177., True), (117., False)):
    seek(scene, time_s)
    assert (analysis.latest_turn(row, replay.sample(scene)['time_s']) is not None) == available
    before = scene.frame_current_final
    layout = Layout()
    panel.draw(layout, bpy.context)
    bpy.context.window_manager.wfrl_home_page = 'REPLAY'
    home.WFRL_PT_Home.draw(SimpleNamespace(layout=Layout()), bpy.context)
    assert scene.frame_current_final == before
    for page in ('DEFLECTION', 'RADAR', 'IMBALANCE'):
        scene.wfrl_farm_panel_page = page
        panel.draw(Layout(), bpy.context)
    assert scene.frame_current_final == before
original = active.imbalance
for path in (farm.default_dual_package(), farm.default_dual_tls_package()):
    addon.load_demo_scene(path, camera_rig=False)
    assert farm.active_for(scene).imbalance == original
    assert replay.sample(scene)['measurement_mode'] == 'dual_beam'
    scene.wfrl_farm_panel_page = 'IMBALANCE'
    panel.draw(Layout(), bpy.context)
with patch.object(analysis, 'load_for_source', side_effect=ValueError('Invalid analysis fixture')):
    addon.load_demo_scene(farm.default_package(), camera_rig=False)
assert farm.active_for(scene).imbalance is None
assert farm.active_for(scene).imbalance_error == 'Invalid analysis fixture'
scene.wfrl_farm_panel_page = 'IMBALANCE'
layout = Layout()
panel.draw(layout, bpy.context)
assert '不平衡分析不可用' in layout.labels
assert replay.sample(scene) is not None
assert analysis.plot_data(scene) is None
other = bpy.data.scenes.new('Analysis isolation check')
assert farm.active_for(other) is None
assert analysis.plot_data(other) is None
bpy.data.scenes.remove(other)
result = dict(status='PASS', module=module, addon_path=str(Path(addon.__file__).resolve().parent),
              turbines=['T1', 'T2', 'T3'], components=6,
              source_modes=['B2', 'hub-axis.v1', 'hub-tls.v1'],
              scope='Native RNA, layout adapter and replay seeks; visible layout requires a window check.')
if os.environ.get('WFRL_TEST_OUTPUT'):
    path = Path(os.environ['WFRL_TEST_OUTPUT'])
    path.mkdir(parents=True, exist_ok=False)
    (path / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
addon.unregister()
assert not hasattr(bpy.types.Scene, 'wfrl_imbalance_metric')
print('IMBALANCE_REGRESSION_PASS', flush=True)
