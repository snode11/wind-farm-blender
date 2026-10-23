"""Source-only native replay workload checks without rendering or saved outputs.

Run in a separate Blender --background --factory-startup process. Counters
describe geometry work, not viewport FPS or scientific measurement accuracy.
"""
from pathlib import Path
import json
import sys

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import clearance_replay, farm_flex, performance, split_turbine_context


assert bpy.app.background, 'Use an independent background Blender process'
assert not hasattr(bpy.types.Scene, 'wfrl_blade_model'), 'Use --factory-startup'
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
scene.wfrl_flex_show_tip_trails = False
scene.wfrl_show_wake = False
active = farm_flex.active_for(scene)
assert len(active.blades) == 9 and len(active.towers) == 3
performance.configure(True)


def coordinates(obj):
    result = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get('co', result)
    return result.reshape(-1, 3)


def meshes():
    return {row[0].name: coordinates(row[0]) for row in [*active.blades, *active.towers]}


def tips():
    return {blade: row['actual'].copy() for blade, row in active.comparison.rows.items()}


def update(time_s, visible):
    active.visible_turbines = set(visible)
    performance.reset()
    active.update(scene, sim_time_s=float(time_s), record_telemetry=False)
    bpy.context.view_layer.update()
    snapshot = performance.snapshot()
    assert snapshot['counts']['flex.scene_updates'] == 1, snapshot
    assert snapshot['timings']['flex.deformation_cache']['calls'] == 2, snapshot
    assert 'flex.rebind_blade' not in snapshot['timings'], snapshot
    assert scene['wfrl_clearance_time_s'] == float(time_s)
    return snapshot


def check_counts(snapshot, blades, supports, measurement_points):
    counts = snapshot['counts']
    assert counts['flex.blade_updates'] == 3 * len(blades), counts
    assert counts['flex.vertices_written'] == sum(len(row[1]) for row in active.blades if row[5] in blades), counts
    assert counts['flex.tower_vertices_written'] == sum(len(row[1]) for row in active.towers if row[4] in supports), counts
    assert counts.get('flex.measurement_points', 0) == measurement_points, counts


start, end = map(float, (active.times[0], active.times[-1]))
sample_times = [start, start + 1 / 60, start + 1 / 40,
                start + 901 / 60, start + 1801 / 60, end]
workloads = {}
for time_s in sample_times:
    overview = update(time_s, {0, 1, 2})
    check_counts(overview, {0, 1, 2}, {0, 1, 2}, 0)
    expected_meshes, expected_tips = meshes(), tips()
    for visible in ({0}, {1}, {2}):
        update(start + 2.375 if time_s != start + 2.375 else start, {0, 1, 2})
        previous = meshes()
        snapshot = update(time_s, visible)
        check_counts(snapshot, visible, visible | {0}, 0 if 0 in visible else 3)
        for obj, _rest, _world, _index, _weight, turbine, *_ in active.blades:
            np.testing.assert_array_equal(coordinates(obj),
                expected_meshes[obj.name] if turbine in visible else previous[obj.name],
                err_msg=f'{obj.name}: visible={visible}, time={time_s}')
        for obj, _rest, _index, _weight, turbine in active.towers:
            np.testing.assert_array_equal(coordinates(obj),
                expected_meshes[obj.name] if turbine in visible | {0} else previous[obj.name],
                err_msg=f'{obj.name}: visible={visible}, time={time_s}')
        for blade, point in expected_tips.items():
            np.testing.assert_array_equal(active.comparison.rows[blade]['actual'], point,
                err_msg=f'T1 structural tip {blade}: visible={visible}, time={time_s}')
        workloads[str(sorted(visible))] = snapshot['counts']
    workloads['all'] = overview['counts']

# Geometry consumers can expand the selection without changing source time.
at = start + 13.125
update(at, {0, 1, 2})
expected = meshes()
update(start + 1.5, {0, 1, 2})
update(at, {1})
update(at, {0, 1, 2})
for obj, *_ in [*active.blades, *active.towers]:
    np.testing.assert_array_equal(coordinates(obj), expected[obj.name])
update(at, {2})
assert len(active.cache) <= 3

