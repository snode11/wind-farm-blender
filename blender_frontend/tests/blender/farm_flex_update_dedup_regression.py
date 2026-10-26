"""Native same-time workload and numerical checks in a factory background process.

WFRL_TEST_OUTPUT must name a new JSON file. CPU timings do not measure viewport FPS.
"""
from pathlib import Path
import json
import os
import sys
import time

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import farm_flex, performance, split_turbine_context


out = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not out.exists(), out
assert bpy.app.background and not hasattr(bpy.types.Scene, 'wfrl_blade_model')
assert Path(farm_flex.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
scene.wfrl_flex_show_tip_trails = False
scene.wfrl_show_wake = False
active = farm_flex.active_for(scene)
active.visible_turbines = {0}
start, end = float(active.times[0]), float(active.times[-1])
at = start + 13.125


def coordinates(obj):
    result = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get('co', result)
    return result.reshape(-1, 3)


def update(t, **kwargs):
    active.update(scene, sim_time_s=t, record_telemetry=False, **kwargs)


update(at)
bpy.context.view_layer.update()
performance.configure(True)
rows = {}
for label, values in [('same_time_20', [at] * 20),
                      ('advancing_20', [at + (i + 1) / 60 for i in range(20)])]:
    performance.reset()
    started = time.perf_counter()
    for value in values:
        update(value)
    rows[label] = dict(mean_ms=(time.perf_counter() - started) * 1000 / len(values),
                       **performance.snapshot())
counts = rows['same_time_20']['counts']
assert counts['flex.update_cache_hit'] == 20, counts
assert counts.get('flex.blade_updates', 0) == counts.get('flex.vertices_written', 0) == 0, counts
assert counts.get('flex.tower_vertices_written', 0) == 0, counts
assert 'flex.interpolation' not in rows['same_time_20']['timings']
assert rows['advancing_20']['counts']['flex.blade_updates'] == 60

# Saved and interpolated structural tips retain the established physical gates.
errors = {'saved': 0., 'interpolated': 0.}
for t in (start, start + 1 / 60, at, end):
    active.visible_turbines = {0}
    update(t)
    expected = {b: row['actual'].copy() for b, row in active.comparison.rows.items()}
    for row in active.comparison.rows.values():
        kind = 'interpolated' if row['interpolated'] else 'saved'
        errors[kind] = max(errors[kind], float(np.abs(row['error']).max()))
    update(t)
    active.visible_turbines = {1}
    update(t)
    for b, point in expected.items():
        np.testing.assert_array_equal(active.comparison.rows[b]['actual'], point)
assert errors['saved'] < .0002, errors
assert errors['interpolated'] < .005, errors

# Same-time workset expansion updates hidden blades and first shared support.
active.visible_turbines = {0, 1, 2}
update(at)
expected = {obj.name: coordinates(obj) for obj, *_ in [*active.blades, *active.towers]}
update(start + 1.5)
active.visible_turbines = {1}
update(at)
split_turbine_context.ensure(scene, prefix='SplitRecon.Reference.', offset=(0., 10000., 0.),
                            names_key='split_reconstruction_structure_names')
update(at)
np.testing.assert_array_equal(coordinates(scene.objects['SplitRecon.Reference.Tower']),
                              expected['WFRL.Turbine.T1.Tower'])
active.visible_turbines = {0, 1, 2}
update(at)
for obj, *_ in [*active.blades, *active.towers]:
    np.testing.assert_array_equal(coordinates(obj), expected[obj.name])

# Native mesh edits and mesh replacements are checked even at a cached time.
obj = active.blades[0][0]
obj.data.vertices[7].co.x += .1
obj.data.update()
performance.reset()
update(at)
assert performance.snapshot()['counts']['flex.blade_updates'] == 1
np.testing.assert_array_equal(coordinates(obj), expected[obj.name])
obj.data = obj.data.copy()
obj.data.vertices[7].co.x += .1
performance.reset()
update(at)
assert performance.snapshot()['counts']['flex.update_cache_miss'] == 1
np.testing.assert_array_equal(coordinates(obj), expected[obj.name])
active.rebind_blade(obj, active.blades[0][1].copy())
assert active._update_geometry is None
update(at)
np.testing.assert_array_equal(coordinates(obj), expected[obj.name])

# Native parent edits change local coordinates without changing the source tip.
obj.parent.location.x += .25
update(at)
assert not np.array_equal(coordinates(obj), expected[obj.name])
obj.parent.location.x -= .25
update(at)
np.testing.assert_array_equal(coordinates(obj), expected[obj.name])
active.export_turbines = {1}
update(at + .01, update_comparison=False)
performance.reset()
update(at + .01, update_comparison=False)
assert performance.snapshot()['counts']['flex.update_cache_hit'] == 1
assert performance.snapshot()['counts'].get('flex.blade_updates', 0) == 0
assert active._world_matrices.evaluated
farm_flex.detach(scene, restore=False)
assert not active.enabled and active._update_geometry is None

report = dict(status='PASS', source=str(Path(farm_flex.__file__).resolve()),
              blender=bpy.app.version_string, visible_turbines=[0], wake=False,
              trails=False, telemetry=False, rows=rows, tip_max_error_m=errors,
              saved_threshold_m=.0002, interpolated_threshold_m=.005,
              scope='native background CPU and mesh checks; no viewport FPS or full-timeline claim')
out.write_text(json.dumps(report, indent=2))
print('FARM_FLEX_UPDATE_DEDUP_PASS', json.dumps(report), flush=True)
addon.unregister()
