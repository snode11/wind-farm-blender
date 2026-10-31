"""Strict binary-little-endian X100 PLY reader and lossless point writer."""
from pathlib import Path
import hashlib
import io
import numpy as np
from .schema import POINT_DTYPE, MAX_POINTS, RadarFrame, FrameTime

PROPERTIES = [('float', 'x'), ('float', 'y'), ('float', 'z'), ('uchar', 'ms'),
              ('float', 'mag'), ('float', 'rcs'), ('float', 'snr'), ('uchar', 'label')]
MAX_HEADER = 16384


def decode_ply(raw):
    stream = io.BytesIO(raw)
    lines = []
    while stream.tell() < MAX_HEADER:
        line = stream.readline()
        if not line or not line.endswith(b'\n'):
            raise ValueError('Truncated PLY header')
        try:
            text = line.decode('ascii').strip()
        except UnicodeDecodeError as exc:
            raise ValueError('Non-ASCII PLY header') from exc
        if text == 'end_header':
            break
        if not text.startswith(('comment ', 'obj_info ')):
            lines.append(text)
    else:
        raise ValueError('PLY header exceeds budget')
    if len(lines) != 11 or lines[:2] != ['ply', 'format binary_little_endian 1.0']:
        raise ValueError('Unsupported PLY header or byte order')
    vertex = lines[2].split()
    if len(vertex) != 3 or vertex[:2] != ['element', 'vertex']:
        raise ValueError('Missing PLY vertex count')
    try:
        count = int(vertex[2])
    except ValueError as exc:
        raise ValueError('Invalid PLY vertex count') from exc
    if not 0 <= count <= MAX_POINTS:
        raise ValueError('PLY vertex count exceeds X100 budget')
    if lines[3:] != ['property ' + t + ' ' + n for t, n in PROPERTIES]:
        raise ValueError('Unsupported PLY field order or types')
    payload = raw[stream.tell():]
    if len(payload) != count * 26:
        raise ValueError('PLY payload length mismatch')
    points = np.frombuffer(payload, dtype=POINT_DTYPE).copy()
    if np.any((points['ms'] != 0) & (points['ms'] != 1)):
        raise ValueError('Unsupported ms encoding')
    return points


def read_ply_bytes(path):
    limit = MAX_HEADER + MAX_POINTS * 26
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('PLY file exceeds budget')
    return raw


def read_ply(path):
    return decode_ply(read_ply_bytes(path))


def encode_ply(points):
    if points.dtype != POINT_DTYPE or points.ndim != 1 or len(points) > MAX_POINTS:
        raise ValueError('Invalid PLY point array')
    header = ['ply', 'format binary_little_endian 1.0', f'element vertex {len(points)}',
              *['property ' + t + ' ' + n for t, n in PROPERTIES], 'end_header', '']
    return '\n'.join(header).encode('ascii') + points.tobytes()


def ply_frame(path, *, sensor_id, session_id, sequence, time=None, source_kind='MEASURED_REPLAY', stream='UNKNOWN', raw_bytes=None):
    path = Path(path)
    raw = read_ply_bytes(path) if raw_bytes is None else raw_bytes
    return RadarFrame(sensor_id, session_id, sequence, source_kind, stream, decode_ply(raw),
                      time or FrameTime(), provenance=dict(parser='x100-ply.v1', file=path.name,
                      sha256=hashlib.sha256(raw).hexdigest(), label_semantics='UNKNOWN',
                      absent=['radial_velocity', 'frame_number', 'timestamp', 'track_id']))
