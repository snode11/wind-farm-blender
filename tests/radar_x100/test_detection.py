"""Incoherent response, resolution, detection statistics and saved diagnostics."""
from dataclasses import replace
import math
import numpy as np
import pytest
from wfrl.radar_x100 import channel, detection, recording, sampling, schema, simulation


def template(sequence=0, completeness='COMPLETE'):
    return schema.RadarFrame('radar', 'engineering-test', sequence, 'TEST', 'GEOMETRY',
        np.zeros(0, dtype=schema.POINT_DTYPE), completeness=completeness,
        time=schema.FrameTime('SIMULATION:test', sequence / 30, quality='SIMULATION'),
        spatial=dict(frame='SENSOR_X_FORWARD_Y_LEFT_Z_UP', units='m', world_from_sensor=np.eye(4).tolist()))


def config(**kwargs):
    return detection.DetectionConfig(**(dict(antenna_pattern='ISOTROPIC',
        false_alarm_probability_per_cell=1., false_alarms_enabled=False,
        processing_gain_db=0., fluctuation_model='SWERLING_1',
        merge_range_m=1., merge_azimuth_deg=2., merge_velocity_m_s=200.,
        range_std_m=0., azimuth_std_deg=0., elevation_std_deg=0.,
        radial_velocity_std_m_s=0.) | kwargs))


def points_at(ranges, *, cfg=None, seed=100, sequence=0, **kwargs):
    xyz = np.column_stack((ranges, np.zeros((len(ranges), 2))))
    return detection.generate_detections(xyz, np.zeros(len(xyz)), np.ones(len(xyz)),
        template=template(sequence), config=config() if cfg is None else cfg, seed=seed, **kwargs)


def rectangle(n=1, t=0., pose=None, x=20.):
    vertices, triangles = [], []
    for iy in range(n):
        for iz in range(n):
            y, z, index = 1 + 3 * iy / n, -.75 + 1.5 * iz / n, len(vertices)
            vertices.extend([[x, y, z], [x, y + 3/n, z],
                             [x, y + 3/n, z + 1.5/n], [x, y, z + 1.5/n]])
            triangles.extend([[index, index+1, index+2], [index, index+2, index+3]])
    return simulation.SceneSample(t, 'plate', f'plate-{n}', np.array(vertices), np.array(triangles),
                                   np.eye(4) if pose is None else pose, 'isolated rectangle')


def simulated(n=1, budget=512, cfg=None, **kwargs):
    return simulation.simulate_detections(lambda t: rectangle(n, t), 1.,
        sensor_id='radar', session_id='test', sequence=30, interval_s=(0., 2.),
        integration_budget=budget, config=config() if cfg is None else cfg,
        visibility=lambda sample, xyz: np.ones(len(xyz), dtype=bool), **kwargs)


def test_fixed_rcs_far_field_equation_and_distance_scaling_not_point_counts():
    cfg = config()
    expected = (cfg.transmit_power_w * .7 * .7 * (299792458 / 77e9)**2 * 1.7
                / ((4 * math.pi)**3 * 40**4))
    assert detection.received_power_w(40., 1.7, config=cfg) == pytest.approx(expected, rel=1e-13, abs=0.)
    power = detection.received_power_w(np.array([40., 80., 160.]), 1.7, config=cfg)
    np.testing.assert_allclose(power / power[0], [1., 1/16, 1/256], rtol=1e-14)
    frames = [points_at([distance], cfg=cfg) for distance in (40., 80., 160.)]
    assert [len(frame.points) for frame in frames] == [1, 1, 1]
    actual = [frame.model_diagnostics.values['model_power_w'][0] for frame in frames]
    np.testing.assert_allclose(np.array(actual) / actual[0], [1, 1/16, 1/256])


