"""Native output cursors and the SB3 adapter preserve time and input ownership."""
import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pytest

from wfrl.sb3_wrapper import WFCRLSB3Wrapper
from wfrl.solver_outputs import OutputReader, SolverOutputs


def native_reader(tmp_path):
    path = tmp_path / "data.out"
    path.write_text("Time RotSpeed\n(s) (rpm)\n1 9\n2 12\n", encoding="utf-8")
    return OutputReader(path)


@pytest.mark.parametrize("target", [np.nan, np.inf, -np.inf, -1., True])
def test_invalid_reader_target_does_not_consume_output(tmp_path, target):
    reader = native_reader(tmp_path)
    with pytest.raises(ValueError, match="target"):
        reader.advance(target)
    assert reader.offset == 0 and reader.latest is None
    assert reader.advance(1.) == (1., ["1", "9"])


def test_reader_rejects_rewind_without_returning_a_future_sample(tmp_path):
    reader = native_reader(tmp_path)
    assert reader.advance(2.) == (2., ["2", "12"])
    with pytest.raises(ValueError, match="target"):
        reader.advance(1.)
    assert reader.advance(2.) == (2., ["2", "12"])
    assert reader.interval == []


def test_disappearing_native_output_is_invalid_after_the_file_was_read(tmp_path):
    reader = native_reader(tmp_path)
    reader.advance(1.)
    reader.path.unlink()
    assert reader.advance(2.) is None
    assert reader.error and "disappeared" in reader.error
    assert reader.latest is None and not reader.future and not reader.interval


@pytest.mark.parametrize("time,interval", [(np.nan, .25), (np.inf, .25), (-1., .25),
                                         (1., np.nan), (1., np.inf), (1., 0.), (1., -.25)])
def test_sampling_rejects_invalid_clock_before_reading(tmp_path, time, interval):
    outputs = SolverOutputs.__new__(SolverOutputs)
    outputs.definitions = [dict(comp_elast=1, gauge_nodes=[1, 9, 17],
                                output_interval_seconds=.25)]
    outputs.readers = [native_reader(tmp_path)]
    outputs.farm_path = tmp_path / "Case.fstf"
    with pytest.raises(ValueError, match="solver_time|coupling_interval"):
        outputs.sample(time, interval)
    assert outputs.readers[0].offset == 0


class MutatingYawFarm(gym.Env):
    def __init__(self):
        self.action_space = spaces.Dict({"yaw": spaces.Box(-5., 5., (2,), np.float32)})
        self.observation_space = spaces.Dict({"yaw": spaces.Box(-40., 40., (2,), np.float32)})
        self.received = None

    def reset(self, *, seed=None, options=None):
        return {"yaw": np.zeros(2, np.float32)}

    def step(self, actions):
        self.received = actions["yaw"].copy()
        actions["yaw"][0] = 0.
        return {"yaw": actions["yaw"].copy()}, np.array([2.5]), False, True, {}


@pytest.mark.parametrize("readonly", [False, True])
def test_sb3_step_keeps_policy_action_ownership(readonly):
    raw = MutatingYawFarm()
    env = WFCRLSB3Wrapper(raw)
    action = np.array([3., -2.], np.float32)
    action.setflags(write=not readonly)
    obs, reward, terminated, truncated, _ = env.step(action)
    np.testing.assert_array_equal(action, [3., -2.])
    np.testing.assert_array_equal(raw.received, [3., -2.])
    np.testing.assert_array_equal(obs["yaw"], [0., -2.])
    assert reward == 2.5 and not terminated and truncated
    assert env.observation_space is raw.observation_space
