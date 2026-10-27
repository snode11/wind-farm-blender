"""Bounded progress delivery and reliable lifecycle handling across disconnects."""
import queue
import socket
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.messages import FrameDecoder, encode_message
from wfrl.blender_bridge.server import BridgeServer
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl.blender_bridge.training_progress import interactive_progress


def scene():
    return SimpleNamespace(turbines=[SimpleNamespace(id='A'), SimpleNamespace(id='B')],
                           dt=3., backend='fastfarm')


def snapshot(step, *, phase=None, safety=False):
    return SimpleNamespace(
        step=step, ctx={}, phase=phase or ('updating' if step % 2 else 'sampling'),
        iters_done=step // 2,
        stats=dict(mean_power=5., mean_reward=.5, vloss=.3, explained_var=.8),
        measure=dict(yaw=np.array([1., 2.]), pitch=np.array([3., 4.]),
                     rotor_speed=np.array([7., 8.]), power=np.array([2., 3.])),
        farm_power=5., reward=.5,
        events=[dict(turbine='A', severity='warn', detail='safety retained')] if safety else [])


def wait_for(predicate, timeout=3.):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'Timed out waiting for fake backend'
        time.sleep(.001)


class GatedTrainer:
    def __init__(self, scene, on_snapshot, **kwargs):
        self.callback = on_snapshot
        self.work = queue.Queue()
        self.stop_event = threading.Event()
        self.error = None

    def start(self):
        def run():
            while not self.stop_event.is_set():
                try:
                    item, delivered = self.work.get(timeout=.05)
                except queue.Empty:
                    continue
                if item is None:
                    delivered.set()
                    return
                self.callback(item)
                delivered.set()
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def submit(self, sample, *, wait=True):
        delivered = threading.Event()
        self.work.put((sample, delivered))
        if wait:
            assert delivered.wait(3.), 'Fake snapshot callback did not return'
        return delivered

    def finish(self, error=None):
        self.error = error
        self.submit(None)
        self._thread.join(3.)
        assert not self._thread.is_alive()

    def stop(self, timeout):
        self.stop_event.set()
        self.work.put((None, threading.Event()))
        self._thread.join(timeout)


class Peer:
    def __init__(self, server, capabilities=('training_stats_v1',)):
        self.server = server
        self.capabilities = list(capabilities)
        self.session_id = None
        self.last_sequence = -1
        self.command_sequence = 0
        self.socket = None
        self.messages = []

    def connect(self, *, sync=False):
        self.socket = socket.create_connection(('127.0.0.1', self.server.port))
        self.socket.setblocking(False)
        self.decoder = FrameDecoder()
        frames = encode_message(dict(
            protocol_version=1, type='hello', session_id='', sequence=0,
            payload=dict(supported_versions=[1], capabilities=self.capabilities,
                         resume_session_id=self.session_id)))
        if sync:
            assert self.session_id is not None
            self.command_sequence += 1
            frames += encode_message(dict(protocol_version=1, type='command',
                      session_id=self.session_id, sequence=self.command_sequence,
                      payload=dict(command='session.sync', arguments={})))
        self.socket.sendall(frames)
        messages = self.until(lambda items: any(m['type'] == 'hello_ack' for m in items))
        ack = messages[0]
        assert ack['type'] == 'hello_ack'
        assert ack['payload']['resumed'] is (self.session_id is not None)
        if self.session_id is not None:
            assert ack['session_id'] == self.session_id
        self.session_id = ack['session_id']
        return ack

    def command(self, name, **arguments):
        self.command_sequence += 1
        self.socket.sendall(encode_message(dict(
            protocol_version=1, type='command', session_id=self.session_id,
            sequence=self.command_sequence, payload=dict(command=name, arguments=arguments))))

    def poll(self):
        self.server.poll()
        while self.socket is not None:
            try:
                data = self.socket.recv(65536)
            except BlockingIOError:
                break
            assert data, 'Bridge disconnected unexpectedly'
            for message in self.decoder.feed(data):
                assert message['sequence'] > self.last_sequence
                self.last_sequence = message['sequence']
                self.messages.append(message)

    def until(self, predicate):
        def ready():
            self.poll()
            return predicate(self.messages)
        wait_for(ready)
        return self.messages

    def settle(self):
        for _ in range(10):
            self.poll()
            time.sleep(.001)

    def clear(self):
        self.messages.clear()

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None


