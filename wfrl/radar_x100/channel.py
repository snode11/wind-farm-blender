"""Bounded local radar channel, independent of the training Bridge."""
from dataclasses import dataclass
import json
import math
import socket
import struct
import time
import numpy as np
from .schema import SCHEMA, POINT_DTYPE, MAX_POINTS, MAX_METADATA, SOURCES, json_bytes, frame_from_metadata

HEADER = struct.Struct('<4sII')
MAGIC = b'X1F1'
VELOCITY_BYTES = np.dtype('<f4').itemsize
MAX_PAYLOAD = MAX_POINTS * (POINT_DTYPE.itemsize + VELOCITY_BYTES)
MAX_PACKET = HEADER.size + MAX_METADATA + MAX_PAYLOAD


def _metadata_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate radar metadata field: ' + key)
        result[key] = value
    return result


def _nonfinite_constant(value):
    raise ValueError('Nonfinite radar metadata constant: ' + value)


def packet(metadata, payload=b''):
    raw = json_bytes(metadata)
    if len(raw) > MAX_METADATA or len(payload) > MAX_PAYLOAD:
        raise ValueError('Radar packet exceeds budget')
    return HEADER.pack(MAGIC, len(raw), len(payload)) + raw + payload


def hello(frame):
    return packet(dict(type='HELLO', schema=SCHEMA, sensor_id=frame.sensor_id,
                       session_id=frame.session_id, source_kind=frame.source_kind))


def encode_frame(frame, *, received_monotonic=None):
    payload = frame.points.tobytes()
    if frame.radial_velocity_m_s is not None:
        payload += frame.radial_velocity_m_s.tobytes()
    metadata = dict(type='FRAME', count=len(frame.points),
                    velocity=frame.radial_velocity_m_s is not None, frame=frame.metadata())
    if received_monotonic is not None:
        metadata['received_monotonic'] = received_monotonic
    return packet(metadata, payload)


@dataclass(frozen=True)
class ReceivedFrame:
    frame: object
    received_monotonic: float


class PacketReader:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, raw):
        if len(self.buffer) + len(raw) > MAX_PACKET * 2:
            raise ValueError('Radar receive buffer exceeds budget')
        self.buffer.extend(raw)

    def pop(self):
        if len(self.buffer) < HEADER.size:
            return None
        magic, meta_size, data_size = HEADER.unpack_from(self.buffer)
        if magic != MAGIC or not 0 < meta_size <= MAX_METADATA or data_size > MAX_PAYLOAD:
            raise ValueError('Invalid radar packet header')
        total = HEADER.size + meta_size + data_size
        if len(self.buffer) < total:
            return None
        try:
            metadata = json.loads(bytes(self.buffer[HEADER.size:HEADER.size + meta_size]),
                                  object_pairs_hook=_metadata_object, parse_constant=_nonfinite_constant)
        except (UnicodeError, RecursionError) as exc:
            raise ValueError('Invalid radar metadata encoding') from exc
        payload = bytes(self.buffer[HEADER.size + meta_size:total])
        del self.buffer[:total]
        if not isinstance(metadata, dict):
            raise ValueError('Radar metadata must be an object')
        if metadata.get('type') == 'HELLO':
            if (payload or metadata.get('schema') != SCHEMA
                    or not isinstance(metadata.get('source_kind'), str) or metadata['source_kind'] not in SOURCES
                    or any(not isinstance(metadata.get(key), str) or not 1 <= len(metadata[key]) <= 256
                           for key in ('session_id', 'sensor_id'))):
                raise ValueError('Invalid radar handshake')
            return metadata
        if metadata.get('type') != 'FRAME':
            raise ValueError('Unknown radar message')
        count, velocity = metadata.get('count'), metadata.get('velocity')
        if (type(count) is not int or not 0 <= count <= MAX_POINTS or type(velocity) is not bool
                or len(payload) != count * (POINT_DTYPE.itemsize + (VELOCITY_BYTES if velocity else 0))):
            raise ValueError('Radar array length mismatch')
        points = np.frombuffer(payload[:count * POINT_DTYPE.itemsize], dtype=POINT_DTYPE)
        speeds = np.frombuffer(payload[count * POINT_DTYPE.itemsize:], dtype='<f4') if velocity else None
        frame = frame_from_metadata(metadata.get('frame'), points, speeds)
        if 'received_monotonic' in metadata:
            stamp = metadata['received_monotonic']
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
                raise ValueError('Invalid local receipt time')
            return ReceivedFrame(frame, stamp)
        return frame


