"""Driver lifecycle and measurement contracts with an in-memory solver peer."""
import collections
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from wfcrl import interface as backend
from wfrl.fastfarm_driver import FastFarmDriver


@pytest.fixture
def solver_driver(monkeypatch, tmp_path):
    class Peer:
        def __init__(self):
            self.active = True
            self.disconnects = 0

        def __bool__(self):
            return self.active

        def Disconnect(self):
            self.disconnects += 1
            self.active = False

    monkeypatch.setattr(backend, "MPI", NS(Intercomm=Peer))

    def make(max_steps=4, start_iter=0, n=3):
        itf = backend.FastFarmInterface.__new__(backend.FastFarmInterface)
        itf._comm = None
        itf._num_iter = 0
        itf.max_iter = start_iter + max_steps
        itf.wind_record = None
        itf.measure_map = {}
        itf.set_wind_request_context = Mock()
        itf.mark_wind_control_start = Mock()
        itf._save_wind_record = Mock()

        def exchange():
            assert itf._comm and itf._num_iter < itf.max_iter
            itf._num_iter += 1
            if itf._num_iter == itf.max_iter:
                itf._comm.Disconnect()
            return itf._num_iter == itf.max_iter

        itf.update_command = Mock(side_effect=exchange)
        names = [f"T{i}" for i in range(n)]

        class Env:
            def __init__(self):
                self.mdp = NS(interface=itf, ACTUATORS_RATE={})
                self.calls = []
                self.close = Mock()
                self.failure = None
                self.infos = {ag: {} for ag in names}

            def reset(self, options=None):
                itf._comm = Peer()
                itf.wind_record = {"loaded_inputs": {}}
                if self.failure:
                    raise self.failure
                for _ in range(start_iter + 1):
                    itf.update_command()
                self.agents = list(names)
                self.agent_selection = names[0]
                self.terminations = dict.fromkeys(names, False)
                self.truncations = dict.fromkeys(names, False)
                self.rewards = dict.fromkeys(names, 0.)

            def observe(self, ag):
                return {"yaw": [10.], "wind_speed": [8.], "wind_direction": [270.]}

            def last(self):
                ag = self.agent_selection
                return None, 0., self.terminations[ag], self.truncations[ag], self.infos[ag]

            def step(self, action):
                self.calls.append(action)
                ag = self.agent_selection
                if self.terminations[ag] or self.truncations[ag]:
                    assert action is None
                    self.agents.remove(ag)
                    self.infos.pop(ag)
                    return
                index = names.index(ag)
                if index == n - 1:
                    ended = itf.update_command()
                    self.truncations = dict.fromkeys(names, ended)
                    self.rewards = dict.fromkeys(names, 2.)
                    self.infos = {name: {"power": 1., "load": np.arange(6.)} for name in names}
                self.agent_selection = names[(index + 1) % n]

        driver = FastFarmDriver.__new__(FastFarmDriver)
        driver.env = driver._uenv = Env()
        driver.max_steps, driver.dt, driver.hub_height = max_steps, 3., 90.
        driver.n, driver.agents = n, names
        driver._idx = {ag: i for i, ag in enumerate(names)}
        driver.controls, driver.act_bounds = ["yaw"], {"yaw": (-5., 5.)}
        driver.wind_time_series = None
        driver._yaw_win = collections.deque()
        driver.yaw_duty_sliding = False
        driver._duty = lambda: {ag: {} for ag in names}
        driver._solver_clock = lambda: {"exchange_index": itf._num_iter}
        driver._freestream = lambda fallback: 8.
        driver._started = driver._solver_owned = driver.cleanup_confirmed = False
        driver.cleanup_error = None
        driver.done, driver._steps = False, 0
        driver.case_dir = str(tmp_path / "__simul__" / "case")
        case = tmp_path / "__simul__" / "case"
        case.mkdir(parents=True, exist_ok=True)
        return driver, itf, case

    return make