def started_server():
    session = BackendSession(scene(), trainer_factory=GatedTrainer, max_events=4)
    server = BridgeServer(port=0, session=session)
    peer = Peer(server)
    peer.connect()
    peer.clear()
    peer.command('run.start', mode='interactive_training', options={})
    peer.until(lambda items: any(m['type'] == 'lifecycle' and
                               m['payload']['run_status'] == 'RUNNING' for m in items))
    peer.settle()
    peer.clear()
    return session, server, peer


def disconnect(server, peer):
    peer.close()
    wait_for(lambda: (server.poll() or server.client is None))
    peer.clear()


def cleanup(session, server=None, peer=None):
    if peer is not None:
        peer.close()
    if server is not None:
        server.close()
    else:
        session.stop(1.)
    wait_for(lambda: not session.alive())
    worker = session._stop_worker
    if worker is not None:
        worker.join(3.)
        assert not worker.is_alive()


def training_records(messages):
    return [m['payload'] for m in messages if m['type'] == 'training_stats']


def test_progress_without_session_poll_is_bounded_and_preserves_latest_metrics():
    session = BackendSession(scene(), trainer_factory=GatedTrainer, max_events=4)
    try:
        session.start('interactive_training', {})
        for step in range(1, 201):
            session._trainer.submit(snapshot(step))
        assert session.status == 'RUNNING'
        assert session.alive()
        events = session.poll()
        assert [p['run_status'] for kind, p in events if kind == 'lifecycle'] == ['STARTING', 'RUNNING']
        records = [p for kind, p in events if kind == 'training_stats']
        assert len(records) <= 4
        assert max(p['step'] for p in records if p['record_kind'] == 'iteration_stats') == 199
        assert max(p['step'] for p in records if p['record_kind'] == 'progress') == 200
        assert [p['step'] for kind, p in events if kind == 'snapshot'] == [200]
        assert not [p for kind, p in events if kind == 'error']
    finally:
        cleanup(session)


def test_disconnected_server_resumes_latest_state_and_renegotiates_capabilities():
    session, server, peer = started_server()
    try:
        session._trainer.submit(snapshot(1))
        peer.until(lambda items: any(m['type'] == 'snapshot' for m in items))
        disconnect(server, peer)
        for step in range(2, 101):
            session._trainer.submit(snapshot(step))
            server.poll()
        assert session.status == 'RUNNING'
        assert len(server.backlog) <= 6
        last_sequence = peer.last_sequence
        ack = peer.connect()
        assert ack['sequence'] > last_sequence
        assert ack['payload']['run_id'] == session.run_id
        assert ack['payload']['run_status'] == 'RUNNING'
        peer.until(lambda items: any(m['type'] == 'snapshot' and m['payload']['step'] == 100 for m in items)
                   and {p['record_kind'] for p in training_records(items)} == {'progress', 'iteration_stats'})
        records = training_records(peer.messages)
        assert max(p['step'] for p in records if p['record_kind'] == 'iteration_stats') == 99
        assert records[-1]['step'] == 100 and records[-1]['phase'] == 'sampling'
        assert all(m['session_id'] == peer.session_id for m in peer.messages)
        disconnect(server, peer)
        peer.capabilities = []
        ack = peer.connect()
        assert 'training_stats_v1' not in ack['payload']['capabilities']
        peer.until(lambda items: any(m['type'] == 'snapshot' for m in items))
        peer.settle()
        assert not training_records(peer.messages)
        assert all('training_stats_v1' not in m['payload']['capabilities']
                   for m in peer.messages if m['type'] == 'lifecycle')
    finally:
        cleanup(session, server, peer)


