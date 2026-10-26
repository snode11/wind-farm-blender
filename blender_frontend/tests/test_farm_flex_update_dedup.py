"""Same-time geometry reuse leaves consumers and native edits observable."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from wfrl_blender import farm_flex, performance, split_turbine_context
from test_farm_flex_world_matrices import Vertices, mathutils_module, replay


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


def test_native_topology_change_fails_without_partial_mesh_write(replay):
    active, scene, _ = replay
    run(active, scene)
    active.blades[0][0].data.vertices.pop()
    with pytest.raises(ValueError, match='topology no longer matches'):
        run(active, scene)
