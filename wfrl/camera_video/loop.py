"""Lossless encoded repetition with explicit source-cycle labels.

The derived delivery embeds the source video and label evidence. Its lossless
render masters remain in the separately verified base package; no new physics
or new render frames are claimed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

import numpy as np

from .media import h264_samples, read_frames, read_sample, sha256, validate_video
from .package import read_json, write_json

SCHEMA = 'wfrl.camera-video-loop.v1'
SOURCE_FILES = ('manifest.json', 'video.mp4', 'frames.csv', 'frame_geometry.npz', 'camera.json')


def repeated_rows(rows, repeats, fps, source_id):
    for cycle in range(repeats):
        for source_index, row in enumerate(rows):
            index = cycle * len(rows) + source_index
            yield {**row, 'frame_id': str(index), 'video_pts': str(index),
                   'video_time_s': str(index / fps), 'source_frame_id': str(source_index),
                   'source_cycle_id': str(cycle), 'source_dataset_id': source_id}


def _identity(manifest):
    return hashlib.sha256(json.dumps({k: v for k, v in manifest.items() if k != 'dataset_id'},
                                    sort_keys=True, allow_nan=False).encode()).hexdigest()


def _packet_hashes(video, ffprobe):
    parameters, length_size, packets = h264_samples(video, ffprobe=ffprobe)
    hashes = []
    with Path(video).open('rb') as handle:
        for packet in packets:
            digest = hashlib.sha256()
            for nal in read_sample(handle, packet, length_size):
                digest.update(len(nal).to_bytes(4, 'big'))
                digest.update(nal)
            hashes.append(digest.hexdigest())
    return parameters, hashes


def verify_loop(directory, *, ffprobe='ffprobe'):
    root = Path(directory)
    manifest = read_json(root / 'manifest.json')
    if manifest.get('schema') != SCHEMA or manifest.get('status') != 'REVIEW_ONLY':
        raise ValueError('Unsupported loop delivery or source status')
    if _identity(manifest) != manifest['dataset_id']:
        raise ValueError('Loop manifest identity mismatch')
    expected_files = {'video.mp4', 'frames.csv', 'frame_geometry.npz', 'camera.json'} | {
        'source/' + name for name in SOURCE_FILES}
    if set(manifest['files']) != expected_files:
        raise ValueError('Loop evidence file set mismatch')
    for name, digest in manifest['files'].items():
        if sha256(root / name) != digest:
            raise ValueError('Loop file checksum mismatch: ' + name)
    source = read_json(root / 'source/manifest.json')
    if source['schema'] != 'wfrl.camera-video.v1' or source['status'] != 'REVIEW_ONLY':
        raise ValueError('Loop source must be a base review package')
    if source['dataset_id'] != manifest['source_dataset_id']:
        raise ValueError('Loop source identity mismatch')
    for name in SOURCE_FILES[1:]:
        if sha256(root / 'source' / name) != source['files'][name]:
            raise ValueError('Embedded source differs from verified base: ' + name)
    repeats = manifest['source_repeat_count']
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 1:
        raise ValueError('Invalid source repeat count')
    fps = source['fps']
    source_rows = read_frames(root / 'source/frames.csv')
    rows = read_frames(root / 'frames.csv')
    if rows != list(repeated_rows(source_rows, repeats, fps, source['dataset_id'])):
        raise ValueError('Derived frame labels do not match repeated source frames')
    expected_contract = {'fps': fps, 'frame_count': len(rows), 'duration_s': len(rows) / fps,
                         'source_frame_count': len(source_rows),
                         'source_duration_s': len(source_rows) / fps,
                         'mapping': 'source_frame_id = frame_id % source_frame_count; source_cycle_id = frame_id // source_frame_count',
                         'simulation_time': 'repeats source segment; not continuous new physics'}
    if manifest['time_contract'] != expected_contract or manifest['frame_count'] != len(rows) or manifest['fps'] != fps:
        raise ValueError('Loop time contract mismatch')
    if read_json(root / 'camera.json') != read_json(root / 'source/camera.json'):
        raise ValueError('Loop camera differs from source')
    with np.load(root / 'source/frame_geometry.npz', allow_pickle=False) as src, np.load(root / 'frame_geometry.npz', allow_pickle=False) as dst:
        if set(dst.files) != set(src.files) | {'source_frame_id', 'source_cycle_id'}:
            raise ValueError('Loop geometry fields mismatch')
        for name in src.files:
            expected = np.arange(len(rows)) if name == 'frame_id' else np.concatenate([src[name]] * repeats, axis=0)
            if not np.array_equal(dst[name], expected, equal_nan=True):
                raise ValueError('Loop geometry differs from source: ' + name)
        if not np.array_equal(dst['source_frame_id'], np.arange(len(rows)) % len(source_rows)) or not np.array_equal(dst['source_cycle_id'], np.arange(len(rows)) // len(source_rows)):
            raise ValueError('Loop geometry source mapping mismatch')
    report = validate_video(root / 'video.mp4', root / 'frames.csv', fps, ffprobe=ffprobe)
    duration_info = json.loads(subprocess.check_output(
        [ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
         'stream=duration:format=duration', '-of', 'json', str(root / 'video.mp4')], text=True))
    durations = [float(duration_info['format']['duration']),
                 float(duration_info['streams'][0]['duration'])]
    if not all(math.isclose(value, len(rows) / fps, rel_tol=0, abs_tol=1e-6) for value in durations):
        raise ValueError('Encoded container or video duration differs from loop contract')
    if (report['width'], report['height']) != (source['width'], source['height']) or (manifest['width'], manifest['height']) != (source['width'], source['height']):
        raise ValueError('Loop resolution differs from source')
    parameters, original = _packet_hashes(root / 'source/video.mp4', ffprobe)
    derived_parameters, derived = _packet_hashes(root / 'video.mp4', ffprobe)
    if parameters != derived_parameters or derived != original * repeats:
        raise ValueError('Loop encoded access units differ from exact source repetition')
    result = {'valid': True, 'dataset_id': manifest['dataset_id'], 'frame_count': len(rows),
              'fps': fps, 'duration_s': len(rows) / fps, 'source_repeat_count': repeats,
              'encoded_duration_s': durations[0],
              'width': source['width'], 'height': source['height'],
              'exact_encoded_repetition': True, 'status': 'REVIEW_ONLY'}
    write_json(root / 'checks/package-verification.json', result)
    return result


def build_loop(source_dir, output_dir, *, repeats=5, ffmpeg='ffmpeg', ffprobe='ffprobe'):
    from .package import verify_package
    source_dir, output = Path(source_dir).resolve(), Path(output_dir).resolve()
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 1:
        raise ValueError('Repeat count must be a positive integer')
    if output == source_dir or source_dir in output.parents:
        raise ValueError('Loop output must be separate from source package')
    stage = output.with_name('.' + output.name + '.building')
    if output.exists() or stage.exists():
        raise FileExistsError('Output or owned build staging already exists: ' + str(output))
    verify_package(source_dir, ffprobe=ffprobe)
    source = read_json(source_dir / 'manifest.json')
    if source['schema'] != 'wfrl.camera-video.v1':
        raise ValueError('Use the original base video as loop source')
    stage.mkdir(parents=True)
    (stage / 'source').mkdir()
    for name in SOURCE_FILES:
        shutil.copy2(source_dir / name, stage / 'source' / name)
    shutil.copy2(source_dir / 'camera.json', stage / 'camera.json')
    rows = read_frames(stage / 'source/frames.csv')
    fps = source['fps']
    labels = list(repeated_rows(rows, repeats, fps, source['dataset_id']))
    with (stage / 'frames.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(labels[0]))
        writer.writeheader()
        writer.writerows(labels)
    with np.load(stage / 'source/frame_geometry.npz', allow_pickle=False) as src:
        arrays = {name: np.concatenate([src[name]] * repeats, axis=0) for name in src.files}
    arrays.update(frame_id=np.arange(len(labels)), source_frame_id=np.arange(len(labels)) % len(rows),
                  source_cycle_id=np.arange(len(labels)) // len(rows))
    np.savez_compressed(stage / 'frame_geometry.npz', **arrays)
    command = [ffmpeg, '-v', 'error', '-nostdin', '-n', '-stream_loop', str(repeats - 1),
               '-i', str(stage / 'source/video.mp4'), '-map', '0:v:0', '-an', '-c:v', 'copy',
               '-video_track_timescale', str(fps), '-movflags', '+faststart', str(stage / 'video.mp4')]
    subprocess.run(command, check=True)
    files = {name: sha256(stage / name) for name in ('video.mp4', 'frames.csv', 'frame_geometry.npz', 'camera.json')}
    files.update({'source/' + name: sha256(stage / 'source' / name) for name in SOURCE_FILES})
    manifest = {'schema': SCHEMA, 'status': 'REVIEW_ONLY',
                'description': '基于仿真数据的单风机相机视频；原始片段重复播放',
                'source_dataset_id': source['dataset_id'], 'source_repeat_count': repeats,
                'source_package_verified_at_creation': True,
                'lossless_render_masters': 'retained in original base package; no new rendering',
                'frame_count': len(labels), 'fps': fps, 'time_base': f'1/{fps}',
                'width': source['width'], 'height': source['height'],
                'time_contract': {'fps': fps, 'frame_count': len(labels), 'duration_s': len(labels) / fps,
                                  'source_frame_count': len(rows), 'source_duration_s': len(rows) / fps,
                                  'mapping': 'source_frame_id = frame_id % source_frame_count; source_cycle_id = frame_id // source_frame_count',
                                  'simulation_time': 'repeats source segment; not continuous new physics'},
                'rtsp_cycle_semantics': 'cycle_id counts the entire derived file; source_cycle_id is inside frame_data',
                'encoding': 'exact H.264 access-unit repetition, no transcoding', 'files': files}
    manifest['dataset_id'] = _identity(manifest)
    write_json(stage / 'manifest.json', manifest)
    write_json(stage / 'checks/loop-build.json', {'command': command, 'source_package': str(source_dir)})
    result = verify_loop(stage, ffprobe=ffprobe)
    stage.rename(output)
    return {**result, 'directory': str(output)}
