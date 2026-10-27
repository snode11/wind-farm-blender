"""Run with the backend environment: python -m wfrl.blender_bridge."""
import argparse
import signal
import time
from .backend_session import BackendSession
from .server import BridgeServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--scene')
    parser.add_argument('--fake', action='store_true', help='SYNTH protocol fixture; demo mode only')
    args = parser.parse_args()
    session = BackendSession()
    if args.fake:
        from .fake_backend import FakeTrainer
        session.trainer_factory = FakeTrainer
        session.fake = True
    if args.scene: session.scene = session._load(args.scene)
    server = BridgeServer(args.host, args.port, session)
    def request_shutdown(_signum, _frame):
        server.shutdown_requested = True
    for name in ('SIGTERM', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_shutdown)
    print(f'WFRL Bridge listening on {args.host}:{server.port}', flush=True)
    stop_requested = False
    try:
        while not server.closed:
            try:
                server.poll()
                if server.shutdown_requested:
                    if not stop_requested:
                        session.stop()
                        stop_requested = True
                    state = session.synchronization_state()
                    if state['cleanup_confirmed'] and not state['worker_alive']:
                        break
                time.sleep(.01)
            except KeyboardInterrupt:
                request_shutdown(None, None)
    finally:
        server.close()
        if session._stop_worker: session._stop_worker.join()


if __name__ == '__main__': main()
