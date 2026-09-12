"""New Part 4 main-thread integration tests; no Blender launch."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from wfrl_blender import runtime
from wfrl_blender.state import FrontendState
from wfrl_blender.animation import KinematicState
from test_animation_math import snapshot


class LiveRuntimeTests(unittest.TestCase):
    def test_workflow_metadata_keeps_channel_rows_and_waits_for_ack(self):
        sent = []
        messages = []
        client = SimpleNamespace(
            session_id='s', _last_outbound=2, last_error='', status='CONNECTED',
            progress='Connected', next_sequence=3,
            poll=lambda: list(messages), send=sent.append, close=lambda: None,
        )
        ui = FrontendState(mode='interactive_training', connection='CONNECTED',
                           run_status='STOPPED', confirmed=True, session_id='s')
        bpy = SimpleNamespace(
            context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])),
            data=SimpleNamespace(objects={}),
        )
        lifecycle = {
            'type': 'lifecycle', 'session_id': 's', 'sequence': 4,
            'payload': {
                'run_status': 'STOPPED', 'reason': None,
                'mode': 'interactive_training', 'capabilities': [],
                'channel_table': [['lidar', 'lidar', 'EXPORTED', False, 'source']],
                'channel_states': {'lidar': False},
            },
        }
        with patch.dict(sys.modules, bpy=bpy), patch.multiple(
                runtime, _client=client, _state=ui, _pending_command=None,
                _pending_lifecycle_sequence=None, _last_tick=None, _health=None,
                _desired_mode='interactive_training', kinematics=KinematicState(),
                safety_events=[], _live_scene=None, channel_table=[], channel_states={}):
            runtime.send_workflow_command('channel.set', {'topic': 'lidar', 'enabled': False})
            self.assertEqual(runtime._pending_command, 'channel.set')
            self.assertEqual(sent[0]['sequence'], 3)
            messages.append(lifecycle)
            runtime.tick()
            self.assertEqual(runtime.channel_table,
                             [['lidar', 'lidar', 'EXPORTED', False, 'source']])
            self.assertEqual(runtime.channel_states, {'lidar': False})
            self.assertIsNone(runtime._pending_command)

    def test_controls_wait_for_lifecycle_and_preserve_safety(self):
        messages = []
        sent = []
        client = SimpleNamespace(session_id='s', _last_outbound=3, last_error='', status='CONNECTED',
                                 poll=lambda: list(messages), send=sent.append, close=lambda: None, progress='Connected')
        ui = FrontendState(mode='interactive_training', connection='CONNECTED', session_id='s', capabilities={'pause'})
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])),
                              data=SimpleNamespace(objects={}))
        with patch.dict(sys.modules, bpy=bpy), patch.multiple(runtime, _client=client, _state=ui,
                _pending_command=None, _last_tick=None, _health=None, kinematics=KinematicState(), safety_events=[],
                _desired_mode='interactive_training', _live_scene=None, build_live_scene=lambda scene: None):
            runtime.send_command('start', {'mode': ui.mode, 'options': {}})
            self.assertEqual(sent[0]['sequence'], 4)
            self.assertEqual(ui.run_status, 'READY')
            self.assertFalse(runtime.configuration_editable())
            self.assertFalse(runtime.allows_command('start'))
            event = {'type': 'safety_event', 'session_id': 's', 'payload': {'event_id': 'safe'}}
            newer = snapshot(8); newer['step'] = 100
            lifecycle = lambda status: {'type': 'lifecycle', 'session_id': 's', 'payload': {
                'run_status': status, 'reason': None, 'mode': 'interactive_training',
                'capabilities': ['pause', 'single_step']}}
            messages.extend([lifecycle('STARTING'),
                             {'type': 'snapshot', 'payload': snapshot()}, event,
                             {'type': 'snapshot', 'payload': newer},
                             lifecycle('RUNNING')])
            runtime.tick()
            self.assertEqual(ui.run_status, 'RUNNING')
            self.assertTrue(runtime.allows_command('pause'))
            self.assertEqual(runtime.kinematics.snapshot['step'], 100)
            self.assertEqual(list(runtime.safety_events), [event])
            messages.clear(); client.last_error = 'lost'
            runtime.tick()
            self.assertEqual(ui.run_status, 'RUNNING')
            self.assertFalse(ui.confirmed)
            self.assertFalse(runtime.allows_command('stop'))

    def test_first_handshake_preserves_requested_mode(self):
        ack = {'type': 'hello_ack', 'session_id': 'new', 'payload': {
            'run_status': 'READY', 'mode': 'demo', 'capabilities': []}}
        sent = []
        client = SimpleNamespace(session_id='new', _last_outbound=0, last_error='', status='CONNECTED',
                                 poll=lambda: [ack], send=sent.append, progress='Connected')
        ui = FrontendState(mode='formal_training', connection='DISCONNECTED')
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])),
                              data=SimpleNamespace(objects={}))
        with patch.dict(sys.modules, bpy=bpy), patch.multiple(runtime, _client=client, _state=ui,
                _pending_command=None, _last_tick=None, _health=None, kinematics=KinematicState(),
                _desired_mode='formal_training', _live_scene=None):
            runtime.tick()
            self.assertEqual(ui.mode, 'demo')
            self.assertEqual(runtime.desired_mode(), 'formal_training')
            runtime.send_command('start')
            self.assertEqual(sent[0]['payload']['arguments']['mode'], 'formal_training')

    def test_formal_lifecycle_replaces_handshake_mode_and_capabilities(self):
        lifecycle = {'type': 'lifecycle', 'session_id': 's', 'payload': {
            'run_status': 'STARTING', 'reason': None, 'mode': 'formal_training',
            'capabilities': []}, 'sequence': 2}
        client = SimpleNamespace(session_id='s', _last_outbound=1, last_error='', status='CONNECTED',
                                 poll=lambda: [lifecycle], send=lambda message: None,
                                 close=lambda: None, progress='Connected')
        ui = FrontendState(mode='demo', connection='CONNECTED', run_status='READY',
                           session_id='s', capabilities={'pause', 'single_step'})
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])),
                              data=SimpleNamespace(objects={}))
        with patch.dict(sys.modules, bpy=bpy), patch.multiple(
                runtime, _client=client, _state=ui, _pending_command='start',
                _last_tick=None, _health=None, kinematics=KinematicState(), safety_events=[],
                _desired_mode='formal_training', _live_scene=None):
            runtime.tick()
            self.assertEqual(ui.mode, 'formal_training')
            self.assertEqual(ui.capabilities, set())
            self.assertEqual(ui.run_status, 'STARTING')
            self.assertFalse(runtime.allows_command('pause'))

    def test_step_remains_pending_until_a_new_snapshot_arrives(self):
        messages = []
        client = SimpleNamespace(session_id='s', _last_outbound=1, last_error='', status='CONNECTED',
                                 poll=lambda: list(messages), send=lambda message: None,
                                 close=lambda: None, progress='Connected')
        ui = FrontendState(mode='interactive_training', connection='CONNECTED', run_status='PAUSED',
                           session_id='s', capabilities={'pause', 'single_step'})
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])),
                              data=SimpleNamespace(objects={}))
        current = snapshot()
        state = KinematicState()
        state.snapshot = current
        with patch.dict(sys.modules, bpy=bpy), patch.multiple(
                runtime, _client=client, _state=ui, _pending_command='step', _pending_step=current['step'],
                _pending_lifecycle_sequence=None,
                _last_tick=None, _health=None, kinematics=state, safety_events=[],
                _desired_mode='interactive_training', _live_scene=None,
                build_live_scene=lambda scene: None):
            messages.append({'type': 'lifecycle', 'session_id': 's', 'payload': {
                'run_status': 'PAUSED', 'reason': None, 'mode': 'interactive_training',
                'capabilities': ['pause', 'single_step']}, 'sequence': 2})
            runtime.tick()
            self.assertFalse(runtime.allows_command('step'))
            advanced = snapshot()
            advanced['step'] = current['step'] + 1
            messages[:] = [{'type': 'snapshot', 'session_id': 's', 'payload': advanced,
                            'sequence': 3}]
            runtime.tick()
            self.assertTrue(runtime.allows_command('step'))

    def test_pause_ignores_snapshot_queued_before_paused_lifecycle(self):
        current = snapshot(); current['step'] = 1
        stale = snapshot(); stale['step'] = 2
        boundary = snapshot(); boundary['step'] = 2
        messages = [
            {'type': 'snapshot', 'session_id': 's', 'payload': stale, 'sequence': 2},
            {'type': 'lifecycle', 'session_id': 's', 'sequence': 3, 'payload': {
                'run_status': 'PAUSED', 'reason': None, 'mode': 'interactive_training',
                'capabilities': ['pause', 'single_step']}},
        ]
        client = SimpleNamespace(session_id='s', _last_outbound=1, last_error='', status='CONNECTED',
                                 poll=lambda: list(messages), send=lambda message: None,
                                 close=lambda: None, progress='Connected')
        ui = FrontendState(mode='interactive_training', connection='CONNECTED', run_status='RUNNING',
                           session_id='s', capabilities={'pause', 'single_step'})
        state = KinematicState(); state.snapshot = current
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])),
                              data=SimpleNamespace(objects={}))
        with patch.dict(sys.modules, bpy=bpy), patch.multiple(
                runtime, _client=client, _state=ui, _pending_command='pause', _pending_step=1,
                _pending_lifecycle_sequence=None, _last_tick=None, _health=None,
                kinematics=state, safety_events=[], _desired_mode='interactive_training',
                _live_scene=None, build_live_scene=lambda scene: None):
            runtime.tick()
            self.assertFalse(runtime.allows_command('step'))
            messages[:] = [{'type': 'snapshot', 'session_id': 's', 'payload': boundary,
                            'sequence': 4}]
            runtime.tick()
            self.assertTrue(runtime.allows_command('step'))

    def test_local_demo_can_connect_to_backend_demo(self):
        clients = []
        class Client:
            def __init__(self, port):
                self.port = port
                self.progress = 'Disconnected'
                clients.append(self)
            def connect(self): self.progress = 'Connecting'
        ui = FrontendState()
        bpy = SimpleNamespace(context=SimpleNamespace(scene=None))
        with patch.dict(sys.modules, bpy=bpy), \
                patch('wfrl_blender.transport.TransportClient', Client), patch.multiple(
                    runtime, _client=None, _state=ui, _desired_mode='demo'):
            runtime.connect(9000)
            self.assertEqual(runtime.get_state().connection, 'DISCONNECTED')
            self.assertEqual(runtime.desired_mode(), 'demo')
            self.assertEqual(clients[0].progress, 'Connecting')

    def test_running_local_demo_cannot_connect(self):
        ui = FrontendState(run_status='RUNNING')
        bpy = SimpleNamespace(context=SimpleNamespace(scene=None))
        with patch.dict(sys.modules, bpy=bpy), patch.multiple(
                runtime, _client=None, _state=ui, _desired_mode='demo'):
            with self.assertRaisesRegex(ValueError, 'Stop the local Demo'):
                runtime.connect(9000)

    def test_safety_history_is_bounded_and_clears_for_new_session(self):
        events = [{'type': 'safety_event', 'session_id': 'old',
                   'payload': {'event_id': str(index)}} for index in range(300)]
        ack = {'type': 'hello_ack', 'session_id': 'new', 'payload': {
            'run_status': 'READY', 'mode': 'demo', 'capabilities': []}}
        batches = [events, [ack]]
        client = SimpleNamespace(session_id='new', _last_outbound=0, last_error='', status='CONNECTED',
                                 poll=lambda: batches.pop(0), send=lambda message: None,
                                 close=lambda: None, progress='Connected')
        ui = FrontendState(mode='demo', connection='CONNECTED', session_id='old')
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])),
                              data=SimpleNamespace(objects={}))
        with patch.dict(sys.modules, bpy=bpy), patch.multiple(
                runtime, _client=client, _state=ui, _pending_command=None, _last_tick=None,
                _health=None, kinematics=KinematicState(), _desired_mode='demo', _live_scene=None):
            runtime.safety_events.clear()
            runtime.tick()
            self.assertEqual(len(runtime.safety_events), 256)
            runtime.tick()
            self.assertEqual(len(runtime.safety_events), 0)

    def test_geometry_ids_rejected_and_layout_change_detected(self):
        from wfrl_blender.live_scene import snapshot_scene
        data = snapshot()
        first = snapshot_scene(data)
        data['scene']['layout'][0]['x'] = 504
        self.assertNotEqual(first, snapshot_scene(data))
        data['scene']['layout'][0]['id'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'IDs do not match'):
            snapshot_scene(data)
        del data['scene']
        with self.assertRaisesRegex(ValueError, 'geometry metadata'):
            snapshot_scene(data)

    def test_saved_live_scene_load_stays_disconnected_and_unconfirmed(self):
        import wfrl_blender
        scene = SimpleNamespace(get=lambda key: 'live' if key == 'wfrl_scene_kind' else None,
                                objects={})
        bpy = SimpleNamespace(data=SimpleNamespace(scenes=[scene], cameras=[]))
        with patch.dict(sys.modules, bpy=bpy), patch.object(runtime, 'disconnect') as disconnect:
            wfrl_blender._on_load(None)
            disconnect.assert_called_once_with(force=True)
