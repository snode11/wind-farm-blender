"""Private socket worker for IsolatedTrainer; launch through mpiexec."""
import json
import socket
import sys
import threading
import time



def main():
    with open(sys.argv[1], encoding='utf-8') as config_stream:
        config = json.load(config_stream)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(config['socket'])
        with connection.makefile('r', encoding='utf-8') as commands_stream:
            return _run_connected(config, connection, commands_stream)


def _run_connected(config, connection, commands_stream):
    from wfrl.scene.schema import load_scene
    from wfrl.studio.trainer import Trainer
    from .snapshot_adapter import SnapshotAdapter
    from .training_progress import interactive_progress
    scene = load_scene(config['scene'])
    options = config['options']
    mode = 'replay' if options.get('replay') else 'demo' if options.get('demo') else 'interactive_training'
    adapter = SnapshotAdapter(scene, config['session_id'], mode)
    permit = threading.Event()
    stopped = threading.Event()
    channels = {}
    errors = []

    def request_stop():
        stopped.set()
        trainer._stop.set()
        trainer._paused.clear()
        permit.set()

    def emit(record):
        connection.sendall((json.dumps(record, allow_nan=False) + '\n').encode())

    def snapshot(sample):
        if stopped.is_set():
            return
        try:
            runtime = trainer.runtime
            table = runtime.channel_table() if runtime else []
            # Store wire fidelity names, matching BackendSession.channel_table.
            fidelity = {'直读':'DIRECT', '导出':'EXPORTED', '合成':'SYNTH', 'DERIVED':'EXPORTED'}
            table = [[a,b,fidelity.get(str(c),str(c)),d,e] for a,b,c,d,e in table]
            driver = getattr(runtime, 'driver', None)
            permit.clear()
            emit(dict(kind='snapshot', payload=adapter.encode(sample),
                      events=list(adapter.safety_events(sample)), channels=table,
                      case_dir=getattr(driver, 'case_dir', None),
                      training=interactive_progress(sample, 'worker', scene.n) if mode == 'interactive_training' else None))
            while not stopped.is_set() and not permit.wait(.1):
                pass
            if runtime and not stopped.is_set():
                for topic, enabled in tuple(channels.items()):
                    runtime.set_enabled(topic, enabled)
        except Exception as exc:
            errors.append('Interactive snapshot transport failed: ' + (str(exc) or type(exc).__name__))
            request_stop()
            raise

    trainer = Trainer(scene, on_snapshot=snapshot, **options)

    def commands():
        try:
            for line in commands_stream:
                if stopped.is_set():
                    return
                command = json.loads(line)
                if command['command'] == 'continue':
                    permit.set()
                elif command['command'] == 'channel':
                    channels[command['topic']] = command['enabled']
                elif command['command'] == 'stop':
                    return
                else:
                    raise ValueError('Unknown worker command')
        except Exception as exc:
            if not stopped.is_set():
                errors.append('Interactive command transport failed: ' + (str(exc) or type(exc).__name__))
        finally:
            request_stop()

    reader = threading.Thread(target=commands, name='wfrl-worker-commands', daemon=True)
    reader_started = False
    try:
        trainer.start()
        reader.start()
        reader_started = True
        while trainer.running:
            time.sleep(.02)
    finally:
        request_stop()
        try:
            trainer.stop()
        finally:
            # Interrupt the reader before closing its buffered file handle.
            try:
                connection.shutdown(socket.SHUT_RD)
            except OSError:
                pass
            if reader_started:
                reader.join()
    if trainer.error:
        errors.insert(0, trainer.error)
    if errors:
        message = '; '.join(errors)
        try:
            emit(dict(kind='error', message=message))
        except OSError as exc:
            print(message + '; error reporting failed: ' + str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