@pytest.mark.parametrize('outcome', ['complete', 'fail', 'stop'])
def test_offline_completion_failure_and_stop_are_observed_without_client(outcome):
    session, server, peer = started_server()
    try:
        disconnect(server, peer)
        for step in range(1, 21):
            session._trainer.submit(snapshot(step))
            server.poll()
        if outcome == 'stop':
            session.stop(1.)
        else:
            session._trainer.finish('fake training failure' if outcome == 'fail' else None)
        terminal = 'FAILED' if outcome == 'fail' else 'STOPPED'
        wait_for(lambda: (server.poll() or session.status == terminal))
        assert not session.alive()
        assert server.client is None
        ack = peer.connect()
        assert ack['payload']['run_status'] == terminal
        assert ack['payload']['state_revision'] == session.state_revision
        peer.until(lambda items: any(m['type'] == 'lifecycle' and m['payload']['run_status'] == terminal
                                    for m in items))
        statuses = [m['payload']['run_status'] for m in peer.messages if m['type'] == 'lifecycle']
        assert statuses == (['FAILED'] if outcome == 'fail' else ['DRAINING', 'STOPPED'])
        assert all(m['payload']['state_revision'] <= ack['payload']['state_revision']
                   for m in peer.messages if m['type'] == 'lifecycle')
        if outcome == 'fail':
            assert any(m['payload'].get('reason') == 'fake training failure' for m in peer.messages)
        assert not any(m['type'] == 'error' and m['payload']['code'] == 'EVENT_OVERFLOW'
                       for m in peer.messages)
    finally:
        cleanup(session, server, peer)


def test_synchronization_state_and_revision_are_captured_atomically():
    serializing, release, attempted, transitioned = (threading.Event() for _ in range(4))
    class BlockingScene:
        def to_dict(self):
            serializing.set()
            assert release.wait(2.)
            return {'name': 'authoritative'}
    session = BackendSession(BlockingScene())
    captured = []
    reader = threading.Thread(target=lambda: captured.append(session.synchronization_state()))
    def transition():
        attempted.set()
        session.lifecycle('STARTING')
        transitioned.set()
    writer = threading.Thread(target=transition)
    try:
        reader.start()
        assert serializing.wait(2.)
        writer.start()
        assert attempted.wait(2.)
        assert not transitioned.is_set()
    finally:
        release.set()
        reader.join(2.)
        writer.join(2.)
    assert not reader.is_alive() and not writer.is_alive()
    assert (captured[0]['run_status'], captured[0]['state_revision'], captured[0]['scene']) == (
        'READY', 0, {'name': 'authoritative'})
    lifecycle = session.poll()[0][1]
    assert (lifecycle['run_status'], lifecycle['state_revision']) == ('STARTING', 1)


def test_incomplete_handshake_does_not_block_backend_completion():
    session, server, peer = started_server()
    unconfirmed = None
    try:
        disconnect(server, peer)
        unconfirmed = socket.create_connection(('127.0.0.1', server.port))
        wait_for(lambda: (server.poll() or server.client is not None))
        assert server.client is not None and not server.ready
        for step in range(1, 21):
            session._trainer.submit(snapshot(step))
            server.poll()
        session._trainer.finish()
        wait_for(lambda: (server.poll() or session.status == 'STOPPED'))
        assert server.client is not None and not server.ready
        assert not session.alive()
        unconfirmed.close()
        unconfirmed = None
        wait_for(lambda: (server.poll() or server.client is None))
        assert peer.connect()['payload']['run_status'] == 'STOPPED'
    finally:
        if unconfirmed is not None:
            unconfirmed.close()
        cleanup(session, server, peer)


class SlowSocket:
    def __init__(self, sock, allowance=7):
        self.sock = sock
        self.allowance = allowance

    def send(self, data):
        if self.allowance == 0:
            raise BlockingIOError()
        count = self.sock.send(data if self.allowance is None else data[:self.allowance])
        if self.allowance is not None:
            self.allowance -= count
        return count

    def __getattr__(self, name):
        return getattr(self.sock, name)


def test_partial_slow_client_bounds_progress_and_keeps_safety_events():
    session, server, peer = started_server()
    try:
        slow = SlowSocket(server.client)
        server.client = slow
        session._trainer.submit(snapshot(1))
        peer.poll()
        assert server.pending[0]['offset'] == 7
        first_frame = server.pending[0]['frame']
        for step in range(2, 101):
            session._trainer.submit(snapshot(step, safety=step == 100))
            peer.poll()
        assert session.status == 'RUNNING'
        assert server.client is slow
        assert server.pending[0]['frame'] == first_frame
        assert len(server.pending) <= 6
        assert server.pending_bytes <= server.max_queue_bytes
        assert len(server.backlog) <= 6
        slow.allowance = None
        peer.until(lambda items: any(m['type'] == 'snapshot' and m['payload']['step'] == 100 for m in items)
                   and any(m['type'] == 'safety_event' for m in items))
        assert len([m for m in peer.messages if m['type'] == 'safety_event']) == 1
        assert max(p['step'] for p in training_records(peer.messages)) == 100
        assert not [m for m in peer.messages if m['type'] == 'error']
    finally:
        cleanup(session, server, peer)


