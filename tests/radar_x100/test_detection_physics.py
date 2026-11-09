"""Independent physical, probabilistic and resolution checks of X100 defaults."""
from dataclasses import replace
import math
import numpy as np
import pytest
from wfrl.radar_x100 import detection, schema, simulation


def template(sequence=0):
    return schema.RadarFrame('radar', 'physics', sequence, 'TEST', 'GEOMETRY',
        np.zeros(0, dtype=schema.POINT_DTYPE),
        spatial=dict(frame='SENSOR_X_FORWARD_Y_LEFT_Z_UP', units='m'))


def ideal_resolution_config(**kwargs):
    return detection.DetectionConfig(**(dict(false_alarm_probability_per_cell=1.,
        false_alarms_enabled=False, range_std_m=0., azimuth_std_deg=0.,
        elevation_std_deg=0., radial_velocity_std_m_s=0.) | kwargs))


def frame(ranges, azimuth=0., elevation=0., velocity=0., *, config=None, sequence=0, rcs=1.):
    r, az, el, speed = np.broadcast_arrays(np.asarray(ranges), azimuth, elevation, velocity)
    az, el = np.radians(az), np.radians(el)
    xyz = np.column_stack((r * np.cos(el) * np.cos(az), r * np.cos(el) * np.sin(az), r * np.sin(el)))
    return detection.generate_detections(xyz, speed, np.full(len(r), rcs),
        template=template(sequence), config=config)


@pytest.mark.parametrize('pattern,az_width,el_width', [('GAUSSIAN', 45., 15.),
    ('GAUSSIAN', 10., 5.), ('GAUSSIAN', 90., 40.), ('ISOTROPIC', 45., 15.)])
def test_passive_antenna_gain_integral_and_full_sphere_normalization(pattern, az_width, el_width):
    cfg = detection.DetectionConfig(antenna_pattern=pattern,
        azimuth_half_power_deg=az_width, elevation_half_power_deg=el_width)
    # Independent midpoint integration, including radiation outside the detection FOV.
    az = -180 + (np.arange(1440) + .5) * 360 / 1440
    el = -90 + (np.arange(720) + .5) * 180 / 720
    power_pattern = (np.ones((len(az), len(el))) if pattern == 'ISOTROPIC' else
        np.exp(-math.log(2) * ((az[:, None] / az_width)**2 + (el[None, :] / el_width)**2)))
    domega = math.radians(.25)**2 * np.cos(np.radians(el))[None, :]
    integral = float(np.sum(power_pattern * domega))
    assert cfg.directivity_linear * integral == pytest.approx(4 * math.pi, rel=1e-5)
    for gain, efficiency in ((cfg.tx_gain_linear, cfg.tx_efficiency), (cfg.rx_gain_linear, cfg.rx_efficiency)):
        assert gain * integral / (4 * math.pi) == pytest.approx(efficiency, rel=1e-5)
        assert gain * integral <= 4 * math.pi
    if pattern == 'GAUSSIAN':
        in_fov = (np.abs(az[:, None]) <= 60) & (np.abs(el[None, :]) <= 22)
        assert cfg.tx_gain_linear * np.sum((power_pattern * domega)[in_fov]) < 4 * math.pi


def test_frequency_thermal_noise_processing_and_assumed_anchor_are_separate():
    cfg = detection.DetectionConfig()
    derived = detection.model_derived_parameters(cfg)
    assert cfg.wavelength_m == pytest.approx(299792458 / 77e9)
    assert cfg.noise_power_w == pytest.approx(1.380649e-23 * 290 * 1e6 * 10**.6, rel=1e-14, abs=0.)
    assert derived['peak_eirp_w'] <= 10**(39 / 10) / 1000
    assert cfg.tx_gain_linear == pytest.approx(9.6752435058067)
    assert derived['current_anchor_probability_detection'] == pytest.approx(.9, abs=1e-12)
    assert cfg.calibration_status == 'UNCALIBRATED' and cfg.calibration_reference is None
    warmer = replace(cfg, noise_temperature_k=580.)
    assert warmer.processing_gain_db == cfg.processing_gain_db
    assert detection.model_derived_parameters(warmer)['current_anchor_snr_linear'] == pytest.approx(
        derived['current_anchor_snr_linear'] / 2)
    no_processing = replace(cfg, processing_gain_db=0.)
    assert detection.received_power_w(60., 1., config=cfg) == detection.received_power_w(60., 1., config=no_processing)
    assert no_processing.detector_noise_power_w / cfg.detector_noise_power_w == pytest.approx(cfg.processing_gain_linear)
    power = detection.received_power_w([20., 40., 80., 160.], 1., config=cfg)
    np.testing.assert_allclose(power / power[0], [1., 1/16, 1/256, 1/4096], rtol=1e-14)
    saved = frame([60.]).provenance['observation_model']
    assert 'ASSUMPTION' in saved['sensitivity_anchor']['source']
    assert saved['parameters']['calibration_status'] == 'UNCALIBRATED'
    assert saved['derived_parameters'] == derived


