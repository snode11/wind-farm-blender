"""Source-native connection identity recovery with inert socketpair peers.

Real RNA, operators, transport framing, runtime state and unregister are checked.
External TCP connections, modal dialog layout and user input are not exercised.
"""
from collections import deque
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = Path(os.environ.get('WFRL_TEST_SOURCE_ROOT', ROOT)).expanduser().resolve()
OUTPUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
OUTPUT.mkdir(parents=True, exist_ok=False)
sys.path[:0] = [str(SOURCE_ROOT / 'blender_frontend'), str(SOURCE_ROOT)]
report = dict(status='FAIL', checks={}, source_root=str(SOURCE_ROOT),
              script=str(Path(__file__).resolve()), blender=bpy.app.version_string,
              background=bpy.app.background, external_tcp_connections=0,
              limitations=['Modal dialog layout and real user input are not exercised.'], errors=[])


def check(name, condition, detail=''):
    report['checks'][name] = bool(condition)
    assert condition, detail or name


def identity(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def rejected(action, fragment):
    try:
        result = action()
    except RuntimeError as exc:
        assert fragment.lower() in str(exc).lower(), str(exc)
        return str(exc)
    assert result == {'CANCELLED'}, result
    return 'CANCELLED'


addon = None
registered = False
transport = None
original_init = None
links = []
old_backend = dict(session_id='native-old-session', run_id='native-old-run', run_status='RUNNING', stops=0)
new_backend = dict(session_id='native-new-session', run_id=None, run_status='READY', stops=0)

try:
    check('background_factory_context', bpy.app.background and not hasattr(bpy.types.Scene, 'wfrl_workflow'))
    addon = importlib.import_module('wfrl_blender')
    runtime = importlib.import_module('wfrl_blender.runtime')
    transport = importlib.import_module('wfrl_blender.transport')
    state = importlib.import_module('wfrl_blender.state')
    connection = importlib.import_module('wfrl_blender.operators.connection')
    status = importlib.import_module('wfrl_blender.panels.status')
    protocol = importlib.import_module('wfrl_blender.protocol')
    check('source_module_identity', all(Path(module.__file__).resolve().is_relative_to(
        SOURCE_ROOT / 'blender_frontend') for module in (addon, runtime, transport, state, connection, status, protocol)))
    report['sources'] = {name: identity(module.__file__) for name, module in (
        ('addon', addon), ('runtime', runtime), ('transport', transport), ('state', state),
        ('connection_operator', connection), ('status_panel', status), ('protocol_loader', protocol))}
    report['sources']['protocol_codec'] = identity(SOURCE_ROOT / 'wfrl/blender_bridge/messages.py')
    report['sources']['regression'] = identity(__file__)
    addon.register()
    registered = True
    classes = addon._classes()
    check('operator_registration', hasattr(connection, 'WFRL_OT_ConnectNewBackend')
          and connection.WFRL_OT_ConnectNewBackend in classes
          and connection.WFRL_OT_ConnectNewBackend.is_registered)
    operator = bpy.ops.wfrl.bridge_connect_new
    confirm = operator.get_rna_type().properties['confirm_forget']
    check('confirmation_rna_default_false', confirm.type == 'BOOLEAN' and confirm.default is False)
    check('poll_rejects_no_claimed_session', not operator.poll())

    def acknowledgement(backend, *, resumed, sequence):
        return protocol.encode_message(dict(protocol_version=1, type='hello_ack',
            session_id=backend['session_id'], sequence=sequence,
            payload=dict(selected_version=1, capabilities=['pause', 'single_step'],
                         run_status=backend['run_status'], mode='interactive_training',
                         resumed=resumed, run_id=backend['run_id'], state_revision=10 if backend is old_backend else 0)))

    peers = deque([(old_backend, False, 1), (old_backend, True, 2), (new_backend, False, 1)])

    class InertSocket:
        """A ready IPC socket with a scripted peer; connect_ex never contacts TCP."""
        def __init__(self, _family, _kind):
            self.client, self.peer = socket.socketpair()
            self.backend, self.resumed, self.sequence = peers.popleft()
            self.decoder = protocol.FrameDecoder()
            self.messages = []
            self.closed = False
            self.address = None
            links.append(self)

        def connect_ex(self, address):
            self.address = address
            self.peer.sendall(acknowledgement(self.backend, resumed=self.resumed, sequence=self.sequence))
            return 0

        def setblocking(self, blocking):
            self.client.setblocking(blocking)

        def fileno(self):
            return self.client.fileno()

        def getsockopt(self, *args):
            return self.client.getsockopt(*args)

        def send(self, data):
            sent = self.client.send(data)
            for message in self.decoder.feed(bytes(data[:sent])):
                self.messages.append(message)
                if message['type'] == 'command' and message['payload']['command'] == 'run.stop':
                    self.backend['stops'] += 1
                    self.backend['run_status'] = 'STOPPED'
            return sent

        def recv(self, size):
            return self.client.recv(size)

        def close(self):
            self.closed = True
            self.client.close()

    original_init = transport.TransportClient.__init__

    def inert_connection(client, *args, **kwargs):
        kwargs['socket_factory'] = InertSocket
        original_init(client, *args, **kwargs)

    transport.TransportClient.__init__ = inert_connection
    check('initial_connect_operator', bpy.ops.wfrl.bridge_connect() == {'FINISHED'})
    runtime.tick()
    old_client = runtime._client
    ui = runtime.get_state()
    check('old_session_confirmed_by_real_transport', type(old_client) is transport.TransportClient
          and old_client.status == 'CONNECTED' and ui.confirmed
          and (ui.session_id, ui.run_id, ui.run_status) == (
              old_backend['session_id'], old_backend['run_id'], 'RUNNING'))
    check('poll_requires_disconnection', not operator.poll())
    report['connected_rejection'] = rejected(lambda: operator(confirm_forget=True), 'poll')
    check('connected_rejection_preserves_identity', runtime._client is old_client
          and ui.session_id == old_backend['session_id'] and ui.confirmed and not runtime.forgotten_connections)
    check('disconnect_operator', bpy.ops.wfrl.bridge_disconnect() == {'FINISHED'})
    check('disconnected_identity_remains_unconfirmed', not ui.confirmed and ui.connection == 'DISCONNECTED'
          and ui.run_status == 'RUNNING' and old_client.session_id == old_backend['session_id'])
    check('poll_allows_disconnected_claimed_session', operator.poll())
    report['unacknowledged_rejection'] = rejected(lambda: operator('EXEC_DEFAULT'), 'confirmation')
    check('unacknowledged_action_preserves_identity', runtime._client is old_client
          and (ui.session_id, ui.run_id, ui.run_status, ui.confirmed) == (
              old_backend['session_id'], old_backend['run_id'], 'RUNNING', False)
          and old_client.session_id == old_backend['session_id'] and len(links) == 1
          and not runtime.forgotten_connections)
    check('reconnect_operator', bpy.ops.wfrl.bridge_connect() == {'FINISHED'})
    check('poll_rejects_pending_handshake', runtime.progress() == 'Connecting' and not operator.poll())
    check('disconnect_pending_handshake', bpy.ops.wfrl.bridge_disconnect() == {'FINISHED'} and operator.poll())
    check('acknowledged_action', operator('EXEC_DEFAULT', confirm_forget=True) == {'FINISHED'})
    previous = runtime.forgotten_connections[-1]
    report['forgotten_connection'] = dict(previous)
    check('old_job_diagnostics_retained', previous == dict(session_id=old_backend['session_id'],
          run_id=old_backend['run_id'], run_status='RUNNING', confirmed=False))
    check('new_connection_waits_for_its_own_handshake', runtime._client is not old_client
          and type(runtime._client) is transport.TransportClient and runtime._client.session_id is None
          and not runtime.get_state().confirmed and not operator.poll())
    check('old_transport_identity_forgotten_explicitly', old_client.session_id is None
          and old_client.forgotten_session_id == old_backend['session_id'])
    runtime.tick()
    ui = runtime.get_state()
    check('replacement_identity_confirmed_separately', ui.confirmed and ui.connection == 'CONNECTED'
          and (ui.session_id, ui.run_id, ui.run_status) == (new_backend['session_id'], None, 'READY'))
    check('old_job_not_marked_or_stopped', old_backend['run_status'] == 'RUNNING'
          and old_backend['stops'] == 0 and previous['run_status'] == 'RUNNING' and previous['confirmed'] is False)
    check('no_backend_control_commands_sent', all(message['type'] == 'hello'
          for link in links for message in link.messages))
    report['socket_connections'] = [dict(address=list(link.address), session_id=link.backend['session_id'],
        messages=[message['type'] for message in link.messages]) for link in links]
    addon.unregister()
    registered = False
    check('clean_unregister', all(not cls.is_registered for cls in classes)
          and not bpy.app.timers.is_registered(runtime.tick)
          and not hasattr(bpy.types.WindowManager, 'wfrl_home_page')
          and all(link.closed for link in links))
    try:
        bpy.ops.wfrl.bridge_connect_new.get_rna_type()
    except (RuntimeError, KeyError):
        report['checks']['operator_rna_removed'] = True
    else:
        check('operator_rna_removed', False)
    check('unregister_does_not_stop_old_job', old_backend['run_status'] == 'RUNNING'
          and old_backend['stops'] == 0)
    check('sources_unchanged', all(identity(item['path']) == item for item in report['sources'].values()))
    report['status'] = 'PASS'
except Exception:
    report['errors'].append(traceback.format_exc())
    raise
finally:
    if transport is not None and original_init is not None:
        transport.TransportClient.__init__ = original_init
    if registered:
        try:
            addon.unregister()
        except Exception:
            report['errors'].append(traceback.format_exc())
            report['status'] = 'FAIL'
    for link in links:
        link.close()
        link.peer.close()
    (OUTPUT / 'checks.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')

assert report['status'] == 'PASS', report
print('CONNECTION_RECOVERY_NATIVE_PASS', json.dumps(report, ensure_ascii=False), flush=True)