def test_one_way_pattern_squared_excess_attenuation_and_system_loss():
    isotropic = detection.received_power_w(40., 1., config=config())
    gaussian = config(antenna_pattern='GAUSSIAN')
    boresight = detection.received_power_w(40., 1., config=gaussian)
    assert detection.received_power_w(40., 1., config=gaussian,
        azimuth_deg=gaussian.azimuth_half_power_deg) == pytest.approx(boresight / 4, rel=1e-13, abs=0.)
    attenuated = detection.received_power_w(40., 1., config=config(attenuation_db_per_m=.1,
                                                                            system_loss_linear=2.))
    assert attenuated / isotropic == pytest.approx(10**(-8/10) / 2)
    np.testing.assert_allclose(detection.surface_rcs([2., 4., 6.], [1., .5, 0.],
        config=config(surface_rcs_density=.3, incidence_exponent=2)), [.6, .3, 0.])


def test_mesh_and_integration_refinement_converge_in_power_and_detection_statistics():
    cfg = config(merge_range_m=400., merge_azimuth_deg=120., merge_elevation_deg=44.,
                 false_alarm_probability_per_cell=math.exp(-4.))
    # Independent midpoint surface integral in physical coordinates.
    y = 1 + (np.arange(600) + .5) * 3 / 600
    z = -.75 + (np.arange(300) + .5) * 1.5 / 300
    r = np.sqrt(20**2 + y[:, None]**2 + z[None, :]**2)
    reference = float(np.mean(detection.received_power_w(r, 4.5 * .1 * 20 / r, config=cfg)))
    expected_pd = math.exp(-4 * cfg.noise_power_w / (reference + cfg.noise_power_w))
    errors = []
    for budget in (128, 2048, 16384):
        level_errors = []
        for mesh in (1, 4, 12):
            frame = simulated(mesh, budget, cfg)
            stats = detection.detection_statistics(frame)['frame_model_statistics']
            assert stats['occupied_cells'] == 1
            level_errors.append(abs(stats['total_model_power_w'] / reference - 1))
            if budget == 16384:
                assert stats['total_model_power_w'] == pytest.approx(reference, rel=3e-5, abs=0.)
                assert stats['expected_target_detections_before_output_filters'] == pytest.approx(expected_pd, abs=2e-5)
        errors.append(np.mean(level_errors))
    assert errors[-1] < errors[0] / 10
    # Common frame seeds compare actual detection statistics after both refinements.
    coarse = [len(simulated(1, 128, cfg, noise_seed=seed).points) for seed in range(64)]
    refined = [len(simulated(12, 16384, cfg, noise_seed=seed).points) for seed in range(64)]
    assert abs(np.mean(coarse) - np.mean(refined)) < .03
    assert abs(np.mean(refined) - expected_pd) < .15


def test_integration_budget_exceeds_device_limit_and_report_cap_does_not_change_response():
    first = simulated(budget=8192, output_point_limit=1)
    second = simulated(budget=8192, output_point_limit=4096)
    assert len(first.points) == 1 < len(second.points) <= 4096
    a, b = [frame.provenance['detection_statistics'] for frame in (first, second)]
    assert a['total_model_power_w'] == b['total_model_power_w']
    assert a['occupied_cells'] == b['occupied_cells']
    assert a['output_truncated'] == len(second.points) - 1
    assert second.provenance['integration_budget'] == 8192
    assert second.model_diagnostics.values['integration_samples'].sum() == 8192
    assert first.provenance['observation_model']['output_point_limit'] == 1
    with pytest.raises(ValueError, match='report budget'):
        simulation.simulate(lambda t: rectangle(t=t), 1., sensor_id='r', session_id='s',
                            sequence=0, interval_s=(0, 2), candidate_budget=8192)


