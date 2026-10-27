"""Single-owner loopback server with bounded nonblocking framed I/O."""
from collections import deque
from copy import deepcopy
import ipaddress
import socket
import time
import uuid
from .backend_session import BackendSession, _aged_payload
from .messages import FrameDecoder, SessionSequenceGuard, ProtocolError, encode_message, TRAINING_STATS_CAPABILITY


class _SendQueueFull(ProtocolError):
    pass


class BridgeServer:
    def __init__(self, host='127.0.0.1', port=8765, session=None, handshake_timeout=5., max_queue_bytes=2*1024*1024):
        address = ipaddress.ip_address(host)
        if not address.is_loopback: raise ValueError('Bridge binds only loopback IP literals')
        self.session = session or BackendSession()
        self.listener = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port)); self.listener.listen(1); self.listener.setblocking(False)
        self.port = self.listener.getsockname()[1]
        self.client = None
        self.sequence = 0
        self.guard = SessionSequenceGuard(self.session.session_id)
        self.handshake_timeout = handshake_timeout
        self.max_queue_bytes = max_queue_bytes
        self.pending = deque()
        self.pending_bytes = 0
        self.backlog = deque()
        self._deferred = {}
        self.close_after_flush = False
        self.negotiated_capabilities = frozenset()
        self.ready = False
        self.session_claimed = False
        self.closed = False

    def _observation_key(self, kind, payload):
        return kind, payload.get('record_kind') if kind == 'training_stats' else None

    def _drop_replaceable(self, kind, payload):
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
            prioritize = kind in {'lifecycle', 'safety_event', 'error'} and item['kind'] == 'snapshot'
            obsolete = identifies_run and item['payload'].get('run_id') != payload['run_id']
            if same or prioritize or obsolete:
                if prioritize and not obsolete:
                    self._deferred[('snapshot', None)] = item['payload'], item['queued_monotonic']
                self.pending.remove(item)
                self.pending_bytes -= len(item['frame'])

    def _connection_capabilities(self, capabilities):
        return [cap for cap in capabilities if cap != TRAINING_STATS_CAPABILITY] + sorted(self.negotiated_capabilities)

    def _send(self, kind, payload, *, retain=True):
        if kind == 'training_stats' and TRAINING_STATS_CAPABILITY not in self.negotiated_capabilities:
            return
        if kind in ('hello_ack', 'lifecycle'):
            payload = dict(payload, capabilities=self._connection_capabilities(payload['capabilities']))
        if kind in {'training_stats', 'snapshot'}:
            payload = deepcopy(payload)
        # Partially sent frames are immutable; unsent observations can coalesce.
        self._drop_replaceable(kind, payload)
        key = self._observation_key(kind, payload)
        self._deferred.pop(key, None)
        self.sequence += 1
        data = encode_message(dict(protocol_version=1, type=kind, session_id=self.session.session_id,
                                   sequence=self.sequence, payload=payload))
        if self.pending_bytes + len(data) > self.max_queue_bytes:
            if kind in {'snapshot', 'training_stats'}:
                self._deferred[key] = payload, time.monotonic()
                return
            raise _SendQueueFull('Server send queue overflow')
        self.pending.append(dict(kind=kind, payload=payload, frame=data, offset=0,
                                 sequence=self.sequence, queued_monotonic=time.monotonic(), retain=retain))
        self.pending_bytes += len(data)
        if kind == 'error' and payload.get('fatal'):
            self.close_after_flush = True

    def _retain_reliable_pending(self):
        retained = [(item['kind'], item['payload']) for item in self.pending
                    if item['kind'] in {'lifecycle', 'safety_event', 'error'} and item.get('retain', True)]
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
        # One reliable batch may be outstanding. Backpressure leaves later
        # events in the session's bounded buffer, including while disconnected.
        if any(item['kind'] in {'lifecycle', 'safety_event', 'error'} for item in self.pending):
            return
        while self.backlog:
            kind, payload = self.backlog[0]
            try:
                self._send(kind, payload)
            except _SendQueueFull:
                if self.pending:
                    return
                raise
            self.backlog.popleft()
        if any(item['kind'] in {'lifecycle', 'safety_event', 'error'} for item in self.pending):
            return
        events = self.session.poll()
        for index, (kind, payload) in enumerate(events):
            try:
                self._send(kind, payload)
            except (ProtocolError, ValueError, TypeError):
                reliable = [event for event in events[index:]
                            if event[0] in {'lifecycle', 'safety_event', 'error'}]
                self.backlog.extend(reliable)
                raise
        if not any(item['kind'] in {'lifecycle', 'safety_event', 'error'} for item in self.pending):
            for key, (payload, queued_at) in list(self._deferred.items()):
                self._send(key[0], _aged_payload(payload, max(0., time.monotonic() - queued_at)))

    def _flush(self):
        if not self.pending:
            return
        item = self.pending[0]
        if item['kind'] in {'training_stats', 'snapshot'} and item['offset'] == 0:
            # Always derive from the original ages: would-block retries must not
            # compound elapsed queue time. Once bytes leave, framing is immutable.
            elapsed = max(0., time.monotonic() - item['queued_monotonic'])
            payload = _aged_payload(item['payload'], elapsed)
            frame = encode_message(dict(protocol_version=1, type=item['kind'],
                session_id=self.session.session_id, sequence=item['sequence'], payload=payload))
            pending_bytes = self.pending_bytes + len(frame) - len(item['frame'])
            if pending_bytes > self.max_queue_bytes:
                raise _SendQueueFull('Server send queue overflow')
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

    def poll(self):
        if self.closed: return
        if hasattr(self.session, 'tick'):
            self.session.tick()
        # A client may close and reconnect before the next 10 ms poll. Reap
        # its EOF before accepting, or the stale owner rejects the resume.
        # MSG_PEEK leaves any live owner's queued commands untouched.
        if self.client is not None:
            try:
                if self.client.recv(1, socket.MSG_PEEK) == b'':
                    self._disconnect()
            except BlockingIOError:
                pass
            except OSError:
                self._disconnect()
        try:
            conn, _ = self.listener.accept()
            if self.client is not None: conn.close()
            else:
                self.client = conn; conn.setblocking(False)
                self.decoder = FrameDecoder(); self._incoming = []
                self.deadline = time.monotonic() + self.handshake_timeout
        except BlockingIOError: pass
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
                            if hasattr(self.session, 'clear_observations'):
                                self.session.clear_observations()
                            self.session.session_id = uuid.uuid4().hex
                            self.guard = SessionSequenceGuard(self.session.session_id)
                            self.sequence = 0
                        self.negotiated_capabilities = frozenset(message['payload']['capabilities']) & {TRAINING_STATS_CAPABILITY}
                        if hasattr(self.session, 'synchronization_state'):
                            state = self.session.synchronization_state()
                        else:
                            # Unversioned producers keep strict transition handling;
                            # the transport cannot infer a safe history watermark.
                            state = dict(capabilities=self.session.capabilities, run_status=self.session.status,
                                         mode=self.session.mode, run_id=getattr(self.session, 'run_id', None))
                        self._send('hello_ack', dict(state, selected_version=1, resumed=resume is not None))
                        self.ready = True
                        self.session_claimed = True
                        if resume is not None and hasattr(self.session, 'replay_latest'):
                            self.session.replay_latest()
                    else:
                        self.guard.accept(message)
                        if message['type'] != 'command': raise ProtocolError('Expected command')
                        try: self.session.command(message['payload'])
                        except (ValueError, TypeError, OSError) as exc:
                            payload = dict(code='COMMAND_REJECTED', message=str(exc), fatal=False)
                            try:
                                self._send('error', payload)
                            except (ProtocolError, ValueError, TypeError):
                                self.backlog.append(('error', payload))
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
        self.closed = True; self._disconnect(); self.session.stop()
        self.listener.close()


Server = BridgeServer
