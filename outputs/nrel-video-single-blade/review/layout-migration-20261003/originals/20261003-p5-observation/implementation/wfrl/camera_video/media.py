"""Lossless-master encoding and strict presentation timestamp validation."""
from __future__ import annotations

import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess


def _run(args):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True, text=True).stdout


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_frames(path):
    with Path(path).open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(int(row['frame_id']) != i or int(row['video_pts']) != i for i, row in enumerate(rows)):
        raise ValueError('frames.csv must contain contiguous zero-based frame_id and video_pts')
    return rows


def validate_video(video, frames_csv, fps, *, ffprobe='ffprobe'):
    """Decode every frame and require exact 1/fps time base, PTS and GOP."""
    rows = read_frames(frames_csv)
    result = json.loads(_run([ffprobe, '-v', 'error', '-threads', '0', '-select_streams', 'v:0', '-show_streams',
                              '-show_frames', '-show_entries',
                              'stream=codec_name,pix_fmt,time_base,avg_frame_rate,has_b_frames,width,height,color_space,color_transfer,color_primaries,color_range:frame=pts,key_frame,pict_type',
                              '-of', 'json', video]))
    stream = result['streams'][0]
    frames = result['frames']
    if stream['codec_name'] != 'h264' or stream['pix_fmt'] != 'yuv420p' or int(stream['has_b_frames']) != 0:
        raise ValueError('Expected H.264 yuv420p without B frames')
    expected_color = {'color_space': 'bt709', 'color_transfer': 'iec61966-2-1',
                      'color_primaries': 'bt709', 'color_range': 'tv'}
    if any(stream.get(key) != value for key, value in expected_color.items()):
        raise ValueError('Video color metadata does not match sRGB-transfer / BT.709-matrix / limited-range contract')
    if Fraction(stream['time_base']) != Fraction(1, fps) or Fraction(stream['avg_frame_rate']) != fps:
        raise ValueError('Unexpected video time base or frame rate')
    if len(frames) != len(rows) or [int(f['pts']) for f in frames] != [int(r['video_pts']) for r in rows]:
        raise ValueError('Decoded presentation frames do not exactly match frames.csv')
    if any(f['pict_type'] == 'B' for f in frames):
        raise ValueError('B frames are not permitted')
    keys = [i for i, f in enumerate(frames) if f['key_frame']]
    if not keys or keys[0] != 0 or any(b - a > fps for a, b in zip(keys, keys[1:] + [len(frames)])):
        raise ValueError('Video requires an initial keyframe and a maximum one-second GOP')
    return {'valid': True, 'frame_count': len(frames), 'fps': fps, 'time_base': f'1/{fps}',
            'pts': [int(f['pts']) for f in frames], 'keyframes': keys,
            'width': stream['width'], 'height': stream['height'], 'color_contract': expected_color, 'video_sha256': sha256(video),
            'frames_csv_sha256': sha256(frames_csv)}


def _write_json_atomic(path, value):
    path = Path(path)
    writing = path.with_name(path.name + '.writing')
    writing.write_text(json.dumps(value, indent=2) + '\n')
    writing.replace(path)


