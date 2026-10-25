"""Compatibility checks for SB3 policies and their saved normalization statistics."""

import os

import numpy as np
from stable_baselines3.common.utils import check_for_correct_spaces


def require_checkpoint_files(model_path, normalization_path):
    for path in (str(model_path) + ".zip", normalization_path):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Required checkpoint file is missing: {path}")


def validate_normalization(venv, *, norm_keys=None):
    """Require finite moments for precisely the channels normalized by the policy."""
    observation_keys = set(venv.observation_space.spaces)
    keys = observation_keys if norm_keys is None else set(norm_keys)
    if (not keys <= observation_keys or not venv.norm_obs
            or set(venv.norm_obs_keys or ()) != keys):
        raise ValueError("Checkpoint normalization keys do not match the policy channels")
    if not isinstance(venv.obs_rms, dict) or set(venv.obs_rms) != keys:
        raise ValueError("Checkpoint has incompatible observation statistics")
    statistics = [(key, rms, venv.observation_space[key].shape)
                  for key, rms in venv.obs_rms.items()]
    statistics.append(("reward", venv.ret_rms, ()))
    for key, rms, shape in statistics:
        if (np.shape(rms.mean) != shape or np.shape(rms.var) != shape
                or not np.isfinite(rms.mean).all() or not np.isfinite(rms.var).all()
                or np.any(rms.var < 0) or np.shape(rms.count) != ()
                or not np.isfinite(rms.count) or rms.count <= 0):
            raise ValueError(f"Checkpoint has invalid {key} normalization statistics")


def validate_model_normalization(model, venv, *, norm_keys=None):
    """Check spaces, moments, discount and optional model/statistics pair identity."""
    check_for_correct_spaces(venv, model.observation_space, model.action_space)
    validate_normalization(venv, norm_keys=norm_keys)
    if not np.isclose(model.gamma, venv.gamma, rtol=0, atol=1e-12):
        raise ValueError("Model and normalization discount factors do not match")
    if model.__dict__.get("_wfrl_checkpoint_id") != venv.__dict__.get("_wfrl_checkpoint_id"):
        raise ValueError("Model and normalization checkpoint identities do not match")
