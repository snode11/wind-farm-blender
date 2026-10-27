"""Real Bridge frames consumed by the frontend transport and runtime."""
from collections import deque
import importlib.util
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.server import BridgeServer
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl.scene.schema import Scene, Turbine
from wfrl_blender import runtime
from wfrl_blender.animation import KinematicState
from wfrl_blender.state import FrontendState
from wfrl_blender.training import TrainingDashboard


class ControlledTrainer:
    def __init__(self, scene, on_snapshot, **options):
        self.callback = on_snapshot
        self.work = queue.Queue()
        self.stopped = threading.Event()
        self.error = None

    def start(self):
        def work():
            while not self.stopped.is_set():
                sample = self.work.get()
                if sample is None:
                    return
                self.callback(sample)
        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def finish(self, error=None):
        self.error = error
        self.work.put(None)
        self._thread.join(2.)
        assert not self._thread.is_alive()

    def stop(self, timeout=2.):
        self.stopped.set()
        self.work.put(None)
        self._thread.join(timeout)


def farm(name='current', x=0.):
    return Scene(name=name, turbines=[Turbine('A', x, 0.), Turbine('B', x + 504., 0.)])


def sample():
    return SimpleNamespace(step=1, ctx={}, phase='sampling', iters_done=0,
                           measure=dict(yaw=[1., 2.], pitch=[3., 4.], rotor_speed=[7., 8.], power=[2., 3.]),
                           farm_power=5., reward=.5, events=[])


def wait_for(predicate):
    deadline = time.monotonic() + 3.
    while not predicate():
        assert time.monotonic() < deadline, 'Timed out waiting for connection state'
        time.sleep(.001)


@pytest.fixture
def frontend(monkeypatch):
    bpy = SimpleNamespace(context=SimpleNamespace(scene=None, window_manager=SimpleNamespace(windows=[])),
                          data=SimpleNamespace(objects={}))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    replacements = dict(_client=None, _state=FrontendState(mode='interactive_training', connection='DISCONNECTED'),
                        _desired_mode='interactive_training', _pending_command=None, _pending_step=None,
                        _pending_lifecycle_sequence=None, _last_tick=None, _health=None, _live_scene=None,
                        _backend_scene=None, _last_display_state=None, _last_async_display_state=None,
                        kinematics=KinematicState(), training_dashboard=TrainingDashboard(), safety_events=deque(maxlen=256),
                        backend_events=deque(maxlen=256), forgotten_connections=deque(maxlen=16),
                        workflow_scene={}, channel_states={}, channel_table=[])
    for key, value in replacements.items():
        monkeypatch.setattr(runtime, key, value, raising=False)
    built = []
    monkeypatch.setattr(runtime, 'build_live_scene', lambda scene: built.append(scene.name))
    yield built
    if runtime._client:
        runtime._client.close()


def pump(server, predicate):
    def ready():
        server.poll()
        runtime.tick()
        return predicate()
    wait_for(ready)


@pytest.mark.parametrize('outcome', ['complete', 'fail', 'pause'])
def test_resumed_producer_history_does_not_disconnect_or_regress_state(frontend, outcome):
    session = BackendSession(farm(), trainer_factory=ControlledTrainer)
    server = BridgeServer(port=0, session=session)
    try:
        runtime.connect(server.port)
        pump(server, lambda: runtime.get_state().connection == 'CONNECTED')
        runtime.send_command('start')
        pump(server, lambda: runtime.get_state().run_status == 'RUNNING')
        session_id, run_id = session.session_id, session.run_id
        runtime.disconnect()
        wait_for(lambda: (server.poll() or server.client is None))
        safety = next(SnapshotAdapter(session.scene, session_id).safety_events(
            SimpleNamespace(**dict(vars(sample()), events=[dict(turbine='A', severity='warn', detail='retained warning')]))))
        session.emit('safety_event', safety)
        session.emit('error', dict(code='OFFLINE_DIAGNOSTIC', message='retained diagnostic', fatal=False))
        if outcome == 'pause':
            session.command(dict(command='run.pause'))
            session._trainer.work.put(sample())
            terminal = 'PAUSED'
        else:
            session._trainer.finish('controlled failure' if outcome == 'fail' else None)
            terminal = 'FAILED' if outcome == 'fail' else 'STOPPED'
        wait_for(lambda: (server.poll() or session.status == terminal))
        runtime.connect(server.port)
        pump(server, lambda: len(runtime.safety_events) == 1 and runtime.get_state().connection == 'CONNECTED'
             and any(m['type'] == 'lifecycle' and m['payload']['run_status'] == terminal for m in runtime.backend_events))
        for _ in range(20):
            server.poll()
            runtime.tick()
        ui = runtime.get_state()
        assert (ui.connection, ui.confirmed, ui.run_status, ui.session_id, ui.run_id) == (
            'CONNECTED', True, terminal, session_id, run_id)
        assert runtime._client.last_error is None
        assert runtime.workflow_scene['name'] == 'current'
        assert [m['payload']['event_id'] for m in runtime.safety_events] == [safety['event_id']]
        assert any(m['type'] == 'error' and m['payload']['code'] == 'OFFLINE_DIAGNOSTIC'
                   for m in runtime.backend_events)
        if outcome == 'pause':
            runtime.send_command('resume')
            pump(server, lambda: runtime.get_state().run_status == 'RUNNING')
    finally:
        server.close()
        wait_for(lambda: not session.alive())


