"""Numerical response limits and finite detection summaries."""
from dataclasses import replace
import numpy as np
import pytest
from wfrl.radar_x100 import detection, schema
from tests.radar_x100.test_detection import template
from tests.radar_x100.test_detection_physics import ideal_resolution_config


def test_exponential_order_statistics_cap_keep_and_do_not_draw_for_empty_requests():
    expected_rng, actual_rng = np.random.default_rng(12), np.random.default_rng(12)
    expected = detection._top_exponential(expected_rng, 3, 3)
    actual = detection._top_exponential(actual_rng, 3, 7)
    np.testing.assert_array_equal(actual, expected)
    assert len(actual) == 3 and np.isfinite(actual).all()
    assert np.all(np.diff(actual) <= 0) and np.all(actual > 0)
    assert detection._top_exponential(actual_rng, 0, 7).size == 0
    assert detection._top_exponential(actual_rng, 7, 0).size == 0
    assert actual_rng.random() == expected_rng.random()


def test_subnormal_velocity_extent_still_counts_one_partial_search_cell():
    cfg = ideal_resolution_config(false_alarms_enabled=True, merge_range_m=400.,
        merge_azimuth_deg=120., merge_elevation_deg=44., merge_velocity_m_s=1e308,
        velocity_min_m_s=0., velocity_max_m_s=1e-320)
    frame = detection.generate_detections(np.empty((0, 3)), [], [], template=template(), config=cfg)
    stats = frame.provenance['detection_statistics']
    assert stats['search_cells'] == stats['false_alarms_before_output_filters'] == 1
    assert len(frame.points) == 1
    schema.json_bytes(frame.metadata())


def test_surface_rcs_rejects_nonfinite_products_and_invalid_incidence():
    with pytest.raises(ValueError, match='Nonfinite surface RCS'):
        detection.surface_rcs([1e308], [1.], config=detection.DetectionConfig(surface_rcs_density=10.))
    for incidence in ([-.1], [np.nan]):
        with pytest.raises(ValueError, match='incidence'):
            detection.surface_rcs([1.], incidence)
    np.testing.assert_array_equal(detection.surface_rcs([0., 1.], [1., 0.]), [0., 0.])


def test_zero_rcs_stays_zero_when_positive_range_fourth_power_underflows():
    assert detection.received_power_w(1e-100, 0.) == 0.
    with pytest.raises(ValueError, match='Nonfinite received power'):
        detection.received_power_w(1e-100, 1.)
    for distance, rcs in ((0., 0.), (1., -1.), (1., np.nan)):
        with pytest.raises(ValueError, match='inputs'):
            detection.received_power_w(distance, rcs)


def test_tiny_nonzero_target_probability_survives_frame_summary():
    cfg = ideal_resolution_config(false_alarm_probability_per_cell=1e-25)
    frame = detection.generate_detections([[60., 0., 0.]], [0.], [1e-20],
        template=template(), config=cfg)
    stats = frame.provenance['detection_statistics']
    expected = stats['expected_target_detections_before_output_filters']
    assert 0 < expected < np.finfo(float).eps
    assert stats['probability_any_target_before_output_filters'] == pytest.approx(expected, rel=1e-14, abs=0.)
    schema.json_bytes(detection.summarize_detections([frame]))


@pytest.mark.parametrize('field', ['model_power_w', 'model_rcs_m2'])
def test_overflowing_selected_totals_are_rejected_without_poisoning_json(field):
    values = np.zeros(2, dtype=schema.MODEL_DIAGNOSTIC_DTYPE)
    values[field] = 1e308
    frame = replace(template(), points=np.zeros(2, dtype=schema.POINT_DTYPE),
        model_diagnostics=schema.ModelDiagnostics(detection.MODEL_ID, values))
    with pytest.raises(ValueError, match='statistic totals'):
        detection.summarize_detections([frame])
    selected = detection.detection_statistics(frame, point_mask=[True, False])
    assert selected['selected_' + field] == 1e308
    schema.json_bytes(selected)


def test_origin_rear_wrap_and_zenith_are_finite_but_outside_hardware_profile():
    xyz = np.array([[0., 0., 0.], [1e-100, 0., 0.], [397., 0., 0.],
                    [-10., 0., 0.], [-10., -0., 0.], [0., 0., 10.]])
    polar = detection._polar(xyz)
    assert np.isfinite(polar).all()
    np.testing.assert_array_equal(polar[3:5, 1], [180., -180.])
    assert polar[-1, 2] == 90.
    frame = detection.generate_detections(xyz, np.zeros(len(xyz)), np.ones(len(xyz)),
        template=template(), config=ideal_resolution_config())
    assert frame.validity == 'VALID_EMPTY'
    assert frame.provenance['detection_statistics']['outside_hardware_profile'] == len(xyz)
    schema.json_bytes(detection.summarize_detections([frame]))


@pytest.mark.parametrize('model', ['SWERLING_0', 'SWERLING_1'])
def test_pd_is_bounded_at_floating_point_snr_and_pfa_limits(model):
    cfg = detection.DetectionConfig(fluctuation_model=model,
        false_alarm_probability_per_cell=float(np.nextafter(0., 1.)))
    snr = np.array([0., 1., 1000., np.finfo(float).max])
    pd = detection.probability_detection(snr, config=cfg)
    assert np.isfinite(pd).all() and np.all((pd >= 0) & (pd <= 1))
    assert np.all(np.diff(pd) >= 0) and pd[-1] == 1.
    assert pd[0] == cfg.false_alarm_probability_per_cell
