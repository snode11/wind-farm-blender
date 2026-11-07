"""Replay and ordinary scenes retain independent plotted and export histories."""
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import charts

assert bpy.app.background
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
addon.register()
addon.load_demo_scene(camera_rig=False)
a = bpy.context.scene
a.frame_set(121)
assert charts.history_for(a).points('T1', 'yaw')
b = bpy.data.scenes.new('Ordinary chart consumer')
b.wfrl_chart_visible = True
assert b.get('wfrl_scene_kind') != 'manual'
assert charts._plot_data('yaw', 'ALL', b).bounds is None
assert not charts.raw_history_for(b).series
record = dict(value=8., unit='MW', validity='valid', fidelity='SYNTHETIC', provenance={})
message = dict(type='snapshot', session_id='separate-consumer', sequence=1,
               payload=dict(mode='replay', step=8, run_id='separate-run',
                            timestamp=dict(value=8., timebase='simulation_seconds'),
                            turbines=[dict(turbine_id='B1', channels={'power': record})], farm={}))
charts.record_snapshot(message, scene=b)
before = charts.raw_history_for(b).snapshot()
a.frame_set(61)
assert charts.raw_history_for(b).snapshot() == before
assert charts.history_for(a).turbine_ids() == ('T1', 'T2', 'T3')
assert charts.history_for(b).turbine_ids() == ('B1',)
key = b.as_pointer()
bpy.data.scenes.remove(b)
charts.history_for(a)
assert key not in charts._scene_histories
addon.unregister()
assert not charts._scene_histories
result = dict(status='PASS', final=True, blender=bpy.app.version_string, addon_file=addon.__file__,
              ordinary_scene_empty=True, replay_seek_isolated=True, export_isolated=True,
              deleted_scene_released=True, gpu_pixels='NOT_RUN')
(OUT / 'result.json').write_text(json.dumps(result, indent=2))
print('CHART_SCENE_OWNERSHIP_PASS', json.dumps(result), flush=True)
