"""Main-thread coordinator; workers never access Blender data."""
from .state import FrontendState
from .animation import KinematicState
from .training import TrainingDashboard
from .live_scene import snapshot_scene, build_live_scene
from .wake import WakeFrameBuffer, validate_wake_payload, apply_disxy_frame
from collections import deque
import time

# Blender reloads submodules too. Tear down the old timer before rebinding its
# name, while retaining the last session identity and uncertainty for reconnect.
_previous_shutdown = globals().get('shutdown')
if _previous_shutdown:
    _previous_shutdown()
_state = globals().get('_state', FrontendState())
_client = globals().get('_client')
_health = None
kinematics = KinematicState()
training_dashboard = TrainingDashboard()
safety_events = deque(globals().get('safety_events', []), maxlen=256)
_desired_mode = globals().get('_desired_mode', _state.mode)
_live_scene = None
_pending_step = None
_pending_command = None
_pending_lifecycle_sequence = None
_last_tick = None
health_results = globals().get('health_results', {})
wake_buffer = globals().get('wake_buffer', WakeFrameBuffer())
latest_wake = globals().get('latest_wake')
workflow_scene = {}
channel_states = {}
channel_table = list(globals().get('channel_table', []))


def get_state():
    import bpy
    if _state.connection == 'LOCAL DEMO' and getattr(bpy.context, 'scene', None):
        _state.run_status = bpy.context.scene.get('wfrl_run_status', 'READY')
    return _state


def configuration_editable():
    return not _pending_command and get_state().allows('configure') and not (_client and _client.progress in {'Connecting', 'Awaiting handshake'})


def select_mode(mode):
    global _state, _client, kinematics, _pending_command, _pending_lifecycle_sequence, _desired_mode, _live_scene, latest_wake
    from .state import MODES
    if mode not in MODES or not configuration_editable():
        raise ValueError('Stop the active session before changing mode')
    if _client:
        _client.close()
        _client = None
    kinematics = KinematicState()
    _desired_mode = mode
    _live_scene = None
    wake_buffer.pop_latest()
    latest_wake = None
    _pending_command = None
    _pending_lifecycle_sequence = None
    safety_events.clear()
    from . import charts
    charts.clear()
    training_dashboard.reset()
    workflow_scene.clear()
    channel_states.clear()
    channel_table.clear()
    if mode != 'demo':
        import bpy
        from . import _cancel_playback
        _cancel_playback()
        for obj in bpy.data.objects:
            if obj.name.startswith('WFRL.Turbine.'):
                obj.animation_data_clear()
            if obj.name.startswith(('WFRL.WakeProxy.', 'WFRL.Fixture.')):
                obj.hide_render = True
                obj.hide_set(True)
    elif mode == 'demo':
        # A backend scene may already have built the same turbine hierarchy
        # before the user switches to Demo. That scene is marked ``live``
        # and has no demo keyframes, so merely changing FrontendState leaves
        # Start Demo playing a timeline whose rotor pose never changes.
        # Reclassify the existing geometry explicitly and configure the
        # deterministic 66-second presentation timeline.
        import bpy
        scene = getattr(bpy.context, 'scene', None)
        if scene is not None and scene.objects.get('WFRL.Turbine.T1.Rotor'):
            from .state import END_FRAME
            from . import _cancel_playback, _update_demo_status, _update_layers
            _cancel_playback()
            scene.frame_start = 1
            scene.frame_end = END_FRAME
            scene.use_preview_range = False
            scene['wfrl_scene_kind'] = 'demo'
            scene['wfrl_run_status'] = 'READY'
            _update_layers(scene)
            _update_demo_status(scene)
    _state = FrontendState(mode=mode, connection='LOCAL DEMO' if mode == 'demo' else 'DISCONNECTED')


