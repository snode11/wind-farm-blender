import json
import math
import struct
import unittest

from wfrl.blender_bridge.messages import (
    FrameDecoder, ProtocolError, SessionSequenceGuard, encode_message,
    normalize_rotor_speed, validate_message, MAX_FRAME_BYTES, data_age_seconds, channel_is_stale,
)


def msg(kind='command', sequence=1, session='s', **payload):
    return dict(protocol_version=1, type=kind, session_id=session,
                sequence=sequence, payload=payload or {'command': 'run.start', 'arguments': {}})


def channel(value=4.0, **changes):
    result = dict(value=value, unit='rpm', validity='valid', error=None,
                  fidelity='DIRECT', provenance={'backend_session': 's', 'channel': 'RotSpeed'},
                  source_age_seconds=0, stale_after_seconds=2)
    result.update(changes)
    return result


def snapshot(**changes):
    result = msg('snapshot', mode='interactive_training', step=1,
                 timestamp={'value': 0.1, 'timebase': 'simulation_seconds'},
                 turbines=[{'turbine_id': 'T1', 'channels': {'rotor_speed': channel()}}],
                 farm={})
    result['payload'].update(changes)
    return result


class CodecTests(unittest.TestCase):
    def test_fragmented_and_coalesced(self):
        data = encode_message(msg()) + encode_message(msg(sequence=2))
        decoder = FrameDecoder()
        got = []
        for byte in data:
            got += decoder.feed(bytes([byte]))
        self.assertEqual([m['sequence'] for m in got], [1, 2])
        self.assertEqual(len(FrameDecoder().feed(data)), 2)

    def test_invalid_frame(self):
        for raw in [b'\xff', b'[]', b'{"protocol_version":1}', b'{"x":NaN}', b'{"x":1,"x":2}']:
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                FrameDecoder().feed(struct.pack('!I', len(raw)) + raw)
        for length in [0, MAX_FRAME_BYTES + 1]:
            with self.assertRaises(ProtocolError):
                FrameDecoder().feed(struct.pack('!I', length))

    def test_envelope(self):
        for key in ['protocol_version', 'type', 'session_id', 'sequence', 'payload']:
            bad = msg(); del bad[key]
            with self.assertRaises(ProtocolError): encode_message(bad)
        for key, value in [('protocol_version', 2), ('sequence', -1), ('sequence', True), ('session_id', ''), ('type', 'unknown')]:
            bad = msg(); bad[key] = value
            with self.assertRaises(ProtocolError): encode_message(bad)

    def test_handshake_and_binary(self):
        encode_message(msg('hello', 0, '', supported_versions=[1], capabilities=[], resume_session_id=None))
        encode_message(msg('hello_ack', selected_version=1, capabilities=[], run_status='READY', mode='demo', resumed=False))
        with self.assertRaises(ProtocolError): encode_message(msg('hello_ack', selected_version=2))
        with self.assertRaisesRegex(ProtocolError, 'binary'): encode_message(msg('wake_binary'))

    def test_sequence_session(self):
        guard = SessionSequenceGuard('s')
        guard.accept(msg(sequence=1)); guard.accept(msg(sequence=5))
        for message in [msg(sequence=5), msg(sequence=4), msg(sequence=6, session='old')]:
            with self.assertRaises(ProtocolError): guard.accept(message)
        guard.accept(msg(sequence=6))

    def test_sources_null_and_nonfinite(self):
        validate_message(snapshot())
        for value in [math.nan, math.inf, -math.inf, None]:
            bad = snapshot(); bad['payload']['turbines'][0]['channels']['rotor_speed']['value'] = value
            with self.assertRaises(ProtocolError): encode_message(bad)
        bad = snapshot(); del bad['payload']['turbines'][0]['channels']['rotor_speed']['provenance']
        with self.assertRaises(ProtocolError): encode_message(bad)
        good = snapshot(); good['payload']['turbines'][0]['channels']['rotor_speed'] = channel(None, validity='waiting', error='No first frame')
        encode_message(good)
        good['payload']['farm'] = {'power': channel(3, fidelity='EXPORTED', provenance={'file': 'run.csv', 'channel': 'power'})}
        encode_message(good)

    def test_aliases(self):
        self.assertEqual(normalize_rotor_speed({'rotorspeed': 3}), {'rotor_speed': 3})
        self.assertEqual(normalize_rotor_speed({'rotorspeed': 3, 'rotor_speed': 3}), {'rotor_speed': 3})
        with self.assertRaises(ProtocolError): normalize_rotor_speed({'rotorspeed': 3, 'rotor_speed': 4})
        bad = snapshot(); bad['payload']['turbines'][0]['channels']['rotorspeed'] = channel()
        with self.assertRaises(ProtocolError): encode_message(bad)

    def test_schema_types(self):
        for kind in ['command', 'lifecycle', 'snapshot', 'safety_event', 'curve', 'wake', 'error']:
            with self.subTest(kind=kind), self.assertRaises(ProtocolError): encode_message(msg(kind, nonsense=1))
        encode_message(msg('lifecycle', run_status='RUNNING', reason=None,
                           mode='interactive_training', capabilities=['pause', 'single_step']))
        for changes in ({'mode': None}, {'capabilities': None}, {'capabilities': ['']}):
            payload = dict(run_status='RUNNING', reason=None,
                           mode='interactive_training', capabilities=['pause'])
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ProtocolError):
                encode_message(msg('lifecycle', **payload))
        encode_message(msg('error', code='TEST', message='test', fatal=False))

    def test_data_families(self):
        context = dict(mode='replay', step=2, timestamp={'value': 2, 'timebase': 'replay_seconds'})
        encode_message(msg('curve', **context, channel='power', turbine_id=None, data=channel([[0, 1], [1, 2]])))
        encode_message(msg('wake', **context, encoding='json', turbine_ids=['T1'], data=channel([[[1.0]]], stale_after_seconds=5)))
        encode_message(msg('safety_event', **context, event_id='e1', severity='warning', message='limited', turbine_id='T1', data=channel(True, stale_after_seconds=10)))
        bad = snapshot(); bad['payload']['turbines'][0]['channels']['rotor_speed'] = channel(4, validity='stale', error='age threshold exceeded')
        encode_message(bad)

    def test_decoder_poison_and_nested_types(self):
        decoder = FrameDecoder()
        with self.assertRaises(ProtocolError): decoder.feed(struct.pack('!I', 0))
        with self.assertRaises(ProtocolError): decoder.feed(encode_message(msg()))
        for field in ['type', 'protocol_version', 'sequence', 'session_id']:
            bad = msg(); bad[field] = {}
            with self.assertRaises(ProtocolError): validate_message(bad)
        with self.assertRaises(ProtocolError): encode_message(msg(arguments={'bad': '\ud800'}, command='run.start'))
        for raw in (b'{"protocol_version":1,"type":"error","session_id":"s","sequence":1,'
                    b'"payload":{"code":"BAD","message":"\\ud800","fatal":false}}',
                    b'{"protocol_version":1,"type":"command","session_id":"s","sequence":1,'
                    b'"payload":{"command":"run.start","arguments":{"\\ud800":1}}}'):
            decoder = FrameDecoder()
            with self.assertRaisesRegex(ProtocolError, 'UTF-8'):
                decoder.feed(struct.pack('!I', len(raw)) + raw)

    def test_oversized_encoding(self):
        with self.assertRaises(ProtocolError): encode_message(msg(command='run.start', arguments={'large': 'a' * MAX_FRAME_BYTES}))

    def test_monotonic_age(self):
        data = channel(source_age_seconds=0.5)
        self.assertEqual(data_age_seconds(data, 100, 101), 1.5)
        self.assertFalse(channel_is_stale(data, 100, 101))
        self.assertTrue(channel_is_stale(data, 100, 102))
        bad = snapshot(); bad['payload']['turbines'][0]['channels']['rotor_speed']['stale_after_seconds'] = 999
        with self.assertRaises(ProtocolError): encode_message(bad)

    def test_scalar_numeric_channels_and_curve_units(self):
        for key in ['rotor_speed', 'power', 'yaw', 'pitch']:
            for value in ['not a number', True, [1, 2]]:
                bad = snapshot(); bad['payload']['turbines'][0]['channels'] = {key: channel(value)}
                with self.subTest(key=key, value=value), self.assertRaises(ProtocolError): encode_message(bad)
        context = dict(mode='replay', step=2, timestamp={'value': 2, 'timebase': 'replay_seconds'})
        with self.assertRaises(ProtocolError):
            encode_message(msg('curve', **context, channel='rotor_speed', turbine_id='T1', data=channel([[1, 2]], unit='m/s')))