@dataclass
class StreamState:
    frame: object = None
    received_monotonic: float | None = None
    display_drops: int = 0
    duplicates: int = 0
    gaps: int = 0
    pending: bool = False


class LiveState:
    def __init__(self, stale_after_s=.5):
        if not math.isfinite(stale_after_s) or stale_after_s <= 0:
            raise ValueError('Invalid stale threshold')
        self.streams, self.peer, self.connected = {}, None, False
        self.stale_after_s = stale_after_s

    def handshake(self, peer):
        self.peer, self.streams, self.connected = peer, {}, True

    def accept(self, frame, received_monotonic=None):
        if not self.connected or self.peer is None or any(getattr(frame, key) != self.peer[key]
                for key in ('sensor_id', 'session_id', 'source_kind')):
            raise ValueError('Radar session identity mismatch; reconnect required')
        stream = self.streams.setdefault(frame.stream, StreamState())
        if stream.frame is not None:
            if frame.sequence <= stream.frame.sequence:
                stream.duplicates += 1
                return False
            stream.gaps += frame.sequence - stream.frame.sequence - 1
        if stream.pending:
            stream.display_drops += 1
        stream.frame, stream.received_monotonic, stream.pending = frame, (
            time.monotonic() if received_monotonic is None else received_monotonic), True
        return True

    def status(self, stream_name, now=None):
        if not self.connected:
            return 'DISCONNECTED'
        stream = self.streams.get(stream_name)
        if stream is None:
            return 'WAITING'
        if (time.monotonic() if now is None else now) - stream.received_monotonic > self.stale_after_s:
            return 'STALE'
        return stream.frame.validity

    def take(self, stream_name):
        stream = self.streams.get(stream_name)
        if stream is None or not stream.pending:
            return None
        stream.pending = False
        return stream.frame


class LocalClient:
    """Blender's main thread performs bounded nonblocking polls; no bpy worker."""
    def __init__(self, port, *, stale_after_s=.5):
        self.reader, self.state = PacketReader(), LiveState(stale_after_s)
        self.error, self.started, self.closed = None, time.monotonic(), False
        self.socket = socket.socket()
        try:
            self.socket.setblocking(False)
            self.socket.connect_ex(('127.0.0.1', port))
        except BaseException:
            self.socket.close()
            raise

    def poll(self, *, byte_budget=256 * 1024, frame_budget=8, time_budget_s=.004):
        if self.closed:
            return
        start, received, frames = time.monotonic(), 0, 0
        try:
            while received < byte_budget and frames < frame_budget and time.monotonic() - start < time_budget_s:
                message = self.reader.pop()
                if message is not None:
                    if isinstance(message, dict):
                        if self.state.peer is not None:
                            raise ValueError('Repeated radar handshake; reconnect required')
                        self.state.handshake(message)
                    else:
                        if isinstance(message, ReceivedFrame):
                            self.state.accept(message.frame, received_monotonic=message.received_monotonic)
                        else:
                            self.state.accept(message)
                        frames += 1
                    continue
                try:
                    raw = self.socket.recv(min(65536, byte_budget - received))
                except (BlockingIOError, InterruptedError):
                    break
                except OSError as exc:
                    if exc.errno in (57, 107) and time.monotonic() - self.started < 2:
                        break
                    raise
                if not raw:
                    if self.reader.buffer:
                        raise ValueError('Truncated radar packet at disconnect')
                    self.close()
                    break
                received += len(raw)
                self.reader.feed(raw)
            if self.state.peer is None and time.monotonic() - self.started > 3:
                raise TimeoutError('Radar handshake timeout')
        except Exception as exc:
            self.error = str(exc)
            self.close()

    def close(self):
        self.socket.close()
        self.state.connected, self.closed = False, True
