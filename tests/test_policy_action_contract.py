"""Action metadata validation without a simulator or checkpoint mutation."""

import numpy as np
import pytest

from wfrl.policy_actions import PolicyActionContract, load_action_contract
from wfrl.scene.schema import Scene, Turbine


BOUNDS = {"yaw": (-5.0, 5.0), "pitch": (-1.0, 1.0), "torque": (-1000.0, 1000.0)}


def _scene(controls=("yaw",)):
    return Scene(name="policy-test", backend="fastfarm", dt=3.0,
                 controls=list(controls), sensors=[],
                 turbines=[Turbine("T1", 0, 0), Turbine("T2", 500, 0)])


def _checkpoint(controls=("yaw",), transform="tanh_scale"):
    obs_dim = len(controls) + 2
    return dict(
        controls=list(controls), act_dim=len(controls), obs_dim=obs_dim,
        obs_keys=list(controls) + ["wind_speed", "wind_direction"],
        obs_mean=np.zeros(obs_dim), obs_var=np.ones(obs_dim), state_dim=2 * obs_dim,
        action_contract=PolicyActionContract.from_bounds(controls, BOUNDS, transform).to_dict(),
    )


def _load(checkpoint, scene=None):
    scene = scene or _scene()
    return load_action_contract(checkpoint, controls=scene.controls,
                                n_agents=scene.n, scene=scene.to_dict())


@pytest.mark.parametrize("controls", [("yaw",), ("pitch",), ("torque",),
                                      ("torque", "yaw", "pitch")])
def test_tanh_contract_maps_fixed_outputs_to_each_named_channel(controls):
    contract = _load(_checkpoint(controls), _scene(controls))
    raw = np.broadcast_to([[0.5], [-0.5]], (2, len(controls))).astype(np.float32)
    commands = contract.commands(raw, 2)
    assert list(commands) == list(controls)
    for control in controls:
        np.testing.assert_allclose(commands[control],
                                   np.array([1, -1]) * np.tanh(0.5) * BOUNDS[control][1])
    np.testing.assert_array_equal(raw[:, 0], [0.5, -0.5])


def test_clip_contract_keeps_cli_raw_yaw_and_clips_to_asymmetric_bounds():
    contract = PolicyActionContract.from_bounds(["yaw"], {"yaw": (-2.0, 5.0)}, "clip")
    np.testing.assert_array_equal(
        contract.commands(np.array([[0.5], [-10.0], [10.0]]), 3)["yaw"],
        [0.5, -2.0, 5.0])
    assert PolicyActionContract.from_dict(contract.to_dict()) == contract


@pytest.mark.parametrize("updates, error", [
    ({"version": 2}, "version"), ({"version": True}, "version"),
    ({"transform": "sigmoid"}, "transform"),
    ({"controls": []}, "controls"), ({"controls": ["yaw", "yaw"]}, "controls"),
    ({"controls": ["speed"]}, "controls"),
    ({"low": [float("nan")]}, "finite"), ({"high": [float("inf")]}, "finite"),
    ({"low": [True]}, "finite"), ({"high": ["5"]}, "finite"),
    ({"high": []}, "finite"), ({"high": [[5.0]]}, "finite"),
    ({"low": [5.0]}, "low < high"), ({"low": [-2.0]}, "symmetric"),
    ({"extra": 0}, "exactly"),
])
def test_rejects_corrupt_action_contract(updates, error):
    value = _checkpoint()["action_contract"]
    value.update(updates)
    with pytest.raises(ValueError, match=error):
        PolicyActionContract.from_dict(value)


@pytest.mark.parametrize("field", ["version", "controls", "transform", "low", "high"])
def test_contract_requires_every_versioned_field(field):
    value = _checkpoint()["action_contract"]
    value.pop(field)
    with pytest.raises(ValueError, match="exactly"):
        PolicyActionContract.from_dict(value)


@pytest.mark.parametrize("updates, error", [
    ({"act_dim": 3}, "act_dim"), ({"act_dim": True}, "act_dim"),
    ({"obs_dim": 4}, "obs_dim"), ({"state_dim": 3}, "turbine count"),
    ({"obs_keys": ["wind_speed", "yaw", "wind_direction"]}, "obs_keys"),
    ({"obs_mean": [0.0, 0.0]}, "obs_mean"),
    ({"obs_var": [1.0, -1.0, 1.0]}, "nonnegative"),
    ({"obs_var": [1.0, np.nan, 1.0]}, "obs_var"),
    ({"controls": ["pitch"]}, "conflicts"),
    ({"policy_source": "train_fastfarm"}, "transform"),
    ({"policy_source": {}}, "policy_source"),
    ({"action_contract": None}, "exactly"),
    ({"obs_duty": "false"}, "obs_duty"),
])
def test_rejects_missing_or_conflicting_checkpoint_metadata(updates, error):
    ck = _checkpoint()
    ck.update(updates)
    with pytest.raises(ValueError, match=error):
        _load(ck)


