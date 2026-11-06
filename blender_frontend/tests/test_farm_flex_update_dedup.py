"""Same-time geometry reuse leaves consumers and native edits observable."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from wfrl_blender import farm_flex, performance, split_turbine_context
from test_farm_flex_world_matrices import Vertices, mathutils_module, replay  # noqa: F401 -- pytest discovers both fixtures by name.


@pytest.fixture(autouse=True)
def counters():
    performance.configure(True)
    yield
    performance.configure(False)


def run(active, scene, time=.5, **kwargs):
    performance.reset()
    active.update(scene, sim_time_s=time, record_telemetry=False, **kwargs)
    return performance.snapshot()


def test_repeated_update_reuses_calculation_but_honors_each_consumer(replay, monkeypatch):
    from wfrl_blender.nrel_defects import editor
    active, scene, _ = replay
    comparison = Mock(wraps=active.comparison.update)
    telemetry, attachments, structures = Mock(), Mock(), Mock()
    monkeypatch.setattr(active.comparison, 'update', comparison)
    monkeypatch.setattr(active, 'record_telemetry', telemetry)
    monkeypatch.setattr(editor, 'after_update', attachments)
    monkeypatch.setattr(split_turbine_context, 'sync', structures)
    run(active, scene, update_comparison=False)
    result = run(active, scene)
    assert result['counts']['flex.update_cache_hit'] == 1
    assert result['counts'].get('flex.blade_updates', 0) == 0
    assert 'flex.interpolation' not in result['timings']
    assert comparison.call_count == 1 and telemetry.call_count == 0
    active.update(scene, sim_time_s=.5, record_telemetry=True)
    assert telemetry.call_count == 1 and comparison.call_count == 2
    assert attachments.call_count == structures.call_count == 3
    original = active.comparison.rows[1]['error'].copy()
    active.comparison.data['simulation'] += 1
    run(active, scene)
    np.testing.assert_allclose(active.comparison.rows[1]['error'], original - 1)


def test_same_frame_time_workset_and_reader_changes_resample(replay):
    active, scene, _ = replay
    active.visible_turbines = {1}
    run(active, scene, time=0.)
    changed = run(active, scene)
    assert changed['counts']['flex.update_cache_miss'] == 1
    assert changed['counts']['flex.blade_updates'] == 3
    hidden = {b: row['actual'].copy() for b, row in active.comparison.rows.items()}
    active.visible_turbines = {0, 1, 2}
    assert run(active, scene)['counts']['flex.update_cache_miss'] == 1
    for b, actual in hidden.items():
        np.testing.assert_array_equal(active.comparison.rows[b]['actual'], actual)
    active.readers['T1'] = object()
    assert run(active, scene)['counts']['flex.update_cache_miss'] == 1
    active.export_turbines = {2}
    assert run(active, scene)['counts']['flex.update_cache_miss'] == 1
    assert active._world_matrices.evaluated


def test_native_mesh_and_transform_edits_cannot_be_hidden_by_cache(replay):
    active, scene, _ = replay
    run(active, scene)
    obj = active.blades[0][0]
    expected = np.array([v.co.copy() for v in obj.data.vertices])
    obj.data.vertices[0].co[0] += .25
    result = run(active, scene)
    assert result['counts']['flex.update_cache_hit'] == 1
    assert result['counts']['flex.blade_updates'] == 1
    np.testing.assert_array_equal([v.co for v in obj.data.vertices], expected)
    scene.objects['WFRL.Turbine.T1.YawRoot'].basis.values[0, 3] += 1
    result = run(active, scene)
    assert result['timings']['flex.interpolation']['calls'] == 3
    assert not np.array_equal([v.co for v in obj.data.vertices], expected)


def test_mesh_rebinding_and_source_edits_invalidate_pure_geometry(replay):
    active, scene, _ = replay
    run(active, scene)
    obj, rest, *_ = active.blades[0]
    obj.data = SimpleNamespace(vertices=Vertices(rest), update=lambda: None)
    assert run(active, scene)['counts']['flex.update_cache_miss'] == 1
    active.rebind_blade(obj, rest + [.01, 0., 0.])
    assert active._update_geometry is None
    assert run(active, scene)['counts']['flex.update_cache_miss'] == 1
    previous = obj.data.vertices[0].co.copy()
    active.transforms[1, ..., 0, 3] += .02
    result = run(active, scene)
    assert result['counts']['flex.update_cache_miss'] == 1
    assert result['counts']['flex.cache_miss'] == 1
    assert not np.array_equal(obj.data.vertices[0].co, previous)


@pytest.mark.parametrize('position', [0, -1])
def test_native_topology_change_fails_without_partial_mesh_write(replay, monkeypatch, position):
    active, scene, _ = replay
    run(active, scene)
    from wfrl_blender import clearance_replay, playback
    monkeypatch.setattr(playback, 'cancel', Mock())
    monkeypatch.setattr(clearance_replay, 'clear', lambda scene, reason: scene.update(wfrl_flex_active=False))
    telemetry = Mock()
    monkeypatch.setattr(active, 'record_telemetry', telemetry)
    active.blades[position][0].data.vertices.pop()
    before = [[v.co.copy() for v in row[0].data.vertices] for row in active.blades]
    with pytest.raises(ValueError, match='topology no longer matches'):
        active.update(scene, sim_time_s=.75)
    assert scene['wfrl_clearance_time_s'] == .5
    assert not scene['wfrl_flex_frame_valid'] and active.frame_error
    assert telemetry.call_count == 0
    for row, coords in zip(active.blades, before):
        np.testing.assert_array_equal([v.co for v in row[0].data.vertices], coords)


def test_yaw_wrap_matches_reader_geometry_comparison_and_telemetry(replay, monkeypatch):
    from wfrl.lidar.replay import ReplayReader
    from wfrl_blender import charts, clearance_replay, deflection

    active, scene, _ = replay
    active.poses[:, :, 0] = [[179.], [-179.]]
    active.comparison.data['poses'][:, 0] = [179., -179.]
    reader = ReplayReader(SimpleNamespace(
        manifest={'segment': {'start_s': 0., 'end_s': 1.}}, measurements=[], cumulative=[],
        motion=[dict(time_s=t, yaw_deg=yaw, azimuth_deg=0., rotor_speed_rpm=0., pitch_deg=[0.] * 3)
                for t, yaw in ((0., 179.), (1., -179.))]))
    active.readers['T1'] = reader
    run(active, scene)
    expected = reader.at(.5)['motion']['yaw_deg']
    assert expected == 180.
    assert scene.objects['WFRL.Turbine.T1.YawRoot'].rotation_euler.z == pytest.approx(np.pi)
    row = active.comparison.rows[1]
    hub, axes, _ = deflection.rigid_frame(active.comparison.data['scalars'], [expected, 0., 0., 0., 0., 0.], 1)
    np.testing.assert_allclose(row['reference'], hub + axes[:, 2] * active.comparison.data['scalars']['TipRad'])
    active.last_telemetry_frame, active.telemetry, active.manifest = None, None, {}
    scene.wfrl_channel_telemetry = True
    monkeypatch.setattr(clearance_replay, 'sample', lambda _: reader.at(.5))
    recorded = []
    monkeypatch.setattr(charts, 'record_farm', lambda t, pose, *args, **kw: recorded.append(pose.copy()))
    active.record_telemetry(scene)
    assert recorded[0][0, 0] == expected


def test_pause_seek_and_restart_reproduce_endpoint_and_interpolated_geometry(replay):
    active, scene, _ = replay
    expected = {}
    for time in (0., .5, 1., 1., 0., .5):
        run(active, scene, time=time)
        values = np.array([[v.co.copy() for v in row[0].data.vertices] for row in active.blades])
        if time in expected:
            np.testing.assert_array_equal(values, expected[time])
        else:
            expected[time] = values
        assert scene['wfrl_clearance_time_s'] == time
        assert all(row['time'] == time for row in active.comparison.rows.values())


def test_binding_failure_restores_old_deformation_and_reports_cause(replay, monkeypatch):
    from wfrl_blender import clearance_replay

    active, scene, _ = replay
    run(active, scene)
    for obj, *_ in (*active.blades, *active.towers):
        obj.data.users = 1
        obj.data.use_fake_user = False
        obj.data.shape_keys = obj.data.library = None
    for tid in active.readers:
        scene.objects[f'WFRL.Turbine.{tid}.Tower'] = object()
    monkeypatch.setattr(active.comparison, 'close', Mock())
    monkeypatch.setattr(farm_flex, '_load_package', lambda _: SimpleNamespace(
        manifest={'turbine_ids': list(active.readers)}))
    monkeypatch.setattr(clearance_replay, 'clear', lambda scene, reason: scene.update(wfrl_clearance_status=reason))

    def fail_binding(*args, **kwargs):
        raise ValueError('mesh binding unavailable')

    monkeypatch.setattr(farm_flex, 'FarmFlex', fail_binding)
    with pytest.raises(ValueError, match='mesh binding unavailable'):
        farm_flex.attach(scene, 'candidate')
    assert not active.enabled and farm_flex.active_for(scene) is None
    assert not scene['wfrl_flex_active']
    assert 'mesh binding unavailable' in scene['wfrl_home_load_error']
    assert 'mesh binding unavailable' in scene['wfrl_clearance_status']
    for obj, rest, *_ in active.blades:
        np.testing.assert_array_equal([v.co for v in obj.data.vertices], rest)
