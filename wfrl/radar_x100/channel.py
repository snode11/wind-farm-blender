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
MAX_PAYLOAD = MAX_POINTS * 30
MAX_PACKET = HEADER.size + MAX_METADATA + MAX_PAYLOAD


def packet(metadata, payload=b''):
    raw = json_bytes(metadata)
    if len(raw) > MAX_METADATA or len(payload) > MAX_PAYLOAD:
        raise ValueError('Radar packet exceeds budget')
    return HEADER.pack(MAGIC, len(raw), len(payload)) + raw + payload


def hello(frame):
    return packet(dict(type='HELLO', schema=SCHEMA, sensor_id=frame.sensor_id,
                       session_id=frame.session_id, source_kind=frame.source_kind))


def encode_frame(frame):
    payload = frame.points.tobytes()
    if frame.radial_velocity_m_s is not None:
        payload += frame.radial_velocity_m_s.tobytes()
    return packet(dict(type='FRAME', count=len(frame.points),
                       velocity=frame.radial_velocity_m_s is not None, frame=frame.metadata()), payload)


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
        metadata = json.loads(bytes(self.buffer[HEADER.size:HEADER.size + meta_size]))
        payload = bytes(self.buffer[HEADER.size + meta_size:total])
        del self.buffer[:total]
        if metadata['type'] == 'HELLO':
            if (payload or metadata['schema'] != SCHEMA or metadata['source_kind'] not in SOURCES
                    or not metadata['session_id'] or not metadata['sensor_id']):
                raise ValueError('Invalid radar handshake')
            return metadata
        if metadata['type'] != 'FRAME':
            raise ValueError('Unknown radar message')
        count, velocity = metadata['count'], metadata['velocity']
        if (type(count) is not int or not 0 <= count <= MAX_POINTS or type(velocity) is not bool
                or len(payload) != count * (30 if velocity else 26)):
            raise ValueError('Radar array length mismatch')
        points = np.frombuffer(payload[:count * 26], dtype=POINT_DTYPE)
        speeds = np.frombuffer(payload[count * 26:], dtype='<f4') if velocity else None
        return frame_from_metadata(metadata['frame'], points, speeds)


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
        self.socket = socket.socket()
        self.socket.setblocking(False)
        self.socket.connect_ex(('127.0.0.1', port))
        self.reader, self.state = PacketReader(), LiveState(stale_after_s)
        self.error, self.started, self.closed = None, time.monotonic(), False

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