@pytest.mark.parametrize('pfa', [1., .1, 1e-6, 1e-12, 1e-100, 1e-300])
def test_swerling_zero_marcum_q_matches_noncentral_chi_square(pfa):
    ncx2 = pytest.importorskip('scipy.stats').ncx2
    cfg = detection.DetectionConfig(false_alarm_probability_per_cell=pfa)
    snr = np.array([0., 1e-8, .1, 1., 10., 20., 37.436754719339696, 100., 700., 1400., 1e6])
    expected = ncx2.sf(-2 * math.log(pfa), 2, 2 * snr)
    np.testing.assert_allclose(detection.probability_detection(snr, config=cfg), expected,
                               atol=3e-13, rtol=2e-11)
    sw1 = replace(cfg, fluctuation_model='SWERLING_1')
    np.testing.assert_allclose(detection.probability_detection(snr, config=sw1),
                               np.exp(math.log(pfa) / (1 + snr)))


@pytest.mark.parametrize('model', ['SWERLING_0', 'SWERLING_1'])
def test_sampled_detection_frequency_matches_each_fluctuation_model(model):
    cfg = detection.DetectionConfig(fluctuation_model=model, false_alarms_enabled=False)
    snr = 30.
    rcs = snr * cfg.detector_noise_power_w / detection.received_power_w(100., 1., config=cfg)
    expected = float(detection.probability_detection(snr, config=cfg))
    frames = [frame([100.], config=cfg, rcs=rcs, sequence=i) for i in range(800)]
    assert np.mean([len(f.points) for f in frames]) == pytest.approx(expected, abs=.05)
    saved = frames[0].provenance['detection_statistics']
    assert saved['expected_target_detections_before_output_filters'] == pytest.approx(expected)
    for f in frames:
        if len(f.points):
            assert f.model_diagnostics.values['model_snr_linear'][0] == pytest.approx(snr)


def test_false_alarm_trials_include_doppler_and_match_pfa_and_threshold():
    cfg = detection.DetectionConfig()
    expected_cells = (math.ceil((396 - .2) / .2)
        * math.ceil(2 * math.sin(math.radians(60)) / math.radians(1))
        * math.ceil(2 * math.sin(math.radians(22)) / math.radians(2))
        * math.ceil((600 / 3.6) / .1))
    derived = detection.model_derived_parameters(cfg)
    assert derived['noise_only_search_cells'] == expected_cells == 7257784600
    assert derived['expected_noise_only_false_alarms_per_frame'] == pytest.approx(.0072577846)
    assert math.exp(-cfg.threshold_noise_ratio) == pytest.approx(cfg.false_alarm_probability_per_cell, rel=1e-13, abs=0.)
    noisy = replace(cfg, false_alarm_probability_per_cell=1e-10, range_std_m=0.,
        azimuth_std_deg=0., elevation_std_deg=0., radial_velocity_std_m_s=0.)
    frames = [frame([], config=noisy, sequence=i) for i in range(600)]
    mean = np.mean([len(f.points) for f in frames])
    assert mean == pytest.approx(expected_cells * 1e-10, abs=.12)
    assert frames[0].provenance['detection_statistics']['expected_false_alarms_before_output_filters'] == pytest.approx(expected_cells * 1e-10)
    speeds = np.concatenate([f.radial_velocity_m_s for f in frames])
    assert np.any(speeds < -10) and np.any(speeds > 10)


@pytest.mark.parametrize('offset', [0., .003, .09, 1.245])
def test_close_pairs_merge_across_bin_boundaries_but_resolved_pairs_stay_separate(offset):
    cfg = ideal_resolution_config()
    assert len(frame(np.array([10.1999, 10.2001]) + offset, config=cfg).points) == 1
    assert len(frame(np.array([10.01, 10.99]) + offset, config=cfg).points) == 2
    assert len(frame(np.array([10., 10.199]) + offset, config=cfg).points) == 1
    assert len(frame(np.array([10., 10.201]) + offset, config=cfg).points) == 2


