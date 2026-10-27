"""Resumed controllers share the backend's command sequence watermark."""
import importlib.util
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.server import BridgeServer
from wfrl_blender.transport import TransportClient


SPEC = importlib.util.spec_from_file_location(
    'retry_launcher', Path(__file__).resolve().parents[2] / 'scripts/blender/wfrl_launcher.py')
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def test_stop_can_retry_then_resume_from_another_controller():
    session = BackendSession()
    stops = []
    trainer = SimpleNamespace(running=True)

    def stop(timeout):
        stops.append(timeout)
        if len(stops) == 2:
            trainer.running = False

    trainer.stop = stop
    session._trainer = trainer
    session.lifecycle('RUNNING')
    server = BridgeServer(port=0, session=session)
    done = threading.Event()

    def serve():
        while not done.is_set():
            server.poll()
            done.wait(.001)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    client = TransportClient(server.port)
    try:
        assert not launcher.request_protocol_stop(server.port, session.session_id, .15)
        assert launcher.request_protocol_stop(server.port, session.session_id, 1.)
        assert len(stops) == 2
        client.session_id = session.session_id
        client.connect()
        deadline = time.monotonic() + 1
        while client.status != 'CONNECTED' and time.monotonic() < deadline:
            client.poll()
            time.sleep(.001)
        assert client.status == 'CONNECTED'
        assert client.next_sequence > server.guard.last_sequence
        client.send(dict(protocol_version=1, type='command', session_id=session.session_id,
                         sequence=client.next_sequence,
                         payload=dict(command='session.sync', arguments={})))
        target = client.next_sequence - 1
        while server.guard.last_sequence < target and time.monotonic() < deadline:
            client.poll()
            time.sleep(.001)
        assert server.guard.last_sequence == target
        assert target < 100
    finally:
        client.close()
        trainer.running = False
        done.set()
        worker.join(1)
        server.close()
