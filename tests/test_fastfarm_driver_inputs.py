"""Driver input validation must finish before an AEC agent receives a command."""
import collections
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from wfrl.fastfarm_driver import FastFarmDriver


def action_driver():
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.n = 3
    driver.controls = ["yaw", "pitch", "torque"]
    driver.act_bounds = {"yaw": (-5., 5.), "pitch": (-1., 1.), "torque": (-1000., 1000.)}
    driver.agents = ["T1", "T2", "T3"]
    driver._idx = {name: i for i, name in enumerate(driver.agents)}
    driver.done, driver._steps, driver.max_steps = False, 0, 10
    driver.yaw_duty_sliding = False
    driver.env = NS(agent_selection="T1", last=Mock(return_value=(None, 0., False, False, {})),
                    step=Mock(), rewards={"T1": 0.})
    driver._read = lambda reward: {"reward": reward}
    return driver


@pytest.mark.parametrize("action", [[], [1., 2.], [1., 2., 3., 4.], [[1., 2., 3.]],
                                        [0., np.nan, 1.], [0., np.inf, 1.], True, object()])
def test_invalid_action_rejected_before_any_agent_step(action):
    driver = action_driver()
    with pytest.raises(ValueError, match="pitch"):
        driver.step(yaw_delta=1., pitch_delta=action)
    driver.env.last.assert_not_called()
    driver.env.step.assert_not_called()
    assert driver._steps == 0


@pytest.mark.parametrize("bounds", [None, (2., -2.), (-np.inf, 5.), (0., np.nan), (1.,)])
def test_invalid_action_bounds_raise_value_error(bounds):
    driver = action_driver()
    driver.act_bounds["pitch"] = bounds
    with pytest.raises(ValueError, match="pitch"):
        driver.step(pitch_delta=1.)
    driver.env.step.assert_not_called()
    with pytest.raises(ValueError, match="unknown"):
        driver._clip("unknown", 1.)


def test_each_control_uses_its_own_bounds_and_scalar_or_column_broadcast():
    driver = action_driver()
    commands = []

    def receive(command):
        commands.append(command)
        driver.env.agent_selection = driver.agents[len(commands) % driver.n]

    driver.env.step.side_effect = receive
    driver.step([-9., 1., 9.], 2., [[-2000.], [0.], [2000.]])
    for key, expected in {"yaw": [-5., 1., 5.], "pitch": [1., 1., 1.],
                          "torque": [-1000., 0., 1000.]}.items():
        np.testing.assert_array_equal([command[key].item() for command in commands], expected)
        assert all(command[key].dtype == np.float32 for command in commands)
    assert driver._steps == 1


@pytest.mark.parametrize("options", [
    {"wind_speed": True}, {"wind_speed": "8"}, {"wind_speed": 0.}, {"wind_speed": -1.},
    {"wind_speed": np.nan}, {"wind_speed": np.inf}, {"wind_speed": [8.]},
    {"wind_direction": np.bool_(True)}, {"wind_direction": "270"},
    {"wind_direction": np.nan}, {"wind_direction": -np.inf}, {"wind_direction": {}},
])
def test_floris_reset_rejects_invalid_wind_before_environment(options):
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.env = NS(mdp=NS(interface=NS()), reset=Mock())
    with pytest.raises(ValueError, match="wind_"):
        driver.reset(**options)
    driver.env.reset.assert_not_called()


def test_floris_reset_keeps_arbitrary_finite_direction_and_resets_control_state():
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.env = NS(mdp=NS(interface=NS()), reset=Mock())
    driver._yaw_win = collections.deque([[1.]])
    driver._freestream = lambda fallback: fallback
    driver._read = lambda reward: {"reward": reward}
    driver.done, driver._steps = True, 4
    assert driver.reset(wind_speed=8., wind_direction=240.) == {"reward": 0.}
    driver.env.reset.assert_called_once_with(options={"wind_speed": 8., "wind_direction": 240.})
    assert not driver._yaw_win
    assert driver._steps == 0 and not driver.done and driver._started
    assert driver.u_inf == 8.