def test_velocity_and_off_axis_angular_resolution():
    cfg = ideal_resolution_config()
    opposing = frame([60., 60.], velocity=[10., -10.], config=cfg)
    np.testing.assert_allclose(sorted(opposing.radial_velocity_m_s), [-10., 10.])
    assert len(frame([60., 60.], velocity=[-.049, .049], config=cfg).points) == 1
    assert len(frame([60., 60.], velocity=[-.051, .051], config=cfg).points) == 2
    for angles, expected in (([0., .9], 1), ([0., 1.1], 2), ([49., 50.5], 1), ([49., 51.], 2)):
        assert len(frame([60., 60.], azimuth=angles, config=cfg).points) == expected
    for angles, expected in (([0., 1.9], 1), ([0., 2.1], 2)):
        assert len(frame([60., 60.], elevation=angles, config=cfg).points) == expected
    fast = frame([60., 60., 60.], velocity=[-112., 56., 0.], config=cfg)
    assert len(fast.points) == 1
    assert fast.provenance['detection_statistics']['outside_velocity_profile'] == 2


def test_dense_surface_chain_cannot_bridge_resolvable_targets_and_order_is_repeatable():
    cfg = ideal_resolution_config()
    ranges = 40 + np.arange(50) * .15
    forward = frame(ranges, config=cfg)
    backward = frame(ranges[::-1], config=cfg)
    assert len(forward.points) >= 25
    assert forward.model_diagnostics.values['integration_samples'].max() <= 2
    assert forward.points.tobytes() == backward.points.tobytes()
    assert forward.model_diagnostics.values.tobytes() == backward.model_diagnostics.values.tobytes()


@pytest.mark.parametrize('count', [0, 1, 64, 512])
@pytest.mark.parametrize('widths', [[1., 1., 1., 1.], [.2, .017, .035, .1]])
def test_resolution_hash_matches_complete_neighbour_scan(count, widths):
    rng = np.random.default_rng(2026)
    normalized = rng.uniform(-3., 3., (count, 4))
    if count >= 64:
        # Exact boundaries, duplicates and dense chains exercise all four hash axes.
        normalized[:16] = np.arange(16)[:, None] * .6 - 4.
        normalized[16:24] = 0.
        for axis in range(4):
            normalized[24 + 3*axis:27 + 3*axis] = 0.
            normalized[24 + 3*axis:27 + 3*axis, axis] = [
                np.nextafter(-1., -np.inf), -1., np.nextafter(-1., np.inf)]
    coordinates = normalized * widths
    normalized = coordinates / widths
    expected = np.full(count, -1, dtype=np.int64)
    clusters = 0
    for index in np.lexsort(normalized[:, ::-1].T):
        if expected[index] >= 0:
            continue
        candidates = np.flatnonzero((expected < 0)
            & (np.abs(normalized - normalized[index]) <= 1).all(axis=1))
        minimum = normalized[candidates].min(axis=0)
        members = candidates[(normalized[candidates] - minimum <= 1).all(axis=1)]
        expected[members] = clusters
        clusters += 1
    before = coordinates.copy()
    labels, actual_count = detection._resolution_clusters(coordinates, widths)
    assert actual_count == clusters
    np.testing.assert_array_equal(labels, expected)
    np.testing.assert_array_equal(coordinates, before)


def test_datasheet_accuracy_is_two_sigma_assumption_and_errors_are_nonzero():
    cfg = detection.DetectionConfig(false_alarms_enabled=False)
    sigmas = np.array([cfg.range_std_m, cfg.azimuth_std_deg, cfg.elevation_std_deg, cfg.radial_velocity_std_m_s])
    np.testing.assert_allclose(sigmas, [.025, .05, .1, .025])
    frames = [frame([60.], config=cfg, sequence=i) for i in range(400)]
    xyz = np.concatenate([f.xyz for f in frames])
    errors = np.column_stack((np.linalg.norm(xyz, axis=1) - 60,
        np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0])),
        np.degrees(np.arctan2(xyz[:, 2], np.hypot(xyz[:, 0], xyz[:, 1]))),
        np.concatenate([f.radial_velocity_m_s for f in frames])))
    np.testing.assert_allclose(np.std(errors, axis=0), sigmas, rtol=.15)


@pytest.mark.parametrize('distance', [50., 60.])
def test_surface_entry_point_passes_the_physical_defaults(distance):
    def sample(t):
        return simulation.SceneSample(t, 'plate', 'plate',
            np.array([[distance, -1.5, -.75], [distance, 1.5, -.75],
                      [distance, 1.5, .75], [distance, -1.5, .75]]),
            np.array([[0, 1, 2], [0, 2, 3]]), np.eye(4), 'isolated plate')
    result = simulation.simulate_detections(sample, 1., sensor_id='radar', session_id='physics',
        sequence=0, interval_s=(0., 2.), integration_budget=2048)
    model = result.provenance['observation_model']
    assert model['parameters']['carrier_frequency_hz'] == 77e9
    assert model['parameters']['fluctuation_model'] == 'SWERLING_0'
    assert model['parameters']['merge_range_m'] == .2
    assert 2 <= len(result.points) <= 20
    assert result.provenance['detection_statistics']['total_model_rcs_m2'] == pytest.approx(.45, rel=.001)


