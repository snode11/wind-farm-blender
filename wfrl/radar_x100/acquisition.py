"""External acquisition boundaries for decoded frames, with separate recording and display budgets."""
from dataclasses import dataclass, replace
import math
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


@dataclass(frozen=True)
class SessionStart:
    """Trusted producer declaration of a sensor's current acquisition session."""
    sensor_id: str
    session_id: str
    source_kind: str


class Assembler:
    """Adapter-supplied fragment counts and explicit terminal evidence; no vendor wire assumptions."""
    def __init__(self, *, timeout_s=.2, max_pending=16):
        if (not math.isfinite(timeout_s) or timeout_s <= 0
                or type(max_pending) is not int or not 1 <= max_pending <= 1024):
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
        if not math.isfinite(now):
            raise ValueError('Invalid assembler time')
        for key, value in list(self.pending.items()):
            if now - value['started'] >= self.timeout_s:
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
                or type(fragment.terminal) is not bool
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
                return None
            else:
                self.counters['duplicate'] += 1
        else:
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
    def __init__(self, recorder=None, *, record_capacity=32, display_capacity=64):
        if type(record_capacity) is not int or not 1 <= record_capacity <= 1024:
            raise ValueError('Invalid recording queue budget')
        if type(display_capacity) is not int or not 1 <= display_capacity <= 1024:
            raise ValueError('Invalid display identity budget')
        self.recorder = recorder
        self.queue = queue.Queue(maxsize=record_capacity)
        self.latest, self.display_drops, self.record_failures = {}, 0, 0
        self.latest_received = {}
        self.display_capacity = display_capacity
        self.watermarks, self.sources, self.active_sources, self.deliveries = {}, {}, {}, {}
        self.accepting = True
        self.failure = None
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

    def begin_session(self, sensor_id, session_id, source_kind):
        """Admit a producer session without reviving retired identities or clearing watermarks."""
        with self.lock:
            return self._begin_session((sensor_id, session_id, source_kind))

    def _begin_session(self, identity):
        if not self.accepting or any(not isinstance(value, str) or not value for value in identity):
            return False
        sensor = identity[0]
        if identity in self.sources:
            return self.active_sources.get(sensor) == identity
        if len(self.sources) >= self.display_capacity:
            return False
        self.sources[identity] = object()
        self.active_sources[sensor] = identity
        for key in tuple(self.latest):
            if key[0] == sensor:
                self.latest.pop(key)
                self.latest_received.pop(key)
                self.display_drops += 1
        for key in tuple(self.deliveries):
            if key[0] == sensor:
                self.deliveries.pop(key)
        return True

    def submit_display(self, frame, *, received_monotonic=None, restoring=False, restore_token=None):
        if not restoring:
            frame = frame.snapshot()
        with self.lock:
            return self._submit_display(frame, received_monotonic=received_monotonic,
                                        restoring=restoring, restore_token=restore_token)

    def _submit_display(self, frame, *, received_monotonic=None, restoring=False, restore_token=None):
        key = frame.sensor_id, frame.stream
        source = frame.sensor_id, frame.session_id, frame.source_kind
        identity = (*source, frame.stream)
        if not self.accepting:
            return False
        if restoring:
            delivery = self.deliveries.get(key)
            if delivery is None or restore_token is not delivery[2]:
                self.display_drops += 1
                return False
            self.deliveries.pop(key)
            if (delivery[0] is not frame or self.active_sources.get(frame.sensor_id) != source
                    or self.watermarks.get(identity) != frame.sequence or key in self.latest
                    or len(self.latest) >= 8):
                self.display_drops += 1
                return False
            self.latest[key], self.latest_received[key] = frame, delivery[1]
            return True
        if received_monotonic is not None and (
                isinstance(received_monotonic, bool) or not isinstance(received_monotonic, (int, float))
                or not math.isfinite(received_monotonic)):
            raise ValueError('Invalid local receipt time')
        if frame.sensor_id not in self.active_sources:
            self._begin_session(source)
        if (self.active_sources.get(frame.sensor_id) != source
                or (identity not in self.watermarks and len(self.watermarks) >= self.display_capacity)
                or frame.sequence <= self.watermarks.get(identity, -1)):
            self.display_drops += 1
            return False
        self.watermarks[identity] = frame.sequence
        self.deliveries.pop(key, None)
        if key in self.latest:
            self.display_drops += 1
        if key not in self.latest and len(self.latest) >= 8:
            evicted = next(iter(self.latest))
            self.latest.pop(evicted)
            self.latest_received.pop(evicted)
            self.display_drops += 1
        self.latest[key] = frame
        self.latest_received[key] = time.monotonic() if received_monotonic is None else received_monotonic
        return True

    def take_latest(self):
        observation = self.take_latest_with_time()
        return observation[0] if observation is not None else None

    def take_latest_with_time(self):
        """Consume a display frame as (frame, received_monotonic), or None."""
        with self.lock:
            if not self.latest:
                return None
            key = next(iter(self.latest))
            frame, received = self.latest.pop(key), self.latest_received.pop(key)
            return frame, received

    def begin_delivery(self):
        """Reserve (frame, received_monotonic, token) for sending or restoring."""
        with self.lock:
            if not self.latest:
                return None
            key = next(iter(self.latest))
            frame, received = self.latest.pop(key), self.latest_received.pop(key)
            delivery = frame, received, object()
            self.deliveries[key] = delivery
            return delivery

    def finish_delivery(self, delivery):
        with self.lock:
            key = delivery[0].sensor_id, delivery[0].stream
            if self.deliveries.get(key) is delivery:
                self.deliveries.pop(key)

    def _record(self):
        try:
            while not self.stopping.is_set() or not self.queue.empty():
                if self.failure is not None:
                    break
                try:
                    frame, replay_time = self.queue.get(timeout=.05)
                except queue.Empty:
                    continue
                try:
                    if self.recorder is not None and self.recorder.state == 'RECORDING':
                        self.recorder.append(frame, replay_time)
                finally:
                    self.queue.task_done()
            if self.recorder is not None:
                if self.failure is not None:
                    self.recorder.fail(self.failure)
                else:
                    self.recorder.close()
        except Exception as exc:
            with self.lock:
                self.record_failures += 1
                self.failure = self.failure or str(exc) or repr(exc)
                self.stopping.set()
            if self.recorder is not None:
                try:
                    self.recorder.fail(self.failure)
                except Exception as cleanup_error:
                    self.failure += '; recording failure cleanup: ' + str(cleanup_error)
        finally:
            self.stopping.set()
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
                self.queue.task_done()

    def request_close(self):
        with self.lock:
            self.accepting = False
            self.stopping.set()
            self.latest.clear()
            self.latest_received.clear()
            self.deliveries.clear()

    def close(self):
        self.request_close()
        self.worker.join(timeout=5)
        if self.worker.is_alive():
            raise TimeoutError('Recording worker has not stopped; recording remains incomplete')


