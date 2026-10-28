"""Main-thread coordinator; workers never access Blender data."""
from .state import FrontendState
from .animation import KinematicState
from .training import TrainingDashboard
from .live_scene import snapshot_scene, build_live_scene
from .wake import WakeFrameBuffer, validate_wake_payload, apply_disxy_frame, set_disxy_validity
from collections import deque
import time
import traceback
from . import performance

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
backend_events = deque(globals().get('backend_events', []), maxlen=256)
forgotten_connections = deque(globals().get('forgotten_connections', []), maxlen=16)
_desired_mode = globals().get('_desired_mode', _state.mode)
_live_scene = None
_pending_step = None
_pending_command = None
_pending_lifecycle_sequence = None
_last_tick = None
_last_async_display_state = None
_last_display_state = None
_backend_scene = None
_tick_failures = set()
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


def manual_display_active():
    import sys
    bpy = sys.modules.get("bpy")
    scene = getattr(getattr(bpy, "context", None), "scene", None)
    return bool(scene is not None and hasattr(scene, "get") and scene.get("wfrl_scene_kind") == "manual")


def has_backend_session():
    """A claimed session retains its transport counters until synchronization."""
    return bool(_state.session_id or getattr(_client, 'session_id', None))


def configuration_editable(scene=None):
    manual = manual_display_active() if scene is None else scene.get("wfrl_scene_kind") == "manual"
    return not manual and not _pending_command and get_state().allows('configure') and not (_client and _client.progress in {'Connecting', 'Awaiting handshake'})


def select_mode(mode):
    global _state, _client, kinematics, _pending_command, _pending_lifecycle_sequence, _desired_mode, _live_scene, latest_wake, _backend_scene
    from .state import MODES
    if mode not in MODES or not configuration_editable():
        raise ValueError('Stop the active session before changing mode')
    from .scene_builder import validate_scene_build
    validate_scene_build()
    _clear_backend_wake()
    if _client:
        _client.close()
        _client = None
    kinematics = KinematicState()
    _desired_mode = mode
    _live_scene = None
    _backend_scene = None
    wake_buffer.pop_latest()
    latest_wake = None
    _pending_command = None
    _pending_lifecycle_sequence = None
    safety_events.clear()
    backend_events.clear()
    from . import charts
    charts.clear()
    training_dashboard.reset()
    workflow_scene.clear()
    channel_states.clear()
    channel_table.clear()
    if mode != 'demo':
        import bpy
        from . import _cancel_playback
        _cancel_playback(bpy.context.scene)
        for obj in bpy.data.objects:
            if obj.name.startswith('WFRL.Turbine.'):
                obj.animation_data_clear()
            if obj.name.startswith(('WFRL.WakeProxy.', 'WFRL.Fixture.')):
                obj.hide_render = True
                obj.hide_set(True)
    _state = FrontendState(mode=mode, connection='LOCAL DEMO' if mode == 'demo' else 'DISCONNECTED')
    if mode == 'demo':
        from . import load_demo_scene
        load_demo_scene()
    else:
        import bpy
        from . import farm_flex, clearance_replay, tip_tracking
        farm_flex.detach()
        scene = getattr(bpy.context, 'scene', None)
        if scene is not None:
            tip_tracking.disable(scene, 'backend_mode')
            clearance_replay.clear(scene)



def connect(port):
    global _client, _state, _backend_scene, _live_scene, kinematics, _last_tick
    if manual_display_active():
        raise ValueError("请先返回原场景，再连接后端")
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
    try:
        scene = backend_scene()
    except ValueError:
        scene = None
    if scene is None:
        _backend_scene = getattr(bpy.context, 'scene', None)
        _live_scene = None
        kinematics = KinematicState()
        _last_tick = None
        _clear_backend_wake()
        scene = backend_scene()
    if scene is not None:
        scene[backend_inflow.KEY] = "[]"
        scene["wfrl_cinematic_inflow_valid"] = False
    _client.connect()


def disconnect(force=False):
    global kinematics, _live_scene, _last_tick, _pending_command, _pending_step
    global _pending_lifecycle_sequence, latest_wake, _backend_scene
    if _client:
        _client.close()
    _clear_backend_wake('stale')
    if force or get_state().connection != 'LOCAL DEMO':
        _state.disconnect('Disconnected; backend state is unconfirmed')
        if not force and not _state.session_id and not (_client and _client.session_id):
            _state.confirmed = True
    if force:
        kinematics = KinematicState()
        _live_scene = None
        _backend_scene = None
        _last_tick = None
        _pending_command = _pending_step = _pending_lifecycle_sequence = None
        wake_buffer.pop_latest()
        latest_wake = None


