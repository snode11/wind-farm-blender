"""Strict allowlisted video handoff and streaming RGB decoding."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import subprocess
import numpy as np

from wfrl.camera_video.media import sha256

CALIBRATION_KEYS = {'camera_id', 'frame_id', 'width', 'height', 'K',
                    'T_camera_cv_from_world', 'pixel_convention', 'distortion'}
MOTION_KEYS = {'frame_id', 'sim_time_s', 'T_world_from_blade_root', 'source', 'units'}
DATASET_KEYS = {'schema', 'target', 'model', 'units', 'videos', 'files',
                'sampling', 'input_policy', 'reference_state', 'imaging'}
SAMPLING_KEYS = {'start_s', 'end_s_exclusive', 'fps', 'frame_count_per_camera', 'simulation_time', 'pts'}
POLICY = {'calibration': 'simulation ideal pinhole',
          'rigid_motion': 'simulation ideal B1 blade-root rigid pose',
          'exact_flexible_deformation': False, 'truth_surface': False,
          'truth_masks_or_correspondences': False, 'decoded_rgb': 'delivered MP4 only'}
IMAGING_KEYS = {'shading', 'studio_light', 'scene_lights', 'scene_world', 'studio_rotation',
                'studio_intensity', 'studio_background_alpha', 'studio_background_blur',
                'studio_view_rotation', 'color_management', 'display_device', 'image_encoding'}
COLOR_KEYS = {'view_transform', 'look', 'exposure', 'gamma', 'use_curve_mapping'}
REFERENCE_KEYS = {'t_ref_sim_time_s', 'frame_id', 'selection_basis', 'fit_frame_ids', 'heldout_frame_ids'}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def probe_video(path):
    args = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_streams',
            '-show_frames', '-show_entries',
            'stream=width,height,avg_frame_rate,time_base,codec_name:frame=pts,best_effort_timestamp_time',
            '-of', 'json', str(path)]
    return json.loads(subprocess.run(args, check=True, capture_output=True, text=True).stdout)


def iter_rgb(path, width, height):
    """Yield one actual MP4 decoded RGB8 frame; never allocate a full video."""
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-an',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1']
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = int(width) * int(height) * 3
    try:
        while True:
            chunks, remaining = [], size
            while remaining:
                chunk = proc.stdout.read(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if not chunks:
                break
            if remaining:
                raise ValueError('Truncated decoded RGB frame')
            yield np.frombuffer(b''.join(chunks), dtype=np.uint8).reshape(height, width, 3).copy()
        stderr = proc.stderr.read().decode(errors='replace')
        if proc.wait() != 0:
            raise ValueError(f'FFmpeg decode failed: {stderr}')
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def _matrix(value, shape):
    result = np.asarray(value, dtype=float)
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f'Invalid matrix, expected {shape}')
    return result


def load_bundle(directory):
    """Reject unknown fields and resolve only files inside algorithm-input."""
    root = Path(directory).resolve()
    manifest_path = root / 'dataset.json'
    if manifest_path.resolve().parent != root:
        raise ValueError('Dataset manifest resolves outside input directory')
    data = json.loads(manifest_path.read_text())
    if set(data) != DATASET_KEYS or data['schema'] != 'nrel-single-blade-video.v1':
        raise ValueError('Unsupported dataset schema or unknown dataset fields')
    if data['target'] != 'T1/B1' or data['units'] != {'length': 'm', 'time': 's', 'image': 'px'}:
        raise ValueError('Target or units mismatch')
    if data['model'] != 'NREL 5MW' or data['videos'] != {c: f'{c}.mp4' for c in ('C1', 'C2', 'C3')}:
        raise ValueError('Unexpected model or video paths')
    if set(data['sampling']) != SAMPLING_KEYS or data['input_policy'] != POLICY:
        raise ValueError('Unexpected sampling or input policy fields')
    if set(data['imaging']) != IMAGING_KEYS or set(data['imaging']['color_management']) != COLOR_KEYS:
        raise ValueError('Unexpected imaging fields')
    if data['reference_state'] is not None and set(data['reference_state']) != REFERENCE_KEYS:
        raise ValueError('Unexpected reference state fields')
    imaging = data['imaging']
    color = imaging['color_management']
    for key in ('shading', 'studio_light', 'display_device', 'image_encoding'):
        if not isinstance(imaging[key], str):
            raise ValueError('Invalid imaging string')
    for key in ('scene_lights', 'scene_world', 'studio_view_rotation'):
        if type(imaging[key]) is not bool:
            raise ValueError('Invalid imaging boolean')
    for value in [imaging[k] for k in ('studio_rotation', 'studio_intensity', 'studio_background_alpha', 'studio_background_blur')] + [color['exposure'], color['gamma']]:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Invalid imaging numeric value')
    if not all(isinstance(color[k], str) for k in ('view_transform', 'look')) or color['use_curve_mapping'] is not False:
        raise ValueError('Unsupported color transform')
    sampling = data['sampling']
    if any(not isinstance(sampling[k], (int, float)) or not math.isfinite(sampling[k])
           for k in ('start_s', 'end_s_exclusive', 'fps', 'frame_count_per_camera')):
        raise ValueError('Nonfinite sampling values')
    if sampling['fps'] not in (15, 20, 25) or sampling['frame_count_per_camera'] != int(sampling['frame_count_per_camera']) or sampling['frame_count_per_camera'] <= 0:
        raise ValueError('Invalid sample count or fps')
    if abs((sampling['end_s_exclusive'] - sampling['start_s']) * sampling['fps'] - sampling['frame_count_per_camera']) > 1e-8:
        raise ValueError('Inconsistent sampling interval')
    ref = data['reference_state']
    if ref is not None:
        fid = ref['frame_id']; n = sampling['frame_count_per_camera']
        if type(fid) is not int or not 0 <= fid < n or not math.isfinite(ref['t_ref_sim_time_s']):
            raise ValueError('Invalid reference frame or time')
        if abs(ref['t_ref_sim_time_s'] - (sampling['start_s'] + (fid + .5) / sampling['fps'])) > 1e-8:
            raise ValueError('Reference state does not map to an actual decoded frame')
        selected = ref['fit_frame_ids'] + ref['heldout_frame_ids']
        if any(type(i) is not int or not 0 <= i < n for i in selected) or len(selected) != len(set(selected)):
            raise ValueError('Invalid, duplicated or overlapping fit and heldout frames')
        if not isinstance(ref['selection_basis'], str):
            raise ValueError('Invalid reference selection basis')
    allowed = {'C1.mp4', 'C2.mp4', 'C3.mp4', 'frame_map.csv',
               'camera_calibration.jsonl', 'motion_observations.csv'}
    if set(data['files']) != allowed:
        raise ValueError('Unexpected files in allowlisted input manifest')
    for name, signature in data['files'].items():
        path = root / name
        if path.resolve().parent != root or sha256(path) != signature:
            raise ValueError(f'Input path or signature mismatch: {name}')
    with (root / 'frame_map.csv').open() as f:
        reader = csv.DictReader(f)
        if set(reader.fieldnames) != {'camera_id', 'frame_id', 'video_pts_s', 'sim_time_s', 'valid'}:
            raise ValueError('Unexpected frame map fields')
        rows = list(reader)
    calibrations = [json.loads(s) for s in (root / 'camera_calibration.jsonl').read_text().splitlines()]
    with (root / 'motion_observations.csv').open() as f:
        motion = list(csv.DictReader(f))
    for m in motion:
        m['frame_id'] = int(m['frame_id'])
        m['sim_time_s'] = float(m['sim_time_s'])
        m['T_world_from_blade_root'] = json.loads(m['T_world_from_blade_root'])
    if any(set(c) != CALIBRATION_KEYS for c in calibrations) or any(set(m) != MOTION_KEYS for m in motion):
        raise ValueError('Unknown calibration or motion field')
    lookup = {(c['camera_id'], int(c['frame_id'])): c for c in calibrations}
    moves = {int(m['frame_id']): m for m in motion}
    count = int(data['sampling']['frame_count_per_camera'])
    fps = int(data['sampling']['fps'])
    if len(lookup) != 3 * count or len(moves) != count or len(rows) != 3 * count or len(calibrations) != 3*count or len(motion) != count:
        raise ValueError('Incomplete or duplicate frame data')
    for camera in ('C1', 'C2', 'C3'):
        selected = [r for r in rows if r['camera_id'] == camera]
        if [int(r['frame_id']) for r in selected] != list(range(count)):
            raise ValueError(f'Frame sequence mismatch: {camera}')
        for row in selected:
            i = int(row['frame_id']); cal = lookup[(camera, i)]
            if not all(math.isfinite(float(v)) for v in (row['video_pts_s'], row['sim_time_s'], moves[i]['sim_time_s'])):
                raise ValueError('Nonfinite timestamp')
            if row['valid'] != '1' or abs(float(row['video_pts_s']) - i / fps) > 1e-8:
                raise ValueError('Invalid frame or presentation time')
            expected = data['sampling']['start_s'] + (i + .5) / fps
            if abs(float(row['sim_time_s']) - expected) > 1e-8 or abs(moves[i]['sim_time_s'] - expected) > 1e-8:
                raise ValueError('Simulation time mismatch')
            K = _matrix(cal['K'], (3, 3))
            if K[0, 0] <= 0 or K[1, 1] <= 0 or not np.allclose(K[2], [0, 0, 1]):
                raise ValueError('Invalid pinhole intrinsics')
            for transform in (cal['T_camera_cv_from_world'], moves[i]['T_world_from_blade_root']):
                T = _matrix(transform, (4, 4)); R = T[:3, :3]
                if not np.allclose(T[3], [0, 0, 0, 1]) or not np.allclose(R.T @ R, np.eye(3), atol=1e-3) or not np.isclose(np.linalg.det(R), 1, atol=1e-3):
                    raise ValueError('Expected a proper rigid pose; deformation is not allowed')
            if cal['width'] != 1920 or cal['height'] != 1080 or moves[i]['units'] != 'm':
                raise ValueError('Unsupported image dimensions or motion units')
    return data, lookup, moves


def decode_inspection(directory, output, selected_ids=None):
    """Fully decode signed MP4s, save only selected RGB inspection thumbnails."""
    from PIL import Image, ImageDraw
    root, out = Path(directory), Path(output)
    data, cal, motion = load_bundle(root)
    out.mkdir(parents=True, exist_ok=True)
    n = data['sampling']['frame_count_per_camera']; fps = data['sampling']['fps']
    ids = set(selected_ids if selected_ids is not None else range(0, n, max(1, fps // 2)))
    report = {'input_policy': data['input_policy'], 'videos': {}, 'saved_frame_ids': sorted(ids)}
    strips = []
    for camera in ('C1', 'C2', 'C3'):
        path = root / f'{camera}.mp4'; info = probe_video(path)
        stream, frames = info['streams'][0], info['frames']
        if any((cal[(camera, i)]['width'], cal[(camera, i)]['height']) != (stream['width'], stream['height']) for i in range(n)):
            raise ValueError('Video and calibration dimensions differ')
        if len(frames) != n or any(abs(float(f['best_effort_timestamp_time']) - i / fps) > 1e-6 for i, f in enumerate(frames)):
            raise ValueError(f'Actual decoded PTS mismatch: {camera}')
        count = 0; thumbnails = []
        for i, rgb in enumerate(iter_rgb(path, stream['width'], stream['height'])):
            count += 1
            if i in ids:
                im = Image.fromarray(rgb)
                im.thumbnail((480, 270)); thumbnails.append((i, im.copy()))
                im.save(out / f'{camera}_{i:06d}.jpg', quality=92)
        if count != n:
            raise ValueError(f'Complete decode frame count mismatch: {camera}')
        report['videos'][camera] = {'sha256': sha256(path), 'frame_count': count,
                                   'pts_s': [float(f['best_effort_timestamp_time']) for f in frames],
                                   'width': stream['width'], 'height': stream['height']}
        cols = 5; tile_w, tile_h = 480, 294
        canvas = Image.new('RGB', (cols * tile_w, ((len(thumbnails) + cols - 1) // cols) * tile_h), 'white')
        draw = ImageDraw.Draw(canvas)
        for j, (i, im) in enumerate(thumbnails):
            x, y = j % cols * tile_w, j // cols * tile_h
            canvas.paste(im, (x, y)); draw.text((x+6, y+272), f'{camera} n={i} t={motion[i]["sim_time_s"]:.3f}s', fill='black')
        canvas.save(out / f'{camera}_contact.jpg', quality=90)
    report['decoder'] = subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    write_json(out / 'decode_manifest.json', report)
    return report
