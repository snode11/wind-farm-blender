import json
import time

import pytest

from wfrl.blender_bridge.training_progress import TrainingProgressWriter, TrainingProgressReader, resume_counts


def test_producer_reader_numbers_and_age(tmp_path):
    path = tmp_path / 'run.jsonl'
    writer = TrainingProgressWriter(path, 'run')
    writer.progress('warmup', 1)
    writer.step, writer.agent_step = 4, 12
    writer.progress('updating')
    writer.iteration_stats(1, 6.25, -0.125, 2.5, .75)
    writer.progress('done'); writer.close()
    events, errors = [], []
    reader = TrainingProgressReader(path, 'run', events.append, errors.append)
    time.sleep(.01); reader.finish()
    assert not errors
    assert [p['phase'] for p in events] == ['warmup', 'updating', 'updating', 'done']
    stats = events[2]['stats']
    assert stats['mean_power']['value'] == 6.25
    assert stats['mean_reward']['value'] == -.125
    assert stats['value_loss']['value'] == 2.5
    assert stats['explained_variance']['value'] == .75
    assert stats['episode_return']['validity'] == 'unsupported'
    assert stats['learning_rate']['value'] is None
    assert events[2]['step'] == 4 and events[2]['agent_step'] == 12
    assert stats['mean_power']['source_age_seconds'] >= .01


def test_partial_truncation_run_and_order(tmp_path):
    path = tmp_path / 'run.jsonl'
    writer = TrainingProgressWriter(path, 'run'); writer.progress('sampling', 2); writer.close()
    original = path.read_bytes()
    for broken in (original[:-1], original.replace(b'"run"', b'"other"'),
                   original + original.replace(b'"iteration": 2', b'"iteration": 1'),
                   b'x' * 101, b'{broken}\n'):
        path.write_bytes(broken)
        events, errors = [], []
        reader = TrainingProgressReader(path, 'run', events.append, errors.append, max_line_bytes=1000 if len(broken) != 101 else 100)
        reader.finish(); assert len(errors) == 1
    path.write_bytes(original)
    reader = TrainingProgressReader(path, 'run', lambda _: None, errors.append)
    reader.read_available(); path.write_bytes(b''); reader.read_available()
    assert 'truncated' in errors[-1]
    path.write_bytes(original[:-3]); events, errors = [], []
    reader = TrainingProgressReader(path, 'run', events.append, errors.append)
    reader.read_available(); assert not events and not errors
    with path.open('ab') as stream: stream.write(original[-3:])
    reader.finish(); assert len(events) == 1 and not errors


def test_nonfinite_and_no_optimizer_are_null(tmp_path):
    writer = TrainingProgressWriter(tmp_path / 'run', 'run')
    writer.iteration_stats(1, float('nan'), float('inf'), None, -.4); writer.close()
    stats = json.loads((tmp_path / 'run').read_text())['stats']
    assert stats['mean_power']['value'] is None
    assert stats['mean_reward']['validity'] == 'invalid'
    assert stats['value_loss']['error'] == 'No optimization minibatches'


def test_exact_resume_counts():
    checkpoint = dict(iteration=3, obs_dim=3, state_dim=9,
                      history=dict(reward=[1,2,3], cfg=dict(n_steps=4)))
    assert resume_counts(checkpoint) == (12, 36)
    checkpoint['training_progress'] = dict(step=20, agent_step=60)
    assert resume_counts(checkpoint) == (20, 60)
    checkpoint.pop('training_progress'); checkpoint['history']['cfg'] = {}
    with pytest.raises(ValueError, match='ambiguous'): resume_counts(checkpoint)


