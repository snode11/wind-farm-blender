"""Bridge helper failures preserve resource ownership and committed state."""
from collections import deque
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from wfrl.blender_bridge import __main__ as bridge
from wfrl.blender_bridge import launcher_state, tip_client
from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.floris_session import FlorisDemoSession
from wfrl.blender_bridge.workflow import validate_run_options
from test_floris_session import Driver, scene


def test_binding_write_failure_keeps_committed_session_and_generation(tmp_path, monkeypatch):
    path = tmp_path / 'binding.json'
    binding = launcher_state.SessionBinding(path, 'token', '127.0.0.1', 8765)
    binding.publish('127.0.0.1', 8765, 'first')
    original = path.read_bytes()
    writer = launcher_state.atomic_write
    monkeypatch.setattr(launcher_state, 'atomic_write', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        binding.publish('127.0.0.1', 8765, 'uncommitted')
    assert (binding.session_id, binding.generation) == ('first', 0)
    assert path.read_bytes() == original
    monkeypatch.setattr(launcher_state, 'atomic_write', writer)
    binding.publish('127.0.0.1', 8765, 'second')
    assert json.loads(path.read_text(encoding='utf-8'))['session_generation'] == 1
    assert list(tmp_path.iterdir()) == [path]


def test_floris_failed_cleanup_retains_driver_and_blocks_restart():
    class FailingDriver(Driver):
        def close(self, **kwargs):
            raise OSError('driver remains open')

    trainer = FlorisDemoSession(scene(), driver_factory=FailingDriver, max_steps=1,
                                frame_interval=0)
    trainer.start()
    trainer._thread.join(2)
    session = BackendSession()
    session._trainer, session.status = trainer, 'FAILED'
    assert not session.synchronization_state()['cleanup_confirmed']
    assert trainer._driver is Driver.instances[-1]
    with pytest.raises(RuntimeError, match='cleanup|Cleanup'):
        trainer.start()


@pytest.mark.parametrize('power', [[None, 2., 3.], [], [True, 2., 3.], [np.nan, 2., 3.]])
def test_floris_missing_power_is_invalid_without_aborting_session(power):
    class MissingPowerDriver(Driver):
        def step(self, delta):
            return dict(super().step(delta), power=power)

    samples = []
    trainer = FlorisDemoSession(scene(), samples.append, driver_factory=MissingPowerDriver,
                                max_steps=1, frame_interval=0)
    trainer.start()
    trainer._thread.join(2)
    assert trainer.error is None
    assert len(samples) == 1 and np.isnan(samples[0].farm_power)
    assert Driver.instances[-1].closed


def test_tip_invalid_package_never_opens_connection(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(tip_client.socket, 'create_connection', connect)
    with pytest.raises(TypeError):
        tip_client.TipBridgeClient('127.0.0.1:8765', None)
    connect.assert_not_called()


def test_tip_constructor_keeps_primary_failure_when_stop_also_fails(tmp_path, monkeypatch):
    sock = Mock()
    monkeypatch.setattr(tip_client.socket, 'create_connection', Mock(return_value=sock))
    primary = ValueError('pause rejected')
    sock.sendall.side_effect = [None, None, primary, None]
    monkeypatch.setattr(tip_client.TipBridgeClient, '_receive', Mock(side_effect=[
        {'type': 'hello_ack', 'session_id': 'test', 'payload': {'run_status': 'READY'}},
        TimeoutError('stop timed out'),
    ]))
    with pytest.raises(ValueError, match='pause rejected') as caught:
        tip_client.TipBridgeClient('127.0.0.1:8765', tmp_path)
    assert caught.value is primary
    assert any('stop timed out' in note for note in getattr(primary, '__notes__', []))
    sock.close.assert_called_once_with()


@pytest.mark.parametrize('operation', ['next_sample', 'close'])
def test_tip_operation_deadline_is_not_extended_by_unrelated_messages(monkeypatch, operation):
    client = tip_client.TipBridgeClient.__new__(tip_client.TipBridgeClient)
    client.socket = Mock()
    client.messages, client.sequence, client.received = deque(), 0, 0
    client.session, client.owned, client.started, client.finished = 'test', True, False, False
    client.timeout = 1.
    clock = [0.]

    def monotonic():
        clock[0] += .1
        return clock[0]

    def decode(_data):
        assert client.received < 30, 'lifecycle traffic extended the operation indefinitely'
        return [{'sequence': client.received + 1, 'session_id': 'test', 'type': 'lifecycle',
                 'payload': {'run_status': 'PAUSED'}}]

    client.decoder = SimpleNamespace(feed=decode)
    monkeypatch.setattr(tip_client, 'time', SimpleNamespace(monotonic=monotonic))
    with pytest.raises(TimeoutError):
        getattr(client, operation)()
    if operation == 'close':
        assert client.socket is None and not client.owned


@pytest.mark.parametrize('failure', ['signal', 'close'])
def test_bridge_main_failure_still_closes_server_and_joins_stop_worker(monkeypatch, failure):
    monkeypatch.setattr('sys.argv', ['wfrl.blender_bridge'])
    monkeypatch.setattr(bridge.SessionBinding, 'from_environment', lambda *_args: None)
    session = Mock()
    monkeypatch.setattr(bridge, 'BackendSession', lambda: session)
    server = SimpleNamespace(port=8765, closed=True, close=Mock())
    monkeypatch.setattr(bridge, 'BridgeServer', lambda *_args, **_kwargs: server)
    register = Mock(side_effect=RuntimeError('signal registration failed') if failure == 'signal' else None)
    monkeypatch.setattr(bridge, 'signal', SimpleNamespace(SIGTERM=1, signal=register))
    if failure == 'close':
        server.close.side_effect = RuntimeError('server close failed')
    with pytest.raises(RuntimeError, match='failed'):
        bridge.main()
    server.close.assert_called_once_with()
    session._stop_worker.join.assert_called_once_with()


def test_interactive_resume_is_rejected_before_worker_start(tmp_path):
    checkpoint = tmp_path / 'policy.pt'
    checkpoint.write_bytes(b'existing checkpoint')
    with pytest.raises(ValueError, match='does not support resuming interactive'):
        validate_run_options('interactive_training', {'resume_from': str(checkpoint)})
    validate_run_options('formal_training', {'resume_from': str(checkpoint)})
    validate_run_options('replay', {'ckpt_path': str(checkpoint)})
