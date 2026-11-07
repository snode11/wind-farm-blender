"""Checkpoint contracts for Gaussian policy outputs and control increments."""

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Integral, Real

import numpy as np


# Bounds used by the supported unversioned Studio and train_fastfarm writers.
_LEGACY_BOUNDS = {"yaw": (-5.0, 5.0), "pitch": (-1.0, 1.0),
                  "torque": (-1000.0, 1000.0)}
_CLI_FIELDS = {"env_id", "obs_duty", "recurrent", "duty_penalty"}


def _controls(value, label="controls"):
    if (not isinstance(value, (list, tuple)) or not value
            or any(not isinstance(c, str) or c not in _LEGACY_BOUNDS for c in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} must be an ordered, nonempty list of unique yaw/pitch/torque controls")
    return tuple(value)


def _integer(value, label):
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return int(value)


def _vector(value, size, label):
    try:
        array = np.asarray(value, dtype=object)
        valid = (array.shape == (size,) and not np.ma.isMaskedArray(value)
                 and all(isinstance(v, Real) and not isinstance(v, (bool, np.bool_))
                         for v in array))
        if not valid:
            raise ValueError
        array = np.asarray(value, dtype=np.float64)
        if not np.isfinite(array).all():
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must contain {size} finite numbers") from exc
    return array


@dataclass(frozen=True)
class PolicyActionContract:
    """Version 1 maps each agent's raw Gaussian vector to named increments.

    ``tanh_scale`` is tanh(raw) * high and requires symmetric bounds.
    ``clip`` clips the raw vector to low/high. PPO retains the raw vector
    and its Gaussian log probability; this mapping only feeds the actuator path.
    """

    controls: tuple
    transform: str
    low: tuple
    high: tuple
    version: int = 1

    def __post_init__(self):
        if type(self.version) is not int or self.version != 1:
            raise ValueError(f"unsupported action_contract version: {self.version!r}")
        controls = _controls(self.controls, "action_contract.controls")
        if self.transform not in ("clip", "tanh_scale"):
            raise ValueError(f"unsupported action_contract transform: {self.transform!r}")
        lo = _vector(self.low, len(controls), "action_contract.low")
        hi = _vector(self.high, len(controls), "action_contract.high")
        if np.any(lo >= hi):
            raise ValueError("action_contract requires low < high for every control")
        if self.transform == "tanh_scale" and not np.array_equal(lo, -hi):
            raise ValueError("tanh_scale requires symmetric action bounds")
        object.__setattr__(self, "controls", controls)
        object.__setattr__(self, "low", tuple(lo))
        object.__setattr__(self, "high", tuple(hi))

    @classmethod
    def from_bounds(cls, controls, bounds, transform):
        controls = _controls(controls)
        if not isinstance(bounds, Mapping) or any(c not in bounds for c in controls):
            raise ValueError("action bounds are missing a required control")
        pairs = [_vector(bounds[c], 2, f"action bounds for {c}") for c in controls]
        return cls(controls, transform, tuple(p[0] for p in pairs),
                   tuple(p[1] for p in pairs))

    @classmethod
    def from_dict(cls, value):
        fields = {"version", "controls", "transform", "low", "high"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError(f"action_contract must contain exactly {sorted(fields)}")
        return cls(**value)

    def to_dict(self):
        return {"version": self.version, "controls": list(self.controls),
                "transform": self.transform, "low": list(self.low),
                "high": list(self.high)}

    def validate_runtime(self, controls, bounds):
        if _controls(controls) != self.controls:
            raise ValueError("checkpoint controls/order are incompatible with the scene")
        runtime = self.from_bounds(controls, bounds, self.transform)
        if runtime.low != self.low or runtime.high != self.high:
            raise ValueError("checkpoint action bounds are incompatible with the runtime")

    def commands(self, raw, n_agents):
        """Return one finite (agents,) increment vector for each saved control."""
        n_agents = _integer(n_agents, "n_agents")
        raw = np.asarray(raw)
        if (raw.shape != (n_agents, len(self.controls))
                or raw.dtype.kind not in "fiu" or not np.isfinite(raw).all()):
            raise ValueError("policy action must be a finite (agents, controls) numeric array")
        dtype = np.result_type(raw.dtype, np.float32)
        raw = raw.astype(dtype, copy=False)
        lo, hi = np.asarray(self.low, dtype=dtype), np.asarray(self.high, dtype=dtype)
        values = np.tanh(raw) * hi if self.transform == "tanh_scale" else np.clip(raw, lo, hi)
        if not np.isfinite(values).all():
            raise ValueError("action_contract produced a non-finite control increment")
        return {c: values[:, j] for j, c in enumerate(self.controls)}


def _validate_scene(saved, current, controls):
    if not isinstance(saved, Mapping):
        raise ValueError("checkpoint scene must be a mapping")
    if _controls(saved.get("controls"), "checkpoint scene.controls") != controls:
        raise ValueError("checkpoint scene.controls conflicts with action_contract.controls")
    if current is None:
        raise ValueError("checkpoint requires its compatible scene")
    for key in ("backend", "turbine", "dt", "layout", "controls"):
        if key not in saved or key not in current or saved[key] != current[key]:
            raise ValueError(f"checkpoint scene {key} is incompatible with the current scene")


def load_action_contract(checkpoint, *, controls, n_agents=None, scene=None, env_id=None):
    """Validate metadata before inference; ambiguous unversioned models fail closed.

    Supported unversioned writers are identified by their complete Studio
    scene/controls schema or CLI env/duty/critic schema, never by action count.
    Their bounds are the fixed default-control values in this module. CLI
    checkpoints without a saved scene can validate counts, controls and bounds,
    but cannot establish layout, turbine or timestep compatibility.
    """
    if not isinstance(checkpoint, Mapping):
        raise ValueError("checkpoint must be a mapping")
    ck = checkpoint
    if "action_contract" in ck:
        contract = PolicyActionContract.from_dict(ck["action_contract"])
    else:
        if "policy_source" in ck:
            raise ValueError("policy_source requires an explicit action_contract")
        studio = {"controls", "scene", "critic"} <= ck.keys()
        cli = _CLI_FIELDS <= ck.keys()
        if studio and not (_CLI_FIELDS & ck.keys()):
            saved_controls, transform = _controls(ck["controls"]), "tanh_scale"
        elif cli and not ({"controls", "scene"} & ck.keys()):
            saved_controls, transform = ("yaw",), "clip"
        else:
            raise ValueError("ambiguous or conflicting legacy action metadata; a verified action_contract is required")
        contract = PolicyActionContract.from_bounds(saved_controls, _LEGACY_BOUNDS, transform)

    if "policy_source" in ck:
        expected = {"studio": "tanh_scale", "train_fastfarm": "clip"}
        source = ck["policy_source"]
        if (not isinstance(source, str) or source not in expected
                or expected[source] != contract.transform):
            raise ValueError("policy_source conflicts with action_contract.transform")
        if source == "studio" and ("scene" not in ck or _CLI_FIELDS & ck.keys()):
            raise ValueError("Studio policy_source requires scene metadata without CLI fields")
        if source == "train_fastfarm" and not _CLI_FIELDS <= ck.keys():
            raise ValueError("train_fastfarm policy_source requires complete CLI metadata")
    if _controls(controls) != contract.controls:
        raise ValueError("checkpoint controls/order are incompatible with the scene")
    if "controls" in ck and _controls(ck["controls"]) != contract.controls:
        raise ValueError("checkpoint controls conflicts with action_contract.controls")
    if _integer(ck.get("act_dim"), "checkpoint act_dim") != len(contract.controls):
        raise ValueError("checkpoint act_dim conflicts with action_contract.controls")

    obs_keys = list(contract.controls) + ["wind_speed", "wind_direction"]
    if ck.get("obs_keys") != obs_keys:
        raise ValueError("checkpoint obs_keys conflicts with control/observation order")
    obs_duty = ck.get("obs_duty", False)
    if type(obs_duty) is not bool or (obs_duty and "yaw" not in contract.controls):
        raise ValueError("checkpoint obs_duty must be boolean and requires yaw")
    obs_dim = _integer(ck.get("obs_dim"), "checkpoint obs_dim")
    if obs_dim != len(obs_keys) + int(obs_duty):
        raise ValueError("checkpoint obs_dim conflicts with obs_keys/obs_duty")
    _vector(ck.get("obs_mean"), obs_dim, "checkpoint obs_mean")
    variance = _vector(ck.get("obs_var"), obs_dim, "checkpoint obs_var")
    if np.any(variance < 0):
        raise ValueError("checkpoint obs_var must be nonnegative")
    state_dim = _integer(ck.get("state_dim"), "checkpoint state_dim")
    if state_dim % obs_dim or (n_agents is not None and state_dim != _integer(n_agents, "n_agents") * obs_dim):
        raise ValueError("checkpoint state_dim is incompatible with the scene turbine count")

    if _CLI_FIELDS & ck.keys():
        if not _CLI_FIELDS <= ck.keys():
            raise ValueError("checkpoint CLI metadata is incomplete")
        if contract.controls != ("yaw",):
            raise ValueError("train_fastfarm CLI metadata requires yaw-only controls")
        if (not isinstance(ck["env_id"], str) or not ck["env_id"]
                or type(ck["recurrent"]) is not bool):
            raise ValueError("checkpoint CLI env_id/recurrent metadata is invalid")
        penalty = _vector([ck["duty_penalty"]], 1, "checkpoint duty_penalty")[0]
        if penalty < 0:
            raise ValueError("checkpoint duty_penalty must be nonnegative")
        if env_id is not None and ck["env_id"] != env_id:
            raise ValueError("checkpoint env_id is incompatible with the requested environment")
    if "scene" in ck:
        _validate_scene(ck["scene"], scene, contract.controls)
    return contract
