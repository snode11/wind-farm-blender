"""Training extension schema and connection negotiation without Blender."""
import time
import pytest
from wfrl_blender.protocol import (ProtocolError, validate_message, encode_message,
    FrameDecoder, channel_is_stale, TRAINING_STATS_CHANNELS)
from wfrl_blender.transport import TransportClient
from wfrl.blender_bridge.server import BridgeServer


def record(kind='iteration_stats'):
    return dict(run_id='run-1', record_kind=kind, mode='formal_training', step=4,
                agent_step=8, timestamp=dict(value=1000., timebase='unix_seconds'),
                iteration=1, phase='sampling', source_age_seconds=12., stale_after_seconds=30.,
                stats={} if kind == 'progress' else {name: dict(value=None,
                    unit='MW' if name == 'mean_power' else '', validity='unsupported',
                    error='not supplied', fidelity='EXPORTED', provenance=dict(file='run.jsonl', channel=name),
                    source_age_seconds=12., stale_after_seconds=30.) for name in TRAINING_STATS_CHANNELS})


def envelope(payload=None, sequence=2):
    return dict(protocol_version=1, type='training_stats', session_id='session',
                sequence=sequence, payload=record() if payload is None else payload)


def test_roundtrip_and_independent_freshness():
    for kind in ('progress', 'iteration_stats'):
        message = envelope(record(kind))
        assert FrameDecoder().feed(encode_message(message)) == [message]
    old = record()['stats']['mean_power']
    assert channel_is_stale(old, 10, 28)
    assert not channel_is_stale(record('progress'), 28, 28)


@pytest.mark.parametrize('field', list(record()))
def test_missing_fields(field):
    p = record(); del p[field]
    with pytest.raises(ProtocolError): validate_message(envelope(p))


@pytest.mark.parametrize('field,value', [('step', True), ('agent_step', -1), ('iteration', 2**53),
    ('episode', None), ('phase', 'failed'), ('run_id', ''), ('source_age_seconds', -1),
    ('stale_after_seconds', 2), ('timestamp', dict(value=2, timebase='simulation_seconds'))])
def test_invalid_context(field, value):
    p = record(); p[field] = value
    with pytest.raises(ProtocolError): validate_message(envelope(p))


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, [], '3'])
def test_invalid_statistic(value):
    p = record(); p['stats']['mean_power'].update(value=value, validity='valid', error=None)
    with pytest.raises(ProtocolError): validate_message(envelope(p))


def test_progress_cannot_contain_stats_and_channel_metadata_required():
    p = record(); p['record_kind'] = 'progress'
    with pytest.raises(ProtocolError): validate_message(envelope(p))
    for field in record()['stats']['mean_power']:
        p = record(); del p['stats']['mean_power'][field]
        with pytest.raises(ProtocolError): validate_message(envelope(p))


class Session:
    session_id = 'session'
    status = 'READY'
    mode = 'demo'
    capabilities = ['pause', 'training_stats_v1']
    def __init__(self): self.events = []
    def poll(self):
        events, self.events = self.events, []
        return events
    def alive(self): return True
    def stop(self): pass
    def command(self, payload): pass


def pump(server, client):
    result = []
    for _ in range(50):
        server.poll(); result.extend(client.poll()); time.sleep(.001)
    return result


def test_server_intersection_mode_switch_and_reconnect():
    session = Session(); server = BridgeServer(port=0, session=session)
    client = TransportClient(server.port)
    try:
        client.connect(); messages = pump(server, client)
        assert 'training_stats_v1' in messages[0]['payload']['capabilities']
        session.events = [('training_stats', record())]
        assert pump(server, client)[0]['type'] == 'training_stats'
        last = client._last_inbound
        client.close(); pump(server, client)
        client.capabilities = frozenset(); client.connect()
        messages = pump(server, client)
        assert messages[0]['sequence'] > last
        assert messages[0]['payload']['resumed']
        assert 'training_stats_v1' not in messages[0]['payload']['capabilities']
        session.events = [('lifecycle', dict(run_status='RUNNING', mode='formal_training',
            reason=None, capabilities=session.capabilities)), ('training_stats', record())]
        messages = pump(server, client)
        assert [m['type'] for m in messages] == ['lifecycle']
        assert messages[0]['payload']['capabilities'] == ['pause']
    finally: client.close(); server.close()