@pytest.mark.parametrize("fault", ["env_reset", "upstream_record", "control_start", "telemetry", "freestream", "read"])
def test_failed_reset_owns_solver_and_can_drain_without_completed_aec_reset(solver_driver, fault):
    driver, itf, case = solver_driver()
    error = OSError(f"{fault} write failure")
    options = {}
    if fault == "env_reset":
        driver.env.failure = error
    elif fault == "upstream_record":
        options["upstream_request"] = {"wind_speed": 8.}
        itf._save_wind_record.side_effect = error
    elif fault == "control_start":
        itf.mark_wind_control_start.side_effect = error
    elif fault == "telemetry":
        driver._solver_outputs = NS(start=Mock(side_effect=error))
    else:
        setattr(driver, "_freestream" if fault == "freestream" else "_read", Mock(side_effect=error))
    with pytest.raises(OSError) as failed:
        driver.reset(wind_speed=8., **options)
    assert failed.value is error
    assert driver._solver_owned and not driver._started
    assert driver.close(purge=True, strict=True) is True
    assert driver.cleanup_confirmed and driver.cleanup_error is None
    assert not driver._solver_owned and not itf._comm
    assert itf._num_iter == itf.max_iter and itf._comm.disconnects == 1
    assert not driver.env.calls and driver.env.close.call_count == 1
    assert not case.exists()


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("fault", ["drain", "cap", "close", "connected"])
def test_unconfirmed_cleanup_always_preserves_case_and_failure(solver_driver, strict, fault):
    driver, itf, case = solver_driver()
    driver.reset(wind_speed=8.)
    kwargs = {}
    if fault == "drain":
        itf.update_command.side_effect = RuntimeError("drain failed")
    elif fault == "cap":
        kwargs["drain_cap"] = 1
    elif fault == "close":
        driver.env.close.side_effect = RuntimeError("close failed")
    else:
        itf._comm.Disconnect = Mock()
    if strict:
        with pytest.raises(RuntimeError):
            driver.close(purge=True, strict=True, **kwargs)
    else:
        assert driver.close(purge=True, **kwargs) is False
    assert case.exists() and not driver.cleanup_confirmed
    assert isinstance(driver.cleanup_error, RuntimeError)
    driver.env.close.assert_called_once()
    calls = itf.update_command.call_count
    assert driver.close(purge=True) is False
    with pytest.raises(RuntimeError, match="previously failed"):
        driver.close(purge=True, strict=True)
    assert itf.update_command.call_count == calls
    driver.env.close.assert_called_once()
    assert case.exists()
    with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
        driver.reset(wind_speed=8.)


@pytest.mark.parametrize("strict", [False, True])
def test_interrupted_drain_attempts_safe_close_and_reraises_original(solver_driver, strict):
    driver, itf, case = solver_driver()
    driver.reset(wind_speed=8.)
    interrupted = KeyboardInterrupt("cancel drain")
    itf.update_command.side_effect = interrupted
    driver._safe_close = Mock(wraps=driver._safe_close)
    with pytest.raises(KeyboardInterrupt) as failed:
        driver.close(purge=True, strict=strict)
    assert failed.value is interrupted and driver.cleanup_error is interrupted
    driver._safe_close.assert_called_once_with(strict=True)
    driver.env.close.assert_called_once()
    assert not driver.cleanup_confirmed and case.exists()


@pytest.mark.parametrize("strict", [False, True])
def test_successful_close_is_confirmed_and_idempotent(solver_driver, strict):
    driver, itf, case = solver_driver()
    driver.reset(wind_speed=8.)
    assert driver.close(purge=True, strict=strict)
    assert driver.close(purge=True, strict=strict)
    assert driver.cleanup_confirmed and not case.exists()
    assert itf._num_iter == itf.max_iter and itf._comm.disconnects == 1
    driver.env.close.assert_called_once()


