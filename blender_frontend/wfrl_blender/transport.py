"""Bounded, nonblocking loopback transport; call poll from Blender's timer."""
from collections import deque
import errno
import ipaddress
import select
import socket
import time

from .protocol import FrameDecoder, ProtocolError, encode_message, TRAINING_STATS_CAPABILITY


class TransportClient:
    """A lost socket never implies a change to the backend's run status.

    Envelopes returned by poll include hello_ack for lifecycle reconciliation.
    Callers supply complete command envelopes with increasing sequence numbers.
    next_sequence is the next available outbound sequence after a reconnect.
    """

    def __init__(self, port, host='127.0.0.1', timeout_seconds=5.0,
                 max_queue_bytes=2 * 1024 * 1024, max_bytes_per_poll=65536,
                 max_messages_per_poll=64, max_io_calls_per_poll=16,
                 max_pending_messages=1024, clock=time.monotonic,
                 socket_factory=socket.socket, capabilities=(TRAINING_STATS_CAPABILITY,)):
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError('host must be a loopback IP literal; DNS is disabled') from exc
        if not address.is_loopback:
            raise ValueError('host must be a loopback IP literal')
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('port must be between 1 and 65535')
        if timeout_seconds <= 0 or min(max_queue_bytes, max_bytes_per_poll, max_messages_per_poll, max_io_calls_per_poll, max_pending_messages) < 1:
            raise ValueError('timeout and queue/poll limits must be positive')
        if not isinstance(capabilities, (list, tuple, set, frozenset)) or any(not isinstance(cap, str) or not cap.strip() or 'binary' in cap for cap in capabilities):
            raise ValueError('invalid client capabilities')
        self.capabilities = frozenset(capabilities)
        self.negotiated_capabilities = frozenset()
        self.host, self.port = host, port
        self.timeout_seconds = timeout_seconds
        self.max_queue_bytes = max_queue_bytes
        self.max_bytes_per_poll = max_bytes_per_poll
        self.max_messages_per_poll = max_messages_per_poll
        self.max_io_calls_per_poll = max_io_calls_per_poll
        self.max_pending_messages = max_pending_messages
        self._clock, self._socket_factory = clock, socket_factory
        self._family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        self.status = 'DISCONNECTED'
        self.progress = 'Disconnected'
        self.last_error = None
        self.session_id = None
        self._last_inbound = -1
        self._last_outbound = 0
        self._socket = None
        self._phase = 'closed'
        self._outgoing = deque()
        self._incoming = deque()
        self._queued_bytes = 0
        self._decoder = FrameDecoder()
        self._deadline = 0

    @property
    def next_sequence(self):
        return self._last_outbound + 1

    def connect(self):
        self.close()
        self.last_error = None
        self.progress = 'Connecting'
        self._deadline = self._clock() + self.timeout_seconds
        try:
            self._socket = self._socket_factory(self._family, socket.SOCK_STREAM)
            self._socket.setblocking(False)
            result = self._socket.connect_ex((self.host, self.port))
            if result not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR):
                raise OSError(result, 'connection failed')
            self._phase = 'connecting'
        except OSError as exc:
            self._fail(str(exc))

    def close(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        self._phase = 'closed'
        self.negotiated_capabilities = frozenset()
        self.status = 'DISCONNECTED'
        self.progress = 'Disconnected; backend state unconfirmed' if self.session_id else 'Disconnected'
        self._outgoing.clear()
        self._incoming.clear()
        self._queued_bytes = 0
        self._decoder = FrameDecoder()

    def _fail(self, error):
        self.close()
        self.last_error = str(error)
        self.progress = 'Failed: ' + self.last_error

    def _enqueue(self, message):
        frame = encode_message(message)
        if self._queued_bytes + len(frame) > self.max_queue_bytes:
            raise ProtocolError('outgoing queue overflow')
        self._outgoing.append(memoryview(frame))
        self._queued_bytes += len(frame)

    def send(self, message):
        try:
            if self.status != 'CONNECTED':
                raise ProtocolError('cannot send control before handshake confirmation')
            if message.get('type') != 'command':
                raise ProtocolError('client may send only commands')
            if message.get('session_id') != self.session_id:
                raise ProtocolError('outbound session mismatch')
            sequence = message.get('sequence')
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= self._last_outbound:
                raise ProtocolError('outbound sequence must increase')
            self._enqueue(message)
            self._last_outbound = sequence
        except (ValueError, TypeError, AttributeError) as exc:
            self._fail(str(exc))

    def _accept(self, message):
        if self._phase == 'handshake':
            if message['type'] != 'hello_ack':
                raise ProtocolError('expected hello_ack before application messages')
            payload = message['payload']
            session = message['session_id']
            if payload['selected_version'] != 1:
                raise ProtocolError('unsupported negotiated protocol version')
            if payload['resumed']:
                if session != self.session_id or message['sequence'] <= self._last_inbound:
                    raise ProtocolError('invalid resumed session or sequence')
            else:
                if session == self.session_id:
                    raise ProtocolError('new session must use a new session_id')
                self._last_inbound = -1
                self._last_outbound = 0
            extensions = set(payload['capabilities']) & {TRAINING_STATS_CAPABILITY}
            if not extensions <= self.capabilities:
                raise ProtocolError('unrequested training_stats_v1 capability')
            self.negotiated_capabilities = frozenset(extensions)
            self.session_id = session
            self._phase = 'connected'
            self.status = 'CONNECTED'
            self.progress = 'Connected; session confirmed'
        elif message['type'] in ('hello', 'hello_ack'):
            raise ProtocolError('unexpected repeated handshake')
        if message['type'] == 'training_stats' and TRAINING_STATS_CAPABILITY not in self.negotiated_capabilities:
            raise ProtocolError('training_stats_v1 not negotiated')
        if message['type'] == 'lifecycle' and TRAINING_STATS_CAPABILITY in message['payload']['capabilities'] and TRAINING_STATS_CAPABILITY not in self.negotiated_capabilities:
            raise ProtocolError('lifecycle cannot introduce unnegotiated training_stats_v1')
        if message['session_id'] != self.session_id:
            raise ProtocolError('inbound session mismatch')
        if message['sequence'] <= self._last_inbound:
            raise ProtocolError('inbound sequence must increase')
        self._last_inbound = message['sequence']

    def poll(self):
        if self._socket is None:
            return []
        result = []
        try:
            if self._phase in ('connecting', 'handshake') and self._clock() >= self._deadline:
                raise TimeoutError('connection/handshake timed out')
            if self._phase == 'connecting':
                _, writable, exceptional = select.select([], [self._socket], [self._socket], 0)
                if not writable and not exceptional:
                    return []
                error = self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise OSError(error, 'connection failed')
                self._enqueue(dict(protocol_version=1, type='hello', session_id='', sequence=0,
                                   payload=dict(supported_versions=[1], capabilities=sorted(self.capabilities),
                                                resume_session_id=self.session_id)))
                self._phase = 'handshake'
                self.progress = 'Awaiting handshake'
            budget = self.max_bytes_per_poll
            io_calls = 0
            while self._outgoing and budget and io_calls < self.max_io_calls_per_poll:
                io_calls += 1
                chunk = self._outgoing[0]
                try:
                    sent = self._socket.send(chunk[:budget])
                except (BlockingIOError, InterruptedError):
                    break
                if sent == 0:
                    raise ConnectionError('socket closed during send; backend state unconfirmed')
                budget -= sent
                self._queued_bytes -= sent
                if sent == len(chunk):
                    self._outgoing.popleft()
                else:
                    self._outgoing[0] = chunk[sent:]
            # One recv bounds decoding work even for a peer flooding tiny messages.
            if not self._incoming:
                try:
                    chunk = self._socket.recv(self.max_bytes_per_poll)
                except (BlockingIOError, InterruptedError):
                    chunk = None
                if chunk == b'':
                    raise ConnectionError('connection lost; backend state unconfirmed')
                if chunk:
                    messages = self._decoder.feed(chunk)
                    if len(messages) > self.max_pending_messages:
                        raise ProtocolError('incoming queue overflow')
                    self._incoming.extend(messages)
            for _ in range(min(len(self._incoming), self.max_messages_per_poll)):
                message = self._incoming.popleft()
                self._accept(message)
                result.append(message)
                if message['type'] == 'error' and message['payload']['fatal']:
                    self._fail(message['payload']['message'])
                    break
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._fail(str(exc))
            return []
        return result
