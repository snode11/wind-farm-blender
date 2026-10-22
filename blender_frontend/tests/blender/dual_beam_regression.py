"""Source native Blender: dual sidecar, calibrated rays, seek, switch and reload."""
from bisect import bisect_right
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import importlib
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
module = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if module == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
wfrl_blender = importlib.import_module(module)
farm_flex = importlib.import_module(module+'.farm_flex')
clearance_replay = importlib.import_module(module+'.clearance_replay')
radar_feedback = importlib.import_module(module+'.radar_feedback')
registered_here = module not in bpy.context.preferences.addons
if registered_here:
    wfrl_blender.register()
package = Path(os.environ.get('WFRL_DUAL_PACKAGE', farm_flex.default_dual_package()))
installation_audit_path = package / 'installation-audit.json'
installation_audit = (json.loads(installation_audit_path.read_text())
                      if installation_audit_path.is_file() else None)
wfrl_blender.load_demo_scene(package, camera_rig=False)
scene = bpy.context.scene
scene.wfrl_farm_panel_page = 'RADAR'
assert len(farm_flex._ACTIVE.blades) == 9
assert farm_flex._ACTIVE.manifest['measurement_mode'] == 'dual_beam'


def seek(t):
    reader = clearance_replay.reader_for(scene)
    timebase = scene['wfrl_clearance_timebase_fps']
    frame = scene.frame_start + (t - reader.start_s) * timebase
    whole = math.floor(frame)
    scene.frame_set(whole, subframe=frame - whole)
    farm_flex.update(scene)
    clearance_replay.update(scene)
    bpy.context.view_layer.update()
    value = clearance_replay.sample(scene)
    assert abs(value['time_s'] - t) < 1e-6
    return value


def check_reader_and_rays(tid, reader, t):
    value = seek(t)
    # Compare at the represented Blender subframe time: fractional frames are
    # stored as floats and can fall immediately before an exact 40 Hz boundary.
    expected = reader.at(value['time_s'])
    assert value == expected, (tid, t, value, expected)
    assert value['measurement_mode'] == 'dual_beam'
    assert radar_feedback.alarm_state(value) == expected['status']
    row = reader.rows[bisect_right(reader._times, value['time_s']) - 1]
    activity = radar_feedback.beam_activity(reader, value['time_s'])
    for beam_id in (1, 2, 3):
        beam = scene.objects[f'WFRL.Turbine.{tid}.ClearanceRadar.Beam{beam_id}']
        points = beam.data.splines[0].points
        start = beam.matrix_world @ Vector(points[0].co[:3])
        end = beam.matrix_world @ Vector(points[1].co[:3])
        observation = row['observations'][f'S{beam_id}']
        assert (start - Vector(observation['origin_m'])).length < .0002, (tid, t, beam_id)
        assert ((end - start).normalized() - Vector(observation['direction'])).length < .00001
        assert bool(beam.get('wfrl_beam_active', False)) == activity[beam_id - 1]
    return value


# Draw functions run through a recording layout to verify S1 stays visible with
# missing measurements, and that selected simulation geometry is labeled honestly.
class Layout:
    def __init__(self):
        self.labels = []

    def box(self):
        return self

    def row(self, **kwargs):
        return self

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text', ''))


panel = importlib.import_module(module+'.panels.clearance')
draw_dual_measurement, selected_installation_label = panel.draw_dual_measurement, panel.selected_installation_label

assert selected_installation_label({}) == '仿真安装位已选定'
assert selected_installation_label({'installation': {'region': 'INNER_NACELLE'}}) == '机舱内侧仿真安装位'


def check_card(value):
    layout = Layout()
    draw_dual_measurement(layout, scene, value)
    assert any('1号光束报警' in label for label in layout.labels)
    assert panel.dual_method_label(value) in layout.labels
    assert '算法版本：' + value['algorithm_version'] in layout.labels
    assert '研究回放 · REVIEW_ONLY' in layout.labels
    if value['performance_status'] == 'PENDING_ACCEPTANCE':
        assert '性能待验收 · PENDING_ACCEPTANCE' in layout.labels
    if value['reconstruction_method'] == 'hub-tls.v1':
        assert any('TLS 候选' in label for label in layout.labels)
        assert not any('hub-axis.v1' in label or '轮毂轴线外推' in label for label in layout.labels)
    else:
        assert any('hub-axis.v1' in label for label in layout.labels)
        assert not any('TLS 候选' in label for label in layout.labels)
    assert not any('阈值' in label or '正常' == label for label in layout.labels)
    if value['measurement'] is None:
        assert '本次无有效净空' in layout.labels
        assert '双束估计净空：-- m' in layout.labels
    else:
        assert any(f"叶片 {value['measurement']['blade_id']}" in label for label in layout.labels)
    if value['installation_status'] == 'SIMULATION_SELECTED':
        reader = clearance_replay.reader_for(scene)
        if reader.config.get('installation', {}).get('region') == 'INNER_NACELLE':
            assert '机舱内侧仿真安装位' in layout.labels
        else:
            assert '仿真安装位已选定' in layout.labels
            assert not any('机舱内侧' in label for label in layout.labels)
        assert not any('轮毂前侧' in label for label in layout.labels)
        assert any('现场安装待核验' in label for label in layout.labels)
        if reader.config.get('installation'):
            assert any('S1 标称塔壁间距：' in label for label in layout.labels)
            assert any('参考目标：约 ' in label for label in layout.labels)
            if installation_audit is not None:
                assert f"S1 标称塔壁间距：{installation_audit['nominal_gap_m']:.2f} m" in layout.labels
                assert f"参考目标：约 {installation_audit['target_gap_m']:.2f} m" in layout.labels
        assert not any('目标待确认' in label for label in layout.labels)
    return layout.labels