def test_safety_and_command_rejection_survive_disconnect_during_partial_send():
    session, server, peer = started_server()
    try:
        server.client = SlowSocket(server.client)
        session._trainer.submit(snapshot(1, safety=True))
        peer.poll()
        assert server.pending[0]['offset'] == 7
        peer.command('run.step')
        wait_for(lambda: (server.poll() or server.guard.last_sequence == peer.command_sequence))
        disconnect(server, peer)
        assert [kind for kind, _ in server.backlog] == ['safety_event', 'error']
        for step in range(2, 41):
            session._trainer.submit(snapshot(step))
            server.poll()
        peer.connect()
        peer.until(lambda items: any(m['type'] == 'snapshot' and m['payload']['step'] == 40 for m in items))
        peer.settle()
        assert [m['type'] for m in peer.messages if m['type'] in {'safety_event', 'error'}] == [
            'safety_event', 'error']
        error = next(m for m in peer.messages if m['type'] == 'error')
        assert error['payload']['code'] == 'COMMAND_REJECTED'
        assert not error['payload']['fatal']
        assert session.status == 'RUNNING'
    finally:
        cleanup(session, server, peer)


def test_reliable_backlog_larger_than_send_budget_drains_after_resume():
    session = BackendSession(scene(), max_events=4)
    server = BridgeServer(port=0, session=session, max_queue_bytes=420)
    peer = Peer(server, capabilities=())
    try:
        peer.connect()
        peer.clear()
        for status in ('STARTING', 'RUNNING', 'STOPPED'):
            session.lifecycle(status)
        session.emit('error', dict(code='RETAINED', message='retained error', fatal=False))
        server.poll()
        assert server.client is None
        assert [kind for kind, _ in server.backlog] == ['lifecycle', 'lifecycle', 'lifecycle', 'error']
        frame_sizes = [len(encode_message(dict(protocol_version=1, type=kind,
                         session_id=session.session_id, sequence=100, payload=payload)))
                       for kind, payload in server.backlog]
        assert max(frame_sizes) < server.max_queue_bytes < sum(frame_sizes)
        peer.close()
        peer.connect(sync=True)
        peer.until(lambda items: len([m for m in items if m['type'] == 'lifecycle']) == 4)
        assert [m['type'] for m in peer.messages] == [
            'hello_ack', 'lifecycle', 'lifecycle', 'lifecycle', 'error', 'lifecycle']
        assert [m['payload']['run_status'] for m in peer.messages if m['type'] == 'lifecycle'] == [
            'STARTING', 'RUNNING', 'STOPPED', 'STOPPED']
        assert peer.messages[-2]['payload']['code'] == 'RETAINED'
        assert server.guard.last_sequence == peer.command_sequence
        assert server.max_queue_bytes == 420
        assert not server.backlog
    finally:
        cleanup(session, server, peer)


def test_command_rejection_overflow_is_retained_and_delivered_after_resume():
    session = BackendSession(scene(), max_events=4)
    server = BridgeServer(port=0, session=session, max_queue_bytes=420)
    peer = Peer(server, capabilities=())
    try:
        peer.connect()
        peer.clear()
        server.client = SlowSocket(server.client, allowance=0)
        session.lifecycle('READY', 'retained acknowledgement')
        server.poll()
        rejection = dict(code='COMMAND_REJECTED', message='Command not valid in current state', fatal=False)
        frame_size = len(encode_message(dict(protocol_version=1, type='error',
                         session_id=session.session_id, sequence=server.sequence + 1, payload=rejection)))
        assert frame_size < server.max_queue_bytes < server.pending_bytes + frame_size
        peer.command('run.step')
        wait_for(lambda: (server.poll() or server.client is None))
        assert [kind for kind, _ in server.backlog] == ['lifecycle', 'error']
        assert server.backlog[-1][1] == rejection
        peer.close()
        peer.connect()
        peer.until(lambda items: any(m['type'] == 'error' for m in items))
        assert [m['type'] for m in peer.messages] == ['hello_ack', 'lifecycle', 'error']
        assert peer.messages[-1]['payload'] == rejection
        assert server.max_queue_bytes == 420
        assert not server.backlog
    finally:
        cleanup(session, server, peer)


