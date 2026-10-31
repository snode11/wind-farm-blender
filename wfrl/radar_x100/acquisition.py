"""External acquisition boundaries for decoded frames, with separate recording and display budgets."""
from dataclasses import dataclass, replace
import queue
import threading
import time
import numpy as np
from .schema import POINT_DTYPE, MAX_POINTS


@dataclass(frozen=True)
class Fragment:
    template: object
    index: int
    count: int
    points: np.ndarray
    terminal: bool = False


class Assembler:
    """Adapter-supplied fragment counts and explicit terminal evidence; no vendor wire assumptions."""
    def __init__(self, *, timeout_s=.2, max_pending=16):
        import math
        if not math.isfinite(timeout_s) or timeout_s <= 0 or not 1 <= max_pending <= 1024:
            raise ValueError('Invalid assembler budget')
        self.timeout_s, self.max_pending = timeout_s, max_pending
        self.pending, self.completed = {}, {}
        self.counters = dict(duplicate=0, timeout=0, corrupt=0, overflow=0)
        self.sessions = {}

    def begin_session(self, sensor_id, session_id):
        if sensor_id not in self.sessions and len(self.sessions) >= 8:
            raise ValueError('Assembler sensor budget exceeded')
        self.sessions[sensor_id] = session_id
        self.pending = {key: value for key, value in self.pending.items() if key[0] != sensor_id}
        self.completed = {key: value for key, value in self.completed.items() if key[0] != sensor_id}

    def expire(self, now):
        for key, value in list(self.pending.items()):
            if now - value['started'] > self.timeout_s:
                del self.pending[key]
                self.completed[key[:3]] = max(key[3], self.completed.get(key[:3], -1))
                self.counters['timeout'] += 1

    def push(self, fragment, *, now=None):
        now = time.monotonic() if now is None else now
        self.expire(now)
        frame, index, count = fragment.template, fragment.index, fragment.count
        key = frame.identity
        stream_key = key[:3]
        if frame.sensor_id not in self.sessions:
            self.begin_session(frame.sensor_id, frame.session_id)
        if self.sessions[frame.sensor_id] != frame.session_id:
            self.counters['corrupt'] += 1
            return None
        if frame.completeness == 'CORRUPT':
            self.pending.pop(key, None)
            self.completed[stream_key] = max(frame.sequence, self.completed.get(stream_key, -1))
            self.counters['corrupt'] += 1
            return None
        if key not in self.pending and key[3] <= self.completed.get(stream_key, -1):
            self.counters['duplicate'] += 1
            return None
        if (type(count) is not int or type(index) is not int or not 1 <= count <= 256 or not 0 <= index < count
                or fragment.points.dtype != POINT_DTYPE or fragment.points.ndim != 1
                or frame.radial_velocity_m_s is not None or len(fragment.points) > MAX_POINTS):
            self.counters['corrupt'] += 1
            return None
        if key not in self.pending:
            if len(self.pending) >= self.max_pending:
                self.counters['overflow'] += 1
                return None
            self.pending[key] = dict(started=now, count=count, parts={}, terminal=False,
                                     metadata=frame.metadata())
        state = self.pending[key]
        if state['count'] != count or state['metadata'] != frame.metadata():
            del self.pending[key]
            self.completed[stream_key] = max(frame.sequence, self.completed.get(stream_key, -1))
            self.counters['corrupt'] += 1
            return None
        if index in state['parts']:
            if state['parts'][index].tobytes() != fragment.points.tobytes():
                del self.pending[key]
                self.completed[stream_key] = max(frame.sequence, self.completed.get(stream_key, -1))
                self.counters['corrupt'] += 1
            else:
                self.counters['duplicate'] += 1
            return None
        state['parts'][index] = fragment.points.copy()
        state['terminal'] |= fragment.terminal
        if sum(map(len, state['parts'].values())) > MAX_POINTS:
            del self.pending[key]
            self.completed[stream_key] = max(frame.sequence, self.completed.get(stream_key, -1))
            self.counters['corrupt'] += 1
            return None
        if len(state['parts']) != count or not state['terminal']:
            return None
        del self.pending[key]
        self.completed[stream_key] = max(frame.sequence, self.completed.get(stream_key, -1))
        try:
            return replace(frame, points=np.concatenate([state['parts'][i] for i in range(count)]),
                           completeness='COMPLETE')
        except ValueError:
            self.counters['corrupt'] += 1
            return None


