"""Private stdin/stdout worker for IsolatedTrainer; launch through mpiexec."""
import json
import socket
import sys
import threading
import time



def main():
    config = json.loads(open(sys.argv[1]).read())
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(config['socket'])
    commands_stream = connection.makefile('r')
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

    def emit(record):
        connection.sendall((json.dumps(record, allow_nan=False) + '\n').encode())

    def snapshot(sample):
        if stopped.is_set():
            return
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
        if runtime:
            for topic, enabled in tuple(channels.items()):
                runtime.set_enabled(topic, enabled)

    trainer = Trainer(scene, on_snapshot=snapshot, **options)

    def commands():
        for line in commands_stream:
            command = json.loads(line)
            if command['command'] == 'continue':
                permit.set()
            elif command['command'] == 'channel':
                channels[command['topic']] = command['enabled']
            elif command['command'] == 'stop':
                stopped.set()
                trainer._stop.set()
                trainer._paused.clear()
                permit.set()
                return
        stopped.set()
        trainer._stop.set()
        permit.set()

    threading.Thread(target=commands, daemon=True).start()
    trainer.start()
    while trainer.running:
        time.sleep(.02)
    if trainer.error:
        emit(dict(kind='error', message=trainer.error))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