@pytest.mark.parametrize("start_iter", [0, 2])
@pytest.mark.parametrize("max_steps", [1, 2, 5])
def test_final_real_exchange_reports_done_without_a_dead_agent_round(solver_driver, max_steps, start_iter):
    driver, itf, _ = solver_driver(max_steps=max_steps, start_iter=start_iter)
    reset = driver.reset(wind_speed=8.)
    assert reset["step"] == 0 and itf._num_iter == start_iter + 1
    assert reset["done"] == (max_steps == 1)
    for k in range(1, max_steps):
        measurement = driver.step(np.ones(driver.n))
        assert measurement["step"] == k
        assert measurement["solver_clock"]["exchange_index"] == start_iter + 1 + k
        assert measurement["done"] == (k == max_steps - 1)
        assert np.isfinite(measurement["power"]).all()
        assert measurement["reward"] == 2.
    assert itf._num_iter == itf.max_iter and not itf._comm
    assert len(driver.env.calls) == driver.n * (max_steps - 1)
    again = driver.step(np.ones(driver.n))
    assert again["done"] and again["step"] == max_steps - 1
    assert len(driver.env.calls) == driver.n * (max_steps - 1)
    assert itf._num_iter == itf.max_iter
    assert all(action is not None for action in driver.env.calls)
    assert driver.close(strict=True)


@pytest.mark.parametrize("max_steps,start_iter", [(1, 0), (2, 0), (5, 2)])
def test_wfcrl_reset_and_aec_use_the_mpi_budget_including_initial_observation(
        solver_driver, max_steps, start_iter):
    from wfcrl.multiagent_env import MAWindFarmEnv

    driver, itf, _ = solver_driver(max_steps=max_steps, start_iter=start_iter)

    class Peer(backend.MPI.Intercomm):
        def Get_rank(self):
            return 0

        def Send(self, **kwargs):
            pass

        def Barrier(self):
            pass

        def Recv(self, buffer, **kwargs):
            if buffer.size == 1:
                buffer[:] = 10
            else:
                sample = np.zeros((driver.n, 10))
                sample[:, :2] = [8., 1e6]
                sample[:, 4:] = 1e7
                buffer[:] = sample.ravel()

    backend.MPI_Interface.__init__(itf, num_turbines=driver.n, comm=Peer(),
        max_iter=start_iter + max_steps, buffer_size=20,
        measure_map={"wind_speed": 0, "power": 1, "yaw": 2, "wind_direction": 3,
                     "load": list(range(4, 10)), "freewind_measurements": None})
    itf.init = lambda *args: backend.MPI_Interface.init(itf)
    itf.update_command = backend.MPI_Interface.update_command.__get__(itf)
    itf._sample_wind_conditions = Mock()
    itf._finalize_mpi_comm = backend.MPI_Interface._finalize_mpi_comm.__get__(itf)
    farm = NS(num_turbines=driver.n, dt=3., set_wind_speed=True,
              set_wind_direction=True, wind_time_series=None)
    with pytest.warns(UserWarning, match="Interface already instantiated"):
        env = MAWindFarmEnv(itf, farm, controls={"yaw": [-40., 40., 5.]},
                           start_iter=start_iter, max_num_steps=max_steps)
    driver.env = driver._uenv = env
    driver.agents = list(env.possible_agents)
    driver._idx = {ag: i for i, ag in enumerate(driver.agents)}
    driver._duty = lambda: {ag: {} for ag in driver.agents}
    measurement = driver.reset(wind_speed=8.)
    assert itf._num_iter == start_iter + 1
    for k in range(1, max_steps):
        measurement = driver.step()
        assert itf._num_iter == start_iter + 1 + k
        assert measurement["step"] == k
        assert measurement["done"] == (k == max_steps - 1)
        np.testing.assert_allclose(measurement["power"], np.ones(driver.n))
        np.testing.assert_allclose(measurement["load"], np.ones(driver.n))
    assert measurement["done"] and not itf._comm
    assert driver.step()["step"] == max_steps - 1
    assert env.agents == env.possible_agents
    assert driver.close(strict=True)