@pytest.mark.parametrize('queue_bytes', [420, 2 * 1024 * 1024])
def test_reset_discards_unsent_and_deferred_observations_from_previous_run(queue_bytes):
    session = BackendSession(scene(), max_events=4)
    session.status = 'STOPPED'
    session.mode = 'interactive_training'
    session.run_id = 'previous-run'
    server = BridgeServer(port=0, session=session, max_queue_bytes=queue_bytes)
    peer = Peer(server)
    try:
        peer.connect()
        peer.clear()
        slow = SlowSocket(server.client, allowance=0)
        server.client = slow
        telemetry = SnapshotAdapter(scene(), session.session_id).encode(snapshot(1))
        telemetry['run_id'] = session.run_id
        session.emit('snapshot', telemetry)
        session.emit('training_stats', interactive_progress(snapshot(1), session.run_id, 2))
        peer.poll()
        if queue_bytes == 420:
            assert {key[0] for key in server._deferred} == {'snapshot', 'training_stats'}
        else:
            assert {item['kind'] for item in server.pending} == {'snapshot', 'training_stats'}
        peer.command('run.reset')
        wait_for(lambda: (server.poll() or server.guard.last_sequence == peer.command_sequence))
        assert session.status == 'READY'
        assert session.run_id is None
        assert not server._deferred
        assert all(item['kind'] not in {'snapshot', 'training_stats'} for item in server.pending)
        slow.allowance = None
        peer.until(lambda items: any(m['type'] == 'lifecycle' and m['payload']['run_status'] == 'READY'
                                    for m in items))
        peer.settle()
        assert [m['type'] for m in peer.messages] == ['lifecycle']
    finally:
        cleanup(session, server, peer)


def test_replayed_observations_include_offline_age_without_compounding(monkeypatch):
    from wfrl.blender_bridge import backend_session as session_module

    now = [10.]
    monkeypatch.setattr(session_module.time, 'monotonic', lambda: now[0])
    session = BackendSession(scene(), max_events=4)
    adapter = SnapshotAdapter(scene(), session.session_id)
    telemetry = adapter.encode(snapshot(1))
    metrics = interactive_progress(snapshot(1), 'run', 2)
    metrics['stats']['mean_power']['source_age_seconds'] = 7.
    session.emit('snapshot', telemetry)
    session.emit('training_stats', metrics)
    now[0] = 15.
    progress = interactive_progress(snapshot(2), 'run', 2)
    session.emit('training_stats', progress)
    session.poll()
    for elapsed in (40., 45.):
        now[0] = 10. + elapsed
        session.replay_latest()
        events = session.poll()
        records = [p for kind, p in events if kind == 'training_stats']
        assert [p['record_kind'] for p in records] == ['iteration_stats', 'progress']
        assert records[0]['source_age_seconds'] == elapsed
        assert records[0]['stats']['mean_power']['source_age_seconds'] == elapsed + 7.
        assert records[1]['source_age_seconds'] == elapsed - 5.
        latest = next(p for kind, p in events if kind == 'snapshot')
        assert latest['farm']['power']['source_age_seconds'] == elapsed
    assert metrics['source_age_seconds'] == 0.
    assert metrics['stats']['mean_power']['source_age_seconds'] == 7.
    assert telemetry['farm']['power']['source_age_seconds'] == 0.


def test_true_reliable_overflow_preserves_accepted_safety_history_and_fails():
    session = BackendSession(scene(), max_events=4)
    adapter = SnapshotAdapter(scene(), session.session_id)
    accepted = []
    for step in range(4):
        event = next(adapter.safety_events(snapshot(step, safety=True)))
        accepted.append(event['event_id'])
        session.emit('safety_event', event)
    with pytest.raises(RuntimeError, match='Reliable event queue overflow'):
        session.emit('safety_event', next(adapter.safety_events(snapshot(5, safety=True))))
    assert session.status == 'FAILED'
    assert len(session._events) <= 4
    events = session.poll()
    assert len(events) == 6
    assert [p['event_id'] for kind, p in events if kind == 'safety_event'] == accepted
    assert [(kind, p.get('code', p.get('run_status'))) for kind, p in events[-2:]] == [
        ('error', 'EVENT_OVERFLOW'), ('lifecycle', 'FAILED')]
    assert events[-2][1]['fatal'] is True


