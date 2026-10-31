"""Backend data remains attached to its scene across active-scene changes."""
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import charts, cinematic, runtime
from wfrl_blender.animation import KinematicState
from wfrl_blender.live_scene import snapshot_scene
from wfrl_blender.state import FrontendState
from wfrl_blender.wake import WakeFrameBuffer, validate_wake_payload
from test_wake_payload import channel
from test_animation_math import snapshot


class Scene(dict):
    def __init__(self, name):
        super().__init__(name=name, wfrl_scene_kind='live')
        self.objects = {}


@pytest.fixture
def live_owner(monkeypatch):
    owner, other = Scene('owner'), Scene('other')
    data = snapshot()
    messages = [dict(type='snapshot', sequence=1, payload=data)]
    client = NS(poll=lambda: list(messages), last_error='', status='CONNECTED',
                session_id='s', port=8765, progress='Connected',
                connect=lambda: setattr(client, 'progress', 'Connecting'),
                close=lambda: setattr(client, 'status', 'DISCONNECTED'))
    bpy = NS(context=NS(scene=other, window_manager=NS(windows=[])),
             data=NS(objects={}, scenes=[owner, other]))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    for key, value in dict(_client=client, _health=None, _last_tick=None,
            _last_display_state=None, _last_async_display_state=None,
            _pending_command=None, _pending_lifecycle_sequence=None,
            _backend_scene=owner, _live_scene=snapshot_scene(data),
            _state=FrontendState(mode='interactive_training', connection='CONNECTED',
                                 run_status='PAUSED', confirmed=True, session_id='s'),
            kinematics=KinematicState(), wake_buffer=WakeFrameBuffer(), latest_wake=None).items():
        monkeypatch.setattr(runtime, key, value)
    monkeypatch.setattr(charts, 'export_job', NS(status='IDLE', poll=lambda: None))
    monkeypatch.setattr(cinematic, 'update', lambda *_args: None)
    return NS(owner=owner, other=other, bpy=bpy, client=client, messages=messages)


def test_snapshot_updates_owned_scene_while_another_scene_is_active(live_owner):
    before = dict(live_owner.other)
    runtime.tick()
    assert live_owner.owner.get('wfrl_telemetry_time_s') == 1e8
    assert live_owner.owner.get('wfrl_cinematic_inflow_valid') is True
    assert live_owner.other == before
    assert runtime._state.connection == 'CONNECTED'


@pytest.mark.parametrize('pending_snapshot', [False, True])
def test_deleted_owner_does_not_reassign_data_to_active_scene(live_owner, pending_snapshot):
    if not pending_snapshot:
        live_owner.messages.clear()
    live_owner.bpy.data.scenes.remove(live_owner.owner)
    before = dict(live_owner.other)
    runtime.tick()
    assert live_owner.other == before
    assert runtime._state.connection == 'DISCONNECTED'
    assert not runtime._state.confirmed


def test_explicit_reconnect_rebinds_deleted_owner_and_preserves_identity(live_owner):
    live_owner.bpy.data.scenes.remove(live_owner.owner)
    runtime.tick()
    runtime.connect(8765)
    assert runtime._backend_scene is live_owner.other
    assert runtime._live_scene is None
    assert runtime._state.session_id == live_owner.client.session_id == 's'
    assert live_owner.client.progress == 'Connecting'


def test_live_updates_continue_when_active_scene_is_manual(live_owner):
    live_owner.other['wfrl_scene_kind'] = 'manual'
    runtime.tick()
    assert live_owner.owner.get('wfrl_telemetry_time_s') == 1e8
    assert 'wfrl_telemetry_time_s' not in live_owner.other


@pytest.mark.parametrize('previous', [[], [['lidar', 'lidar', 'EXPORTED', False, 'source']]])
def test_snapshot_updates_actual_channel_table_after_lifecycle(live_owner, monkeypatch, previous):
    monkeypatch.setattr(runtime, 'channel_table', list(previous))
    monkeypatch.setattr(runtime, 'channel_states', {'lidar': True})
    runtime._state.run_id = 'current'
    payload = live_owner.messages[0]['payload']
    payload.update(run_id='current', channel_table=[['lidar', 'lidar', 'EXPORTED', True, 'source']])

    runtime.tick()

    assert runtime._state.connection == 'CONNECTED'
    assert runtime.kinematics.snapshot['step'] == 99
    assert runtime.channel_table == [['lidar', 'lidar', 'EXPORTED', True, 'source']]
    assert runtime.channel_states == {'lidar': True}


@pytest.mark.parametrize('table', [None, []])
def test_snapshot_channel_table_absence_and_empty_have_distinct_meanings(live_owner, monkeypatch, table):
    monkeypatch.setattr(runtime, 'channel_table', [['lidar', 'lidar', 'EXPORTED', True, 'source']])
    if table is not None:
        live_owner.messages[0]['payload']['channel_table'] = table

    runtime.tick()

    assert runtime.channel_table == ([] if table == [] else [['lidar', 'lidar', 'EXPORTED', True, 'source']])


def test_old_run_snapshot_cannot_replace_actual_channel_table(live_owner, monkeypatch):
    monkeypatch.setattr(runtime, 'channel_table', [['lidar', 'lidar', 'EXPORTED', True, 'source']])
    runtime._state.run_id = 'current'
    live_owner.messages[0]['payload'].update(run_id='old', channel_table=[])

    runtime.tick()

    assert runtime.channel_table == [['lidar', 'lidar', 'EXPORTED', True, 'source']]
    assert runtime.kinematics.snapshot is None


