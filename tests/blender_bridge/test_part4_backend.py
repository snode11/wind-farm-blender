import socket
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS
import numpy as np
import pytest
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.server import BridgeServer
from wfrl.blender_bridge.messages import encode_message, FrameDecoder


def scene(): return NS(turbines=[NS(id='A'), NS(id='B')], dt=3, backend='fastfarm')
def snap(step=1): return NS(step=step, ctx={}, measure=dict(yaw=np.array([1., 2.]), pitch=np.array([3., 4.]), rotor_speed=np.array([np.nan, 8.]), power=np.array([2., 3.])), farm_power=5., reward=np.float32(.5), events=[])
def envelope(kind, payload, sid='s', seq=1): return dict(protocol_version=1,type=kind,payload=payload,session_id=sid,sequence=seq)


def test_adapter_preserves_missing_invalid_units_ids_fidelity():
    adapter = SnapshotAdapter(scene(),'s')
    data = adapter.encode(snap())
    assert [t['turbine_id'] for t in data['turbines']] == ['A','B']
    c = data['turbines'][0]['channels']
    assert c['rotor_speed']['value'] is None and c['rotor_speed']['validity'] == 'invalid'
    assert c['rotor_speed']['fidelity'] == 'SYNTH'
    assert c['power']['unit'] == 'MW' and c['power']['fidelity'] == 'DIRECT'
    assert c['torque']['validity'] == 'unsupported'
    encode_message(envelope('snapshot',data))
    sample=snap(); sample.events=[dict(turbine='A',severity='warn', detail='clipped', requested=np.float64(2))]
    event=list(adapter.safety_events(sample))[0]
    assert event['severity']=='warning' and event['turbine_id']=='A'
    encode_message(envelope('safety_event',event))
    sample.measure['rotorspeed']=np.array([3,4])
    with pytest.raises(ValueError, match='Conflicting'): adapter.encode(sample)


class FakeTrainer:
    def __init__(self, scene, on_snapshot, **kwargs):
        self.callback=on_snapshot; self.stop_event=threading.Event(); self.error=None
    def start(self):
        def run():
            for i in range(1000):
                if self.stop_event.is_set(): break
                self.callback(snap(i)); time.sleep(.005)
        self._thread=threading.Thread(target=run, daemon=True); self._thread.start()
    def stop(self, timeout): self.stop_event.set(); self._thread.join(timeout)


def wait_for(predicate, timeout=2):
    end=time.monotonic()+timeout
    while not predicate():
        assert time.monotonic()<end
        time.sleep(.005)


def test_session_pause_step_resume_stop():
    s=BackendSession(scene(), trainer_factory=FakeTrainer)
    s.start('demo',{})
    lifecycle = [payload for kind, payload in s.poll() if kind == 'lifecycle']
    assert lifecycle[-1]['mode'] == 'demo'
    assert lifecycle[-1]['capabilities'] == ['pause', 'single_step']
    s.command(dict(command='run.pause'))
    time.sleep(.03); before=s.poll()[-1][1]['step']
    time.sleep(.02); assert not [e for e in s.poll() if e[0]=='snapshot']
    s.command(dict(command='run.step'))
    time.sleep(.03); after=[e for e in s.poll() if e[0]=='snapshot']
    assert len(after)==1 and after[0][1]['step']==before+1
    s.command(dict(command='run.resume'))
    time.sleep(.02)
    s.stop(.5); wait_for(lambda:s.status=='STOPPED')
    assert not s.alive()
    s.stop(.5)


def test_timeout_does_not_claim_stopped_or_allow_reset():
    class Stuck(FakeTrainer):
        def stop(self,timeout): pass
    s=BackendSession(scene(),trainer_factory=Stuck); s.start('demo',{})
    s.stop(0); wait_for(lambda:s.status=='FAILED')
    with pytest.raises(ValueError): s.command(dict(command='run.reset'))
    s._trainer.stop_event.set(); s._thread.join(1)


def test_failed_status_survives_repeated_stop_until_reset():
    session = BackendSession(scene(), trainer_factory=FakeTrainer)
    worker = threading.Thread(target=lambda: None)
    worker.start(); worker.join()
    session.status = 'RUNNING'
    session._thread = worker
    session._trainer = NS(error='boom')
    session.poll()
    assert session.status == 'FAILED'
    session.stop()
    assert session.status == 'FAILED'
    session.command(dict(command='run.reset'))
    assert session.status == 'READY'