class LocalPublisher:
    """One local display client; slow/disconnected readers cannot block acquisition."""
    def __init__(self, fanout, port=9877):
        import socket
        self.fanout, self.errors, self.sent = fanout, 0, 0
        self.stopping = threading.Event()
        self.client, self.client_lock = None, threading.Lock()
        self.server = socket.socket()
        try:
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind(('127.0.0.1', port))
            self.port = self.server.getsockname()[1]
            self.server.listen(1)
            self.server.settimeout(.1)
            self.worker = threading.Thread(target=self._serve, name='x100-display', daemon=True)
            self.worker.start()
        except BaseException:
            self.server.close()
            raise

    def _serve(self):
        from .channel import hello, encode_frame
        import select
        import socket
        while not self.stopping.is_set():
            try:
                client, address = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self.stopping.is_set():
                    self.errors += 1
                break
            with self.client_lock:
                if self.stopping.is_set():
                    client.close()
                    break
                self.client = client
            with client:
                peer = None
                observation = None
                try:
                    client.settimeout(.2)
                    while not self.stopping.is_set():
                        ready, _, _ = select.select([client, self.server], [], [], .005)
                        # A new explicit connection takes over the single display slot.
                        if self.server in ready or client in ready:
                            break
                        observation = self.fanout.begin_delivery()
                        if observation is None:
                            continue
                        frame, received, _token = observation
                        identity = frame.sensor_id, frame.session_id, frame.source_kind
                        if peer is None:
                            client.sendall(hello(frame))
                            peer = identity
                        elif peer != identity:
                            break
                        client.sendall(encode_frame(frame, received_monotonic=received))
                        self.sent += 1
                        self.fanout.finish_delivery(observation)
                        observation = None
                except (OSError, ValueError):
                    if not self.stopping.is_set():
                        self.errors += 1
                finally:
                    with self.client_lock:
                        self.client = None
                    if observation is not None:
                        self.fanout.submit_display(observation[0], received_monotonic=observation[1],
                                                   restoring=True, restore_token=observation[2])

    def close(self):
        import socket
        self.stopping.set()
        with self.client_lock:
            if self.client is not None:
                try:
                    self.client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.server.close()
        self.worker.join(timeout=2)
        if self.worker.is_alive():
            raise TimeoutError('Local publisher worker has not stopped')
