"""Numerical observation invariants and recording snapshot/failure boundaries."""
from itertools import permutations
import json

import numpy as np
import pytest

from wfrl.radar_x100 import analysis, detection, ply, recording
from tests.radar_x100.test_detection import template
from tests.radar_x100.test_detection_physics import ideal_resolution_config
from tests.radar_x100.test_recording_integrity import observation


@pytest.mark.parametrize('count', [1, 2])
def test_subnormal_power_preserves_continuous_position_and_doppler_centroids(count):
    config = ideal_resolution_config()
    polar = np.array([[60., .25, .125], [60.05, .30, .15]])[:count]
    speed = np.array([.03, .05])[:count]
    rcs = np.full(count, 1e-308)
    radius, azimuth, elevation = polar.T
    az, el = np.radians(azimuth), np.radians(elevation)
    xyz = np.column_stack((radius*np.cos(el)*np.cos(az),
                           radius*np.cos(el)*np.sin(az), radius*np.sin(el)))
    powers = detection.received_power_w(radius, rcs, config=config,
        azimuth_deg=azimuth, elevation_deg=elevation)
    assert np.all((powers > 0) & (powers < np.finfo(float).tiny))
    weights = powers / powers.max()
    weights /= weights.sum()
    expected_radius, expected_azimuth, expected_elevation = weights @ polar
    az, el = np.radians([expected_azimuth, expected_elevation])
    expected_xyz = expected_radius * np.array([np.cos(el)*np.cos(az),
                                               np.cos(el)*np.sin(az), np.sin(el)])

    frame = detection.generate_detections(xyz, speed, rcs, template=template(), config=config)

    assert len(frame.points) == 1
    assert frame.model_diagnostics.values['integration_samples'].tolist() == [count]
    np.testing.assert_allclose(frame.xyz[0], expected_xyz, rtol=1e-7, atol=1e-8)
    np.testing.assert_allclose(frame.radial_velocity_m_s, [weights @ speed], rtol=1e-7, atol=0.)


@pytest.mark.parametrize('indices', list(permutations(range(3))))
def test_thin_valid_triangle_keeps_interior_projection_and_exterior_edge_distance(indices):
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1e-8, 0.]])
    points = np.array([[.75, 2e-9, 1e-10], [.5, 1e-8, 1e-10]])
    with np.errstate(divide='raise', invalid='raise'):
        distance, nearest, triangles = analysis.closest_reference(points, vertices, [indices])

    np.testing.assert_allclose(distance, [1e-10, np.hypot(5e-9, 1e-10)], rtol=1e-7, atol=1e-16)
    np.testing.assert_allclose(nearest, [[.75, 2e-9, 0.], [.5, 5e-9, 0.]], rtol=0., atol=np.finfo(float).eps)
    np.testing.assert_array_equal(triangles, [0, 0])


@pytest.mark.parametrize('buffer_type', [bytearray, memoryview])
def test_original_ply_recording_uses_one_immutable_snapshot(tmp_path, monkeypatch, buffer_type):
    sink = recording.Recorder(tmp_path / 'buffer-snapshot')
    frame = observation(10.)
    expected = ply.encode_ply(frame.points)
    producer_buffer = bytearray(expected)
    supplied = producer_buffer if buffer_type is bytearray else memoryview(producer_buffer)
    atomic = recording._atomic

    def mutate_before_write(path, raw):
        if path.suffix == '.ply':
            producer_buffer[:] = ply.encode_ply(observation(99.).points)
        return atomic(path, raw)

    monkeypatch.setattr(recording, '_atomic', mutate_before_write)
    try:
        sink.append(frame, 0., original_ply=supplied)
        sink.close()
    finally:
        sink.index.close()

    replay = recording.Replay(sink.path)
    np.testing.assert_array_equal(replay.frame(0).points, frame.points)
    assert (sink.path / replay.rows[0]['file']).read_bytes() == expected
    assert bytes(producer_buffer) != expected


@pytest.mark.parametrize('operation', ['append', 'close'])
@pytest.mark.parametrize('error_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('message', ['injected recording interruption', ''])
def test_recording_interruption_closes_index_and_cannot_publish_complete(
        tmp_path, monkeypatch, operation, error_type, message):
    sink = recording.Recorder(tmp_path / 'interrupted-recording')
    sink.append(observation(10.), 0.)
    failure = error_type(message)
    if operation == 'append':
        atomic = recording._atomic

        def interrupt_payload(path, raw):
            if path.name == '00000001.ply':
                raise failure
            return atomic(path, raw)

        monkeypatch.setattr(recording, '_atomic', interrupt_payload)
        interrupt = lambda: sink.append(observation(20.), .1)
    else:
        manifest = sink._manifest

        def interrupt_manifest(state=None):
            if state == 'COMPLETE':
                raise failure
            return manifest(state)

        monkeypatch.setattr(sink, '_manifest', interrupt_manifest)
        interrupt = sink.close

    try:
        with pytest.raises(error_type) as raised:
            interrupt()
        assert raised.value is failure
        assert sink.index.closed
        assert sink.state == 'FAILED' and sink.rows == 1
        sink.close()
        assert (sink.path / 'INCOMPLETE').exists()
        manifest = json.loads((sink.path / 'manifest.json').read_bytes())
        assert manifest['state'] == 'FAILED' and manifest['frames'] == 1
        assert manifest['error'] == (message or error_type.__name__)
        assert not list(sink.path.rglob('*.partial'))
        with pytest.raises(ValueError, match='incomplete'):
            recording.Replay(sink.path)
    finally:
        sink.index.close()
