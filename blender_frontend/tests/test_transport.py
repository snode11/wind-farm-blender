import json
import socket
import struct
import time
import unittest
from wfrl_blender.transport import TransportClient
from wfrl_blender.protocol import encode_message


def wire(message):
    body = json.dumps(message).encode()
    return struct.pack('!I', len(body)) + body


def ack(session='test', sequence=0, resumed=False):
    return dict(protocol_version=1, type='hello_ack', session_id=session, sequence=sequence,
                payload=dict(selected_version=1, capabilities=[], run_status='READY', mode='demo', resumed=resumed))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen()
        self.server.settimeout(1)
        self.client = TransportClient(self.server.getsockname()[1])
        self.peer = None

    def tearDown(self):
        self.client.close()
        if self.peer:
            self.peer.close()
        self.server.close()

    def connect(self):
        self.client.connect()
        self.peer, _ = self.server.accept()
        self.peer.settimeout(1)
        self.client.poll()
        return self.peer.recv(8192)

    def wait_messages(self):
        result = []
        for _ in range(100):
            result.extend(self.client.poll())
            if result or self.client.last_error:
                break
            time.sleep(.001)
        return result

    def test_handshake_fragment_and_disconnect_preserves_session(self):
        hello = self.connect()
        self.assertEqual(json.loads(hello[4:])['type'], 'hello')
        self.assertEqual(self.client.status, 'DISCONNECTED')
        data = wire(ack())
        self.peer.sendall(data[:3])
        self.assertEqual(self.client.poll(), [])
        self.peer.sendall(data[3:])
        self.assertEqual(self.wait_messages(), [ack()])
        self.assertEqual(self.client.status, 'CONNECTED')
        self.peer.close()
        self.peer = None
        self.wait_messages()
        self.assertEqual(self.client.status, 'DISCONNECTED')
        self.assertEqual(self.client.session_id, 'test')
        self.assertTrue(self.client.last_error)

    def test_handshake_timeout(self):
        now = [0.0]
        self.client = TransportClient(self.server.getsockname()[1], clock=lambda: now[0], timeout_seconds=.2)
        self.connect()
        now[0] = 1
        self.assertEqual(self.client.poll(), [])
        self.assertIn('timed out', self.client.last_error)

    def test_invalid_version_fails(self):
        self.connect()
        message = ack()
        message['protocol_version'] = 2
        self.peer.sendall(wire(message))
        self.wait_messages()
        self.assertEqual(self.client.status, 'DISCONNECTED')
        self.assertTrue(self.client.last_error)

    def test_send_before_handshake_fails(self):
        self.client.send(ack())
        self.assertTrue(self.client.last_error)

    def establish(self, message=None):
        self.connect()
        self.peer.sendall(wire(message or ack()))
        self.wait_messages()
        self.assertEqual(self.client.status, 'CONNECTED')

    def test_reconnect_preserves_sequence_and_resume_request(self):
        self.establish()
        self.client.close()
        self.peer.close()
        self.peer = None
        hello = self.connect()
        self.assertEqual(json.loads(hello[4:])['payload']['resume_session_id'], 'test')
        self.peer.sendall(wire(ack(sequence=1, resumed=True)))
        self.assertEqual(self.wait_messages()[0]['sequence'], 1)
        self.assertEqual(self.client.status, 'CONNECTED')

    def test_reconnect_rejects_replayed_ack(self):
        self.establish()
        self.peer.close()
        self.peer = None
        self.connect()
        self.peer.sendall(wire(ack(resumed=True)))
        self.wait_messages()
        self.assertIn('sequence', self.client.last_error)

    def test_malformed_frame(self):
        self.connect()
        self.peer.sendall(struct.pack('!I', 1) + b'\xff')
        self.wait_messages()
        self.assertTrue(self.client.last_error)
        self.assertEqual(self.client.status, 'DISCONNECTED')

    def test_outgoing_overflow_is_explicit(self):
        self.client = TransportClient(self.server.getsockname()[1], max_queue_bytes=1)
        self.connect()
        self.assertIn('overflow', self.client.last_error)

    def test_message_processing_budget(self):
        self.client = TransportClient(self.server.getsockname()[1], max_messages_per_poll=1)
        self.connect()
        self.peer.sendall(wire(ack()) + wire(ack(sequence=1)))
        self.assertEqual(len(self.wait_messages()), 1)
        self.assertEqual(self.client.status, 'CONNECTED')
        self.assertEqual(self.client.poll(), [])
        self.assertIn('handshake', self.client.last_error)

    def test_partial_send_and_would_block(self):
        self.establish()
        real_socket = self.client._socket

        class PartialSocket:
            blocked = True
            def send(self, data):
                if self.blocked:
                    self.blocked = False
                    raise BlockingIOError()
                return real_socket.send(data[:3])
            def recv(self, count):
                return real_socket.recv(count)
            def close(self):
                real_socket.close()

        self.client._socket = PartialSocket()
        # Queue a valid frame internally to isolate partial-write mechanics.
        frame = ack(sequence=10)
        self.client._enqueue(frame)
        self.client.poll()
        self.assertTrue(self.client._outgoing)
        for _ in range(100):
            self.client.poll()
            if not self.client._outgoing:
                break
        self.assertFalse(self.client._outgoing)
        expected = encode_message(frame)
        received = b''
        while len(received) < len(expected):
            received += self.peer.recv(8192)
        self.assertEqual(received, expected)

    def test_incoming_overflow_is_explicit(self):
        self.client = TransportClient(self.server.getsockname()[1], max_pending_messages=1)
        self.connect()
        self.peer.sendall(wire(ack()) * 2)
        self.wait_messages()
        self.assertIn('incoming queue overflow', self.client.last_error)

    def test_connection_refused(self):
        self.server.close()
        self.client.connect()
        self.wait_messages()
        self.assertEqual(self.client.status, 'DISCONNECTED')
        self.assertTrue(self.client.last_error)

    def test_public_send_and_repeated_sequence_rejected(self):
        self.establish()
        message = dict(protocol_version=1, type='command', session_id='test', sequence=1,
                       payload=dict(command='session.sync', arguments={}))
        self.client.send(message)
        self.assertIsNone(self.client.last_error)
        self.client.poll()
        self.assertEqual(json.loads(self.peer.recv(8192)[4:]), message)
        self.client.send(message)
        self.assertIn('sequence', self.client.last_error)

    def test_old_session_and_repeated_inbound_sequence_fail(self):
        self.establish()
        self.peer.sendall(wire(dict(protocol_version=1, type='lifecycle', session_id='old',
                                    sequence=1, payload=dict(run_status='RUNNING', reason=None,
                                                             mode='demo', capabilities=[]))))
        self.wait_messages()
        self.assertIn('session', self.client.last_error)

    def test_fatal_error_is_delivered_then_disconnects(self):
        self.establish()
        fatal = dict(protocol_version=1, type='error', session_id='test', sequence=1,
                     payload=dict(code='FATAL', message='cannot continue', fatal=True))
        self.peer.sendall(wire(fatal))
        self.assertEqual(self.wait_messages(), [fatal])
        self.assertEqual(self.client.status, 'DISCONNECTED')
        self.assertIn('cannot continue', self.client.last_error)

    def test_new_session_resets_outbound_counter(self):
        self.establish()
        self.client.send(dict(protocol_version=1, type='command', session_id='test', sequence=7,
                              payload=dict(command='session.sync', arguments={})))
        self.assertEqual(self.client.next_sequence, 8)
        self.peer.close()
        self.peer = None
        self.connect()
        self.peer.sendall(wire(ack(session='new')))
        self.wait_messages()
        self.assertEqual(self.client.session_id, 'new')
        self.assertEqual(self.client.next_sequence, 1)

    def test_no_dns_or_non_loopback(self):
        for host in ['localhost', 'example.com', '192.168.1.2']:
            with self.assertRaises(ValueError):
                TransportClient(5555, host=host)


if __name__ == '__main__':
    unittest.main()