def connect(port):
    global _client, _state
    from .transport import TransportClient
    if get_state().connection == 'LOCAL DEMO' and not configuration_editable():
        raise ValueError('Stop the local Demo before connecting to a backend')
    if get_state().connection == 'LOCAL DEMO':
        _state = FrontendState(mode='demo', connection='DISCONNECTED')
    if _client is None:
        _client = TransportClient(port=port)
    elif _client.port != port:
        if not configuration_editable():
            raise ValueError('Cannot change port while session state is unconfirmed')
        _client.close()
        _client = TransportClient(port=port)
    import bpy
    from . import backend_inflow
    if bpy.context.scene is not None:
        bpy.context.scene[backend_inflow.KEY] = "[]"
        bpy.context.scene["wfrl_cinematic_inflow_valid"] = False
    _client.connect()


def disconnect(force=False):
    if _client:
        _client.close()
    if force or get_state().connection != 'LOCAL DEMO':
        _state.disconnect('Disconnected; backend state is unconfirmed')
        if not force and not _state.session_id and not (_client and _client.session_id):
            _state.confirmed = True


def enter_local_demo():
    global _client, _state, kinematics, _pending_command, _pending_lifecycle_sequence, _live_scene, _desired_mode, latest_wake
    if _client:
        _client.close()
    _client = None
    _state = FrontendState(mode='demo', connection='LOCAL DEMO')
    _desired_mode = 'demo'
    kinematics = KinematicState()
    _pending_command = None
    _pending_lifecycle_sequence = None
    _live_scene = None
    wake_buffer.pop_latest()
    latest_wake = None
    safety_events.clear()
    from . import charts
    charts.clear()
    training_dashboard.reset()
    workflow_scene.clear()
    channel_states.clear()
    channel_table.clear()


def check_environment(preferences):
    global _health
    import bpy
    from .health import HealthChecker, HealthConfig
    if _health:
        _health.cancel()
    _health = HealthChecker()
    health_results.clear()
    config = HealthConfig(**{key: getattr(preferences, key, '') for key in
                             ('project_dir', 'backend_python', 'mpi_path', 'fastfarm_path')})
    _health.start(config, blender_version=bpy.app.version)


def desired_mode():
    return _desired_mode


def training_stats_negotiated():
    return bool(_client and 'training_stats_v1' in getattr(_client, 'negotiated_capabilities', set()))


def allows_command(action):
    ui = get_state()
    if ui.connection != 'CONNECTED' or _pending_command:
        return False
    if action == 'reset':
        return ui.confirmed and ui.run_status in {'READY', 'FAILED', 'STOPPED'}
    return ui.allows(action)


def send_command(action, arguments=None):
    global _pending_command, _pending_step, _pending_lifecycle_sequence
    if not allows_command(action):
        raise ValueError('Control unavailable until backend state is confirmed')
    arguments = dict(arguments or {})
    if action == 'start':
        arguments['mode'] = _desired_mode
    _client.send({'protocol_version': 1, 'type': 'command',
                  'session_id': _client.session_id, 'sequence': _client._last_outbound + 1,
                  'payload': {'command': 'run.' + action, 'arguments': arguments or {}}})
    if _client.last_error:
        raise ValueError(_client.last_error)
    _pending_command = action
    _pending_step = kinematics.snapshot["step"] if kinematics.snapshot else -1
    _pending_lifecycle_sequence = None


def send_workflow_command(name, arguments):
    """Enqueue configuration; only backend acknowledgements update displayed state."""
    global _pending_command
    if name not in {'scene.load', 'channel.set'}:
        raise ValueError('Unknown workflow command')
    ui = get_state()
    if ui.connection != 'CONNECTED' or not ui.confirmed or _pending_command:
        raise ValueError('Wait for a confirmed backend connection')
    if name == 'scene.load' and not configuration_editable():
        raise ValueError('Stop the active session before changing configuration')
    _client.send({'protocol_version': 1, 'type': 'command',
                  'session_id': _client.session_id, 'sequence': _client.next_sequence,
                  'payload': {'command': name, 'arguments': dict(arguments)}})
    if _client.last_error:
        raise ValueError(_client.last_error)
    _pending_command = name