def encode_video(package_dir, fps, *, pattern='%06d.png', ffmpeg='ffmpeg', ffprobe='ffprobe', crf=18, preset='medium'):
    """Encode an immutable PNG master; resume only an identical owned transaction.

    A pending intent binds every master hash, CSV hash and encoding option before
    FFmpeg starts. An interrupted partial MP4 can be rebuilt only under that
    matching intent. Unknown pre-existing video files are never overwritten.
    """
    if fps not in (15, 20, 25):
        raise ValueError('Supported frame rates: 15, 20, 25')
    root = Path(package_dir)
    rows = read_frames(root / 'frames.csv')
    master = root / 'master_frames'
    expected = [master / (pattern % i) for i in range(len(rows))]
    if any(not p.is_file() for p in expected) or len(list(master.glob('*.png'))) != len(expected):
        raise ValueError('PNG master count or frame numbering differs from frames.csv')
    output = root / 'video.mp4'
    temporary = root / 'video.encoding.mp4'
    checks = root / 'checks'; checks.mkdir(exist_ok=True)
    pending = checks / 'encoding.pending.json'
    final_report = checks / 'encoding.json'
    intent = {'fps': fps, 'pattern': pattern, 'crf': crf, 'preset': preset,
              'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe),
              'master_sha256': {p.name: sha256(p) for p in expected},
              'frames_csv_sha256': sha256(root / 'frames.csv'),
              'encoder_source_sha256': sha256(__file__)}
    state = json.loads(pending.read_text()) if pending.exists() else None
    if state is not None and state.get('intent') != intent:
        raise ValueError('Pending encoding belongs to different masters, CSV, code or parameters; use a new package directory')
    if output.exists():
        report = state.get('report') if state else None
        if report and sha256(output) == report['video_sha256']:
            validate_video(output, root / 'frames.csv', fps, ffprobe=ffprobe)
            _write_json_atomic(final_report, report)
            pending.unlink()
            return report
        raise FileExistsError(output)
    if temporary.exists() and state is None:
        raise FileExistsError(f'Unowned encoding temporary: {temporary}')
    args = [ffmpeg, '-v', 'error', '-nostdin', '-n', '-framerate', fps, '-start_number', 0,
            '-i', master / pattern, '-frames:v', len(rows), '-an', '-c:v', 'libx264', '-preset', preset,
            '-crf', crf, '-vf', 'scale=in_range=full:out_range=tv:out_color_matrix=bt709,setparams=range=limited:color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709',
            '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709',
            '-color_primaries', 'bt709', '-color_trc', 'iec61966-2-1', '-bf', 0, '-g', fps, '-keyint_min', fps,
            '-sc_threshold', 0, '-fps_mode', 'passthrough', '-video_track_timescale', fps,
            '-movflags', '+faststart', temporary]
    _write_json_atomic(pending, {'intent': intent})
    report = None
    if temporary.exists():
        try:
            report = validate_video(temporary, root / 'frames.csv', fps, ffprobe=ffprobe)
        except (ValueError, KeyError, IndexError, subprocess.CalledProcessError):
            temporary.unlink()  # Only the matching intent grants ownership.
    if report is None:
        _run(args)
        report = validate_video(temporary, root / 'frames.csv', fps, ffprobe=ffprobe)
    report.update({'command': [str(a) for a in args], 'ffmpeg_version': _run([ffmpeg, '-version']).splitlines()[0],
                   'ffprobe_version': _run([ffprobe, '-version']).splitlines()[0],
                   'master_sha256': intent['master_sha256'],
                   'encoder_source_sha256': intent['encoder_source_sha256'], 'lossless_master_retained': True,
                   'color_conversion': 'Full-range display-transformed sRGB PNG to limited-range BT.709 YCbCr; sRGB transfer retained and signaled; lossy H.264 4:2:0'})
    _write_json_atomic(pending, {'intent': intent, 'report': report})
    temporary.rename(output)
    _write_json_atomic(final_report, report)
    pending.unlink()
    return report


def _hex_dump(text):
    return bytes.fromhex(''.join(line.split(':', 1)[1].split('  ', 1)[0].replace(' ', '')
                                for line in text.splitlines() if ':' in line))


def h264_samples(video, *, ffprobe='ffprobe'):
    """Read the original MP4 AVCC samples by file position, without re-encoding."""
    info = json.loads(_run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_streams',
                            '-show_data', '-show_entries', 'stream=extradata,is_avc,nal_length_size', '-of', 'json', video]))['streams'][0]
    if info.get('is_avc') != 'true':
        raise ValueError('An AVCC H.264 MP4 is required')
    config = _hex_dump(info['extradata'])
    length_size = (config[4] & 3) + 1
    pos = 6
    parameters = []
    for _ in range(config[5] & 31):
        length = int.from_bytes(config[pos:pos + 2], 'big'); pos += 2
        parameters.append(config[pos:pos + length]); pos += length
    count = config[pos]; pos += 1
    for _ in range(count):
        length = int.from_bytes(config[pos:pos + 2], 'big'); pos += 2
        parameters.append(config[pos:pos + length]); pos += length
    packets = json.loads(_run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_packets',
                               '-show_entries', 'packet=pts,dts,pos,size,flags', '-of', 'json', video]))['packets']
    return parameters, length_size, packets


def read_sample(handle, packet, length_size):
    handle.seek(int(packet['pos']))
    data = handle.read(int(packet['size']))
    nals, offset = [], 0
    while offset < len(data):
        length = int.from_bytes(data[offset:offset + length_size], 'big'); offset += length_size
        if not length or offset + length > len(data):
            raise ValueError('Invalid AVCC sample')
        nals.append(data[offset:offset + length]); offset += length
    return nals