@pytest.mark.parametrize("field", ["act_dim", "obs_dim", "state_dim", "obs_keys", "obs_mean", "obs_var"])
def test_required_checkpoint_metadata_cannot_be_missing(field):
    ck = _checkpoint()
    ck.pop(field)
    with pytest.raises(ValueError, match=field):
        _load(ck)


def test_legacy_studio_is_identified_by_scene_controls_and_writer_schema():
    scene = _scene(["pitch"])
    ck = _checkpoint(scene.controls)
    ck.pop("action_contract")
    ck.update(scene=scene.to_dict(), critic={})
    assert _load(ck, scene).transform == "tanh_scale"
    ck["scene"]["controls"] = ["yaw"]
    with pytest.raises(ValueError, match="scene.controls"):
        _load(ck, scene)


def test_legacy_cli_is_identified_by_env_and_duty_metadata_and_remains_clip():
    ck = _checkpoint()
    ck.pop("action_contract")
    ck.pop("controls")
    ck.update(env_id="Dec_Turb2_Row1_Fastfarm", obs_duty=False,
              recurrent=False, duty_penalty=0.0)
    contract = _load(ck)
    assert contract.transform == "clip"
    np.testing.assert_array_equal(contract.commands(np.full((2, 1), 0.5), 2)["yaw"], [0.5, 0.5])
    with pytest.raises(ValueError, match="env_id"):
        load_action_contract(ck, controls=["yaw"], env_id="different")
    for field in ("env_id", "obs_duty", "recurrent", "duty_penalty"):
        broken = dict(ck)
        broken.pop(field)
        with pytest.raises(ValueError, match="ambiguous"):
            _load(broken)


def test_dimensions_alone_never_identify_a_legacy_policy():
    for controls in (["yaw"], ["pitch"], ["yaw", "pitch", "torque"]):
        ck = _checkpoint(controls)
        ck.pop("action_contract")
        with pytest.raises(ValueError, match="ambiguous"):
            _load(ck, _scene(controls))


def test_conflicting_legacy_writer_signatures_and_missing_new_contract_fail():
    ck = _checkpoint()
    ck.pop("action_contract")
    ck.update(scene=_scene().to_dict(), critic={}, env_id="cli",
              obs_duty=False, recurrent=False, duty_penalty=0.0)
    with pytest.raises(ValueError, match="conflicting legacy"):
        _load(ck)
    ck["policy_source"] = "studio"
    with pytest.raises(ValueError, match="explicit action_contract"):
        _load(ck)


@pytest.mark.parametrize("field, value", [
    ("backend", "floris"), ("turbine", "other"), ("dt", 4.0),
    ("layout", [{"id": "T1", "x": 1.0, "y": 0.0}]),
])
def test_saved_scene_requires_compatible_dynamics_and_layout(field, value):
    ck = _checkpoint()
    ck["scene"] = _scene().to_dict()
    ck["scene"][field] = value
    with pytest.raises(ValueError, match=f"scene {field}"):
        _load(ck)


def test_saved_control_order_must_match_the_scene_exactly():
    ck = _checkpoint(["yaw", "pitch"])
    with pytest.raises(ValueError, match="controls/order"):
        _load(ck, _scene(["pitch", "yaw"]))


@pytest.mark.parametrize("bounds, error", [
    ({"yaw": (-4.0, 4.0)}, "incompatible"), ({}, "missing"),
    ({"yaw": (-np.inf, np.inf)}, "finite"),
])
def test_runtime_bound_changes_are_not_silently_rescaled(bounds, error):
    contract = _load(_checkpoint())
    with pytest.raises(ValueError, match=error):
        contract.validate_runtime(["yaw"], bounds)


@pytest.mark.parametrize("raw", [np.zeros(2), np.zeros((2, 2)), [[np.nan], [0.0]],
                                  [[np.inf], [0.0]], [[True], [False]]])
def test_commands_require_one_finite_vector_per_agent(raw):
    with pytest.raises(ValueError, match="finite \\(agents, controls\\)"):
        _load(_checkpoint()).commands(raw, 2)