def can_connect_new_backend():
    return (not manual_display_active() and get_state().connection == 'DISCONNECTED'
            and has_backend_session() and progress() not in {'Connecting', 'Awaiting handshake'})


def connect_new_backend(port, *, confirmed=False):
    """Require explicit consent to abandon local ownership of an unknown backend."""
    global _client, _state, kinematics, _pending_command, _pending_step, _pending_lifecycle_sequence
    global _live_scene, _backend_scene, latest_wake
    if confirmed is not True:
        raise ValueError('Explicit confirmation is required; forgetting this connection does not stop the previous backend')
    if not can_connect_new_backend():
        raise ValueError('Disconnect the previous backend before connecting to a new identity')
    from .transport import TransportClient
    replacement = TransportClient(port=port)
    _clear_backend_wake()
    forgotten_connections.append(dict(session_id=_state.session_id or getattr(_client, 'session_id', None),
                                     run_id=_state.run_id, run_status=_state.run_status, confirmed=False))
    if _client:
        _client.close()
        _client.forget_session(confirmed=True)
    _client = replacement
    _state = FrontendState(mode=_desired_mode, connection='DISCONNECTED', confirmed=False)
    kinematics = KinematicState()
    _pending_command = _pending_step = _pending_lifecycle_sequence = None
    _live_scene = _backend_scene = None
    wake_buffer.pop_latest()
    latest_wake = None
    safety_events.clear()
    backend_events.clear()
    training_dashboard.reset()
    from . import charts
    charts.clear()
    workflow_scene.clear()
    channel_states.clear()
    channel_table.clear()
    connect(port)


def enter_local_demo():
    global _client, _state, kinematics, _pending_command, _pending_lifecycle_sequence, _live_scene, _desired_mode, latest_wake, _backend_scene
    if has_backend_session() and not configuration_editable():
        raise ValueError('Confirm the previous backend state before entering local Demo')
    _clear_backend_wake()
    if _client:
        _client.close()
    _client = None
    _state = FrontendState(mode='demo', connection='LOCAL DEMO')
    _desired_mode = 'demo'
    kinematics = KinematicState()
    _pending_command = None
    _pending_lifecycle_sequence = None
    _live_scene = None
    _backend_scene = None
    wake_buffer.pop_latest()
    latest_wake = None
    safety_events.clear()
    backend_events.clear()
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
    if ui.connection != 'CONNECTED':
        return False
    if _pending_command and not (action == 'stop' and _pending_command in {'pause', 'step'}):
        return False
    if _pending_command and (not _client or ui.session_id != _client.session_id):
        return False
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


@performance.timed("runtime.tick")
def tick():
    """Keep the timer alive and report backend or local polling errors."""
    try:
        return _tick()
    except Exception as exc:
        error = f'Runtime polling failed: {exc}'
        if _client is not None or _state.connection not in {'LOCAL DEMO', 'OFFLINE RESULTS'}:
            _state.disconnect(error + '; backend state is unconfirmed')
        else:
            _state.error = error
        failure = (type(exc), str(exc))
        if failure not in _tick_failures:
            _tick_failures.add(failure)
            try:
                traceback.print_exc()
            except Exception:
                pass
        try:
            if _client is not None:
                _client.close()
            _clear_backend_wake('stale')
        except Exception:
            pass
        try:
            _redraw_viewports()
        except Exception:
            pass
        return 0.05


def _redraw_viewports(*, scenes=None):
    import bpy
    targets = tuple(scenes) if scenes is not None else (getattr(bpy.context, 'scene', None),)
    for window in bpy.context.window_manager.windows:
        scene = getattr(window, 'scene', getattr(bpy.context, 'scene', None))
        if not any(scene == target for target in targets):
            continue
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                performance.count("runtime.redraw_requests")
                area.tag_redraw()


def _async_display_state(job, context):
    """Track labels published by export and window-recording callbacks."""
    scenes = (getattr(window, 'scene', getattr(context, 'scene', None))
              for window in context.window_manager.windows)
    return (job.status, getattr(job, 'error', None), getattr(job, 'path', None),
            tuple((_scene_key(scene), scene.get('wfrl_history_export_status'), scene.get('wfrl_capture_status'))
                  for scene in scenes if scene is not None))


def _scene_key(scene):
    return scene.as_pointer() if hasattr(scene, 'as_pointer') else id(scene)


def backend_scene():
    """Return the bound live scene; a deleted owner is never replaced implicitly."""
    import bpy
    scene = _backend_scene
    if scene is None:
        return None
    try:
        key = _scene_key(scene)
        scenes = getattr(getattr(bpy, 'data', None), 'scenes', None)
        if not key or (scenes is not None and not any(_scene_key(item) == key for item in scenes)):
            raise ValueError('Backend scene is unavailable; reconnect with an explicitly selected scene')
    except ReferenceError as exc:
        raise ValueError('Backend scene was removed or reloaded; data application is stopped') from exc
    return scene