def test_unresolved_targets_merge_power_before_detection_without_vendor_field_mapping():
    frame = points_at([10.31, 10.65])
    weights = detection.received_power_w([10.31, 10.65], [1., 1.], config=config())
    assert len(frame.points) == 1
    assert frame.points['x'][0] == pytest.approx(np.average([10.31, 10.65], weights=weights))
    assert frame.points['x'][0] != pytest.approx(10.7)
    assert frame.model_diagnostics.values['model_power_w'][0] == pytest.approx(weights.sum(), rel=1e-13, abs=0.)
    assert frame.model_diagnostics.values['model_rcs_m2'][0] == 2
    assert frame.model_diagnostics.values['integration_samples'][0] == 2
    assert frame.material_ids is None
    assert all(np.isnan(frame.points[name]).all() for name in ('mag', 'rcs', 'snr'))
    assert frame.points['ms'][0] == 0 and frame.provenance['label_semantics'] == 'NOT_GENERATED'
    assert len(points_at([10.31, 12.65]).points) == 2
    # Fine angular cells can resolve targets that broad angular cells merge.
    xyz = np.array([[10.4, -.4, 0.], [10.4, .4, 0.]])
    for width, expected in ((120., 1), (1., 2)):
        result = detection.generate_detections(xyz, [0., 0.], [1., 1.], template=template(),
                                              config=config(merge_azimuth_deg=width))
        assert len(result.points) == expected


def test_threshold_miss_and_false_alarm_controls_have_distinct_counters():
    threshold = points_at([10.31], cfg=config(false_alarm_probability_per_cell=1e-300))
    dropped = points_at([10.31], cfg=config(miss_probability=1.))
    assert threshold.validity == dropped.validity == 'VALID_EMPTY'
    assert threshold.provenance['detection_statistics']['threshold_misses'] == 1
    assert dropped.provenance['detection_statistics']['dropout_misses'] == 1
    noisy = points_at([], cfg=config(false_alarms_enabled=True, merge_range_m=400.,
                                    merge_azimuth_deg=120., merge_elevation_deg=44.))
    assert len(noisy.points) == 1
    values = noisy.model_diagnostics.values
    assert values['false_alarm'][0] == 1 and values['integration_samples'][0] == 0
    assert values['model_power_w'][0] == values['model_rcs_m2'][0] == values['model_snr_linear'][0] == 0
    assert values['model_observed_power_w'][0] > 0
    assert detection.summarize_detections([noisy])['support_fraction'] == 0


def test_false_alarm_and_target_detection_frequencies_follow_declared_power_model():
    cfg = config(false_alarms_enabled=True, merge_range_m=400., merge_azimuth_deg=120.,
                 merge_elevation_deg=44., false_alarm_probability_per_cell=math.exp(-1.), miss_probability=.2)
    false = [len(points_at([], cfg=cfg, sequence=i).points) for i in range(500)]
    target_frames = [points_at([40.], cfg=cfg, sequence=i) for i in range(500)]
    pd = target_frames[0].provenance['detection_statistics']['expected_target_detections_before_output_filters']
    assert np.mean(false) == pytest.approx(math.exp(-1), abs=.065)
    assert np.mean([len(f.points) for f in target_frames]) == pytest.approx(pd, abs=.065)
    assert all(f.provenance['detection_statistics']['false_alarms_before_output_filters'] == 0 for f in target_frames)


def test_measurement_error_is_independent_of_resolution_and_seed_is_reproducible():
    cfg = config(range_std_m=.2, azimuth_std_deg=.1, radial_velocity_std_m_s=.05)
    first = points_at([40.31], cfg=cfg, seed=44)
    repeated = points_at([40.31], cfg=cfg, seed=44)
    assert first.points.tobytes() == repeated.points.tobytes()
    assert first.metadata() == repeated.metadata()
    assert first.radial_velocity_m_s.tobytes() == repeated.radial_velocity_m_s.tobytes()
    coarse = points_at([40.31], cfg=replace(cfg, merge_range_m=50., merge_azimuth_deg=120.), seed=44)
    np.testing.assert_array_equal(first.xyz, coarse.xyz)
    assert points_at([40.31], cfg=cfg, seed=45).points.tobytes() != first.points.tobytes()
    ranges = [np.linalg.norm(points_at([40.31], cfg=cfg, sequence=i).xyz[0]) for i in range(300)]
    assert np.std(ranges) == pytest.approx(.2, abs=.03)


