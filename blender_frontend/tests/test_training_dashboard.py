from copy import deepcopy
import pytest
from wfrl_blender.training import TrainingDashboard
from wfrl_blender.protocol import ProtocolError


def message(kind='iteration_stats', sequence=1):
    units = {'mean_power': 'MW', 'mean_reward': '', 'value_loss': '',
             'explained_variance': '', 'episode_return': '', 'learning_rate': ''}
    stats = {key: dict(value=1.5, unit=unit, validity='valid', error=None, fidelity='EXPORTED',
                      provenance={'file': '/tmp/run.jsonl', 'channel': key},
                      source_age_seconds=3., stale_after_seconds=30.) for key, unit in units.items()}
    return dict(protocol_version=1, type='training_stats', session_id='session', sequence=sequence,
                payload=dict(run_id='run', record_kind=kind, mode='formal_training', step=8,
                             agent_step=24, iteration=1, phase='updating',
                             timestamp={'value': 100., 'timebase': 'unix_seconds'},
                             source_age_seconds=3., stale_after_seconds=30.,
                             stats=stats if kind == 'iteration_stats' else {}))


def test_progress_does_not_refresh_metric_clock():
    clock = [0.]
    dashboard = TrainingDashboard(clock=lambda: clock[0])
    dashboard.reset('run')
    dashboard.ingest(message(), negotiated=True)
    clock[0] = 28.
    progress = message('progress', 2)
    progress['payload']['source_age_seconds'] = 0.
    progress['payload']['phase'] = 'sampling'
    dashboard.ingest(progress, negotiated=True)
    assert dashboard.phase()['validity'] == 'valid'
    assert dashboard.phase()['phase'] == 'sampling'
    metric = dashboard.metric('mean_power')
    assert metric['validity'] == 'stale'
    assert metric['age'] == 31.
    assert metric['context']['phase'] == 'updating'


def test_run_isolation_negotiation_and_sequence():
    dashboard = TrainingDashboard()
    dashboard.reset('run')
    with pytest.raises(ProtocolError):
        dashboard.ingest(message())
    dashboard.ingest(message(), negotiated=True)
    with pytest.raises(ProtocolError):
        dashboard.ingest(message(), negotiated=True)
    dashboard.reset('second')
    assert dashboard.metric('mean_power')['validity'] == 'waiting'
    with pytest.raises(ProtocolError):
        dashboard.ingest(message(sequence=2), negotiated=True)
    assert dashboard.metric('value_loss', negotiated=False)['validity'] == 'unsupported'


def test_unsupported_remains_unsupported_and_copy_is_independent():
    dashboard = TrainingDashboard()
    dashboard.reset('run')
    msg = message()
    msg['payload']['stats']['episode_return'].update(value=None, validity='unsupported', error='Not emitted by trainer')
    dashboard.ingest(msg, negotiated=True)
    msg['payload']['stats']['mean_power']['value'] = 999
    assert dashboard.metric('mean_power')['value'] == 1.5
    assert dashboard.metric('episode_return')['validity'] == 'unsupported'


def test_unemitted_optional_statistic_is_not_waiting_forever():
    dashboard = TrainingDashboard()
    dashboard.reset('run')
    assert dashboard.metric('mean_raw_reward')['validity'] == 'waiting'
    dashboard.ingest(message(), negotiated=True)
    assert dashboard.metric('mean_raw_reward')['validity'] == 'unsupported'
    dashboard.ingest(message('progress', 2), negotiated=True)
    assert dashboard.metric('mean_raw_reward')['validity'] == 'unsupported'