def _build_backend_scene(dto):
    global _backend_scene
    import bpy
    scene = backend_scene()
    if scene is None:
        scene = getattr(bpy.context, 'scene', None)
        _backend_scene = scene
    if scene is None or scene == getattr(bpy.context, 'scene', None):
        build_live_scene(dto)
    else:
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0],
                                       collection=scene.collection):
            build_live_scene(dto)


def _clear_backend_wake(validity='waiting'):
    global latest_wake
    wake_buffer.pop_latest()
    latest_wake = None
    try:
        scene = backend_scene()
    except ValueError:
        return
    if scene is not None:
        scene['wfrl_wake_validity'] = validity
        set_disxy_validity(scene, validity)


def _display_state():
    """Observe displayed values and expiry boundaries without continuous redraws."""
    connected = _state.connection == 'CONNECTED' and _state.confirmed
    snapshot = kinematics.snapshot
    validity = []
    if snapshot:
        groups = [snapshot.get('farm', {})]
        groups.extend(t['channels'] for t in snapshot.get('turbines', ()))
        validity = [kinematics.validity(record, connected)
                    for channels in groups for record in channels.values()]
    training = ()
    if training_stats_negotiated():
        phase = training_dashboard.phase()
        training = (training_dashboard.last_sequence, phase['validity'],
                    int(phase['age']) if phase['age'] is not None else None,
                    tuple((key, record['validity'], int(record['age']) if 'age' in record else None)
                          for key in training_dashboard.metrics
                          for record in (training_dashboard.metric(key),)))
    return (_state.connection, _state.run_status, _state.confirmed, _state.error,
            _state.mode, _desired_mode, _pending_command,
            getattr(_client, 'progress', None), getattr(_client, 'last_error', None),
            id(snapshot), tuple(validity), tuple(getattr(kinematics, 'rotor_angles', {}).items()),
            training, len(safety_events),
            (latest_wake.sequence, latest_wake.validity_at(time.monotonic(), connected=connected))
            if latest_wake is not None else None)