def test_hardware_fov_range_and_first_hit_are_separate_from_detection():
    result = detection.generate_detections([[.1, 0, 0], [-10, 0, 0], [397, 0, 0], [10, 0, 0]],
        [0]*4, [1]*4, template=template(), config=config())
    assert len(result.points) == 1
    assert result.provenance['detection_statistics']['outside_hardware_profile'] == 3
    def stacked(t):
        vertices = np.array([[10,-1,-1], [10,1,-1], [10,0,1],
                             [20,-1,-1], [20,1,-1], [20,0,1]])
        return simulation.SceneSample(t, 'occlusion', 'two-triangles', vertices,
                                      np.array([[0,1,2], [3,4,5]]), np.eye(4), 'near and far')
    kwargs = dict(sensor_id='r', session_id='s', sequence=0, interval_s=(0., 2.),
                  integration_budget=256, config=config())
    blocked = simulation.simulate_detections(stacked, 1., **kwargs)
    clear = simulation.simulate_detections(stacked, 1., visibility=lambda s, xyz: np.ones(len(xyz), bool), **kwargs)
    assert blocked.provenance['occluded'] > 0 and clear.provenance['occluded'] == 0
    assert blocked.provenance['detection_statistics']['total_model_power_w'] < clear.provenance['detection_statistics']['total_model_power_w']


def test_detection_retains_profile_boundary_tolerance_without_negative_cell_indices():
    angles = np.radians([[-60 - 1e-11, 0], [60 + 1e-11, 0], [0, -22 - 1e-11], [0, 22 + 1e-11]])
    a, e = angles.T
    xyz = 10 * np.column_stack((np.cos(e)*np.cos(a), np.cos(e)*np.sin(a), np.sin(e)))
    assert simulation.in_fov(xyz).all()
    result = detection.generate_detections(xyz, np.zeros(4), np.ones(4), template=template(), config=config())
    assert len(result.points) == 4
    assert result.provenance['detection_statistics']['measurement_rejected'] == 0


def test_missing_empty_and_incomplete_frames_remain_distinct_in_statistics_and_replay(tmp_path):
    empty = points_at([])
    assert empty.validity == 'VALID_EMPTY'
    assert detection.generate_detections(None, None, None, template=None) is None
    invalid = []
    for completeness in ('INCOMPLETE', 'CORRUPT'):
        frame = detection.generate_detections(None, None, None, template=template(completeness=completeness),
            config=config(false_alarms_enabled=True))
        assert frame.validity == completeness and len(frame.points) == 0
        invalid.append(frame)
    stats = detection.summarize_detections([empty, None, empty, *invalid, points_at([40.])])
    assert stats['expected_frames'] == 6 and stats['unknown_frames'] == 3
    assert stats['empty_frames'] == 2 and stats['support_fraction'] is None
    assert stats['support_fraction_known'] == pytest.approx(1/3)
    assert stats['longest_known_miss_run_frames'] == 1
    assert detection.summarize_detections([])['support_fraction'] is None
    recorder = recording.Recorder(tmp_path / 'detections')
    recorder.append(empty, 0.)
    recorder.append(replace(points_at([40.]), sequence=2), 2/30)
    recorder.close()
    replay = recording.Replay(recorder.path)
    assert replay.frame(0).validity == 'VALID_EMPTY' and replay.frame(1).sequence == 2
    assert replay.frame(0).metadata() == empty.metadata()
    assert replay.frame(1).model_diagnostics is not None


