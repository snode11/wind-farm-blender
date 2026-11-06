"""Shared geometric visibility, phase-center motion and missing samples."""
from dataclasses import replace
import numpy as np
import pytest
from wfrl.radar_x100 import detection, sampling, simulation


def scene(t, *, rotate=False, lever_arm=False):
    angle = .2 * t if rotate else 0.
    c, s = np.cos(angle), np.sin(angle)
    pose = np.eye(4)
    pose[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    pose[:3, 3] = pose[:3, :3] @ [0., 2., 0.] if lever_arm else [0., 2., 0.]
    return simulation.SceneSample(t, 'static-scene', 'triangle',
        np.array([[20., 1., -1.], [20., 3., -1.], [20., 2., 1.]]),
        np.array([[0, 1, 2]]), pose, 'one static surface')


def kwargs():
    return dict(sensor_id='radar', session_id='test', sequence=1, interval_s=(0., 2.))


def cfg():
    return detection.DetectionConfig(antenna_pattern='ISOTROPIC', false_alarm_probability_per_cell=1.,
        false_alarms_enabled=False, merge_range_m=400., merge_azimuth_deg=120., merge_elevation_deg=44.,
        merge_velocity_m_s=200., range_std_m=0., azimuth_std_deg=0., elevation_std_deg=0.,
        radial_velocity_std_m_s=0.)


@pytest.mark.parametrize('rotate', [False, True])
def test_static_scene_orientation_about_fixed_phase_center_cannot_create_radial_velocity(rotate):
    source = lambda t: scene(t, rotate=rotate)
    geometry = simulation.simulate(source, 1., candidate_budget=128, **kwargs())
    frame = simulation.simulate_detections(source, 1., integration_budget=128, config=cfg(), **kwargs())
    assert len(frame.points) == 1 and len(geometry.points) == 128
    np.testing.assert_array_equal(geometry.radial_velocity_m_s, np.zeros(128))
    np.testing.assert_array_equal(frame.radial_velocity_m_s, [0.])
    assert not frame.points['ms'].any() and not geometry.points['ms'].any()


def test_rotating_lever_arm_contributes_phase_center_velocity_exactly_once():
    source = lambda t: scene(t, rotate=True, lever_arm=True)
    geometry = simulation.simulate(source, 1., candidate_budget=128, **kwargs())
    current = source(1.)
    matrix = current.world_from_sensor
    los = geometry.xyz @ matrix[:3, :3].T
    los = los / np.linalg.norm(los, axis=1)[:, None]
    phase_velocity = np.array([-.4 * np.cos(.2), -.4 * np.sin(.2), 0.])
    expected = -los @ phase_velocity
    np.testing.assert_allclose(geometry.radial_velocity_m_s, expected, atol=1e-7)
    frame = simulation.simulate_detections(source, 1., integration_budget=128, config=cfg(), **kwargs())
    plan = sampling.make_plan(source(0.), 128)
    world = plan.points(current)
    ranges = np.linalg.norm(world - matrix[:3, 3], axis=1)
    rcs = detection.surface_rcs(plan.area_weights_m2,
        np.abs((world[:, 0] - matrix[0, 3]) / ranges), config=cfg())
    power = detection.received_power_w(ranges, rcs, config=cfg())
    assert frame.radial_velocity_m_s[0] == pytest.approx(np.average(expected, weights=power), abs=1e-7)


def test_material_subset_callback_matches_full_scene_integration_and_velocity():
    source = lambda t: scene(t, rotate=True, lever_arm=True)
    calls = []
    def material(t, ids, *, barycentric):
        sample = source(t)
        calls.append(len(ids))
        return simulation.MaterialSample(t, sample.source_id, sample.geometry_id,
            np.einsum('nij,ni->nj', sample.vertices[sample.triangles[ids]], barycentric),
            sample.world_from_sensor)
    full = simulation.simulate_detections(source, 1., integration_budget=8192, config=cfg(), **kwargs())
    subset = simulation.simulate_detections(source, 1., integration_budget=8192, config=cfg(),
                                           material_at=material, **kwargs())
    assert calls == [8192, 8192]
    assert full.points.tobytes() == subset.points.tobytes()
    assert full.radial_velocity_m_s.tobytes() == subset.radial_velocity_m_s.tobytes()
    assert full.model_diagnostics.values.tobytes() == subset.model_diagnostics.values.tobytes()


@pytest.mark.parametrize('missing_time', [0., 1., .999, 1.001])
def test_missing_reference_current_or_velocity_sample_never_becomes_a_valid_empty_frame(missing_time):
    source = lambda t: None if t == missing_time else scene(t)
    assert simulation.simulate(source, 1., **kwargs()) is None
    assert simulation.simulate_detections(source, 1., config=cfg(), **kwargs()) is None


def test_all_out_of_fov_retains_a_valid_empty_frame_with_model_metadata():
    def source(t):
        sample = scene(t)
        vertices = sample.vertices.copy()
        vertices[:, 0] *= -1
        return replace(sample, vertices=vertices)
    geometry = simulation.simulate(source, 1., **kwargs())
    frame = simulation.simulate_detections(source, 1., integration_budget=512, config=cfg(), **kwargs())
    assert geometry.validity == frame.validity == 'VALID_EMPTY'
    assert frame.provenance['observation_model']['id'] == detection.MODEL_ID
    assert frame.provenance['detection_statistics']['total_model_power_w'] == 0


@pytest.mark.parametrize('bad_time', [0., 1., .999, 1.001])
def test_scene_time_mismatch_is_rejected_instead_of_relabeling_saved_motion(bad_time):
    def source(t):
        sample = scene(t)
        return replace(sample, time_s=t + .01) if t == bad_time else sample
    with pytest.raises(ValueError, match='time'):
        simulation.simulate_detections(source, 1., config=cfg(), **kwargs())