def test_pause_step_resume_and_rejection_remain_confirmed_after_disconnect():
    session, server, peer = started_server()
    try:
        disconnect(server, peer)
        for step in range(1, 21):
            session._trainer.submit(snapshot(step))
            server.poll()
        peer.connect()
        peer.until(lambda items: any(m['type'] == 'snapshot' for m in items))
        peer.settle()
        peer.clear()
        peer.command('run.pause')
        wait_for(lambda: (server.poll() or session._paused))
        session._trainer.submit(snapshot(21, safety=True), wait=False)
        peer.until(lambda items: any(m['type'] == 'snapshot' and m['payload']['step'] == 21 for m in items)
                   and any(m['type'] == 'lifecycle' and m['payload']['run_status'] == 'PAUSED' for m in items))
        paused = next(m for m in peer.messages if m['type'] == 'lifecycle')
        boundary = next(m for m in peer.messages if m['type'] == 'snapshot')
        assert paused['sequence'] < boundary['sequence']
        assert session.status == 'PAUSED'
        peer.settle()
        assert len([m for m in peer.messages if m['type'] == 'safety_event']) == 1
        peer.clear()
        peer.command('run.step')
        session._trainer.submit(snapshot(22), wait=False)
        peer.until(lambda items: any(m['type'] == 'snapshot' and m['payload']['step'] == 22 for m in items))
        paused = next(m for m in peer.messages if m['type'] == 'lifecycle')
        boundary = next(m for m in peer.messages if m['type'] == 'snapshot')
        assert paused['payload']['run_status'] == 'PAUSED'
        assert paused['sequence'] < boundary['sequence']
        assert session.status == 'PAUSED'
        peer.clear()
        peer.command('run.resume')
        peer.until(lambda items: any(m['type'] == 'lifecycle' and m['payload']['run_status'] == 'RUNNING'
                                    for m in items))
        peer.clear()
        peer.command('run.step')
        peer.until(lambda items: any(m['type'] == 'error' for m in items))
        assert peer.messages[-1]['payload']['code'] == 'COMMAND_REJECTED'
        assert peer.messages[-1]['payload']['fatal'] is False
        assert session.status == 'RUNNING'
    finally:
        cleanup(session, server, peer)


def test_formal_progress_burst_is_coalesced_without_observability_failure(tmp_path, monkeypatch):
    path = tmp_path / 'scene.yaml'
    path.write_text('fake')
    session = BackendSession(scene(), scene_loader=lambda _: scene(), max_events=4)
    server = BridgeServer(port=0, session=session)
    code = '''import sys
from wfrl.blender_bridge.training_progress import TrainingProgressWriter
path = sys.argv[sys.argv.index('--training-progress') + 1]
run = sys.argv[sys.argv.index('--run-id') + 1]
writer = TrainingProgressWriter(path, run)
for step in range(1, 101):
    writer.step = step
    writer.agent_step = step * 2
    writer.progress('sampling', step)
    writer.iteration_stats(step, 5., .5, .3, .8)
writer.progress('done')
writer.close()
'''
    monkeypatch.setattr(session, '_formal_command', lambda *_: (str(__import__('pathlib').Path.cwd()),
                                                              [sys.executable, '-c', code]))
    try:
        session.start('formal_training', dict(scene=str(path)))
        session._process.wait(timeout=5.)
        wait_for(lambda: (server.poll() or session.status == 'STOPPED'))
        assert session._progress_reader is None
        assert session._progress_dir is None
        events = session.poll()
        assert not [payload for kind, payload in events if kind == 'error']
        records = [payload for kind, payload in events if kind == 'training_stats']
        assert len(records) <= 4
        assert records[-1]['phase'] == 'done'
        assert any(p['record_kind'] == 'iteration_stats' and p['step'] == 100 for p in records)
        assert [p['run_status'] for kind, p in events if kind == 'lifecycle'] == [
            'STARTING', 'RUNNING', 'DRAINING', 'STOPPED']
    finally:
        cleanup(session, server)
