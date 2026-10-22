"""Exercise actual loopback peer through the Blender runtime; no physical backend."""
from pathlib import Path
import sys, socket, time, importlib
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
import wfrl_blender
from wfrl_blender import runtime
from wfrl_blender.protocol import encode_message


def until(predicate):
    for _ in range(200):
        runtime.tick()
        if predicate(): return
        time.sleep(.001)
    raise AssertionError('Runtime condition timed out')


def main():
    wfrl_blender.register()
    server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(); server.settimeout(1)
    port = server.getsockname()[1]
    runtime.select_mode('interactive_training')
    runtime.connect(port)
    peer, _ = server.accept()
    runtime.tick()
    assert not runtime.configuration_editable(), 'Handshake must lock configuration'
    assert not bpy.ops.wfrl.bridge_connect.poll(), 'Repeated connect during handshake'
    def message(kind, sequence, payload):
        return encode_message(dict(protocol_version=1, type=kind, session_id='smoke', sequence=sequence, payload=payload))
    peer.sendall(message('hello_ack', 0, dict(selected_version=1, capabilities=['pause'], run_status='READY', mode='interactive_training', resumed=False)))
    until(lambda: runtime.get_state().connection == 'CONNECTED')
    for sequence, status in enumerate(('STARTING','RUNNING','PAUSED'), 1):
        peer.sendall(message('lifecycle', sequence, dict(
            run_status=status, reason=None, mode='interactive_training',
            capabilities=['pause', 'single_step'])))
        until(lambda: runtime.get_state().run_status == status)
    assert not bpy.ops.wfrl.load_demo.poll()
    peer.close()
    until(lambda: runtime.get_state().connection == 'DISCONNECTED')
    assert runtime.get_state().run_status == 'PAUSED'
    assert not runtime.get_state().confirmed
    assert not runtime.configuration_editable()
    old_tick = runtime.tick
    importlib.reload(runtime)
    assert not bpy.app.timers.is_registered(old_tick)
    wfrl_blender.register()
    assert runtime.get_state().run_status == 'PAUSED'
    assert not runtime.get_state().confirmed
    runtime.connect(port)
    peer, _ = server.accept(); runtime.tick()
    peer.sendall(message('hello_ack', 4, dict(selected_version=1, capabilities=[], run_status='STOPPED', mode='interactive_training', resumed=True)))
    until(lambda: runtime.get_state().confirmed)
    assert runtime.configuration_editable()
    runtime.select_mode('demo'); peer.close(); server.close()
    runtime.select_mode('replay')
    runtime.connect(port)  # closed listening port: never acquired a session
    until(lambda: bool(runtime.get_state().error))
    assert runtime.configuration_editable(), 'First refusal must not trap user outside Demo'
    runtime.select_mode('demo')
    assert runtime.get_state().connection == 'LOCAL DEMO'
    server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen()
    runtime.select_mode('replay'); runtime.connect(server.getsockname()[1])
    peer, _ = server.accept(); runtime.tick()
    wfrl_blender.unregister(); wfrl_blender.register()
    assert runtime.configuration_editable(), 'Disable before first ack must allow local fallback'
    runtime.select_mode('demo'); peer.close(); server.close()
    wfrl_blender.unregister()
    print('WFRL_PART3_CONNECTION_SMOKE=PASS')

if __name__ == '__main__': main()
