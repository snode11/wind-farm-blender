"""Seal and verify a camera delivery without upgrading its source validity."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .media import read_frames, sha256, validate_video


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def _build_manifest(directory, *, ffprobe='ffprobe'):
    """Hash a complete file delivery. Streaming/optical acceptance stays separate."""
    root = Path(directory)
    data = read_json(root / 'data_manifest.json')
    render = read_json(root / 'render_manifest.json')
    camera = read_json(root / 'camera.json')
    rows = read_frames(root / 'frames.csv')
    fps = int(data['time_contract']['fps'])
    if not render.get('complete') or render.get('status') != 'complete':
        raise ValueError('Rendering is not complete')
    if data['source_status'] != 'REVIEW_ONLY' or render['source_status'] != 'REVIEW_ONLY':
        raise ValueError('Source review status must be retained')
    if camera != render['camera']:
        raise ValueError('Camera settings differ from the rendered configuration')
    contract = data['time_contract']
    if len(rows) != contract['frame_count']:
        raise ValueError('CSV count differs from time contract')
    if data['turbine_id'] != camera['turbine_id']:
        raise ValueError('Data and camera target turbines differ')
    from .data import frame_schedule
    schedule = frame_schedule(contract['start_s'], contract['end_s'], fps, contract['exposure_s'])
    if len(schedule) != len(rows):
        raise ValueError('Duration differs from CSV frame count')
    for row, expected in zip(rows, schedule):
        if row['turbine_id'] != data['turbine_id']:
            raise ValueError('CSV target turbine mismatch')
        for field in ('video_time_s', 'sim_time_s', 'exposure_start_s', 'exposure_end_s'):
            if not math.isfinite(float(row[field])) or abs(float(row[field]) - expected[field]) > 1e-9:
                raise ValueError(f'CSV time contract mismatch: {field}')
    for key in ('fps', 'start_s', 'end_s', 'frame_count', 'exposure_s'):
        if abs(float(contract[key]) - float(render[key])) > 1e-9:
            raise ValueError(f'Data/render time contract mismatch: {key}')
    required_sources = {'manifest.json', 'data.json', 'geometry.npz', 'tower-motion.npz', 'reference-surfaces.npz'}
    if not required_sources.issubset(data['source_sha256']) or data['source_sha256'] != render['source_sha256']:
        raise ValueError('Data/render source digest set mismatch')
    for name, digest in data['files'].items():
        if sha256(root / name) != digest:
            raise ValueError(f'Data export integrity mismatch: {name}')
    import numpy as np
    rendered_camera = np.load(root / 'camera_world.npy', allow_pickle=False)
    with np.load(root / 'frame_geometry.npz', allow_pickle=False) as geometry:
        expected_shape = (len(rows), 4, 4)
        if rendered_camera.shape != expected_shape or geometry['camera_world_transform'].shape != expected_shape:
            raise ValueError('Camera world transform frame count mismatch')
        if not geometry['camera_transform_valid'].all() or not np.allclose(
                rendered_camera, geometry['camera_world_transform'], atol=1e-4, rtol=0):
            raise ValueError('Rendered camera and label camera world transforms differ')
        if not np.allclose(geometry['sim_time_s'], [float(r['sim_time_s']) for r in rows], atol=1e-10, rtol=0):
            raise ValueError('Geometry/CSV simulation time mismatch')
    verification = validate_video(root / 'video.mp4', root / 'frames.csv', fps, ffprobe=ffprobe)
    encoding = read_json(root / 'checks' / 'encoding.json')
    if encoding['video_sha256'] != verification['video_sha256']:
        raise ValueError('Video changed after encoding acceptance')
    if encoding['frames_csv_sha256'] != verification['frames_csv_sha256']:
        raise ValueError('Frame labels changed after encoding acceptance')
    if (verification['width'], verification['height']) != (render['width'], render['height']):
        raise ValueError('Encoded video dimensions differ from renderer configuration')
    master = root / 'master_frames'
    master_hashes = {p.name: sha256(p) for p in sorted(master.glob('*.png'))}
    if len(master_hashes) != len(rows) or master_hashes != encoding['master_sha256']:
        raise ValueError('Lossless masters changed after encoding')
    records = read_json(root / 'checks' / 'render_frames.json')
    if {f'{int(i):06d}.png': record['sha256'] for i, record in records.items()} != master_hashes:
        raise ValueError('Master frames differ from rendering records')
    for row in rows:
        record = records[str(row['frame_id'])]
        if abs(record['sim_time_s'] - float(row['sim_time_s'])) > 1e-9:
            raise ValueError('Render and CSV center time mismatch')
        shutter = record['exposure_samples_s']
        center, exposure, count = float(row['sim_time_s']), contract['exposure_s'], render['exposure_samples']
        expected_shutter = [center + ((i + .5) / count - .5) * exposure for i in range(count)]
        if len(shutter) != count or not np.allclose(shutter, expected_shutter, atol=1e-9, rtol=0):
            raise ValueError('Rendered exposure times differ from time contract')
    files = {}
    for name in ('data_manifest.json', 'render_manifest.json', 'camera.json',
                 'frames.csv', 'frame_geometry.npz', 'camera_world.npy', 'video.mp4',
                 'checks/encoding.json', 'checks/render_frames.json', 'checks/render.json'):
        files[name] = sha256(root / name)
    identity = {'files': files, 'master_frames': master_hashes}
    import hashlib
    dataset_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    manifest = {
        'schema': 'wfrl.camera-video.v1', 'dataset_id': dataset_id,
        'status': 'REVIEW_ONLY', 'description': '基于仿真数据的合成相机视频',
        'file_delivery_verified': True,
        'acceptance': {'file_alignment': 'passed', 'optical_convergence': 'see render checks',
                       'rtsp': 'separate session evidence required',
                       'thirty_minute_stability': 'not implied by file verification'},
        'frame_count': len(rows), 'fps': fps, 'time_base': f'1/{fps}',
        'width': verification['width'], 'height': verification['height'],
        'time_contract': data['time_contract'],
        'data': data, 'render': render, 'camera': camera,
        'files': files, 'master_frames': master_hashes,
    }
    return manifest


def seal_package(directory, *, ffprobe='ffprobe'):
    manifest = _build_manifest(directory, ffprobe=ffprobe)
    write_json(Path(directory) / 'manifest.json', manifest)
    return manifest


def verify_package(directory, *, ffprobe='ffprobe'):
    root = Path(directory)
    manifest = read_json(root / 'manifest.json')
    if manifest.get('schema') == 'wfrl.camera-video-loop.v1':
        from .loop import verify_loop
        return verify_loop(root, ffprobe=ffprobe)
    if manifest['schema'] != 'wfrl.camera-video.v1' or manifest['status'] != 'REVIEW_ONLY':
        raise ValueError('Unsupported camera video manifest or source status')
    actual = _build_manifest(root, ffprobe=ffprobe)
    if actual != manifest:
        raise ValueError('Manifest identity, metadata or file integrity mismatch')
    report = {'valid': True, 'dataset_id': actual['dataset_id'], 'frame_count': actual['frame_count'],
              'fps': actual['fps'], 'width': actual['width'], 'height': actual['height'],
              'status': actual['status']}
    write_json(root / 'checks' / 'package-verification.json', report)
    return report