def test_resume_history_cannot_restore_an_obsolete_run_or_scene(frontend):
    session = BackendSession(farm('old', 0.))
    server = BridgeServer(port=0, session=session)
    try:
        runtime.connect(server.port)
        pump(server, lambda: runtime.get_state().connection == 'CONNECTED')
        runtime.disconnect()
        wait_for(lambda: (server.poll() or server.client is None))
        session.run_id = 'old-run'
        for status in ('STARTING', 'RUNNING', 'DRAINING', 'STOPPED'):
            session.lifecycle(status)
        session.scene = farm('current', 5000.)
        session.run_id = 'current-run'
        session.lifecycle('READY')
        session.lifecycle('STARTING')
        session.lifecycle('RUNNING')
        session.lifecycle('PAUSED')
        frontend.clear()
        runtime.connect(server.port)
        pump(server, lambda: len(runtime.backend_events) == 8)
        ui = runtime.get_state()
        assert (ui.connection, ui.run_status, ui.run_id) == ('CONNECTED', 'PAUSED', 'current-run')
        assert runtime.training_dashboard.run_id == 'current-run'
        assert runtime.workflow_scene['name'] == 'current'
        assert frontend == ['current']
    finally:
        server.close()


def test_new_live_revision_after_resume_still_rejects_an_illegal_transition(frontend):
    session = BackendSession(farm())
    server = BridgeServer(port=0, session=session)
    try:
        runtime.connect(server.port)
        pump(server, lambda: runtime.get_state().connection == 'CONNECTED')
        runtime.disconnect()
        wait_for(lambda: (server.poll() or server.client is None))
        for status in ('STARTING', 'RUNNING', 'DRAINING', 'STOPPED'):
            session.lifecycle(status)
        runtime.connect(server.port)
        pump(server, lambda: len(runtime.backend_events) == 4)
        assert runtime.get_state().run_status == 'STOPPED'
        session.lifecycle('PAUSED')
        pump(server, lambda: runtime.get_state().connection == 'DISCONNECTED')
        assert runtime.get_state().run_status == 'STOPPED'
        assert 'Invalid lifecycle transition: STOPPED -> PAUSED' in runtime.get_state().error
    finally:
        server.close()


def test_restarted_bridge_requires_confirmed_identity_reset_without_stopping_old_job(frontend):
    old_session = BackendSession(farm(), trainer_factory=ControlledTrainer)
    old_server = BridgeServer(port=0, session=old_session)
    replacement = None
    try:
        runtime.connect(old_server.port)
        pump(old_server, lambda: runtime.get_state().connection == 'CONNECTED')
        runtime.send_command('start')
        pump(old_server, lambda: runtime.get_state().run_status == 'RUNNING')
        old_id, old_run = old_session.session_id, old_session.run_id
        with pytest.raises(ValueError, match='Disconnect'):
            runtime.connect_new_backend(old_server.port, confirmed=True)
        assert runtime.get_state().session_id == old_id
        runtime.disconnect()
        old_server._disconnect()
        old_server.listener.close()
        old_server.closed = True
        replacement = BridgeServer(port=old_server.port, session=BackendSession(farm('replacement')))
        runtime.connect(replacement.port)
        pump(replacement, lambda: bool(runtime._client.last_error))
        assert runtime._client.last_error_code == 'UNKNOWN_SESSION'
        assert runtime._client.session_id == old_id
        assert (runtime.get_state().run_status, runtime.get_state().confirmed) == ('RUNNING', False)
        with pytest.raises(ValueError, match='confirmation'):
            runtime.connect_new_backend(replacement.port)
        with pytest.raises(ValueError):
            runtime.enter_local_demo()
        runtime.connect_new_backend(replacement.port, confirmed=True)
        assert not runtime.get_state().confirmed
        assert old_session.alive()
        pump(replacement, lambda: runtime.get_state().connection == 'CONNECTED')
        ui = runtime.get_state()
        assert (ui.session_id, ui.run_status) == (replacement.session.session_id, 'READY')
        assert runtime.forgotten_connections[-1] == dict(session_id=old_id, run_id=old_run,
                                                        run_status='RUNNING', confirmed=False)
        assert old_session.alive()
    finally:
        if replacement:
            replacement.close()
        old_session.stop(1.)
        wait_for(lambda: not old_session.alive())


def test_new_backend_operator_requires_confirmation_and_invokes_a_dialog(frontend, monkeypatch):
    reports, connected, dialogs = [], [], []
    class Operator:
        def report(self, level, message):
            reports.append(message)
    bpy = SimpleNamespace(types=SimpleNamespace(Operator=Operator),
                          props=SimpleNamespace(StringProperty=lambda **kw: None, BoolProperty=lambda **kw: None))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'wfrl_blender.preferences', SimpleNamespace(get_preferences=lambda context: None))
    path = Path(runtime.__file__).parent / 'operators' / 'connection.py'
    spec = importlib.util.spec_from_file_location('wfrl_blender.operators.connection', path)
    operators = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operators)
    action = operators.WFRL_OT_ConnectNewBackend()
    def connect(port, *, confirmed=False):
        if not confirmed:
            raise ValueError('Explicit confirmation required')
        connected.append(port)
    monkeypatch.setattr(runtime, 'connect_new_backend', connect)
    action.confirm_forget = False
    assert action.execute(None) == {'CANCELLED'}
    assert not connected and reports
    context = SimpleNamespace(window_manager=SimpleNamespace(
        invoke_props_dialog=lambda operator, **kw: dialogs.append(operator) or {'RUNNING_MODAL'}))
    action.confirm_forget = True
    assert action.invoke(context, None) == {'RUNNING_MODAL'}
    assert action.confirm_forget is False
    assert dialogs == [action]
    action.confirm_forget = True
    assert action.execute(None) == {'FINISHED'}
    assert connected == [8765]
