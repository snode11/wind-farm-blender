"""Evaluation truth, missing denominators and observable-only inference."""
from dataclasses import asdict, replace
import copy
import numpy as np
import pytest
from scripts.radar_x100 import evaluate_blades as evaluation
from wfrl.radar_x100 import clearance, detection, sampling, simulation
from tests.radar_x100.test_clearance import fixture


def test_metrics_report_missing_frames_and_absent_classes_without_zero_filling():
    result = evaluation.error_metrics([None, -2., 1., None], 4)
    assert result['estimated_frames'] == 2 and result['estimable_fraction'] == .5
    assert result['rmse_m'] == pytest.approx(np.sqrt(2.5))
    assert result['max_abs_error_m'] == 2.
    assert evaluation.error_metrics([None], 1)['rmse_m'] is None
    result = evaluation.separation_metrics([1, 1, 2, -1], [1, 0, 2, 0])
    assert result['accuracy'] == pytest.approx(2/3)
    assert result['unresolved_truth_points'] == 1
    assert result['classes']['BLADE']['precision'] == 1.
    assert result['classes']['BLADE']['recall'] == .5
    assert result['classes']['OTHER']['recall'] is None


def test_resolution_truth_retains_mixed_cells_and_uses_no_nearest_surface_guess():
    vertices = np.array([[10., -.05, -.05], [10., .05, -.05], [10., 0, .05],
                         [10.01, -.05, -.05], [10.01, .05, -.05], [10.01, 0, .05]])
    def sample(t):
        return simulation.SceneSample(t, 'test', 'two-materials', vertices, [[0, 1, 2], [3, 4, 5]], np.eye(4), 'test')
    plan = sampling.make_plan(sample(0.), 1024, 100)
    config = detection.DetectionConfig(merge_range_m=1., merge_azimuth_deg=5., merge_elevation_deg=5.,
        false_alarms_enabled=False, false_alarm_probability_per_cell=1.,
        range_std_m=0., azimuth_std_deg=0., elevation_std_deg=0., radial_velocity_std_m_s=0.)
    visible = lambda scene, targets: np.ones(len(targets), bool)
    frame = simulation.simulate_detections(sample, .5, sensor_id='r', session_id='s', sequence=1,
        interval_s=(0., 1.), integration_budget=1024, config=config, visibility=visible, sampling_plan=plan)
    observation = simulation._observe(sample, .5, interval_s=(0., 1.), candidate_budget=1024,
        visibility=visible, sampling_plan=plan)
    assert len(frame.points) == 1
    mixed = evaluation.resolution_truth(observation, frame, [0, 1, 2], 1, 1, config)
    assert mixed.tolist() == [-1]
    same_class = evaluation.resolution_truth(observation, frame, [0, 2], 1, 0, config)
    assert same_class.tolist() == [clearance.BLADE]
    with pytest.raises(ValueError, match='reproduce resolution-cell'):
        bad = frame.model_diagnostics.values.copy()
        bad['model_power_w'] *= 1.1
        evaluation.resolution_truth(observation,
            replace(frame, model_diagnostics=replace(frame.model_diagnostics, values=bad)), [0, 1, 2], 1, 1, config)


def test_evaluation_truth_changes_cannot_change_estimated_tips(monkeypatch):
    shape, priors, points, truth, vertices, triangles = fixture()
    monkeypatch.setattr(evaluation, '_surface_clearance', lambda *args: None)
    record = dict(time_s=1., source_index=0, blade=1, passage=0, world_points=points,
        radial_velocity_m_s=np.zeros(len(points)), priors=[asdict(p) for p in priors],
        tower_vertices=vertices, tower_triangles=triangles, position_std_m=np.full(len(points), .05),
        observed_power_w=np.ones(len(points)), observed_snr_linear=np.full(len(points), 30.),
        world_from_analysis=np.eye(4), roi_mask=np.ones(len(points), bool),
        truth=dict(labels=np.ones(len(points), int), false_alarm=np.zeros(len(points)),
            tip_world=truth, tip_clearance_m=8+truth[0], baseline_clearance_m=9., distal_surface_clearance_m=9.))
    data = dict(source_id='test', geometry_id='analytic', installation=dict(id='r'),
        shapes={str(i): asdict(shape) for i in (1, 2, 3)}, meshes={'1': {}}, frames=[record],
        passage_windows=[[{}], [], []])
    original = evaluation.evaluate_detection_observations(data)
    corrupted = copy.deepcopy(data)
    corrupted['frames'][0]['truth'].update(labels=np.full(len(points), 2),
        false_alarm=np.ones(len(points)), tip_world=truth+100, tip_clearance_m=1000.,
        baseline_clearance_m=-100., distal_surface_clearance_m=-10.)
    changed = evaluation.evaluate_detection_observations(corrupted)
    first, second = (r['frames'][0]['estimate'] for r in (original, changed))
    assert first['status'] == second['status'] == 'SUPPORTED'
    np.testing.assert_array_equal(first['tip_world'], second['tip_world'])
    assert first['clearance_m'] == second['clearance_m']
    np.testing.assert_array_equal(original['frames'][0]['point_labels'], changed['frames'][0]['point_labels'])
    assert original['separation']['accuracy'] != changed['separation']['accuracy']