coverage = {}
for tid in ('T1', 'T2', 'T3'):
    bpy.ops.wfrl.farm_flex_view(turbine=tid)
    reader = farm_flex._ACTIVE.readers[tid]
    midpoint = (reader.start_s + reader.end_s) / 2
    for t in (reader.start_s, midpoint, reader.end_s, reader.start_s):
        check_card(check_reader_and_rays(tid, reader, t))
    valid_rows = [row for row in reader.rows if row['reconstruction']['valid']]
    alarm_rows = [row for row in reader.rows if row['s1_observation_state'] == 'triggered']
    # A selected mounting package must prove simultaneous S2/S3 hits on each
    # turbine. The old zero-hit diagnostic package remains usable by this test.
    if reader.config['status'] == 'SIMULATION_SELECTED':
        assert valid_rows, f'{tid}: selected installation has no valid pair'
    if valid_rows:
        row = valid_rows[0]
        # Stay inside the source observation interval despite subframe rounding.
        value = check_reader_and_rays(tid, reader, min(reader.end_s, row['time_s'] + 1e-5))
        assert value['observation_time_s'] == row['time_s']
        assert value['measurement_status'] == 'valid'
        assert value['measurement']['blade_id'] == row['reconstruction']['blade_id']
        assert value['measurement']['estimate_m'] == row['reconstruction']['clearance_estimate']
        assert '净空测量：双束有效' in check_card(value)
    if alarm_rows:
        row = alarm_rows[0]
        value = check_reader_and_rays(tid, reader, min(reader.end_s, row['time_s'] + 1e-5))
        assert value['alarm']['observation_state'] == 'triggered'
        assert radar_feedback.alarm_state(value) == 's1_triggered'
        check_card(value)
    coverage[tid] = dict(valid_pair_samples=len(valid_rows), s1_trigger_samples=len(alarm_rows),
                         valid_pair_checked=bool(valid_rows), alarm_checked=bool(alarm_rows))
    saved = seek(midpoint)
    scene.render.fps = 24
    assert clearance_replay.sample(scene) == saved

saved_time = saved['time_s']
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'dual.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene = bpy.context.scene
    assert scene['wfrl_clearance_turbine'] == 'T3'
    assert clearance_replay.sample(scene) == saved
    assert clearance_replay.sample(scene)['time_s'] == saved_time
    assert farm_flex._ACTIVE.manifest['measurement_mode'] == 'dual_beam'

