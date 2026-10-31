"""Formal sampler ownership and effective backend configuration without solvers."""
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

from scripts.train import train_fastfarm as train
from wfrl.fastfarm_driver import FastFarmDriver
from wfrl.safety import SafetyLimiter
from wfrl.scene.runtime import SceneRuntime
from wfrl.scene.schema import Scene, SceneError, Turbine, load_scene


def measure():
    return dict(yaw=np.zeros(1), wind_speed=np.ones(1) * 8,
                wind_direction=np.ones(1) * 270, reward=0., done=False)


def test_driver_factory_cleans_partial_initialization(monkeypatch):
    calls = []
    def initialize(driver, *args, **kwargs):
        driver.env = SimpleNamespace(close=lambda: calls.append('environment closed'))
        raise ValueError('driver initialization failed')
    monkeypatch.setattr(FastFarmDriver, '__init__', initialize)
    with pytest.raises(ValueError, match='driver initialization failed'):
        train.FastFarmDriver('fake')
    assert calls == ['environment closed']


def test_driver_factory_keeps_unconfirmed_partial_driver_on_error(monkeypatch):
    def initialize(driver, *args, **kwargs):
        driver.env = SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError('close unavailable')))
        raise ValueError('driver initialization failed')
    monkeypatch.setattr(FastFarmDriver, '__init__', initialize)
    with pytest.raises(RuntimeError, match='close unavailable') as result:
        train.FastFarmDriver('fake')
    assert result.value.driver.env is not None
    assert isinstance(result.value.__cause__, ValueError)


@pytest.mark.parametrize('scene_mode', [False, True])
@pytest.mark.parametrize('failure', ['reset', 'warmup', 'pack'])
def test_failed_sampler_start_closes_acquired_driver(monkeypatch, scene_mode, failure):
    closed = []
    driver = SimpleNamespace(n=1, act_low=-5., act_high=5., u_inf=8.)
    def reset(**kwargs):
        if failure == 'reset':
            raise RuntimeError('reset failure')
        return measure()
    def step(*args):
        if failure == 'warmup':
            raise RuntimeError('warmup failure')
        return measure()
    def close(**kwargs):
        closed.append(kwargs)
    driver.reset, driver.step, driver.close = reset, step, close
    monkeypatch.setattr(train, 'FastFarmDriver', lambda *a, **kw: driver)
    if failure == 'pack':
        monkeypatch.setattr(train.FastFarmSampler, '_pack', lambda *a: (_ for _ in ()).throw(RuntimeError('pack failure')))
    if scene_mode:
        monkeypatch.setattr('wfrl.scene.schema.load_scene', lambda _: Scene(turbines=[Turbine('T1', 0., 0.)]))
        def build(rt):
            rt.driver = driver
        monkeypatch.setattr(SceneRuntime, '_build_driver', build)
    with pytest.raises(RuntimeError, match=failure):
        train.FastFarmSampler('fake', 1, warmup_steps=1, scene_path='scene' if scene_mode else None)
    assert closed == [dict(purge=True, strict=True)]


@pytest.mark.parametrize('scene_mode', [False, True])
def test_close_failure_retains_owner_and_blocks_next_episode(monkeypatch, scene_mode):
    driver = SimpleNamespace(n=1, act_low=-5., act_high=5., u_inf=8., reset=lambda **kw: measure())
    calls = []
    failed = [True]
    def close(**kw):
        calls.append(kw)
        if failed[0]:
            raise RuntimeError('unconfirmed close')
    driver.close = close
    monkeypatch.setattr(train, 'FastFarmDriver', lambda *a, **kw: driver)
    sampler = train.FastFarmSampler('fake', 1, warmup_steps=0)
    if scene_mode:
        sampler._rt = SceneRuntime(Scene(turbines=[Turbine('T1', 0., 0.)]))
        sampler._rt.driver = driver
    monkeypatch.setattr(sampler, '_spawn', lambda: pytest.fail('must not spawn'))
    with pytest.raises(RuntimeError, match='unconfirmed close'):
        sampler.new_episode()
    assert sampler.drv is driver
    if scene_mode:
        assert sampler._rt.driver is driver
    failed[0] = False
    sampler.close()
    assert sampler.drv is None and sampler._rt is None
    sampler.close()
    assert len(calls) == 2