def established(caps=(), requested=('training_stats_v1',)):
    client = TransportClient(12345, capabilities=requested)
    client._phase = 'handshake'
    client._accept(dict(protocol_version=1, type='hello_ack', session_id='session', sequence=1,
        payload=dict(selected_version=1, resumed=False, capabilities=list(caps), mode='demo', run_status='READY')))
    return client


def test_receive_gating_lifecycle_and_sequences():
    client = established()
    with pytest.raises(ProtocolError): client._accept(envelope())
    with pytest.raises(ProtocolError):
        client._accept(dict(type='lifecycle', session_id='session', sequence=2,
            payload=dict(capabilities=['training_stats_v1'])))
    with pytest.raises(ProtocolError): established(('training_stats_v1',), ())
    client = established(('training_stats_v1',))
    client._accept(envelope())
    with pytest.raises(ProtocolError): client._accept(envelope())
    client.close()
    assert not client.negotiated_capabilities


def test_server_drops_unnegotiated_before_sequence_or_queue_mutation():
    server = BridgeServer(port=0, session=Session())
    try:
        server._send('training_stats', record())
        assert server.sequence == 0
        assert not server.pending
    finally: server.close()


def test_client_cannot_send_training_stats_even_when_negotiated():
    client = established(('training_stats_v1',))
    client.send(envelope())
    assert 'only commands' in client.last_error
    assert not client._outgoing


@pytest.mark.parametrize('kind', ['hello_ack', 'lifecycle'])
def test_optional_run_id_validation(kind):
    payload = dict(mode='demo', run_status='READY', capabilities=[], reason=None,
                   selected_version=1, resumed=False)
    message = dict(protocol_version=1, type=kind, session_id='session', sequence=1, payload=payload)
    for run_id in (None, 'run-1'):
        payload['run_id'] = run_id
        validate_message(message)
    for run_id in ('', 4, False):
        payload['run_id'] = run_id
        with pytest.raises(ProtocolError): validate_message(message)


def test_resume_ack_identifies_active_run_without_followup_events():
    session = Session()
    session.run_id = 'active-run'
    session.status = 'RUNNING'
    session.mode = 'formal_training'
    server = BridgeServer(port=0, session=session)
    client = TransportClient(server.port)
    try:
        client.connect()
        messages = pump(server, client)
        assert messages[0]['payload']['run_id'] == 'active-run'
        client.close(); pump(server, client)
        client.connect()
        messages = pump(server, client)
        assert len(messages) == 1
        assert messages[0]['type'] == 'hello_ack'
        assert messages[0]['payload']['resumed'] is True
        assert messages[0]['payload']['run_id'] == 'active-run'
        assert messages[0]['payload']['run_status'] == 'RUNNING'
    finally:
        client.close(); server.close()


def test_queue_age_updates_before_first_byte_without_compounding(monkeypatch):
    from wfrl.blender_bridge import server as server_module
    now = [10.]
    monkeypatch.setattr(server_module.time, 'monotonic', lambda: now[0])
    class Peer:
        blocked = True
        chunks = []
        def send(self, chunk):
            if self.blocked: raise BlockingIOError()
            self.chunks.append(chunk[:5])
            return len(chunk[:5])
        def close(self): pass
    server = BridgeServer(port=0, session=Session())
    peer = Peer(); server.client = peer
    server.negotiated_capabilities = frozenset({'training_stats_v1'})
    payload = record()
    payload['stats']['mean_power']['source_age_seconds'] = 17.
    try:
        server._send('training_stats', payload)
        now[0] = 15.
        with pytest.raises(BlockingIOError): server._flush()
        now[0] = 18.
        peer.blocked = False
        server._flush()
        now[0] = 25.
        while server.pending: server._flush()
        message = FrameDecoder().feed(b''.join(peer.chunks))[0]
        assert message['payload']['source_age_seconds'] == 20.
        assert message['payload']['stats']['mean_power']['source_age_seconds'] == 25.
        assert payload['source_age_seconds'] == 12.
        assert server.pending_bytes == 0
        assert message['sequence'] == 1
    finally: server.close()