@pytest.mark.parametrize('table', [[['lidar']], 'lidar', {'lidar': True},
    [['lidar', 'lidar', 'EXPORTED', 'false', 'source']],
    [['lidar', 'lidar', 'EXPORTED', True, 'source'], ['bad']]])
def test_invalid_snapshot_channel_table_disconnects_without_publishing_partial_rows(live_owner, monkeypatch, table):
    from wfrl.blender_bridge.messages import validate_message
    original = [['lidar', 'lidar', 'EXPORTED', False, 'source']]
    monkeypatch.setattr(runtime, 'channel_table', list(original))
    message = live_owner.messages[0]
    message.update(protocol_version=1, session_id='s')
    message['payload']['channel_table'] = table
    validate_message(message)

    runtime.tick()

    assert runtime._state.connection == 'DISCONNECTED'
    assert 'channel table' in runtime._state.error.lower()
    assert runtime.channel_table == original


def test_null_snapshot_channel_table_clears_rows(live_owner, monkeypatch):
    monkeypatch.setattr(runtime, 'channel_table', [['lidar', 'lidar', 'EXPORTED', True, 'source']])
    live_owner.messages[0]['payload']['channel_table'] = None
    runtime.tick()
    assert runtime.channel_table == []


class WakeObject(dict):
    hide_render = False
    hidden = False

    def hide_get(self):
        return self.hidden

    def hide_set(self, hidden):
        self.hidden = hidden


def existing_disxy(live_owner, monkeypatch):
    obj = WakeObject(validity='valid')
    live_owner.owner.objects['WFRL.WakeDisXY'] = obj
    data = channel({'kind': 'disxy', 'shape': [2, 2], 'values': [1, 2, 3, 4],
                    'x': [0, 1], 'y': [0, 1]})
    monkeypatch.setattr(runtime, 'latest_wake', validate_wake_payload(
        {'encoding': 'json', 'turbine_ids': ['T1'], 'data': data}, received_monotonic=0.))
    live_owner.messages.clear()
    return obj


def test_disxy_expires_without_a_new_message(live_owner, monkeypatch):
    obj = existing_disxy(live_owner, monkeypatch)
    clock = [0.]
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock[0])
    runtime.tick()
    assert obj['validity'] == 'valid' and not obj.hidden
    clock[0] = 5.
    runtime.tick()
    assert obj['validity'] == 'stale' and obj.hidden and obj.hide_render
    assert live_owner.owner['wfrl_wake_validity'] == 'stale'
    assert 'wfrl_wake_validity' not in live_owner.other


@pytest.mark.parametrize('validity', ['invalid', 'waiting', 'stale'])
def test_nonvalid_wake_retracts_old_disxy(live_owner, monkeypatch, validity):
    obj = existing_disxy(live_owner, monkeypatch)
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: 0.)
    data = channel(None)
    data.update(validity=validity, error='source unavailable')
    live_owner.messages.append(dict(type='wake', sequence=2, payload={
        'encoding': 'json', 'kind': 'disxy', 'turbine_ids': ['T1'], 'data': data}))
    runtime.tick()
    assert obj['validity'] == validity and obj.hidden and obj.hide_render


def test_disconnect_retracts_current_disxy(live_owner, monkeypatch):
    obj = existing_disxy(live_owner, monkeypatch)
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: 0.)
    live_owner.client.last_error = 'connection lost'
    runtime.tick()
    assert obj['validity'] == 'stale' and obj.hidden


def test_new_run_cannot_keep_previous_run_wake(live_owner, monkeypatch):
    obj = existing_disxy(live_owner, monkeypatch)
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: 0.)
    runtime._state.run_id = 'old'
    live_owner.messages.append(dict(type='lifecycle', sequence=2, session_id='s', payload={
        'run_id': 'new', 'run_status': 'PAUSED', 'mode': 'interactive_training',
        'reason': None, 'capabilities': []}))
    runtime.tick()
    assert runtime.latest_wake is None
    assert obj['validity'] == 'waiting' and obj.hidden


def test_delayed_old_run_wake_cannot_replace_current_data(live_owner, monkeypatch):
    obj = existing_disxy(live_owner, monkeypatch)
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: 0.)
    runtime._state.run_id = 'current'
    current = runtime.latest_wake
    data = channel(None)
    data.update(validity='invalid', error='old run source unavailable')
    live_owner.messages.append(dict(type='wake', sequence=2, payload={
        'encoding': 'json', 'kind': 'disxy', 'run_id': 'old',
        'turbine_ids': ['T1'], 'data': data}))
    runtime.tick()
    assert runtime.latest_wake is current
    assert obj['validity'] == 'valid' and not obj.hidden


@pytest.mark.parametrize('hidden', [False, True])
def test_fresh_disxy_restores_user_visibility_after_expiry(live_owner, hidden):
    from wfrl_blender.wake import set_disxy_validity
    obj = WakeObject(validity='valid')
    obj.hidden = obj.hide_render = hidden
    live_owner.owner.objects['WFRL.WakeDisXY'] = obj
    set_disxy_validity(live_owner.owner, 'stale')
    assert obj.hidden and obj.hide_render
    set_disxy_validity(live_owner.owner, 'valid')
    assert obj.hidden == obj.hide_render == hidden
    live_owner.owner.wfrl_show_disxy = False
    set_disxy_validity(live_owner.owner, 'valid')
    assert obj.hidden and obj.hide_render