def tick():
    """Timer work is bounded by transport budgets; health polls only queued results."""
    global _pending_command, _pending_lifecycle_sequence, _last_tick, kinematics, _live_scene, _desired_mode, latest_wake
    import bpy
    now = time.monotonic()
    dt = 0.0 if _last_tick is None else max(0.0, now - _last_tick)
    _last_tick = now
    if _state.connection != 'LOCAL DEMO':
        kinematics.advance(dt, running=_state.run_status == 'RUNNING',
                           connected=_state.connection == 'CONNECTED' and _state.confirmed)
    latest_snapshot = None
    if _client:
        messages = _client.poll()
        try:
            for message in messages:
                payload = message['payload']
                if message['type'] in {'hello_ack', 'lifecycle'}:
                    if 'run_id' in payload and payload['run_id'] != _state.run_id:
                        _state.run_id = payload['run_id']
                        training_dashboard.reset(_state.run_id)
                        kinematics = KinematicState()
                        latest_snapshot = None
                        from . import charts
                        charts.clear(_state.run_id)
                    if 'scene' in payload:
                        workflow_scene.clear(); workflow_scene.update(payload['scene'] or {})
                        # scene.load is acknowledged before the first training
                        # snapshot. Build the authored wind farm immediately so
                        # the viewport does not remain on Blender's default cube.
                        if payload.get('scene'):
                            from .scene_model import SceneDTO
                            loaded_scene = SceneDTO.from_mapping(payload['scene'])
                            if _live_scene is None or loaded_scene.geometry_key() != _live_scene.geometry_key():
                                build_live_scene(loaded_scene)
                            _live_scene = loaded_scene
                    if 'channel_states' in payload:
                        channel_states.clear(); channel_states.update(payload['channel_states'] or {})
                    if 'channel_table' in payload:
                        channel_table.clear(); channel_table.extend(payload['channel_table'] or [])
                if message['type'] == 'hello_ack':
                    _pending_command = None
                    _pending_lifecycle_sequence = None
                    if _state.session_id != message['session_id']:
                        kinematics = KinematicState()
                        safety_events.clear()
                        from . import charts
                        charts.clear()
                    _state.synchronize(message['session_id'], payload['run_status'],
                                       payload['mode'], payload['capabilities'])
                    if payload['run_status'] in {'STARTING', 'RUNNING', 'PAUSED', 'DRAINING'}:
                        _desired_mode = payload['mode']
                elif message['type'] == 'lifecycle':
                    if (_pending_command not in {'pause', 'step'} or
                            payload['run_status'] in {'DRAINING', 'STOPPED', 'FAILED'}):
                        _pending_command = None
                        _pending_lifecycle_sequence = None
                    elif payload['run_status'] == 'PAUSED':
                        _pending_lifecycle_sequence = message['sequence']
                    _state.lifecycle(message['session_id'], payload['run_status'],
                                     payload['mode'], payload['capabilities'])
                    if payload['run_status'] in {'STARTING', 'RUNNING', 'PAUSED', 'DRAINING'}:
                        _desired_mode = payload['mode']
                elif message['type'] == 'training_stats':
                    from . import charts
                    training_dashboard.ingest(message, negotiated='training_stats_v1' in getattr(_client, 'negotiated_capabilities', set()))
                    charts.record_training(message)
                elif message['type'] == 'curve':
                    from . import charts
                    charts.record_curve(message)
                elif message['type'] == 'snapshot':
                    if _state.run_id is not None and payload.get('run_id', _state.run_id) != _state.run_id:
                        continue
                    from . import charts
                    charts.record_snapshot(message)
                    latest_snapshot = message
                elif message['type'] == 'wake':
                    wake_buffer.push(validate_wake_payload(message['payload'], sequence=message['sequence']))
                elif message['type'] == 'safety_event':
                    safety_events.append(message)
                elif message['type'] == 'error':
                    _pending_command = None
                    _pending_lifecycle_sequence = None
                    _state.error = payload.get('message', 'Bridge error')
            if latest_snapshot:
                payload = latest_snapshot['payload']
                scene = snapshot_scene(payload)
                if _live_scene is None or scene.geometry_key() != _live_scene.geometry_key():
                    build_live_scene(scene)
                _live_scene = scene
                kinematics.apply_snapshot(latest_snapshot)
                _state.mode = payload['mode']
                scene_obj = getattr(getattr(bpy, 'context', None), 'scene', None)
                if scene_obj is not None:
                    scene_data = payload.get('scene') or {}
                    scene_obj['wfrl_backend'] = scene_data.get('backend', 'backend')
                    inflow = scene_data.get('inflow') or {}
                    from . import backend_inflow, cinematic
                    backend_inflow.record(scene_obj, payload)
                    cinematic.update(scene_obj, 0.0)
                    timestamp = payload.get('timestamp') or {}
                    scene_obj['wfrl_telemetry_time_s'] = timestamp.get('value')
                    power_values = []
                    fidelity = None
                    for turbine in payload.get('turbines', ()):
                        channel = (turbine.get('channels') or {}).get('power')
                        if channel and channel.get('validity') == 'valid':
                            power_values.append(channel.get('value'))
                            fidelity = fidelity or channel.get('fidelity')
                    scene_obj['wfrl_power_mw'] = power_values
                    scene_obj['wfrl_fidelity'] = fidelity or 'UNKNOWN'
                crossed_lifecycle = (_pending_lifecycle_sequence is not None and
                                     latest_snapshot['sequence'] > _pending_lifecycle_sequence)
                pause_confirmed = _pending_command == 'pause' and crossed_lifecycle
                step_confirmed = (_pending_command == 'step' and crossed_lifecycle and
                                  payload['step'] > _pending_step)
                if pause_confirmed or step_confirmed:
                    _pending_command = None
                    _pending_lifecycle_sequence = None
            wake_frame = wake_buffer.pop_latest()
            if wake_frame is not None:
                latest_wake = wake_frame
                scene = bpy.context.scene
                scene['wfrl_wake_source'] = wake_frame.source_label
                scene['wfrl_wake_fidelity'] = wake_frame.fidelity
                scene['wfrl_wake_sequence'] = wake_frame.sequence
                scene['wfrl_wake_provenance'] = wake_frame.provenance
                if wake_frame.kind == 'disxy' and wake_frame.is_renderable:
                    apply_disxy_frame(wake_frame)
            if _client.last_error:
                _state.disconnect(_client.last_error)
                if not _state.session_id and not _client.session_id:
                    _state.confirmed = True
            elif _client.status == 'DISCONNECTED' and _state.connection == 'CONNECTED':
                _state.disconnect('Connection lost; backend state is unconfirmed')
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            _client.close()
            _state.disconnect(f'Invalid backend data or scene: {exc}')
    if _state.connection != 'LOCAL DEMO':
        connected = _state.connection == 'CONNECTED' and _state.confirmed
        kinematics.apply_objects(bpy.data.objects, connected=connected)
        from .live_scene import animate_illustrative_wake
        animate_illustrative_wake(getattr(bpy.context, 'scene', None), dt,
                                 running=connected and _state.run_status == 'RUNNING')
    from . import charts
    charts.export_job.poll()
    if _health:
        for result in _health.poll():
            health_results[result.component] = result
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    return 0.05


def progress():
    return _client.progress if _client else ''


def shutdown():
    global _client, _health, _last_tick
    _last_tick = None
    import bpy
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    if _client:
        _client.close()
        if _state.connection != 'LOCAL DEMO':
            _state.disconnect('Extension stopped; backend state is unconfirmed')
            if not _state.session_id and not _client.session_id:
                _state.confirmed = True
    if _health:
        _health.cancel()
    _health = None
    # Keep the client/session counters available for safe reconnect after re-enable.


def register():
    import bpy
    previous = getattr(bpy, '_wfrl_part3_cleanup', None)
    if previous:
        previous()
    bpy._wfrl_part3_cleanup = shutdown
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.05, persistent=True)


def enter_result_replay():
    """Detach a stopped backend and reset transport without a synthetic demo clock."""
    if not configuration_editable():
        raise ValueError('Stop the active backend before loading results')
    enter_local_demo()
    _state.connection = 'OFFLINE RESULTS'
    _state.run_status = 'READY'