def test_failed_initial_cleanup_exposes_sampler_for_ownership(monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError('unconfirmed close')
    driver = SimpleNamespace(n=1, reset=lambda **kw: (_ for _ in ()).throw(ValueError('reset failed')), close=fail)
    monkeypatch.setattr(train, 'FastFarmDriver', lambda *a, **kw: driver)
    with pytest.raises(RuntimeError) as result:
        train.FastFarmSampler('fake', 1, warmup_steps=0)
    assert result.value.sampler.drv is driver
    assert isinstance(result.value.__cause__, ValueError)


@pytest.mark.parametrize('backend', ['fastfarm', 'floris'])
@pytest.mark.parametrize('dt', [.5, 1.5, 3.9])
def test_fractional_dt_is_rejected_before_case_runtime_or_limiter(backend, dt):
    scene = Scene(backend=backend, dt=dt, turbines=[Turbine('T1', 0., 0.)])
    with pytest.raises(SceneError, match='整数秒'):
        scene.validate()
    with pytest.raises(SceneError, match='整数秒'):
        scene.make_case()
    with pytest.raises(SceneError, match='整数秒'):
        SceneRuntime(scene)


def test_all_preset_case_clocks_match_runtime_and_limiter():
    paths = list(Path('scenes').glob('*.yaml'))
    assert len(paths) >= 6
    for path in paths:
        scene = load_scene(path)
        assert scene.make_case().dt == scene.dt == SafetyLimiter(scene).dt


@pytest.mark.parametrize('rpm,torque,requested_delta', [(np.nan, 0., 0.), (12., 0., 100.),
    (12., 0., 1000.), (0., 40000., -50.), (12., 42000., -500.)])
def test_torque_state_events_and_driver_submission_agree(rpm, torque, requested_delta):
    scene = Scene(turbines=[Turbine('T1', 0., 0.)], controls=['torque'])
    limiter = SafetyLimiter(scene)
    submitted = []
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.n, driver._idx, driver.controls = 1, {'a': 0}, ['torque']
    driver.done, driver.yaw_duty_sliding, driver._steps, driver.max_steps = False, False, 0, 100
    driver.act_bounds = {'torque': (-1000., 1000.)}
    driver.env = SimpleNamespace(agent_selection='a', last=lambda: (None, 0, False, False, {}),
        step=lambda cmd: submitted.append(float(cmd['torque'][0])), rewards={})
    driver.agents = ['a']
    driver._read = lambda **kw: {}
    for _ in range(2):
        before = limiter.state['torque'].copy()
        command, events = limiter.apply({'torque': [requested_delta]}, {'torque': [torque], 'rotor_speed': [rpm]})
        driver.step(torque_delta=command['torque'])
        assert abs(submitted[-1]) <= 1000
        assert limiter.state['torque'][0] - before[0] == submitted[-1] == command['torque'][0]
        envelope = [e for e in events if '运行' in e.rule]
        if envelope:
            assert envelope[-1].applied == submitted[-1]
            if torque == 0.:
                assert '尚未进入包线' in envelope[-1].detail


@pytest.mark.parametrize('direction', [240., 270.])
def test_floris_direction_reaches_schedule_and_experiment_identity(direction):
    config = train.experiment_config('Dec_Turb3_Row1_Floris', 1, 1, wind_speed=8., wind_direction=direction)
    assert config['wind_direction'] == direction
    assert train.training_wind_schedule(config)(0) == (8., direction)
    other = train.experiment_config('Dec_Turb3_Row1_Floris', 1, 1, wind_speed=8., wind_direction=direction + 1)
    assert config != other


def test_backend_specific_preset_and_fastfarm_direction_limit():
    floris = train.experiment_config('Dec_Turb3_Row1_Floris', 1, 1, wind_sched='preset:veer')
    assert train.training_wind_schedule(floris)(0) == (8., 240.)
    fastfarm = train.experiment_config('Dec_Turb3_Row1_Fastfarm', 1, 1, wind_speed=8., wind_direction=240.)
    assert fastfarm['wind_direction'] is None
    assert train.training_wind_schedule(fastfarm)(0) == (8., None)