def test_stop_after_natural_exit_emits_draining_before_stopped():
    session = BackendSession(scene(), trainer_factory=FakeTrainer)
    worker = threading.Thread(target=lambda: None)
    worker.start(); worker.join()
    session.status = 'RUNNING'
    session._thread = worker
    session._trainer = NS(error=None)
    session.stop()
    statuses = [payload['run_status'] for kind, payload in session.poll()
                if kind == 'lifecycle']
    assert statuses == ['DRAINING', 'STOPPED']


def test_loopback_handshake_command_disconnect_and_resume():
    session=BackendSession(scene(),trainer_factory=FakeTrainer)
    server=BridgeServer(port=0,session=session)
    sock=socket.create_connection(('127.0.0.1',server.port)); sock.settimeout(.2)
    decoder=FrameDecoder()
    def exchange(message):
        sock.sendall(encode_message(message))
        for _ in range(10): server.poll(); time.sleep(.002)
        return decoder.feed(sock.recv(65536))
    try:
        messages=exchange(envelope('hello',dict(supported_versions=[1],resume_session_id=None,capabilities=[]),'',0))
        sid=messages[0]['session_id']; assert messages[0]['type']=='hello_ack'
        messages=exchange(envelope('command',dict(command='run.start',arguments=dict(mode='demo',options={})),sid,1))
        assert any(m['type']=='snapshot' for m in messages)
        sock.close(); wait_for(lambda:(server.poll() or server.client is None))
        assert session.status in {'RUNNING', 'PAUSED'}
        assert session.alive()
        sock=socket.create_connection(('127.0.0.1',server.port)); sock.settimeout(.2)
        messages=exchange(envelope('hello',dict(supported_versions=[1],resume_session_id=sid,capabilities=[]),'',0))
        assert messages[0]['payload']['resumed']
    finally: sock.close(); server.close()


def test_reject_non_loopback():
    with pytest.raises(ValueError): BridgeServer(host='0.0.0.0')


def test_server_coalesces_unsent_snapshots_and_prioritizes_lifecycle():
    session = BackendSession(scene(), trainer_factory=FakeTrainer)
    server = BridgeServer(port=0, session=session)
    try:
        adapter = SnapshotAdapter(scene(), session.session_id)
        for step in range(20):
            server._send('snapshot', adapter.encode(snap(step)))
        assert len(server.pending) == 1
        decoded = FrameDecoder().feed(server.pending[0]['frame'])
        assert decoded[0]['payload']['step'] == 19
        session.mode = 'demo'
        lifecycle = dict(run_status='RUNNING', reason=None, mode='demo',
                         capabilities=['pause', 'single_step'])
        server._send('lifecycle', lifecycle)
        assert len(server.pending) == 1
        assert server.pending[0]['kind'] == 'lifecycle'
    finally:
        server.close()


def test_server_retains_reliable_events_after_send_queue_overflow():
    session = BackendSession(scene(), trainer_factory=FakeTrainer)
    server = BridgeServer(port=0, session=session, max_queue_bytes=420)
    sock = socket.create_connection(('127.0.0.1', server.port)); sock.settimeout(.2)
    decoder = FrameDecoder()
    try:
        sock.sendall(encode_message(envelope(
            'hello', dict(supported_versions=[1], resume_session_id=None, capabilities=[]), '', 0)))
        for _ in range(10): server.poll(); time.sleep(.002)
        hello = decoder.feed(sock.recv(65536))[0]
        sid = hello['session_id']
        for status in ('STARTING', 'RUNNING'):
            session.lifecycle(status)
        session.emit('error', dict(code='TEST', message='retained', fatal=False))
        server.poll()
        assert server.client is None
        assert [kind for kind, _ in server.backlog] == ['lifecycle', 'lifecycle', 'error']

        server.max_queue_bytes = 2048
        sock.close()
        sock = socket.create_connection(('127.0.0.1', server.port)); sock.settimeout(.2)
        sock.sendall(encode_message(envelope(
            'hello', dict(supported_versions=[1], resume_session_id=sid, capabilities=[]), '', 0)))
        messages = []
        for _ in range(40):
            server.poll(); time.sleep(.002)
            try: messages.extend(decoder.feed(sock.recv(65536)))
            except socket.timeout: pass
            if any(message['type'] == 'error' for message in messages): break
        assert messages[0]['type'] == 'hello_ack' and messages[0]['payload']['resumed']
        assert [message['type'] for message in messages[1:]] == ['lifecycle', 'lifecycle', 'error']
    finally:
        sock.close(); server.close()