@pytest.mark.parametrize("flag", ["terminations", "truncations"])
def test_aec_termination_before_budget_is_seen_on_the_same_physical_round(solver_driver, flag):
    driver, itf, _ = solver_driver()
    driver.reset(wind_speed=8.)
    original_step = driver.env.step

    def step(action):
        original_step(action)
        if len(driver.env.calls) == driver.n:
            getattr(driver.env, flag)[driver.agents[-1]] = True

    driver.env.step = step
    result = driver.step()
    assert result["done"] and result["step"] == 1
    assert itf._num_iter == 2 and np.isfinite(result["power"]).all()
    driver.step()
    assert len(driver.env.calls) == driver.n and itf._num_iter == 2
    assert driver.close(strict=True)
    assert itf._num_iter == itf.max_iter


@pytest.mark.parametrize("load", [None, [], [np.nan], [1., np.inf], ["bad"], np.ma.array([1.], mask=True)])
def test_invalid_load_is_nan_and_marked_invalid(solver_driver, load):
    driver, _, _ = solver_driver(n=1)
    driver.env.infos = {"T0": {"load": load}}
    result = driver._read(0.)
    assert np.isnan(result["load"]).all()
    assert result["load_valid"].tolist() == [False]


def test_missing_measurements_and_true_zero_have_distinct_validity(solver_driver):
    driver, _, _ = solver_driver(n=2)
    driver.env.observe = lambda ag: {} if ag == "T0" else {"yaw": [0.]}
    driver.env.infos = {"T0": {}, "T1": {"load": np.zeros(6)}}
    result = driver._read(0.)
    assert np.isnan(result["load"][0]) and result["load"][1] == 0.
    assert np.isnan(result["yaw"][0]) and result["yaw"][1] == 0.
    assert result["load_valid"].tolist() == [False, True]
    assert result["yaw_valid"].tolist() == [False, True]
    assert result["yaw_command_valid"].tolist() == [False, True]
    assert result["yaw_measured_valid"].tolist() == [False, False]


@pytest.mark.parametrize("controlled", [False, True])
def test_native_yaw_units_and_command_measurement_roles(solver_driver, controlled):
    driver, itf, _ = solver_driver(n=2)
    driver.controls = ["yaw"] if controlled else []
    yaw = [25., np.nan] if controlled else [np.radians(7.), np.nan]
    driver.env.observe = lambda ag: {"yaw": [yaw[driver._idx[ag]]]}
    itf.measure_map = {"yaw": 0}
    itf.current_measures = np.array([[np.radians(7.)], [np.nan]])
    result = driver._read(0.)
    np.testing.assert_allclose(result["yaw"], [25. if controlled else 7., np.nan], equal_nan=True)
    np.testing.assert_allclose(result["yaw_measured"], [7., np.nan], equal_nan=True)
    assert result["yaw_valid"].tolist() == result["yaw_measured_valid"].tolist() == [True, False]
    if controlled:
        np.testing.assert_allclose(result["yaw_command"], [25., np.nan], equal_nan=True)
        assert result["yaw_command"] is not result["yaw"]
    else:
        assert result["yaw_command"] is None
        assert not result["yaw_command_valid"].any()


def test_floris_uncontrolled_yaw_already_uses_degrees(solver_driver):
    driver, _, _ = solver_driver(n=1)
    driver.env.mdp.interface = NS(measure_map={"yaw": 0}, get_measure=lambda name: [17.])
    driver.controls = []
    driver.env.observe = lambda ag: {"yaw": [17.]}
    result = driver._read(0.)
    np.testing.assert_allclose(result["yaw"], [17.])
    np.testing.assert_allclose(result["yaw_measured"], [17.])


def test_missing_load_and_yaw_remain_invalid_through_snapshot_conversion(solver_driver):
    from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
    from wfrl.scene.schema import Scene, Turbine

    driver, _, _ = solver_driver(n=1)
    driver.env.observe = lambda ag: {}
    driver._solver_clock = lambda: None
    measurement = driver._read(0.)
    scene = Scene(turbines=[Turbine("T0", 0., 0.)])
    snapshot = NS(measure=measurement, ctx={}, step=0, farm_power=np.nan, reward=0.)
    channels = SnapshotAdapter(scene, "fixture").encode(snapshot)["turbines"][0]["channels"]
    for key in ("load", "yaw", "yaw_measured", "yaw_command"):
        assert channels[key]["validity"] == "invalid" and channels[key]["value"] is None
