"""Seal/verify regressions for metadata mixing, independently of media codecs.

The labels use the real source archive; only video probing is replaced because
these tests target the package contract rather than repeat encoder tests.
"""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from wfrl.camera_video import package
from wfrl.camera_video.data import SourceGeometry, export_frame_data, sha256

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def source():
    return SourceGeometry(ROOT / 'blender_frontend/wfrl_blender/assets/mappo')


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False, indent=2) + '\n')


@pytest.fixture
def delivery(source, tmp_path, monkeypatch):
    camera = dict(schema='wfrl.camera-video-camera.v1', turbine_id='T1', camera_mount=np.eye(4).tolist(), exposure_s=.025)
    data = export_frame_data(source, tmp_path, fps=20, exposure_s=.025, start_s=117, end_s=117.1,
                             camera_mount=camera['camera_mount'], camera_config=camera)
    write_json(tmp_path / 'camera.json', camera)
    render = dict(schema='wfrl.camera-video-render.v1', complete=True, status='complete',
                  source_status='REVIEW_ONLY', source_sha256=data['source_sha256'], camera=camera,
                  fps=20, start_s=117., end_s=117.1, frame_count=2, width=32, height=18,
                  exposure_s=.025, exposure_samples=2, spatial_samples=1)
    write_json(tmp_path / 'render_manifest.json', render)
    with np.load(tmp_path / 'frame_geometry.npz') as archive:
        np.save(tmp_path / 'camera_world.npy', archive['camera_world_transform'])
    master = tmp_path / 'master_frames'
    master.mkdir()
    records = {}
    for i in range(2):
        frame = master / f'{i:06d}.png'
        # The bytes have no image meaning; the codec layer is explicitly mocked.
        frame.write_bytes(b'test master ' + bytes([i]))
        center = 117 + (i + .5) / 20
        records[str(i)] = dict(sha256=sha256(frame), sim_time_s=center,
                              exposure_samples_s=[center-.00625, center+.00625])
    write_json(tmp_path / 'checks/render_frames.json', records)
    write_json(tmp_path / 'checks/render.json', {'complete': True})
    (tmp_path / 'video.mp4').write_bytes(b'test encoded media')
    report = dict(frame_count=2, width=32, height=18, video_sha256=sha256(tmp_path / 'video.mp4'),
                  frames_csv_sha256=sha256(tmp_path / 'frames.csv'))
    encoding = dict(report, master_sha256={p.name: sha256(p) for p in master.glob('*.png')})
    write_json(tmp_path / 'checks/encoding.json', encoding)
    monkeypatch.setattr(package, 'validate_video', lambda *a, **kw: dict(report))
    return tmp_path


def reseal_sidecar_hashes(directory):
    """Emulate internally consistent sidecars from a different source/time run."""
    data_path = directory / 'data_manifest.json'
    data = json.loads(data_path.read_text())
    data['files'] = {name: sha256(directory / name) for name in data['files']}
    write_json(data_path, data)
    encoding_path = directory / 'checks/encoding.json'
    encoding = json.loads(encoding_path.read_text())
    encoding['frames_csv_sha256'] = sha256(directory / 'frames.csv')
    write_json(encoding_path, encoding)


def test_complete_matching_delivery_verifies(delivery):
    manifest = package.seal_package(delivery)
    assert len(manifest['dataset_id']) == 64
    assert package.verify_package(delivery)['frame_count'] == 2


@pytest.mark.parametrize('field', ['dataset_id', 'data', 'render', 'camera', 'time_contract'])
def test_verify_rejects_manifest_identity_or_embedded_metadata_changes(delivery, field):
    manifest = package.seal_package(delivery)
    if field == 'dataset_id':
        manifest[field] = '0' * 64
    elif field == 'time_contract':
        manifest[field]['start_s'] = 0
    elif field == 'data':
        manifest[field]['source_status'] = 'ACCEPTED'
    elif field == 'render':
        manifest[field]['start_s'] = 0
    else:
        manifest[field]['turbine_id'] = 'T3'
    write_json(delivery / 'manifest.json', manifest)
    with pytest.raises(ValueError):
        package.verify_package(delivery)


def test_seal_rejects_declared_frame_count_even_when_data_render_agree(delivery):
    for name in ('data_manifest.json', 'render_manifest.json'):
        path = delivery / name
        content = json.loads(path.read_text())
        if name == 'data_manifest.json':
            content['time_contract']['frame_count'] = 3
        else:
            content['frame_count'] = 3
        write_json(path, content)
    with pytest.raises(ValueError):
        package.seal_package(delivery)


def test_seal_rejects_source_digest_subset(delivery):
    path = delivery / 'data_manifest.json'
    data = json.loads(path.read_text())
    data['source_sha256'] = {}
    write_json(path, data)
    with pytest.raises(ValueError):
        package.seal_package(delivery)


@pytest.mark.parametrize('field', ['sim_time_s', 'exposure_samples_s'])
def test_seal_rejects_render_record_from_another_time(delivery, field):
    path = delivery / 'checks/render_frames.json'
    records = json.loads(path.read_text())
    records['0'][field] = 10 if field == 'sim_time_s' else [10., 11.]
    write_json(path, records)
    with pytest.raises(ValueError):
        package.seal_package(delivery)


def test_seal_rejects_csv_wrong_turbine_even_with_refreshed_hash(delivery, monkeypatch):
    path = delivery / 'frames.csv'
    with path.open() as f:
        rows = list(csv.DictReader(f))
    rows[0]['turbine_id'] = 'T2'
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    reseal_sidecar_hashes(delivery)
    report = json.loads((delivery / 'checks/encoding.json').read_text())
    monkeypatch.setattr(package, 'validate_video', lambda *a, **kw: report)
    with pytest.raises(ValueError):
        package.seal_package(delivery)
