"""Measured-PLY calibration, configurable rectangular field of view and vendor field mapping."""
from dataclasses import replace
import math

import numpy as np
import pytest

from wfrl.radar_x100 import calibration, detection, schema, simulation


def template(sequence=0):
    return schema.RadarFrame('radar', 'calibration-test', sequence, 'TEST', 'GEOMETRY',
        np.zeros(0, dtype=schema.POINT_DTYPE),
        time=schema.FrameTime('SIMULATION:test', sequence / calibration.FRAME_RATE_HZ, quality='SIMULATION'),
        spatial=dict(frame='SENSOR_X_FORWARD_Y_LEFT_Z_UP', units='m', world_from_sensor=np.eye(4).tolist()))


def test_calibrated_config_declares_reference_and_overrides_field_of_view():
    config = calibration.calibrated_config(azimuth_limit_deg=45.75, elevation_limit_deg=30.)
    assert config.calibration_status == 'USER_CALIBRATED' and '33 Hz' in config.calibration_reference
    assert (config.azimuth_limit_deg, config.elevation_limit_deg) == (45.75, 30.)
    for key, value in calibration.PARAMETERS.items():
        assert getattr(config, key) == value
    default = calibration.calibrated_config()
    assert (default.azimuth_limit_deg, default.elevation_limit_deg) == (
        schema.PROFILE['azimuth_deg'], schema.PROFILE['elevation_deg'])
    assert detection.DetectionConfig().calibration_status == 'UNCALIBRATED'
    assert detection.DetectionConfig().vendor_snr_offset_db is None


def test_far_target_detection_rate_tracks_measured_drone_rates():
    config = calibration.calibrated_config()
    rcs = 10 ** (-3.14 / 10)
    for range_m, measured in calibration.MEASURED['far_target_detection_rate']:
        snr = detection.received_power_w(range_m, rcs, config=config) / config.detector_noise_power_w
        assert abs(float(detection.probability_detection(snr, config=config)) - measured) < .25


@pytest.mark.parametrize('limits', [(0., 22.), (60., 0.), (91., 22.), (60., math.nan)])
def test_field_of_view_limits_are_validated(limits):
    with pytest.raises(ValueError):
        detection.DetectionConfig(azimuth_limit_deg=limits[0], elevation_limit_deg=limits[1])
    with pytest.raises(ValueError):
        simulation.field_of_view(limits)


def test_rectangular_field_of_view_gates_detections():
    xyz = np.array([[20., 0., 0.], [20., 0., 20. * math.tan(math.radians(27))],
                    [20., 20. * math.tan(math.radians(50)), 0.]])
    base = dict(antenna_pattern='ISOTROPIC', false_alarms_enabled=False, processing_gain_db=90.,
                range_std_m=0., azimuth_std_deg=0., elevation_std_deg=0., radial_velocity_std_m_s=0.)
    narrow = detection.DetectionConfig(**base)
    wide = detection.DetectionConfig(**base, azimuth_limit_deg=55., elevation_limit_deg=30.)
    count = lambda config: len(detection.generate_detections(xyz, np.zeros(3), np.ones(3),
        template=template(), config=config).points)
    assert count(narrow) == 2 and count(wide) == 3
    assert simulation.in_fov(xyz, (55., 30.)).all() and simulation.in_fov(xyz).tolist() == [True, False, True]


def test_vendor_fields_follow_configured_mapping_and_stay_unknown_by_default():
    xyz = np.array([[30., 0., 0.], [60., 1., 0.]])
    config = replace(calibration.calibrated_config(), false_alarms_enabled=False, miss_probability=0.,
                     vendor_mag_std=0., processing_gain_db=90.)
    frame = detection.generate_detections(xyz, np.zeros(2), np.full(2, 5.), template=template(), config=config)
    points, values = frame.points, frame.model_diagnostics.values
    noise = config.detector_noise_power_w
    np.testing.assert_allclose(points['snr'], 10 * np.log10(values['model_observed_power_w'] / noise)
                               + config.vendor_snr_offset_db, rtol=1e-5)
    np.testing.assert_allclose(points['mag'], config.vendor_mag_slope * points['snr'] + config.vendor_mag_intercept,
                               rtol=1e-5)
    assert np.isfinite(points['rcs']).all() and frame.provenance['field_of_view_deg'] == [60., 22.]
    unmapped = detection.generate_detections(xyz, np.zeros(2), np.full(2, 5.), template=template(),
        config=replace(config, vendor_snr_offset_db=None))
    assert np.isnan(unmapped.points['snr']).all() and np.isnan(unmapped.points['mag']).all()


def test_ms_threshold_follows_configuration():
    xyz = np.array([[30., 0., 0.], [30., 10., 0.]])
    config = replace(calibration.calibrated_config(), false_alarms_enabled=False, miss_probability=0.,
                     processing_gain_db=90., radial_velocity_std_m_s=0.)
    frame = detection.generate_detections(xyz, np.array([.1, 1.]), np.full(2, 5.), template=template(),
                                          config=config)
    assert sorted(frame.points['ms'].tolist()) == [0, 1]
