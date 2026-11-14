"""Single-owner loopback server with bounded nonblocking framed I/O."""
from collections import deque
from copy import deepcopy
from enum import Enum, auto
import ipaddress
import math
import select
import socket
import time
import uuid
from .backend_session import BackendSession, _aged_payload
from .messages import (FrameDecoder, FrameTooLarge, SessionSequenceGuard, ProtocolError, encode_message,
                       TRAINING_STATS_CAPABILITY, bounded_diagnostic)


_RELIABLE = frozenset({'lifecycle', 'safety_event', 'error'})
_OBSERVATIONS = frozenset({'snapshot', 'training_stats'})
_AGED = _OBSERVATIONS | {'safety_event'}


class _SendQueueFull(ProtocolError):
    pass


class _SendResult(Enum):
    QUEUED = auto()
    DEFERRED = auto()
    DROPPED = auto()


class BridgeServer:
    def __init__(self, host='127.0.0.1', port=8765, session=None, handshake_timeout=5., max_queue_bytes=2*1024*1024,
                 *, session_binding=None):
        address = ipaddress.ip_address(host)
        if not address.is_loopback: raise ValueError('Bridge binds only loopback IP literals')
        try:
            valid_timeout = (type(handshake_timeout) in (int, float)
                             and math.isfinite(handshake_timeout) and handshake_timeout >= 0)
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError('handshake_timeout must be finite and nonnegative')
        self.session = session or BackendSession()
        self.listener = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port)); self.listener.listen(1); self.listener.setblocking(False)
        self.port = self.listener.getsockname()[1]
        self.client = None
        self._waiting_client = None
        self._waiting_deadline = 0.
        self.sequence = 0
        self.guard = SessionSequenceGuard(self.session.session_id)
        self.handshake_timeout = handshake_timeout
        self.max_queue_bytes = max_queue_bytes
        self.pending = deque()
        self.pending_bytes = 0
        self.backlog = deque()
        self._deferred = {}
        self._oversize_reported = set()
        self.close_after_flush = False
        self.negotiated_capabilities = frozenset()
        self.ready = False
        self.session_claimed = False
        self.closed = False
        self.shutdown_requested = False
        self.host, self.session_binding = host, session_binding
        if session_binding is not None:
            try:
                session_binding.publish(host, self.port, self.session.session_id)
            except BaseException:
                self.listener.close()
                raise

    def _observation_key(self, kind, payload):
        return kind, payload.get('record_kind') if kind == 'training_stats' else None

    def _drop_replaceable(self, kind, payload, frame_bytes=0):
        key = self._observation_key(kind, payload)
        identifies_run = kind == 'lifecycle' and 'run_id' in payload
        if identifies_run:
            for deferred_key, (observation, _) in list(self._deferred.items()):
                if observation.get('run_id') != payload['run_id']:
                    self._deferred.pop(deferred_key)
        for item in list(self.pending):
            if item['offset'] or item['kind'] not in {'snapshot', 'training_stats'}:
                continue
            same = self._observation_key(item['kind'], item['payload']) == key
            prioritize = (kind in {'lifecycle', 'safety_event', 'error'} and
                          (item['kind'] == 'snapshot' or self.pending_bytes + frame_bytes > self.max_queue_bytes))
            obsolete = identifies_run and item['payload'].get('run_id') != payload['run_id']
            if same or prioritize or obsolete:
                if prioritize and not obsolete:
                    observation_key = self._observation_key(item['kind'], item['payload'])
                    self._deferred[observation_key] = item['payload'], item['queued_monotonic']
                self.pending.remove(item)
                self.pending_bytes -= len(item['frame'])

    def _connection_capabilities(self, capabilities):
        return [cap for cap in capabilities if cap != TRAINING_STATS_CAPABILITY] + sorted(self.negotiated_capabilities)

    def _prepare_payload(self, kind, payload):
        if kind in ('hello_ack', 'lifecycle'):
            payload = dict(payload, capabilities=self._connection_capabilities(payload['capabilities']))
        field = 'reason' if kind == 'lifecycle' else 'message'
        if kind in _RELIABLE and isinstance(payload.get(field), str):
            payload = dict(payload, **{field: bounded_diagnostic(payload[field])})
        return deepcopy(payload)

    def _encode(self, kind, payload, sequence):
        data = encode_message(dict(protocol_version=1, type=kind, session_id=self.session.session_id,
                                   sequence=sequence, payload=payload))
        if kind not in _OBSERVATIONS and len(data) > self.max_queue_bytes:
            raise FrameTooLarge(f'Frame of {len(data)} bytes exceeds the {self.max_queue_bytes}-byte send budget')
        return data

    def _retain_event(self, kind, payload, queued_at=None):
        """Retain only encodable reliable payloads with their original age baseline."""
        if kind not in _RELIABLE:
            return False
        if queued_at is None:
            queued_at = time.monotonic()
        payload = self._prepare_payload(kind, payload)
        aged = _aged_payload(payload, max(0., time.monotonic() - queued_at)) if kind in _AGED else payload
        try:
            self._encode(kind, aged, self.sequence + 1)
        except (ProtocolError, ValueError, TypeError):
            return False
        self.backlog.append((kind, payload, queued_at))
        return True

    def _send(self, kind, payload, *, retain=True, queued_at=None):
        if kind == 'training_stats' and TRAINING_STATS_CAPABILITY not in self.negotiated_capabilities:
            return _SendResult.DROPPED
        if self.close_after_flush:
            if retain and self._retain_event(kind, payload, queued_at):
                return _SendResult.DEFERRED
            return _SendResult.DROPPED
        payload = self._prepare_payload(kind, payload)
        if queued_at is None:
            queued_at = time.monotonic()
        aged = _aged_payload(payload, max(0., time.monotonic() - queued_at)) if kind in _AGED else payload
        sequence = self.sequence + 1
        try:
            data = self._encode(kind, aged, sequence)
        except FrameTooLarge as exc:
            if kind not in _OBSERVATIONS:
                raise
            # A replaceable observation over the frame limit is skipped, not fatal.
            self._report_oversized(kind, payload, exc)
            return _SendResult.DROPPED
        self.sequence = sequence
        # Partially sent frames are immutable; unsent observations can coalesce.
        self._drop_replaceable(kind, payload, len(data))
        key = self._observation_key(kind, payload)
        self._deferred.pop(key, None)
        if self.pending_bytes + len(data) > self.max_queue_bytes:
            if kind in _OBSERVATIONS:
                self._deferred[key] = payload, queued_at
                return _SendResult.DEFERRED
            raise _SendQueueFull('Server send queue overflow')
        self.pending.append(dict(kind=kind, payload=payload, frame=data, offset=0,
                                 sequence=self.sequence, queued_monotonic=queued_at, retain=retain))
        self.pending_bytes += len(data)
        if kind == 'error' and payload.get('fatal'):
            self.close_after_flush = True
            self._deferred.clear()
        return _SendResult.QUEUED

    def _report_oversized(self, kind, payload, exc):
        """Commit a once-per-run diagnostic only after accepting it for delivery."""
        scope = (kind, payload.get('run_id'))
        if scope in self._oversize_reported:
            return
        if kind == 'error' and payload.get('code') == 'FRAME_TOO_LARGE':
            return
        turbines = len(payload.get('turbines') or ())
        detail = f'; {turbines} turbines in the frame' if turbines else ''
        notice = dict(code='FRAME_TOO_LARGE', fatal=False, message=(
            f'{kind} skipped: {exc}{detail}. Reduce payload size to fit the frame and send queue limits.'))
        try:
            result = self._send('error', notice)
        except _SendQueueFull:
            result = _SendResult.DEFERRED if self._retain_event('error', notice) else _SendResult.DROPPED
        except FrameTooLarge:
            result = _SendResult.DROPPED
        if result is not _SendResult.DROPPED:
            self._oversize_reported.add(scope)

    def _retain_reliable_pending(self):
        retained = [(item['kind'], item['payload'], item['queued_monotonic']) for item in self.pending
                    if item['kind'] in _RELIABLE and item['retain']]
        if retained:
            self.backlog = deque(retained + list(self.backlog))
        self.pending.clear()
        self.pending_bytes = 0
        self._deferred.clear()

    def _disconnect(self):
        if self.client is not None: self.client.close()
        self._retain_reliable_pending()
        self.negotiated_capabilities = frozenset()
        self.client = None; self.ready = False; self.close_after_flush = False

    def _queue_session_events(self):
        if self.close_after_flush:
            return
        # One reliable batch may be outstanding. Backpressure leaves later
        # events in the session's bounded buffer, including while disconnected.
        if any(item['kind'] in {'lifecycle', 'safety_event', 'error'} for item in self.pending):
            return
        while self.backlog and not self.close_after_flush:
            kind, payload, queued_at = self.backlog[0]
            try:
                self._send(kind, payload, queued_at=queued_at)
            except FrameTooLarge as exc:
                self.backlog.popleft()
                self._report_oversized(kind, payload, exc)
                continue
            except _SendQueueFull:
                if self.pending:
                    return
                raise
            self.backlog.popleft()
        if any(item['kind'] in {'lifecycle', 'safety_event', 'error'} for item in self.pending):
            return
        events = self.session.poll()
        queued_at = time.monotonic()
        for index, (kind, payload) in enumerate(events):
            try:
                self._send(kind, payload, queued_at=queued_at)
            except FrameTooLarge as exc:
                self._report_oversized(kind, payload, exc)
            except _SendQueueFull:
                for reliable_kind, reliable_payload in events[index:]:
                    self._retain_event(reliable_kind, reliable_payload, queued_at)
                raise
            except (ProtocolError, ValueError, TypeError):
                for reliable_kind, reliable_payload in events[index + 1:]:
                    self._retain_event(reliable_kind, reliable_payload, queued_at)
                raise
        if not any(item['kind'] in {'lifecycle', 'safety_event', 'error'} for item in self.pending):
            for key, entry in list(self._deferred.items()):
                payload, queued_at = entry
                result = self._send(key[0], payload, queued_at=queued_at)
                if result is not _SendResult.DEFERRED and self._deferred.get(key) is entry:
                    self._deferred.pop(key)

    def _flush(self):
        if not self.pending:
            return
        item = self.pending[0]
        if item['kind'] in _AGED and item['offset'] == 0:
            if not select.select([], [self.client], [], 0)[1]:
                return
            # Always derive from the original ages: would-block retries must not
            # compound elapsed queue time. Once bytes leave, framing is immutable.
            elapsed = max(0., time.monotonic() - item['queued_monotonic'])
            payload = _aged_payload(item['payload'], elapsed)
            try:
                frame = self._encode(item['kind'], payload, item['sequence'])
            except FrameTooLarge as exc:
                self.pending.popleft()
                self.pending_bytes -= len(item['frame'])
                self._report_oversized(item['kind'], item['payload'], exc)
                return
            pending_bytes = self.pending_bytes + len(frame) - len(item['frame'])
            if pending_bytes > self.max_queue_bytes:
                if item['kind'] in _RELIABLE:
                    raise _SendQueueFull('Aged reliable frame requires draining the send queue')
                self.pending.popleft()
                self.pending_bytes -= len(item['frame'])
                key = self._observation_key(item['kind'], item['payload'])
                if key not in self._deferred or self._deferred[key][1] <= item['queued_monotonic']:
                    self._deferred[key] = item['payload'], item['queued_monotonic']
                return
            self.pending_bytes = pending_bytes
            item['frame'] = frame
        chunk = item['frame'][item['offset']:item['offset'] + 65536]
        sent = self.client.send(chunk)
        if sent == 0:
            raise ConnectionError('Socket closed during send')
        item['offset'] += sent
        self.pending_bytes -= sent
        if item['offset'] == len(item['frame']):
            self.pending.popleft()

    def _accept_client(self):
        """Hold one contender without reading it until the current owner disconnects."""
        if self._waiting_client is not None and time.monotonic() >= self._waiting_deadline:
            self._waiting_client.close()
            self._waiting_client = None
        if self._waiting_client is None:
            try:
                conn, _ = self.listener.accept()
            except BlockingIOError:
                return
            conn.setblocking(False)
            self._waiting_client = conn
            self._waiting_deadline = time.monotonic() + self.handshake_timeout
        if self.client is None:
            self.client, self._waiting_client = self._waiting_client, None
            self.decoder = FrameDecoder(); self._incoming = []
            # Waiting for ownership and completing hello share one deadline.
            self.deadline = self._waiting_deadline

    def poll(self):
        if self.closed: return
        self.session.tick()
        self._accept_client()
        if self.client is None: return
        try:
            if not self.ready and time.monotonic() >= self.deadline: raise ProtocolError('Handshake timeout')
            for _ in range(0 if self.close_after_flush else 4):
                if self.ready and self.backlog:
                    break
                if self._incoming:
                    messages, self._incoming = self._incoming, []
                else:
                    try: data = self.client.recv(16384)
                    except BlockingIOError: break
                    if not data: self._disconnect(); return
                    messages = self.decoder.feed(data)
                for index, message in enumerate(messages):
                    if self.ready and self.backlog:
                        # Retain only this decoded read; do not read more commands
                        # until reliable resume history has entered the send queue.
                        self._incoming = messages[index:]
                        break
                    if not self.ready:
                        if message['type'] != 'hello': raise ProtocolError('Expected hello')
                        resume = message['payload']['resume_session_id']
                        if resume not in (None, self.session.session_id):
                            self._send('error', dict(code='UNKNOWN_SESSION', message='Unknown session to resume; explicitly confirm a new backend connection to forget the previous identity',
                                                    fatal=True, rejected_session_id=resume), retain=False)
                            break
                        if resume is None and self.session_claimed:
                            if self.session.alive():
                                self._send('error', dict(code='RESUME_REQUIRED', message='Active session requires resume_session_id',
                                                        fatal=True, rejected_session_id=None), retain=False)
                                break
                            # A fresh protocol session must never inherit terminal
                            # lifecycle or safety events from the previous one.
                            self.backlog.clear()
                            self.session.poll()
                            self.session.clear_observations()
                            self.session.session_id = uuid.uuid4().hex
                            self.guard = SessionSequenceGuard(self.session.session_id)
                            self.sequence = 0
                        self.negotiated_capabilities = frozenset(message['payload']['capabilities']) & {TRAINING_STATS_CAPABILITY}
                        state = self.session.synchronization_state()
                        if self.session_binding is not None:
                            self.session_binding.publish(self.host, self.port, self.session.session_id)
                        self._send('hello_ack', dict(state, selected_version=1, resumed=resume is not None,
                                                    last_command_sequence=self.guard.last_sequence))
                        self.ready = True
                        self.session_claimed = True
                        if resume is not None:
                            self.session.replay_latest()
                    else:
                        self.guard.accept(message)
                        if message['type'] != 'command': raise ProtocolError('Expected command')
                        try:
                            if (self.shutdown_requested and
                                    message['payload']['command'] not in {'run.stop', 'session.sync'}):
                                raise ValueError('Bridge shutdown is pending; only cleanup and synchronization commands are available')
                            self.session.command(message['payload'])
                        except (ValueError, TypeError, OSError) as exc:
                            payload = dict(code='COMMAND_REJECTED', message=bounded_diagnostic(str(exc)), fatal=False,
                                           command_sequence=message['sequence'])
                            try:
                                self._send('error', payload)
                            except _SendQueueFull:
                                self._retain_event('error', payload)
                                raise
                if self.close_after_flush:
                    break
            if self.ready:
                self._queue_session_events()
            if self.pending:
                try:
                    self._flush()
                except BlockingIOError: pass
            if self.close_after_flush and not self.pending:
                self._disconnect()
        except (OSError, ValueError, RuntimeError):
            self._disconnect()

    def serve_forever(self):
        try:
            while not self.closed:
                self.poll(); time.sleep(.01)
        finally: self.close()

    def close(self):
        if self.closed: return
        self.closed = True; self._disconnect()
        if self._waiting_client is not None:
            self._waiting_client.close()
            self._waiting_client = None
        try:
            self.session.stop()
        finally:
            self.listener.close()


Server = BridgeServer
