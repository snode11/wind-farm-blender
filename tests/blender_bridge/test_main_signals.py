"""Shutdown signals retain protocol polling until worker cleanup is confirmed."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wfrl.blender_bridge import __main__ as bridge


@pytest.mark.parametrize(('available', 'received'), [
    (('SIGTERM', 'SIGHUP'), 'SIGTERM'),
    (('SIGTERM', 'SIGHUP'), 'SIGHUP'),
    (('SIGTERM', 'SIGBREAK'), 'SIGBREAK'),
])
def test_shutdown_signal_waits_for_confirmed_cleanup(monkeypatch, available, received):
    handlers = {}
    signals = {name: index + 1 for index, name in enumerate(available)}
    monkeypatch.setattr(bridge, 'signal', SimpleNamespace(
        **signals, signal=lambda number, handler: handlers.__setitem__(number, handler)))
    monkeypatch.setattr('sys.argv', ['wfrl.blender_bridge'])
    monkeypatch.setattr(bridge, 'time', SimpleNamespace(sleep=lambda _seconds: None))
    monkeypatch.setattr(bridge.SessionBinding, 'from_environment', lambda *_args: None)
    session = Mock()
    session.synchronization_state.side_effect = [
        dict(cleanup_confirmed=False, worker_alive=False),
        dict(cleanup_confirmed=True, worker_alive=True),
        dict(cleanup_confirmed=True, worker_alive=False),
    ]
    monkeypatch.setattr(bridge, 'BackendSession', lambda: session)
    server = SimpleNamespace(port=8765, closed=False, shutdown_requested=False)
    polls = []

    def poll():
        assert not server.closed
        assert len(polls) < 3, 'Bridge kept polling after confirmed cleanup'
        assert len(handlers) == len(available)
        assert session.stop.call_count == int(bool(polls))
        handlers[signals[received]](signals[received], None)
        assert server.shutdown_requested
        polls.append(True)

    def close():
        assert len(polls) == session.synchronization_state.call_count == 3
        server.closed = True

    server.poll, server.close = poll, Mock(side_effect=close)
    monkeypatch.setattr(bridge, 'BridgeServer', lambda *_args, **_kwargs: server)

    bridge.main()

    session.stop.assert_called_once_with()
    server.close.assert_called_once_with()
    session._stop_worker.join.assert_called_once_with()