class Fanout:
    """Display replacement never discards an accepted recording job."""
    def __init__(self, recorder=None, *, record_capacity=32):
        if not 1 <= record_capacity <= 1024:
            raise ValueError('Invalid recording queue budget')
        self.recorder = recorder
        self.queue = queue.Queue(maxsize=record_capacity)
        self.latest, self.display_drops, self.record_failures = {}, 0, 0
        self.accepting = True
        self.lock, self.stopping = threading.Lock(), threading.Event()
        self.worker = threading.Thread(target=self._record, name='x100-record', daemon=True)
        self.worker.start()

    def submit(self, frame, replay_time_s):
        frame = frame.snapshot()
        with self.lock:
            if not self.accepting:
                raise RuntimeError('Radar fanout is closing or closed')
            self._submit_display(frame)
            if self.recorder is not None and self.recorder.state == 'RECORDING' and not self.stopping.is_set():
                try:
                    self.queue.put_nowait((frame, replay_time_s))
                except queue.Full:
                    self.record_failures += 1
                    self.failure = ('Recording queue budget exceeded; frames after the last durable index row '
                                    'are not guaranteed; overflow identity ' + repr(frame.identity))
                    self.stopping.set()

    def submit_display(self, frame):
        with self.lock:
            self._submit_display(frame)

    def _submit_display(self, frame):
        key = frame.sensor_id, frame.stream
        if key in self.latest:
            self.display_drops += 1
            previous = self.latest[key]
            if previous.session_id == frame.session_id and previous.sequence >= frame.sequence:
                return
        if key not in self.latest and len(self.latest) >= 8:
            self.latest.pop(next(iter(self.latest)))
            self.display_drops += 1
        self.latest[key] = frame

    def take_latest(self):
        with self.lock:
            return self.latest.pop(next(iter(self.latest))) if self.latest else None

    def _record(self):
        while not self.stopping.is_set() or not self.queue.empty():
            if hasattr(self, 'failure') and self.recorder is not None:
                self.recorder.fail(self.failure)
            try:
                frame, replay_time = self.queue.get(timeout=.05)
            except queue.Empty:
                continue
            if self.recorder is not None and self.recorder.state == 'RECORDING':
                try:
                    self.recorder.append(frame, replay_time)
                except Exception:
                    self.record_failures += 1
        if self.recorder is not None:
            if hasattr(self, 'failure'):
                self.recorder.fail(self.failure)
            else:
                try:
                    self.recorder.close()
                except Exception:
                    self.record_failures += 1

    def close(self):
        with self.lock:
            self.accepting = False
            self.stopping.set()
        self.worker.join(timeout=5)
        if self.worker.is_alive():
            raise TimeoutError('Recording worker has not stopped; recording remains incomplete')


class LocalPublisher:
    """One local display client; slow/disconnected readers cannot block acquisition."""
    def __init__(self, fanout, port=9877):
        import socket
        self.fanout, self.errors, self.sent = fanout, 0, 0
        self.stopping = threading.Event()
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(('127.0.0.1', port))
        self.port = self.server.getsockname()[1]
        self.server.listen(1)
        self.server.settimeout(.1)
        self.worker = threading.Thread(target=self._serve, name='x100-display', daemon=True)
        self.worker.start()

    def _serve(self):
        from .channel import hello, encode_frame
        import socket
        while not self.stopping.is_set():
            try:
                client, address = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                client.settimeout(.2)
                peer = None
                try:
                    while not self.stopping.is_set():
                        frame = self.fanout.take_latest()
                        if frame is None:
                            self.stopping.wait(.005)
                            continue
                        identity = frame.sensor_id, frame.session_id, frame.source_kind
                        if peer is None:
                            client.sendall(hello(frame))
                            peer = identity
                        elif peer != identity:
                            self.fanout.submit_display(frame)
                            break
                        client.sendall(encode_frame(frame))
                        self.sent += 1
                except OSError:
                    self.errors += 1

    def close(self):
        self.stopping.set()
        self.server.close()
        self.worker.join(timeout=2)