# Exercise both installed buttons, including TLS data (not merely a relabeled
# card), and relocate the saved built-in when its former install path is gone.
method_coverage = {}
baseline_s1 = {}
baseline_estimates = {}
for choice, method, version, builtin_path in (
        ('AXIS', 'hub-axis.v1', 'two-point-hub-extrapolation.v1', farm_flex.default_dual_package()),
        ('TLS', 'hub-tls.v1', 'hub-constrained-tls.v1', farm_flex.default_dual_tls_package())):
    assert bpy.ops.wfrl.load_dual_beam(builtin_method=choice) == {'FINISHED'}
    scene = bpy.context.scene
    assert scene.wfrl_farm_panel_page == 'RADAR'
    assert Path(scene['wfrl_farm_flex_path']) == builtin_path
    assert farm_flex._ACTIVE.manifest['dual_beam']['reconstruction_method'] == method
    different_estimates = 0
    for tid in ('T1', 'T2', 'T3'):
        bpy.ops.wfrl.farm_flex_view(turbine=tid)
        reader = farm_flex._ACTIVE.readers[tid]
        assert reader.reconstruction_method == method
        assert reader.algorithm_version == version
        assert reader.at(reader.end_s)['performance_status'] == 'PENDING_ACCEPTANCE'
        pair_row = next(row for row in reader.rows if row['reconstruction']['valid'])
        value = check_reader_and_rays(tid, reader, pair_row['time_s'] + 1e-5)
        assert value['measurement']['estimate_m'] == pair_row['reconstruction']['clearance_estimate']
        assert value['reconstruction_method'] == method
        check_card(value)
        alarm_row = next(row for row in reader.rows if row['s1_observation_state'] == 'triggered')
        value = check_reader_and_rays(tid, reader, alarm_row['time_s'] + 1e-5)
        assert value['alarm']['observation_state'] == 'triggered'
        check_card(value)
        s1 = [(row['observations']['S1'], row['s1_observation_state']) for row in reader.rows]
        estimates = [row['reconstruction']['clearance_estimate'] for row in reader.rows]
        if choice == 'AXIS':
            baseline_s1[tid], baseline_estimates[tid] = s1, estimates
        else:
            assert s1 == baseline_s1[tid], 'TLS must not alter independent S1 observations'
            different_estimates += sum(a != b for a, b in zip(estimates, baseline_estimates[tid]))
    if choice == 'TLS':
        assert different_estimates > 0, 'TLS must read distinct saved candidate estimates'
    saved = seek((reader.start_s + reader.end_s) / 2)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / (choice.lower() + '.blend')
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        scene = bpy.context.scene
        assert clearance_replay.sample(scene) == saved
        assert scene['wfrl_clearance_turbine'] == 'T3'
        # Simulate reinstalling the extension in a new location. The saved
        # manifest hash, including algorithm identity, must select the asset.
        scene['wfrl_farm_flex_path'] = str(Path(tmp) / 'removed-install' / builtin_path.name)
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        scene = bpy.context.scene
        assert clearance_replay.sample(scene) == saved
        assert Path(scene['wfrl_farm_flex_path']) == builtin_path
        assert clearance_replay.sample(scene)['reconstruction_method'] == method
        check_card(clearance_replay.sample(scene))
    method_coverage[method] = dict(actual_estimates=True, independent_s1=True,
                                  reload=True, relocated_reload=True, method_card=True)

# A different manifest is never repaired by substituting the default method.
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'bad-identity.blend'
    scene['wfrl_farm_flex_path'] = str(Path(tmp) / 'removed-install' / 'dual_beam_tls')
    scene['wfrl_farm_manifest_sha256'] = 'wrong-manifest'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene = bpy.context.scene
    assert clearance_replay.sample(scene) is None
    assert not farm_flex.is_active(scene)

clearance_replay.clear(scene, 'test missing package')
assert clearance_replay.sample(scene) is None
assert not any(obj.get('wfrl_beam_active', False) for obj in scene.objects)
# Switch back to old package: historical mode and old angles restored.
wfrl_blender.load_demo_scene(camera_rig=False)
scene = bpy.context.scene
assert clearance_replay.sample(scene).get('measurement_mode') is None
if (farm_flex.default_dual_package()/'manifest.json').is_file():
    assert bpy.ops.wfrl.load_dual_beam() == {'FINISHED'}
    assert clearance_replay.sample(bpy.context.scene)['measurement_mode'] == 'dual_beam'
    assert clearance_replay.sample(bpy.context.scene)['reconstruction_method'] == 'hub-axis.v1'
    assert bpy.context.scene.wfrl_farm_panel_page == 'RADAR'
    assert bpy.ops.wfrl.load_demo() == {'FINISHED'}
    assert clearance_replay.sample(bpy.context.scene).get('measurement_mode') is None
try:
    wfrl_blender.load_demo_scene(package/'missing-dir', camera_rig=False)
except OSError:
    pass
assert clearance_replay.sample(bpy.context.scene) is None
if module != 'wfrl_blender':
    assert 'wfrl' not in sys.modules and 'wfrl_blender' not in sys.modules
result = dict(status='PASS', source_runtime=module=='wfrl_blender', dual_package=True, calibrated_rays=True,
              module=module, addon_path=str(Path(wfrl_blender.__file__).resolve().parent),
              reader_state=True, coverage=coverage, seeks=True, fps=True,
              turbine_switch=True, reload=True, missing_clear=True, legacy_reload=True,
              methods=method_coverage, unmatched_manifest_cleared=True, default_is_old_method=True,
              native_window=not bpy.app.background, installed_zip=module!='wfrl_blender')
Path(os.environ['WFRL_TEST_OUTPUT']).write_text(json.dumps(result, indent=2) + '\n')
print('DUAL_BEAM_NATIVE_PASS', result)
if registered_here:
    wfrl_blender.unregister()