@pytest.mark.parametrize('kwargs', [dict(tx_efficiency=1.01), dict(rx_efficiency=0),
    dict(carrier_frequency_hz=74.95e9), dict(false_alarm_probability_per_cell=0),
    dict(false_alarm_probability_per_cell=1.1), dict(noise_figure_db=-1),
    dict(processing_gain_db=float('inf')), dict(merge_velocity_m_s=0),
    dict(velocity_min_m_s=60), dict(fluctuation_model='EXPONENTIAL')])
def test_invalid_physical_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        detection.DetectionConfig(**kwargs)


@pytest.mark.parametrize('name', ['tx_gain_linear', 'rx_gain_linear', 'noise_power_w', 'wavelength_m', 'threshold_noise_ratio'])
def test_derived_quantities_cannot_be_independently_overridden(name):
    with pytest.raises(TypeError):
        detection.DetectionConfig(**{name: 100.})


def _cluster_fixture(seed):
    rng = np.random.default_rng(seed)
    widths = np.array([.3, .002, .003, .1])
    centers = rng.uniform(-40, 40, (rng.integers(1, 40), 4)) * widths
    points = centers[rng.integers(len(centers), size=600)] + rng.normal(0, .7, (600, 4)) * widths
    # Exact width multiples, duplicates and values one ulp either side of a bin edge.
    points[:40] = np.round(points[:40] / widths) * widths
    points[40:60] = points[60:80]
    edge = np.floor(points[80:100] / widths) * widths
    points[80:90] = np.nextafter(edge[:10], -np.inf)
    points[90:100] = edge[10:]
    return points, widths


@pytest.mark.parametrize('seed', range(12))
def test_neighbor_list_clusters_equal_the_per_seed_bucket_lookup(seed):
    coordinates, widths = _cluster_fixture(seed)
    before = coordinates.copy()
    labels, count = detection._resolution_clusters(coordinates, widths)
    expected, expected_count = detection._resolution_clusters_hashed(coordinates, widths)
    assert count == expected_count
    np.testing.assert_array_equal(labels, expected)
    np.testing.assert_array_equal(coordinates, before)


def test_rounded_unit_difference_across_two_bins_stays_unresolved_as_in_bucket_lookup():
    # 2.0 - (1 - 2**-53) rounds to 1.0, but the bins differ by two and are never adjacent.
    normalized = np.array([[np.nextafter(1., 0.), 0., 0., 0.], [2., 0., 0., 0.]])
    widths = np.ones(4)
    labels, count = detection._resolution_clusters(normalized, widths)
    expected, expected_count = detection._resolution_clusters_hashed(normalized, widths)
    assert count == expected_count == 2
    np.testing.assert_array_equal(labels, expected)


@pytest.mark.parametrize('case', ['empty', 'single', 'pair_budget', 'key_range', 'non_finite'])
def test_cluster_fallbacks_and_degenerate_frames(monkeypatch, case):
    widths = np.ones(4)
    coordinates = {'empty': np.empty((0, 4)), 'single': np.full((1, 4), -3.5),
                   'pair_budget': _cluster_fixture(1)[0] / _cluster_fixture(1)[1],
                   'key_range': np.array([[0., 0., 0., 0.], [1e16, 1e16, 1e16, 1e16], [.5, .5, .5, .5]]),
                   'non_finite': np.array([[0., 0., 0., 0.], [np.inf, 0., 0., 0.]])}[case]
    hashed = detection._resolution_clusters_hashed
    calls = []
    monkeypatch.setattr(detection, '_resolution_clusters_hashed',
                        lambda *args: calls.append(1) or hashed(*args))
    if case == 'pair_budget':
        monkeypatch.setattr(detection, 'MAX_NEIGHBOR_PAIRS', 10)
    if case == 'non_finite':
        # Non-finite input keeps the per-seed lookup's own error unchanged.
        with pytest.raises(ValueError), np.errstate(invalid='ignore'):
            hashed(coordinates, widths)
        with pytest.raises(ValueError), np.errstate(invalid='ignore'):
            detection._resolution_clusters(coordinates, widths)
        assert calls
        return
    labels, count = detection._resolution_clusters(coordinates, widths)
    assert bool(calls) is (case in {'pair_budget', 'key_range'})
    if len(coordinates):
        expected, expected_count = hashed(coordinates, widths)
        assert count == expected_count
        np.testing.assert_array_equal(labels, expected)
    else:
        assert count == 0 and labels.shape == (0,) and labels.dtype == np.int64
