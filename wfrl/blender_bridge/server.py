"""Single-owner loopback server with bounded nonblocking framed I/O."""
from collections import deque
from copy import deepcopy
import ipaddress
import socket
import time
import uuid
from .backend_session import BackendSession
from .messages import FrameDecoder, SessionSequenceGuard, ProtocolError, encode_message, TRAINING_STATS_CAPABILITY


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
        self.close_after_flush = False
        self.negotiated_capabilities = frozenset()
        self.ready = False
        self.session_claimed = False
        self.closed = False

    def _drop_replaceable_tail(self):
        if self.pending and self.pending[-1]['kind'] == 'snapshot' and self.pending[-1]['offset'] == 0:
            self.pending_bytes -= len(self.pending.pop()['frame'])

    def _connection_capabilities(self, capabilities):
        return [cap for cap in capabilities if cap != TRAINING_STATS_CAPABILITY] + sorted(self.negotiated_capabilities)

    def _send(self, kind, payload):
        if kind == 'training_stats' and TRAINING_STATS_CAPABILITY not in self.negotiated_capabilities:
            return
        if kind in ('hello_ack', 'lifecycle'):
            payload = dict(payload, capabilities=self._connection_capabilities(payload['capabilities']))
        if kind == 'training_stats':
            payload = deepcopy(payload)
        # An unsent Snapshot can be replaced or moved behind a reliable event.
        # A partially sent frame must finish to preserve framing.
        self._drop_replaceable_tail()
        self.sequence += 1
        data = encode_message(dict(protocol_version=1, type=kind, session_id=self.session.session_id,
                                   sequence=self.sequence, payload=payload))
        if self.pending_bytes + len(data) > self.max_queue_bytes:
            raise ProtocolError('Server send queue overflow')
        self.pending.append(dict(kind=kind, payload=payload, frame=data, offset=0,
                                 sequence=self.sequence, queued_monotonic=time.monotonic()))
        self.pending_bytes += len(data)
        if kind == 'error' and payload.get('fatal'):
            self.close_after_flush = True

    def _retain_reliable_pending(self):
        retained = [(item['kind'], item['payload']) for item in self.pending
                    if item['kind'] in {'lifecycle', 'safety_event', 'error'}]
        if retained:
            self.backlog = deque(retained + list(self.backlog))
        self.pending.clear()
        self.pending_bytes = 0

    def _disconnect(self):
        if self.client is not None: self.client.close()
        self._retain_reliable_pending()
        self.negotiated_capabilities = frozenset()
        self.client = None; self.ready = False; self.close_after_flush = False

    def _queue_session_events(self):
        while self.backlog:
            kind, payload = self.backlog[0]
            self._send(kind, payload)
            self.backlog.popleft()
        events = self.session.poll()
        for index, (kind, payload) in enumerate(events):
            try:
                self._send(kind, payload)
            except (ProtocolError, ValueError, TypeError):
                reliable = [event for event in events[index:]
                            if event[0] in {'lifecycle', 'safety_event', 'error'}]
                self.backlog.extend(reliable)
                raise

    def _flush(self):
        if not self.pending:
            return
        item = self.pending[0]
        if item['kind'] == 'training_stats' and item['offset'] == 0:
            # Always derive from the original ages: would-block retries must not
            # compound elapsed queue time. Once bytes leave, framing is immutable.
            elapsed = max(0., time.monotonic() - item['queued_monotonic'])
            payload = deepcopy(item['payload'])
            payload['source_age_seconds'] += elapsed
            for channel in payload['stats'].values():
                channel['source_age_seconds'] += elapsed
            frame = encode_message(dict(protocol_version=1, type=item['kind'],
                session_id=self.session.session_id, sequence=item['sequence'], payload=payload))
            pending_bytes = self.pending_bytes + len(frame) - len(item['frame'])
            if pending_bytes > self.max_queue_bytes:
                raise ProtocolError('Server send queue overflow')
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
                self.decoder = FrameDecoder(); self.deadline = time.monotonic() + self.handshake_timeout
        except BlockingIOError: pass
        if self.client is None: return
        try:
            if not self.ready and time.monotonic() >= self.deadline: raise ProtocolError('Handshake timeout')
            for _ in range(4):
                try: data = self.client.recv(16384)
                except BlockingIOError: break
                if not data: self._disconnect(); return
                for message in self.decoder.feed(data):
                    if not self.ready:
                        if message['type'] != 'hello': raise ProtocolError('Expected hello')
                        resume = message['payload']['resume_session_id']
                        if resume not in (None, self.session.session_id): raise ProtocolError('Unknown session to resume')
                        if resume is None and self.session_claimed:
                            if self.session.alive(): raise ProtocolError('Active session requires resume_session_id')
                            # A fresh protocol session must never inherit terminal
                            # lifecycle or safety events from the previous one.
                            self.backlog.clear()
                            self.session.poll()
                            self.session.session_id = uuid.uuid4().hex
                            self.guard = SessionSequenceGuard(self.session.session_id)
                            self.sequence = 0
                        self.negotiated_capabilities = frozenset(message['payload']['capabilities']) & {TRAINING_STATS_CAPABILITY}
                        self._send('hello_ack', dict(selected_version=1, capabilities=self.session.capabilities,
                                   run_status=self.session.status, mode=self.session.mode, resumed=resume is not None,
                                   run_id=getattr(self.session, 'run_id', None)))
                        self.ready = True
                        self.session_claimed = True
                    else:
                        self.guard.accept(message)
                        if message['type'] != 'command': raise ProtocolError('Expected command')
                        try: self.session.command(message['payload'])
                        except (ValueError, TypeError, OSError) as exc:
                            self._send('error', dict(code='COMMAND_REJECTED', message=str(exc), fatal=False))
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