# Rebinding an edited rest mesh must invalidate both visible and hidden results.
obj, original, *_ = active.blades[0]
original = original.copy()
modified = original.copy()
modified[-2, 0] += np.float32(.004)
active.rebind_blade(obj, modified)
assert not active.cache
update(at, {0})
edited_tip = tips()[1]
update(at, {1})
np.testing.assert_array_equal(tips()[1], edited_tip)
active.rebind_blade(obj, original)
assert not active.cache
update(at, {1})
assert np.linalg.norm(tips()[1] - edited_tip) > .001
update(at, {0})
restored_tip = np.asarray(obj.matrix_world @ obj.data.vertices[-2].co)
np.testing.assert_array_equal(tips()[1], restored_tip)

# Native seeking and selection preserve the recorded clock and camera identity.
scene.frame_set(902)
assert bpy.ops.wfrl.farm_flex_view(turbine='T2', angle='DOWN') == {'FINISHED'}
clock = (scene.frame_current, scene.frame_subframe, clearance_replay.sample(scene)['time_s'])
camera = scene.camera
scene.render.fps = 24
assert clearance_replay.sample(scene)['time_s'] == clock[2]
scene.render.fps = 60
scene.frame_set(1601)
scene.frame_set(clock[0], subframe=clock[1])
assert clearance_replay.sample(scene)['time_s'] == clock[2]
for model in ('rootkink', 'nrel5mw'):
    scene.wfrl_blade_model = model
    active = farm_flex.active_for(scene)
    assert active.model == model, scene.get('wfrl_blade_model_error')
    assert active.visible_turbines == {1}
    assert scene.camera == camera
    assert (scene.frame_current, scene.frame_subframe, clearance_replay.sample(scene)['time_s']) == clock
    snapshot = update(clock[2], {1})
    check_counts(snapshot, {1}, {0, 1}, 3)
    hidden_tips = tips()
    update(clock[2], {0})
    for blade, point in hidden_tips.items():
        np.testing.assert_array_equal(tips()[blade], point)
    active.visible_turbines = {1}

# A first shared support created while T1 is hidden uses this same paused time.
at = start + 29.375
update(at, {0, 1, 2})
expected_tower = coordinates(scene.objects['WFRL.Turbine.T1.Tower'])
update(start + 1.5, {0, 1, 2})
update(at, {1})
assert scene.get('split_reconstruction_structure_names') is None
group = split_turbine_context.ensure(scene, prefix='SplitRecon.Reference.',
    offset=(0., 10000., 0.), names_key='split_reconstruction_structure_names')
source_tower = scene.objects['WFRL.Turbine.T1.Tower']
shared_tower = scene.objects['SplitRecon.Reference.Tower']
assert shared_tower.data == source_tower.data
np.testing.assert_array_equal(coordinates(shared_tower), expected_tower)
at += 1 / 60
update(at, {2})
np.testing.assert_array_equal(coordinates(shared_tower), coordinates(source_tower))
assert not shared_tower.hide_render and not shared_tower.hide_viewport
for item in group:
    bpy.data.objects.remove(item, do_unlink=True)
for key in tuple(scene.keys()):
    if key.startswith('split_reconstruction_structure_names'):
        del scene[key]
split_turbine_context.clear_runtime(scene)

# The isolated single-turbine exporter removes the other physical turbines.
active.export_turbines = {1}
active.visible_turbines = {0, 1, 2}
active.blades = [row for row in active.blades if row[5] == 1]
active.towers = [row for row in active.towers if row[4] == 1]
active.fittings = [row for row in active.fittings if row[1] == 1]
active.cache.clear()
for item in list(scene.objects):
    if item.name.startswith(('WFRL.Turbine.T1.', 'WFRL.Turbine.T3.')):
        bpy.data.objects.remove(item, do_unlink=True)
snapshot = update(start + 18.125, {0, 1, 2})
check_counts(snapshot, {1}, {1}, 0)
assert not any(item.name.startswith('WFRL.Turbine.T1.') for item in scene.objects)
workloads['single_export_T2'] = snapshot['counts']
print('FARM_FLEX_WORKLOAD_REGRESSION_PASS', json.dumps(dict(
    status='PASS', source=str(Path(addon.__file__).resolve()),
    sampled_times_s=sample_times, workloads=workloads,
    exact_tip_transport=True, same_time_selection=True, rebind_invalidation=True,
    model_switch_clock=True, first_shared_support=True, missing_T1_export=True,
    scope='native background geometry workload; viewport FPS not measured')), flush=True)
