"""Reject incomplete media and inconsistent camera labels before sealing."""
import csv
import io
import json

import numpy as np
import pytest

from test_camera_video_package import delivery, source, reseal_sidecar_hashes
from wfrl.camera_video import data, media, package, stream


@pytest.mark.parametrize('payload,size,length_size', [
    (b'\x00\x00\x00\x02\x65x', 12, 4),
    (b'', 6, 4),
    (b'', 0, 4),
    (b'\x00\x00\x00\x02\x65x\x00', 7, 4),
    (b'\x00\x00\x00\x02\x65x', 6, 0),
])
def test_avcc_short_or_empty_sample_is_not_a_complete_frame(payload, size, length_size):
    with pytest.raises(ValueError, match='AVCC'):
        media.read_sample(io.BytesIO(payload), {'pos': 0, 'size': size}, length_size)


def test_access_unit_rejects_replaced_unfinished_fragment():
    packets = list(stream.packetize([b'\x65' + b'x' * 30], 100, 7, 10, mtu=12))
    # A second FU start replaces the first fragment without a sequence gap.
    malformed = [packets[0], packets[1][:12] + packets[0][12:], *packets[2:]]
    assembler = stream.AccessUnits()
    assert not any(assembler.feed(packet) for packet in malformed)


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_event_audit_rejects_nonfinite_reference(value):
    source = data.SourceGeometry.__new__(data.SourceGeometry)
    source.measurements = [
        {'center_truth_m': 1.0, 'time_s': 0, 'blade_id': 1},
        {'center_truth_m': value, 'time_s': 1, 'blade_id': 1},
    ]
    source.sample = lambda _: {'clearance_m': [1.0, 1.0, 1.0]}
    with pytest.raises(ValueError, match='finite'):
        source.audit_events()


def test_seal_rejects_nan_render_center(delivery):
    path = delivery / 'checks/render_frames.json'
    records = json.loads(path.read_text())
    records['0']['sim_time_s'] = float('nan')
    path.write_text(json.dumps(records))
    with pytest.raises(ValueError, match='time'):
        package.seal_package(delivery)
    assert not (delivery / 'manifest.json').exists()


def test_seal_rejects_fractional_fps_instead_of_truncating(delivery):
    for name in ('data_manifest.json', 'render_manifest.json'):
        path = delivery / name
        value = json.loads(path.read_text())
        (value['time_contract'] if name == 'data_manifest.json' else value)['fps'] = 20.5
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='frame rates'):
        package.seal_package(delivery)


def test_seal_rejects_video_calibration_resolution_mismatch(delivery):
    camera_path = delivery / 'camera.json'
    camera = json.loads(camera_path.read_text())
    camera['resolution'] = [640, 480]
    camera_path.write_text(json.dumps(camera))
    render_path = delivery / 'render_manifest.json'
    render = json.loads(render_path.read_text())
    render['camera'] = camera
    render_path.write_text(json.dumps(render))
    with pytest.raises(ValueError, match='resolution'):
        package.seal_package(delivery)


@pytest.mark.parametrize('corruption', ['infinite_camera', 'geometry_frame_id', 'broadcast_time',
                                         'valid_nan_rpm', 'invalid_zero_clearance', 'clearance_mask'])
def test_seal_rejects_rehashed_inconsistent_geometry_or_validity(delivery, monkeypatch, corruption):
    geometry_path = delivery / 'frame_geometry.npz'
    with np.load(geometry_path) as archive:
        geometry = dict(archive)
    rows_path = delivery / 'frames.csv'
    with rows_path.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    if corruption == 'infinite_camera':
        geometry['camera_world_transform'][0, 0, 0] = np.inf
        np.save(delivery / 'camera_world.npy', geometry['camera_world_transform'])
    elif corruption == 'geometry_frame_id':
        geometry['frame_id'][0] = 9
    elif corruption == 'broadcast_time':
        geometry['sim_time_s'] = geometry['sim_time_s'][None, :]
    elif corruption == 'valid_nan_rpm':
        rows[0]['rpm'] = 'nan'
    elif corruption == 'invalid_zero_clearance':
        for row in rows:
            for blade in (1, 2, 3):
                if row[f'clearance_b{blade}_valid'] == 'False':
                    row[f'clearance_b{blade}_m'] = '0'
    else:
        geometry['clearance_valid'][0, 0] = not geometry['clearance_valid'][0, 0]
    np.savez_compressed(geometry_path, **geometry)
    with rows_path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    reseal_sidecar_hashes(delivery)
    report = json.loads((delivery / 'checks/encoding.json').read_text())
    monkeypatch.setattr(package, 'validate_video', lambda *a, **kw: report)
    with pytest.raises(ValueError):
        package.seal_package(delivery)
