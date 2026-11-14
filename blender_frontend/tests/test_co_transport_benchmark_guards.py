"""Invalid transport bounds and playback evidence cannot become accepted state."""
from unittest.mock import Mock

import pytest

from wfrl_blender.playback_benchmark import PlaybackTarget, evaluate_playback
from wfrl_blender.protocol import encode_message
from wfrl_blender.transport import TransportClient


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, '5', None])
def test_transport_requires_finite_positive_timeout(value):
    with pytest.raises(ValueError, match='timeout'):
        TransportClient(5555, timeout_seconds=value)


@pytest.mark.parametrize('name', ['max_queue_bytes', 'max_bytes_per_poll',
    'max_messages_per_poll', 'max_io_calls_per_poll', 'max_pending_messages'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), 1.5, True, '2', None])
def test_transport_requires_integer_work_budgets(name, value):
    with pytest.raises(ValueError, match='limits'):
        TransportClient(5555, **{name: value})


def test_rejected_handshake_does_not_advance_command_watermark():
    client = TransportClient(5555, capabilities=())
    client.session_id = 'retained'
    client._last_inbound, client._last_outbound = 4, 7
    client._phase, client._deadline = 'handshake', float('inf')
    message = dict(protocol_version=1, type='hello_ack', session_id='retained', sequence=5,
        payload=dict(selected_version=1, capabilities=['training_stats_v1'],
                     run_status='READY', mode='demo', resumed=True, last_command_sequence=900))
    sock = client._socket = Mock()
    sock.recv.return_value = encode_message(message)

    assert client.poll() == []
    assert client.status == 'DISCONNECTED'
    assert 'unrequested' in client.last_error
    assert client.session_id == 'retained'
    assert client._last_inbound == 4
    assert client.next_sequence == 8
    sock.close.assert_called_once_with()


def evidence():
    rows = [[0., 1], [.1, 31], [.2, 61], [.3, 91], [.4, 101]]
    return dict(target=PlaybackTarget(1, 101, terminal_tolerance_frames=10),
        expected_routes=['1'], scene_rows=rows, draws={'1': [row.copy() for row in rows]},
        started=0., ended=.41, route_states={'1': {'valid': True}},
        termination=dict(reason='completed_segment', previous_frame=91, frame=101,
                         playback_running=True, driver_seek=False))


@pytest.mark.parametrize('name,value', [
    ('simulation_fps', float('nan')), ('simulation_fps', float('inf')),
    ('stall_threshold_s', float('nan')), ('stall_threshold_s', float('inf')),
    ('start_frame', 1.5), ('end_frame', 101.5), ('terminal_tolerance_frames', .5),
    ('start_frame', True)])
def test_playback_target_rejects_invalid_numeric_contract(name, value):
    fields = dict(start_frame=1, end_frame=101, terminal_tolerance_frames=10)
    fields[name] = value
    with pytest.raises(ValueError):
        PlaybackTarget(**fields)


@pytest.mark.parametrize('stream', ['scene', 'draw'])
@pytest.mark.parametrize('bad', [1.5, True, {1: 1}])
def test_playback_rejects_invalid_frame_evidence_without_truncation(stream, bad):
    data = evidence()
    rows = data['scene_rows'] if stream == 'scene' else data['draws']['1']
    rows[0] = bad if isinstance(bad, dict) else [0., bad]
    result = evaluate_playback(**data)
    assert result['status'] == 'FAIL'
    assert any('invalid observation' in failure for failure in result['failures'])
    assert result['completed_target_s'] is None


@pytest.mark.parametrize('bad', ['101', [], {}, float('nan'), True])
def test_malformed_termination_is_reported_as_invalid_evidence(bad):
    data = evidence()
    data['termination']['frame'] = bad
    result = evaluate_playback(**data)
    assert result['status'] == 'FAIL'
    assert 'termination: boundary evidence is invalid' in result['failures']


def test_malformed_route_state_is_reported_as_unverified():
    data = evidence()
    data['route_states']['1'] = None
    result = evaluate_playback(**data)
    assert result['status'] == 'FAIL'
    assert 'C1: invalid or unverified final route state' in result['failures']
