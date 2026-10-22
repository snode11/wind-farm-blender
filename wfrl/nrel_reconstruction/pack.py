"""Generation-side adapter. Not imported by the reconstruction process."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import os
import shutil
import subprocess
import numpy as np

from wfrl.camera_video.media import encode_video, sha256
from .dataset import write_json, load_bundle, IMAGING_KEYS, COLOR_KEYS


def center_schedule(start_s, duration_s, fps):
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start_s, duration_s, fps)):
        raise ValueError('Sampling values must be finite numbers')
    if fps not in (15, 20, 25):
        raise ValueError('Adapter currently supports the encoder contract 15/20/25 fps')
    n = duration_s * fps
    if n <= 0 or abs(n - round(n)) > 1e-8:
        raise ValueError('duration*fps must be a positive integer')
    return [start_s + (i + .5) / fps for i in range(round(n))]


def storage_report(root, capture, fps):
    root, capture = Path(root), Path(capture)
    png = {}
    for camera in ('C1', 'C2', 'C3'):
        paths = list((capture / camera).glob('*.png'))
        total = sum(p.stat().st_size for p in paths)
        png[camera] = {'count': len(paths), 'bytes': total,
                       'mean_bytes_per_frame': total / len(paths) if paths else None}
    videos = {p.name: p.stat().st_size for p in (root / 'algorithm-input').glob('*.mp4')}
    # Unique inode accounting avoids counting the immutable shared frames twice.
    seen = set(); total = 0; temporary = 0
    for p in root.rglob('*'):
        if not p.is_file() or p.is_symlink():
            continue
        st = p.stat(); key = (st.st_dev, st.st_ino)
        if key in seen:
            continue
        seen.add(key); total += st.st_size
        if p.name.endswith(('.tmp', '.writing', '.encoding.mp4')):
            temporary += st.st_size
    total_png = sum(v['bytes'] for v in png.values())
    count = sum(v['count'] for v in png.values())
    return {'png': png, 'png_total_bytes': total_png,
            'mp4': videos, 'mp4_total_bytes': sum(videos.values()),
            'unique_regular_file_bytes_in_run': total, 'temporary_bytes': temporary,
            'mean_png_bytes': total_png / count if count else None,
            'estimate_5s_png_bytes_at_same_fps': total_png / count * 3 * fps * 5 if count else None,
            'estimate_10s_png_bytes_at_same_fps': total_png / count * 3 * fps * 10 if count else None,
            'estimate_status': 'conditional extrapolation from current clip, not measured formal capture',
            'source_png_policy': 'one immutable set; master_frames directory is a symlink, no copying',
            'cache_policy': 'only selected decoded thumbnails/masks; no full float video or feature cache'}


def pack_capture(capture_dir, output_dir, start_s, duration_s, fps=20):
    capture, out = Path(capture_dir).resolve(), Path(output_dir).resolve()
    manifest = json.loads((capture / 'manifest.json').read_text())
    if manifest['status'] != 'complete':
        raise ValueError('Capture must finish its full transaction')
    times = center_schedule(start_s, duration_s, fps); n = len(times)
    records = [json.loads(s) for s in (capture / 'frames.jsonl').read_text().splitlines()]
    motion = [json.loads(s) for s in (capture / 'motion_internal.jsonl').read_text().splitlines()]
    if len(records) != n * 3 or len(motion) != n:
        raise ValueError('Missing camera or motion records')
    by_key = {(r['camera_id'], int(r['sample_index'])): r for r in records}
    moves = {int(m['sample_index']): m for m in motion}
    if len(by_key) != n * 3 or len(moves) != n:
        raise ValueError('Duplicate camera or motion record')
    for i, t in enumerate(times):
        group = [by_key[(c, i)] for c in ('C1', 'C2', 'C3')]
        if any(not math.isfinite(r['time_s']) or abs(r['time_s'] - t) > 1e-8 or r['capture_status'] != 'complete' for r in group):
            raise ValueError('Capture time differs from center sampling')
        if len({r['simulation_state_hash'] for r in group}) != 1:
            raise ValueError('Cameras were captured from different scene states')
        if not math.isfinite(moves[i]['sim_time_s']) or abs(moves[i]['sim_time_s'] - t) > 1e-8 or moves[i]['includes_deformation']:
            raise ValueError('Motion is asynchronous or contains forbidden deformation')
    alg = out / 'algorithm-input'
    alg.mkdir(parents=True, exist_ok=False)
    frame_rows, cal_rows, reports = [], [], {}
    for camera in ('C1', 'C2', 'C3'):
        adapter = out / 'encoding-adapter' / camera
        adapter.mkdir(parents=True, exist_ok=False)
        (adapter / 'master_frames').symlink_to(capture / camera, target_is_directory=True)
        with (adapter / 'frames.csv').open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['frame_id', 'video_pts'])
            writer.writeheader(); writer.writerows({'frame_id': i, 'video_pts': i} for i in range(n))
        report = encode_video(adapter, fps, pattern='frame_%06d.png', crf=18, preset='medium')
        # Moving the MP4 retains a single file, rather than a second video copy.
        (adapter / 'video.mp4').rename(alg / f'{camera}.mp4')
        reports[camera] = report
        for i, t in enumerate(times):
            r = by_key[(camera, i)]
            if (r['image_width'], r['image_height']) != (report['width'], report['height']):
                raise ValueError('Calibration and encoded dimensions mismatch')
            frame_rows.append({'camera_id': camera, 'frame_id': i, 'video_pts_s': i/fps, 'sim_time_s': t, 'valid': 1})
            cal_rows.append({'camera_id': camera, 'frame_id': i, 'width': r['image_width'],
                             'height': r['image_height'], 'K': r['K'],
                             'T_camera_cv_from_world': r['T_camera_cv_from_world'],
                             'pixel_convention': 'top-left pixel centre (0,0); boundaries -.5,W/H-.5',
                             'distortion': 'ideal undistorted pinhole'})
    with (alg / 'frame_map.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(frame_rows[0])); writer.writeheader(); writer.writerows(frame_rows)
    (alg / 'camera_calibration.jsonl').write_text(''.join(json.dumps(r, allow_nan=False)+'\n' for r in cal_rows))
    with (alg / 'motion_observations.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['frame_id', 'sim_time_s', 'T_world_from_blade_root', 'source', 'units'])
        writer.writeheader()
        for i, t in enumerate(times):
            writer.writerow({'frame_id': i, 'sim_time_s': t,
                             'T_world_from_blade_root': json.dumps(moves[i]['T_world_from_blade_root']),
                             'source': 'simulation ideal rigid running observation; no deformation', 'units': 'm'})
    names = ['C1.mp4', 'C2.mp4', 'C3.mp4', 'frame_map.csv', 'camera_calibration.jsonl', 'motion_observations.csv']
    data = {'schema': 'nrel-single-blade-video.v1', 'target': 'T1/B1', 'model': 'NREL 5MW',
            'units': {'length': 'm', 'time': 's', 'image': 'px'},
            'videos': {c: f'{c}.mp4' for c in ('C1', 'C2', 'C3')},
            'files': {name: sha256(alg / name) for name in names},
            'sampling': {'start_s': start_s, 'end_s_exclusive': start_s+duration_s, 'fps': fps,
                         'frame_count_per_camera': n, 'simulation_time': 'start+(n+.5)/fps', 'pts': 'n/fps'},
            'input_policy': {'calibration': 'simulation ideal pinhole',
                             'rigid_motion': 'simulation ideal B1 blade-root rigid pose',
                             'exact_flexible_deformation': False, 'truth_surface': False,
                             'truth_masks_or_correspondences': False, 'decoded_rgb': 'delivered MP4 only'},
            'reference_state': None, 'imaging': {key: manifest['render_profile'][key] for key in IMAGING_KEYS}}
    data['imaging']['color_management'] = {key: manifest['render_profile']['color_management'][key] for key in COLOR_KEYS}
    write_json(alg / 'dataset.json', data)
    load_bundle(alg)
    write_json(out / 'video_validation.json', reports)
    report = storage_report(out, capture, fps)
    write_json(out / 'storage_report.json', report)
    write_json(out / 'manifest.json', {'schema': 'nrel-video-single-blade-run.v1', 'status': 'VIDEO_ENCODED_PENDING_RGB_REVIEW',
                                     'source_capture': str(capture), 'target': 'T1/B1',
                                     'dataset_sha256': sha256(alg / 'dataset.json'), 'source_png_copied': False})
    return report
