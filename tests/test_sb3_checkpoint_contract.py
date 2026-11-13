"""Checkpoint validation with temporary files and lightweight model state."""

from types import SimpleNamespace

import numpy as np
import pytest
from gymnasium import spaces

from wfrl import sb3_checkpoint


def _moments(shape):
    return SimpleNamespace(mean=np.zeros(shape), var=np.ones(shape), count=1.0)


@pytest.fixture
def venv():
    observation_space = spaces.Dict({
        "yaw": spaces.Box(-180.0, 180.0, (2,), dtype=np.float32),
        "wind": spaces.Box(0.0, 100.0, (1,), dtype=np.float32),
    })
    return SimpleNamespace(
        observation_space=observation_space,
        action_space=spaces.Box(-1.0, 1.0, (2,), dtype=np.float32),
        norm_obs=True,
        norm_obs_keys=["yaw", "wind"],
        obs_rms={key: _moments(space.shape) for key, space in observation_space.spaces.items()},
        ret_rms=_moments(()),
        gamma=0.99,
    )


@pytest.fixture
def model(venv):
    return SimpleNamespace(observation_space=venv.observation_space,
                           action_space=venv.action_space, gamma=venv.gamma)


@pytest.mark.parametrize("as_string", [False, True])
def test_checkpoint_files_require_presence_without_loading(tmp_path, as_string):
    model_path = tmp_path / "policy"
    normalization_path = tmp_path / "normalization.pkl"
    model_path.with_suffix(".zip").write_bytes(b"not a real model")
    normalization_path.write_bytes(b"not a pickle")
    if as_string:
        model_path, normalization_path = str(model_path), str(normalization_path)
    assert sb3_checkpoint.require_checkpoint_files(model_path, normalization_path) is None


@pytest.mark.parametrize("missing", ["model", "normalization"])
@pytest.mark.parametrize("is_directory", [False, True])
def test_checkpoint_files_reject_missing_files_and_directories(tmp_path, missing, is_directory):
    model_path = tmp_path / "policy"
    normalization_path = tmp_path / "normalization.pkl"
    paths = {"model": model_path.with_suffix(".zip"), "normalization": normalization_path}
    for name, path in paths.items():
        if name != missing:
            path.write_bytes(b"fixture")
        elif is_directory:
            path.mkdir()
    with pytest.raises(FileNotFoundError) as caught:
        sb3_checkpoint.require_checkpoint_files(model_path, normalization_path)
    assert str(caught.value) == f"Required checkpoint file is missing: {paths[missing]}"


@pytest.mark.parametrize("norm_keys", [None, ["wind", "yaw"], ["yaw"]])
def test_normalization_accepts_all_channels_or_explicit_subset(venv, norm_keys):
    if norm_keys == ["yaw"]:
        venv.norm_obs_keys = norm_keys
        venv.obs_rms = {"yaw": venv.obs_rms["yaw"]}
    assert sb3_checkpoint.validate_normalization(venv, norm_keys=norm_keys) is None


def test_normalization_accepts_zero_variance_and_positive_fractional_counts(venv):
    for rms in [*venv.obs_rms.values(), venv.ret_rms]:
        rms.var = np.zeros_like(rms.var)
        rms.count = 0.25
    assert sb3_checkpoint.validate_normalization(venv) is None


@pytest.mark.parametrize(("attribute", "value", "norm_keys"), [
    ("norm_obs", False, None),
    ("norm_obs_keys", None, None),
    ("norm_obs_keys", ["yaw"], None),
    ("norm_obs_keys", ["yaw", "wind", "unknown"], None),
    ("norm_obs_keys", ["yaw", "wind"], ["yaw"]),
    ("norm_obs_keys", ["unknown"], ["unknown"]),
])
def test_normalization_rejects_incompatible_channels(venv, attribute, value, norm_keys):
    setattr(venv, attribute, value)
    with pytest.raises(ValueError, match="normalization keys do not match"):
        sb3_checkpoint.validate_normalization(venv, norm_keys=norm_keys)


@pytest.mark.parametrize("case", ["not-dict", "empty", "missing", "extra"])
def test_normalization_rejects_incompatible_statistics_keys(venv, case):
    if case == "not-dict":
        venv.obs_rms = SimpleNamespace()
    elif case == "empty":
        venv.obs_rms = {}
    elif case == "missing":
        del venv.obs_rms["wind"]
    else:
        venv.obs_rms["unknown"] = _moments(())
    with pytest.raises(ValueError, match="incompatible observation statistics"):
        sb3_checkpoint.validate_normalization(venv)


@pytest.mark.parametrize("channel", ["yaw", "reward"])
@pytest.mark.parametrize(("field", "invalid"), [
    ("mean", "shape"), ("var", "shape"),
    ("mean", np.nan), ("mean", np.inf),
    ("var", np.nan), ("var", np.inf), ("var", -0.1),
    ("count", [1.0]), ("count", np.nan), ("count", np.inf),
    ("count", 0.0), ("count", -1.0),
])
def test_normalization_rejects_invalid_moments(venv, channel, field, invalid):
    rms = venv.ret_rms if channel == "reward" else venv.obs_rms[channel]
    if isinstance(invalid, str):
        value = np.zeros((3,))
    elif field == "count":
        value = invalid
    else:
        value = np.full(np.shape(getattr(rms, field)), invalid)
    setattr(rms, field, value)
    with pytest.raises(ValueError, match=f"invalid {channel} normalization statistics"):
        sb3_checkpoint.validate_normalization(venv)


@pytest.mark.parametrize("with_identity", [False, True])
def test_model_normalization_accepts_matching_spaces_discount_and_identity(model, venv, with_identity):
    if with_identity:
        model._wfrl_checkpoint_id = venv._wfrl_checkpoint_id = "checkpoint-a"
    model.gamma += 5e-13
    assert sb3_checkpoint.validate_model_normalization(model, venv) is None


def test_model_normalization_forwards_explicit_channel_subset(model, venv):
    venv.norm_obs_keys = ["yaw"]
    del venv.obs_rms["wind"]
    assert sb3_checkpoint.validate_model_normalization(model, venv, norm_keys=["yaw"]) is None


@pytest.mark.parametrize("space", ["observation_space", "action_space"])
def test_model_normalization_rejects_space_mismatch(model, venv, space):
    setattr(model, space, spaces.Box(-1.0, 1.0, (3,), dtype=np.float32))
    with pytest.raises(ValueError, match="spaces do not match"):
        sb3_checkpoint.validate_model_normalization(model, venv)


def test_model_normalization_rejects_invalid_moments(model, venv):
    venv.ret_rms.count = 0
    with pytest.raises(ValueError, match="invalid reward normalization statistics"):
        sb3_checkpoint.validate_model_normalization(model, venv)


@pytest.mark.parametrize("gamma", [0.95, 0.99 + 2e-12, np.nan, np.inf])
def test_model_normalization_rejects_discount_mismatch(model, venv, gamma):
    model.gamma = gamma
    with pytest.raises(ValueError, match="discount factors do not match"):
        sb3_checkpoint.validate_model_normalization(model, venv)


@pytest.mark.parametrize(("model_id", "venv_id"), [
    ("a", "b"), ("a", None), (None, "b"),
])
def test_model_normalization_rejects_unpaired_identity(model, venv, model_id, venv_id):
    if model_id is not None:
        model._wfrl_checkpoint_id = model_id
    if venv_id is not None:
        venv._wfrl_checkpoint_id = venv_id
    with pytest.raises(ValueError, match="checkpoint identities do not match"):
        sb3_checkpoint.validate_model_normalization(model, venv)