@pytest.mark.parametrize("policy", ["zero", "learn"])
def test_actual_trainer_uses_buffers_without_physics(tmp_path, monkeypatch, policy):
    import numpy as np
    pytest.importorskip("torch")
    import scripts.train.train_fastfarm as trainer
    class Sampler:
        n = 2; obs_dim = 3; act_dim = 1; state_dim = 6
        act_low = -1.; act_high = 1.; episode = 0; u_inf = 8.; n_spawns = 1
        obs_keys = ['yaw', 'wind_speed', 'wind_direction']; obs_duty = False; duty_penalty = 0.
        def __init__(self, *args, progress=None, **kwargs):
            self.shaper = kwargs['reward_shaper']; self.progress = progress
            progress.progress('warmup'); progress.progress('sampling')
        def obs(self): return np.zeros((2,3), np.float32)
        def step(self, action): return self.obs(), 2., False, dict(power=[2.,4.])
        def close(self): pass
    monkeypatch.setattr(trainer, 'FastFarmSampler', Sampler)
    monkeypatch.setattr(trainer, 'CKPT_DIR', str(tmp_path))
    monkeypatch.setattr(trainer, '_dump_hist', lambda *args: None)
    monkeypatch.setattr(trainer, '_plot_curves', lambda *args: None)
    path = tmp_path / 'actual.jsonl'
    hist = trainer.train('fake', 1, 4, policy=policy, n_epochs=1, training_progress=path, run_id='run')
    records = [json.loads(line) for line in path.read_text().splitlines()]
    record = next(p for p in records if p['record_kind'] == 'iteration_stats')
    assert record['step'] == 4 and record['agent_step'] == 8
    assert record['stats']['mean_power']['value'] == hist['power_mw'][0] == 6.
    assert record['stats']['mean_reward']['value'] == hist['reward'][0]
    assert record['stats']['explained_variance']['value'] == hist['explained_var'][0]
    if policy == 'zero':
        assert record['stats']['value_loss']['value'] is None
    else:
        assert record['stats']['value_loss']['value'] == hist['vloss'][0]
    assert records[-1]['phase'] == 'done'
    bad = tmp_path / 'bad.pt'; trainer.torch.save({'iteration': 1}, bad)
    with pytest.raises(ValueError, match='ambiguous'):
        trainer.train('fake', 2, 4, training_progress=tmp_path/'bad.jsonl', resume_from=bad)


def test_backend_fake_formal_cli_drains_last_records(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from wfrl.blender_bridge.backend_session import BackendSession
    path = tmp_path / 'scene.yaml'; path.write_text('fake')
    scene = SimpleNamespace(backend='fastfarm', to_dict=lambda: {})
    session = BackendSession(scene=scene, scene_loader=lambda _: scene)
    code = '''import sys
from wfrl.blender_bridge.training_progress import TrainingProgressWriter
p=sys.argv[sys.argv.index('--training-progress')+1]
r=sys.argv[sys.argv.index('--run-id')+1]
w=TrainingProgressWriter(p,r)
w.progress('warmup',1)
w.step=4;w.agent_step=12
w.iteration_stats(1,6.,.5,2.,.75)
w.progress('done');w.close()
'''
    monkeypatch.setattr(session, '_formal_command', lambda *a: (str(__import__('pathlib').Path.cwd()), [sys.executable, '-c', code]))
    session.start('formal_training', dict(scene=str(path)))
    first_run = session.run_id
    deadline = time.monotonic()+5
    events=[]
    while session.status != 'STOPPED' and time.monotonic()<deadline:
        events.extend(session.poll());time.sleep(.01)
    assert session.status == 'STOPPED'
    assert [p['phase'] for k,p in events if k=='training_stats'] == ['warmup','updating','done']
    assert events[-1][0] == 'lifecycle' and events[-1][1]['run_status']=='STOPPED'
    assert session._progress_reader is None and session._progress_dir is None
    assert all(p['run_id']==first_run for k,p in events if k in ('training_stats','lifecycle'))
    session.start('formal_training', dict(scene=str(path)))
    assert session.run_id != first_run
    session._process.wait(timeout=5);session.poll()
