"""Explicit indexed PLY recordings with integrity and incomplete markers."""
from bisect import bisect_right
from pathlib import Path
import hashlib
import json
import math
import os
import numpy as np
from .schema import json_bytes, frame_from_metadata
from .ply import encode_ply, decode_ply, read_ply_bytes

MAX_INDEX_BYTES = 64 * 1024 * 1024


def _atomic(path, raw):
    temporary = path.with_name(path.name + '.partial')
    with temporary.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _safe(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError('Recording path escapes root')
    return path


class Recorder:
    """Synchronous durable sink; acquisition calls it from its recording worker."""
    def __init__(self, path, *, description=None):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=False)
        (self.path / 'frames').mkdir()
        self.state, self.rows, self.last_time, self.error = 'RECORDING', 0, None, None
        self.last_identity = None
        self.description = description or {}
        _atomic(self.path / 'INCOMPLETE', b'Recording has no successful close.\n')
        self._manifest()
        self.index = (self.path / 'frame_index.jsonl').open('xb')

    def _manifest(self):
        _atomic(self.path / 'manifest.json', json_bytes(dict(schema='x100-recording.v1',
            state=self.state, frames=self.rows, error=self.error, description=self.description,
            last_successful_identity=self.last_identity,
            index_sha256=(hashlib.sha256((self.path / 'frame_index.jsonl').read_bytes()).hexdigest()
                          if self.state == 'COMPLETE' else None))))

    def append(self, frame, replay_time_s, *, original_ply=None):
        if self.state != 'RECORDING':
            raise RuntimeError('Recorder is not recording')
        try:
            if (not math.isfinite(replay_time_s) or replay_time_s < 0
                    or (self.last_time is not None and replay_time_s < self.last_time)):
                raise ValueError('Recording times must be finite and nondecreasing')
            name = f'frames/{self.rows:08d}.ply'
            raw = encode_ply(frame.points) if original_ply is None else original_ply
            if original_ply is not None:
                from .ply import decode_ply
                if decode_ply(raw).tobytes() != frame.points.tobytes():
                    raise ValueError('Original PLY does not match frame points')
            row = dict(file=name, sha256=hashlib.sha256(raw).hexdigest(), replay_time_s=replay_time_s,
                       metadata=frame.metadata(), velocity=None)
            if frame.radial_velocity_m_s is not None:
                velocity = frame.radial_velocity_m_s.tobytes()
                vname = f'frames/{self.rows:08d}.velocity.f32'
                row['velocity'] = dict(file=vname, sha256=hashlib.sha256(velocity).hexdigest())
            index_row = json_bytes(row) + b'\n'
            if self.index.tell() + len(index_row) > MAX_INDEX_BYTES:
                raise ValueError('Recording index exceeds budget')
            _atomic(self.path / name, raw)
            if row['velocity'] is not None:
                _atomic(self.path / vname, velocity)
            self.index.write(index_row)
            self.index.flush()
            os.fsync(self.index.fileno())
            self.rows += 1
            self.last_time = replay_time_s
            self.last_identity = frame.identity
        except Exception as exc:
            self.fail(str(exc))
            raise

    def fail(self, reason):
        if self.state != 'RECORDING':
            return
        self.state, self.error = 'FAILED', str(reason)
        if hasattr(self, 'index'):
            self.index.close()
        try:
            self._manifest()
        except OSError:
            pass

    def close(self):
        if self.state != 'RECORDING':
            return
        try:
            if not self.rows:
                raise ValueError('Recording contains no frames')
            self.index.close()
            self.state = 'COMPLETE'
            self._manifest()
            (self.path / 'INCOMPLETE').unlink()
        except Exception as exc:
            self.state = 'RECORDING'
            self.fail(str(exc))
            raise


class Replay:
    """Recorded observations are selected without interpolation or future exposure."""
    def __init__(self, path):
        self.path = Path(path).resolve()
        manifest = json.loads((self.path / 'manifest.json').read_bytes())
        if manifest.get('schema') != 'x100-recording.v1' or manifest.get('state') != 'COMPLETE' or (self.path / 'INCOMPLETE').exists():
            raise ValueError('Recording is incomplete or unsupported')
        index_path = self.path / 'frame_index.jsonl'
        with index_path.open('rb') as stream:
            index = stream.read(MAX_INDEX_BYTES + 1)
        if len(index) > MAX_INDEX_BYTES:
            raise ValueError('Recording index exceeds budget')
        if hashlib.sha256(index).hexdigest() != manifest.get('index_sha256'):
            raise ValueError('Recording index digest mismatch')
        self.rows = [json.loads(line) for line in index.splitlines()]
        self.times = [row['replay_time_s'] for row in self.rows]
        if (not self.rows or manifest['frames'] != len(self.rows)
                or any(not math.isfinite(t) or t < 0 for t in self.times)
                or any(a > b for a, b in zip(self.times, self.times[1:]))):
            raise ValueError('Invalid recording index or times')
        self.duration_s = self.times[-1]

    def frame(self, index):
        row = self.rows[index]
        path = _safe(self.path, row['file'])
        raw = read_ply_bytes(path)
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('PLY recording digest mismatch')
        points = decode_ply(raw)
        velocity = None
        if row['velocity']:
            path = _safe(self.path, row['velocity']['file'])
            with path.open('rb') as stream:
                raw = stream.read(len(points) * 4 + 1)
            if len(raw) != len(points) * 4:
                raise ValueError('Velocity payload length mismatch')
            if hashlib.sha256(raw).hexdigest() != row['velocity']['sha256']:
                raise ValueError('Velocity digest mismatch')
            velocity = np.frombuffer(raw, dtype='<f4')
        return frame_from_metadata(row['metadata'], points, velocity)

    def index_at(self, replay_time_s):
        if not math.isfinite(replay_time_s):
            raise ValueError('Nonfinite replay time')
        return bisect_right(self.times, replay_time_s) - 1

    def at(self, replay_time_s):
        index = self.index_at(replay_time_s)
        return None if index < 0 else self.frame(index)
