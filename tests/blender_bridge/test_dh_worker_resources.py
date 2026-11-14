"""Worker control failures terminate sampling and release IPC resources."""
import io
import json
from contextlib import suppress
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wfrl.blender_bridge import trainer_worker as worker
from wfrl.scene.schema import Scene, Turbine


class InlineReader:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()

    def join(self, *_args):
        pass

    def is_alive(self):
        return False


@pytest.fixture
def harness(tmp_path, monkeypatch):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(socket='fixture', scene='fixture', options={},
                                     session_id='worker')), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['worker', str(config)])
    scene = Scene(name='worker', controls=['yaw'], sensors=[], turbines=[Turbine('T1', 0, 0)])
    monkeypatch.setattr('wfrl.scene.schema.load_scene', lambda _path: scene)
    state = SimpleNamespace(trainer=None, stream=io.StringIO(''), closed=False)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()

        def close(self):
            state.closed = True

        connect = Mock()
        shutdown = Mock()
        sendall = Mock()

        def makefile(self, *_args, **_kwargs):
            return state.stream

    state.connection = Connection()
    monkeypatch.setattr(worker, 'socket', SimpleNamespace(
        AF_UNIX=socket.AF_UNIX, SOCK_STREAM=socket.SOCK_STREAM, SHUT_RD=socket.SHUT_RD,
        socket=lambda *_args: state.connection))
    monkeypatch.setattr(worker, 'threading', SimpleNamespace(Event=threading.Event, Thread=InlineReader))

    class Trainer:
        def __init__(self, _scene, on_snapshot, **_options):
            self._stop, self._paused = threading.Event(), threading.Event()
            self.on_snapshot = on_snapshot
            self.error, self.runtime, self.running = None, None, False
            state.trainer = self

        def start(self):
            self._stop.clear()

        def stop(self):
            self._stop.set()

    state.trainer_type = Trainer
    monkeypatch.setattr('wfrl.studio.trainer.Trainer', Trainer)
    return state


@pytest.mark.parametrize('message', ['invalid json\n', '{}\n', '{"command":"unknown"}\n'])
def test_worker_bad_command_reports_error_and_stops_trainer(harness, message):
    harness.stream = io.StringIO(message)
    assert worker.main() == 1
    assert harness.trainer._stop.is_set()
    records = [json.loads(call.args[0]) for call in harness.connection.sendall.call_args_list]
    assert any(record['kind'] == 'error' and record['message'] for record in records)
    assert harness.closed and harness.stream.closed


@pytest.mark.parametrize('message', ['', '{"command":"stop"}\n'])
def test_worker_early_stop_or_eof_is_not_cleared_by_start(harness, message):
    harness.stream = io.StringIO(message)
    assert worker.main() == 0
    assert harness.trainer._stop.is_set()
    assert harness.closed and harness.stream.closed


@pytest.mark.parametrize('failure', ['connect', 'scene', 'trainer', 'start'])
def test_worker_startup_failure_closes_acquired_resources(harness, monkeypatch, failure):
    error = RuntimeError('startup failed')
    if failure == 'connect':
        harness.connection.connect.side_effect = error
    elif failure == 'scene':
        monkeypatch.setattr('wfrl.scene.schema.load_scene', Mock(side_effect=error))
    elif failure == 'trainer':
        monkeypatch.setattr('wfrl.studio.trainer.Trainer', Mock(side_effect=error))
    else:
        monkeypatch.setattr(harness.trainer_type, 'start', Mock(side_effect=error))
    with pytest.raises(RuntimeError, match='startup failed') as caught:
        worker.main()
    assert caught.value is error
    assert harness.closed
    if failure != 'connect':
        assert harness.stream.closed


def test_worker_snapshot_send_failure_is_fatal_even_if_trainer_callback_is_nonfatal(harness, monkeypatch, capsys):
    harness.connection.sendall.side_effect = BrokenPipeError('IPC disconnected')
    adapter = SimpleNamespace(encode=lambda _sample: {}, safety_events=lambda _sample: [])
    monkeypatch.setattr('wfrl.blender_bridge.snapshot_adapter.SnapshotAdapter', lambda *_args: adapter)

    def start(trainer):
        trainer._stop.clear()
        try:
            trainer.on_snapshot(SimpleNamespace(phase='sampling', step=1, iters_done=0))
        except Exception:
            pass

    monkeypatch.setattr(harness.trainer_type, 'start', start)
    assert worker.main() == 1
    assert harness.trainer._stop.is_set()
    assert harness.closed and harness.stream.closed
    assert 'IPC disconnected' in capsys.readouterr().err


def test_worker_normal_completion_unblocks_and_joins_command_reader(harness, monkeypatch):
    connection, peer = socket.socketpair()
    stream = connection.makefile('r', encoding='utf-8')
    harness.stream = stream
    harness.connection.shutdown = connection.shutdown
    harness.connection.sendall = connection.sendall
    readers = []

    def thread_factory(**kwargs):
        reader = threading.Thread(**kwargs)
        readers.append(reader)
        return reader

    monkeypatch.setattr(worker, 'threading', SimpleNamespace(Event=threading.Event, Thread=thread_factory))
    try:
        assert worker.main() == 0
        assert readers and all(not reader.is_alive() for reader in readers)
        assert harness.closed and stream.closed
    finally:
        peer.close()
        with suppress(OSError):
            connection.shutdown(socket.SHUT_RDWR)
        for reader in readers:
            reader.join(2)
        stream.close()
        connection.close()