def test_diagnostics_at_full_report_capacity_survive_channel_recording_and_snapshot(tmp_path):
    frame = points_at(np.linspace(100., 300., 4096), cfg=config(merge_range_m=.01))
    assert len(frame.points) == 4096
    assert len(schema.json_bytes(frame.metadata())) < schema.MAX_METADATA - 128
    assert not frame.model_diagnostics.values.flags.writeable
    metadata = frame.metadata()
    saved = metadata['model_diagnostics']
    assert saved['units']['model_rcs_m2'] == 'm^2' and saved['units']['model_snr_linear'] == '1'
    assert saved['model_id'] == detection.MODEL_ID
    parser = channel.PacketReader()
    parser.feed(channel.encode_frame(frame))
    received = parser.pop()
    recorder = recording.Recorder(tmp_path / 'full')
    recorder.append(frame, 0.)
    recorder.close()
    for restored in (received, frame.snapshot(), recording.Replay(recorder.path).frame(0)):
        assert restored.metadata() == metadata
        assert restored.model_diagnostics.values.tobytes() == frame.model_diagnostics.values.tobytes()
        assert restored.points.tobytes() == frame.points.tobytes()
    metadata['model_diagnostics']['units']['model_power_w'] = 'vendor mag'
    with pytest.raises(ValueError, match='metadata'):
        schema.frame_from_metadata(metadata, frame.points, frame.radial_velocity_m_s)


def test_noise_only_search_is_bounded_by_output_capacity_and_saves_truncation():
    frame = points_at([], cfg=config(false_alarms_enabled=True))
    stats = frame.provenance['detection_statistics']
    assert len(frame.points) == 4096
    assert stats['false_alarms_before_output_filters'] == stats['search_cells']
    assert stats['output_truncated'] == stats['search_cells'] - 4096
    assert frame.model_diagnostics.values['false_alarm'].all()


def test_model_assumptions_parameters_seeds_calibration_and_roi_statistics_are_saved():
    cfg = config(calibration_status='USER_CALIBRATED', calibration_reference='fixture:link-budget',
                 parameter_basis='Synthetic fixture calibration only.')
    frame = simulated(cfg=cfg, sampling_seed=55, noise_seed=77)
    model = frame.provenance['observation_model']
    assert model['parameters']['calibration_status'] == 'USER_CALIBRATED'
    assert model['parameters']['calibration_reference'] == 'fixture:link-budget'
    assert model['noise_seed'] == 77 and frame.provenance['seed'] == 55
    assert model['response_kind'] == 'REFERENCE_AREA_SURFACE'
    assert any('incoherent' in text for text in model['assumptions'])
    assert any('near-field' in text for text in model['assumptions'])
    mask = np.arange(len(frame.points)) % 2 == 0
    original = frame.metadata()
    selected = detection.detection_statistics(frame, point_mask=mask)
    assert selected['reported_points'] == mask.sum()
    assert selected['frame_model_statistics'] == frame.provenance['detection_statistics']
    selected['frame_model_statistics']['occupied_cells'] = -1
    assert frame.metadata() == original
    with pytest.raises(ValueError, match='boolean'):
        detection.detection_statistics(frame, point_mask=np.ones(len(frame.points), dtype=int))


@pytest.mark.parametrize('kwargs', [dict(noise_bandwidth_hz=0), dict(range_std_m=-1),
    dict(miss_probability=1.1), dict(merge_range_m=0), dict(merge_azimuth_deg=1e-12),
    dict(antenna_pattern='FOV'), dict(false_alarm_probability_per_cell=float('nan')),
    dict(false_alarms_enabled=1), dict(calibration_status='USER_CALIBRATED')])
def test_invalid_detection_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        detection.DetectionConfig(**kwargs)


@pytest.mark.parametrize('limit', [0, 4097, True])
def test_invalid_report_limit_is_rejected(limit):
    with pytest.raises(ValueError, match='report limit'):
        points_at([40.], output_point_limit=limit)