def _tick():
    """Timer work is bounded by transport budgets; health polls only queued results."""
    global _pending_command, _pending_lifecycle_sequence, _last_tick, kinematics, _live_scene, _desired_mode, latest_wake
    global _last_async_display_state, _last_display_state, _backend_scene
    import bpy
    performance.count("runtime.timer_calls")
    if _client is not None and _backend_scene is None and manual_display_active():
        _last_tick = None
        return 0.05
    now = time.monotonic()
    display_before = _last_display_state if _last_display_state is not None else _display_state()
    first_backend_display = (_last_display_state is None
                             and (_client is not None or kinematics.snapshot is not None))
    dt = 0.0 if _last_tick is None else max(0.0, now - _last_tick)
    _last_tick = now
    if _state.connection != 'LOCAL DEMO':
        kinematics.advance(dt, running=_state.run_status == 'RUNNING',
                           connected=_state.connection == 'CONNECTED' and _state.confirmed)
    latest_snapshot = None
    messages = []
    if _client:
        try:
            backend_scene()
            messages = _client.poll()
            for message in messages:
                payload = message['payload']
                if message['type'] == 'hello_ack' and _state.session_id != message['session_id']:
                    backend_events.clear()
                    _clear_backend_wake()
                if message['type'] in {'lifecycle', 'safety_event', 'error'}:
                    backend_events.append(message)
                if message['type'] == 'lifecycle':
                    applied = _state.lifecycle(message['session_id'], payload['run_status'],
                                               payload['mode'], payload['capabilities'],
                                               state_revision=payload.get('state_revision'),
                                               cleanup_confirmed=payload.get('cleanup_confirmed'),
                                               worker_alive=payload.get('worker_alive'))
                    if not applied:
                        continue
                if message['type'] in {'hello_ack', 'lifecycle'}:
                    if 'run_id' in payload and payload['run_id'] != _state.run_id:
                        _state.run_id = payload['run_id']
                        training_dashboard.reset(_state.run_id)
                        kinematics = KinematicState()
                        _clear_backend_wake()
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
                                _build_backend_scene(loaded_scene)
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
                                       payload['mode'], payload['capabilities'],
                                       state_revision=payload.get('state_revision'),
                                       cleanup_confirmed=payload.get('cleanup_confirmed'),
                                       worker_alive=payload.get('worker_alive'))
                    if payload['run_status'] in {'STARTING', 'RUNNING', 'PAUSED', 'DRAINING'}:
                        _desired_mode = payload['mode']
                elif message['type'] == 'lifecycle':
                    if _pending_command == 'stop' and payload['run_status'] not in {'DRAINING', 'STOPPED', 'FAILED'}:
                        pass
                    elif (_pending_command not in {'pause', 'step'} or
                            payload['run_status'] in {'DRAINING', 'STOPPED', 'FAILED'}):
                        _pending_command = None
                        _pending_lifecycle_sequence = None
                    elif payload['run_status'] == 'PAUSED':
                        _pending_lifecycle_sequence = message['sequence']
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
                    if _state.run_id is not None and payload.get('run_id', _state.run_id) != _state.run_id:
                        continue
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
                    _build_backend_scene(scene)
                _live_scene = scene
                kinematics.apply_snapshot(latest_snapshot)
                _state.mode = payload['mode']
                scene_obj = backend_scene()
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
                scene = backend_scene()
                if scene is not None:
                    scene['wfrl_wake_source'] = wake_frame.source_label
                    scene['wfrl_wake_fidelity'] = wake_frame.fidelity
                    scene['wfrl_wake_sequence'] = wake_frame.sequence
                    scene['wfrl_wake_provenance'] = wake_frame.provenance
                    if wake_frame.kind == 'disxy' and wake_frame.is_renderable:
                        apply_disxy_frame(wake_frame, scene=scene)
            if _client.last_error:
                _state.disconnect(_client.last_error)
                if not _state.session_id and not _client.session_id:
                    _state.confirmed = True
            elif _client.status == 'DISCONNECTED' and _state.connection == 'CONNECTED':
                _state.disconnect('Connection lost; backend state is unconfirmed')
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            _client.close()
            _state.disconnect(f'Invalid backend data or scene: {exc}')
    display_scene = None
    if _state.connection != 'LOCAL DEMO':
        connected = _state.connection == 'CONNECTED' and _state.confirmed
        try:
            scene = backend_scene()
        except ValueError:
            scene = None
            connected = False
        display_scene = scene
        if scene is not None and latest_wake is not None:
            validity = latest_wake.validity_at(now, connected=connected)
            if scene.get('wfrl_wake_validity') != validity:
                scene['wfrl_wake_validity'] = validity
            set_disxy_validity(scene, validity if latest_wake.kind == 'disxy' or validity != 'valid' else 'waiting')
        if first_backend_display or display_before != _display_state():
            kinematics.apply_objects(getattr(scene, 'objects', {}), connected=connected)
        from .live_scene import animate_illustrative_wake
        animate_illustrative_wake(scene, dt,
                                 running=connected and _state.run_status == 'RUNNING')
    from . import charts
    async_before = (_async_display_state(charts.export_job, bpy.context)
                      if _last_async_display_state is None else _last_async_display_state)
    charts.export_job.poll()
    async_state = _async_display_state(charts.export_job, bpy.context)
    _last_async_display_state = async_state
    health_changed = False
    if _health:
        for result in _health.poll():
            health_results[result.component] = result
            health_changed = True
    display_state = _display_state()
    backend_changed = first_backend_display or display_state != display_before or bool(messages)
    _last_display_state = display_state
    targets = []
    current_scene = getattr(bpy.context, 'scene', None)
    if backend_changed:
        targets.append(display_scene if display_scene is not None else current_scene)
    if health_changed or async_state[:3] != async_before[:3]:
        targets.append(current_scene)
    previous_rows = {row[0]: row[1:] for row in async_before[3]}
    changed_scenes = {row[0] for row in async_state[3]
                      if row[1:] != previous_rows.get(row[0], row[1:])}
    for window in bpy.context.window_manager.windows:
        scene = getattr(window, 'scene', current_scene)
        if _scene_key(scene) in changed_scenes:
            targets.append(scene)
    if not targets:
        performance.count('runtime.unchanged_redraw_skips')
        return 0.05
    _redraw_viewports(scenes=targets)
    return 0.05


def progress():
    return _client.progress if _client else ''


def shutdown():
    global _client, _health, _last_tick, _last_async_display_state, _last_display_state, _backend_scene
    _last_tick = None
    _last_async_display_state = None
    _last_display_state = None
    _clear_backend_wake('stale')
    _backend_scene = None
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


def enter_result_replay(*, scene=None, restoring=False):
    """Restore saved replay without discarding a claimed backend session.

    Explicit result loading requires an editable backend. File and history
    restoration can bind saved geometry while backend control stays unconfirmed.
    """
    if (restoring and scene is not None
            and scene.get('wfrl_scene_kind') == 'clearance_replay'
            and has_backend_session()):
        if _state.connection != 'DISCONNECTED' or _state.confirmed:
            disconnect(force=True)
        return
    if not configuration_editable(scene):
        raise ValueError('Stop the active backend before loading results')
    enter_local_demo()
    _state.connection = 'OFFLINE RESULTS'
    _state.run_status = 'READY'
