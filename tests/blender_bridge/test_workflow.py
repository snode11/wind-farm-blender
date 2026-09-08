from types import SimpleNamespace
import pytest
from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.workflow import configured_scene, validate_run_options
from wfrl.scene.schema import load_scene


def test_overrides_validate_without_mutating_source():
    source = load_scene('scenes/turb3_demo.yaml')
    changed = configured_scene(source, {'inflow': {'speed': 12}, 'terrain': 'gobi'})
    assert source.wind_speed == 8
    assert changed.wind_speed == 12 and changed.terrain == 'gobi'
    with pytest.raises(ValueError): configured_scene(source, {'inflow': {'speed': 90}})
    with pytest.raises(ValueError): configured_scene(source, {'layout': []})


def test_replay_requires_existing_checkpoint_before_worker_creation(tmp_path):
    with pytest.raises(ValueError, match='ckpt_path'): validate_run_options('replay', {})
    with pytest.raises(ValueError, match='does not exist'):
        validate_run_options('replay', {'ckpt_path': str(tmp_path / 'missing')})
    with pytest.raises(ValueError, match='positive integer'):
        validate_run_options('interactive_training', {'n_steps': 0})


def test_scene_load_errors_are_atomic_and_active_session_locked():
    source = load_scene('scenes/turb3_demo.yaml')
    session = BackendSession(scene=source)
    with pytest.raises(ValueError):
        session.command({'command': 'scene.load', 'arguments': {'scene': source.source_path,
            'scene_overrides': {'inflow': {'speed': 100}}}})
    assert session.scene is source
    session.status = 'RUNNING'
    with pytest.raises(ValueError, match='active'):
        session.command({'command': 'scene.load', 'arguments': {'scene': source.source_path}})


def test_channel_subscriptions_require_real_topic_and_boolean():
    session = BackendSession()
    request = {'command': 'channel.set', 'arguments': {'topic': 'lidar', 'enabled': False}}
    with pytest.raises(ValueError): session.command(request)
    session._trainer = SimpleNamespace(runtime=SimpleNamespace(topics=lambda: ['lidar']))
    session.command(request)
    assert session._channel_states == {'lidar': False}
    request['arguments']['enabled'] = 'false'
    with pytest.raises(ValueError): session.command(request)


def test_channel_table_uses_protocol_fidelity_names():
    session = BackendSession()
    session._trainer = SimpleNamespace(runtime=SimpleNamespace(channel_table=lambda: [
        ('direct', 'DirectSensor', '直读', True, 'simulator'),
        ('derived', 'DerivedSensor', '导出', True, 'formula'),
        ('synth', 'SynthSensor', '合成', False, 'fixture'),
        ('canonical', 'CanonicalSensor', 'DIRECT', True, 'simulator'),
    ]))
    assert session.channel_table() == [
        ['direct', 'DirectSensor', 'DIRECT', True, 'simulator'],
        ['derived', 'DerivedSensor', 'EXPORTED', True, 'formula'],
        ['synth', 'SynthSensor', 'SYNTH', False, 'fixture'],
        ['canonical', 'CanonicalSensor', 'DIRECT', True, 'simulator'],
    ]
def test_pitch_command_and_measured_are_distinct():
    from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
    source = load_scene('scenes/turb3_ctrl3.yaml')
    snap = SimpleNamespace(step=1, ctx={}, measure={'pitch': [3, 4, 5],
        'pitch_meas': [1, 2, 3]}, farm_power=0, reward=0)
    channels = SnapshotAdapter(source, 'test').encode(snap)['turbines'][0]['channels']
    assert channels['pitch_command']['value'] == 3
    assert channels['pitch_measured']['value'] == channels['pitch']['value'] == 1


def test_rotor_fidelity_follows_actual_source_and_missing_reward_is_explicit():
    from types import SimpleNamespace
    from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
    source = SimpleNamespace(turbines=[SimpleNamespace(id='T1')], backend='floris', dt=1., controls=[])
    snap = SimpleNamespace(step=1, iters_done=2, phase='sampling', ctx={},
                           measure={'rotor_speed': [8.]}, farm_power=1., reward=.2)
    payload = SnapshotAdapter(source, 's').encode(snap)
    channels = payload['turbines'][0]['channels']
    assert channels['rotor_speed']['fidelity'] == 'DIRECT'
    assert channels['reward']['validity'] == 'unsupported'
    assert channels['reward']['value'] is None
    source.backend = 'fastfarm'
    channels = SnapshotAdapter(source, 's').encode(snap)['turbines'][0]['channels']
    assert channels['rotor_speed']['fidelity'] == 'SYNTH'
    assert 'FastFarmDriver' in channels['rotor_speed']['provenance']['formula']
    snap.ctx['channel_sources'] = {'rotor_speed': {'fidelity': 'EXPORTED',
        'provenance': {'file': 'actual-turbine.out', 'channel': 'RotSpeed'}}}
    channels = SnapshotAdapter(source, 's').encode(snap)['turbines'][0]['channels']
    assert channels['rotor_speed']['fidelity'] == 'EXPORTED'
    assert channels['rotor_speed']['provenance']['channel'] == 'RotSpeed'
    assert payload['iteration'] == 2 and payload['phase'] == 'sampling'