def test_fresh_session_discards_old_reliable_backlog():
    session = BackendSession(scene(), trainer_factory=FakeTrainer)
    session.status = 'STOPPED'
    server = BridgeServer(port=0, session=session)
    server.session_claimed = True
    old_id = session.session_id
    server.backlog.append(('lifecycle', dict(
        run_status='DRAINING', reason=None, mode='demo', capabilities=['pause', 'single_step'])))
    sock = socket.create_connection(('127.0.0.1', server.port)); sock.settimeout(.2)
    try:
        sock.sendall(encode_message(envelope(
            'hello', dict(supported_versions=[1], resume_session_id=None, capabilities=[]), '', 0)))
        for _ in range(10): server.poll(); time.sleep(.002)
        messages = FrameDecoder().feed(sock.recv(65536))
        assert len(messages) == 1 and messages[0]['type'] == 'hello_ack'
        assert messages[0]['session_id'] != old_id
        assert not server.backlog
    finally:
        sock.close(); server.close()


def test_fresh_client_after_handshake_gets_new_session_id():
    server = BridgeServer(port=0, session=BackendSession(scene(), trainer_factory=FakeTrainer))
    decoder = FrameDecoder()
    def handshake():
        client = socket.create_connection(('127.0.0.1', server.port)); client.settimeout(.2)
        client.sendall(encode_message(envelope(
            'hello', dict(supported_versions=[1], resume_session_id=None, capabilities=[]), '', 0)))
        for _ in range(10): server.poll(); time.sleep(.002)
        return client, decoder.feed(client.recv(65536))[0]['session_id']
    first = second = None
    try:
        first, first_id = handshake()
        first.close(); first = None
        wait_for(lambda: (server.poll() or server.client is None))
        second, second_id = handshake()
        assert second_id != first_id
    finally:
        if first: first.close()
        if second: second.close()
        server.close()


def test_formal_command_maps_values_flags_and_rejects_unknown(tmp_path):
    scene_path = tmp_path / 'scene.yaml'
    scene_path.write_text('placeholder')
    launcher = tmp_path / 'mpiexec'
    launcher.write_text('#!/bin/sh\n')
    launcher.chmod(0o755)
    session = BackendSession(scene(), mpi_launcher=str(launcher))
    _, argv = session._formal_command(scene_path, {
        'n_steps': 8, 'recurrent': True, 'obs_duty': False,
        'resume_from': None, 'reward': 'level',
    })
    assert argv[:3] == [str(launcher), '-n', '1']
    assert argv[-5:] == ['--n-steps', '8', '--recurrent', '--reward', 'level']
    assert '--obs-duty' not in argv and '--resume-from' not in argv
    with pytest.raises(ValueError, match='Unsupported formal options'):
        session._formal_command(scene_path, {'backend': 'fastfarm'})
    with pytest.raises(ValueError, match='must be boolean'):
        session._formal_command(scene_path, {'recurrent': 'yes'})


@pytest.mark.skipif(os.name == 'nt', reason='Backend process groups currently use POSIX signals')
def test_formal_stop_timeout_force_terminates_owned_process_group():
    code = ('import signal,time; '
            'signal.signal(signal.SIGINT, signal.SIG_IGN); '
            'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            'print("ready", flush=True); time.sleep(60)')
    process = subprocess.Popen(
        [sys.executable, '-c', code], stdout=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    assert process.stdout.readline().strip() == 'ready'
    session = BackendSession(scene(), kill_grace_seconds=.1)
    session.mode = 'formal_training'
    session.status = 'RUNNING'
    session._process = process
    try:
        session.stop(.05)
        wait_for(lambda: session.status == 'FAILED')
        lifecycle = [payload for kind, payload in session.poll()
                     if kind == 'lifecycle' and payload['run_status'] == 'FAILED']
        assert 'force terminated' in lifecycle[-1]['reason']
        assert not session.alive()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(2)


@pytest.mark.skipif(os.name == 'nt', reason='Backend process groups currently use POSIX signals')
def test_formal_launcher_exit_with_live_child_fails_and_cleans_group():
    code = ('import subprocess,sys; '
            'subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
            'sys.exit(0)')
    process = subprocess.Popen([sys.executable, '-c', code], start_new_session=True)
    process.wait(2)
    session = BackendSession(scene(), kill_grace_seconds=.1)
    session.mode = 'formal_training'
    session.status = 'RUNNING'
    session._process = process
    try:
        session.poll()
        assert session.status == 'FAILED'
        wait_for(lambda: not session.alive())
        session.stop()
        assert session.status == 'FAILED'
    finally:
        try: os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
