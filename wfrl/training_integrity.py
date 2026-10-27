"""Content-addressed training experiments and validated software recovery records."""
from copy import deepcopy
import hashlib
import importlib.metadata
import importlib.util
import pickle
import json
from pathlib import Path
import platform
import random
import re
import uuid

import numpy as np
import torch

SCHEMA = 1
HISTORY_FIELDS = ("reward", "raw_reward", "power_mw", "power_norm", "u_inf",
                  "explained_var", "pg", "vloss", "episodes")


def canonical(value):
    """Normalize JSON-compatible values; nonfinite configuration is invalid."""
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def experiment_identity(config, source_identity):
    payload = json.dumps({"config": canonical(config), "source": source_identity},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_input_identity(root, scene=None):
    """Hash executable Python, simulator/template inputs, configuration, and versions."""
    root = Path(root)
    spec = importlib.util.find_spec("wfcrl")
    package = (Path(spec.origin).resolve().parent if spec is not None and spec.origin
               else root / "wfcrl-env" / "wfcrl")
    files = {}
    for folder in (root / "wfrl", root / "scripts" / "train"):
        for path in folder.rglob("*.py"):
            files["repository/" + path.relative_to(root).as_posix()] = path
    for label, folder in (("wfcrl", package), ("inputs", root / "inputs"),
                          ("simulators", root / "simulators"),
                          ("configs", root / "configs")):
        if folder.exists():
            for path in folder.rglob("*"):
                if path.is_file() and not any(part in {"__pycache__", ".git", "__simul__"}
                                              for part in path.parts):
                    files[label + "/" + path.relative_to(folder).as_posix()] = path
    if scene is not None:
        import yaml
        files["scene"] = Path(scene)
        document = yaml.safe_load(Path(scene).read_text(encoding="utf-8")) or {}
        turbulence = document.get("inflow", {}).get("turbulence")
        if turbulence:
            wind_path = Path(turbulence)
            if not wind_path.is_absolute():
                wind_path = package / "simulators" / "fastfarm" / "inputs" / "template" / "FarmInputs" / wind_path
            if wind_path.is_file():
                files["scene-wind-input"] = wind_path
    digest = hashlib.sha256()
    for label, path in sorted(files.items()):
        digest.update(label.encode() + b"\0")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        digest.update(b"\0")
    versions = {"python": platform.python_version(), "platform": platform.platform()}
    for name in ("numpy", "torch", "wfcrl", "floris", "gymnasium", "pettingzoo", "mpi4py"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    digest.update(json.dumps(versions, sort_keys=True).encode())
    return digest.hexdigest()


def new_run(config, source_identity, label, parent=None, progress_run_id=None):
    record = {"schema": SCHEMA, "run_id": uuid.uuid4().hex,
              "experiment_id": experiment_identity(config, source_identity),
              "source_identity": source_identity, "label": label,
              "status": "running", "control_steps": 0, "agent_steps": 0,
              "recovery_mode": "new_episode_segment" if parent else "fresh",
              "parent_run_id": parent.get("run", {}).get("run_id") if parent else None,
              "parent_experiment_id": parent.get("run", {}).get("experiment_id") if parent else None}
    if progress_run_id is not None:
        record["progress_run_id"] = progress_run_id
    return record


def artifact_tag(run):
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", run["label"]).strip(".-")[:120] or "training"
    return f"{label}_{run['experiment_id'][:16]}_{run['run_id']}"


def save_checkpoint_exclusive(payload, path):
    """A run never replaces an existing artifact, including a partial artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        torch.save(payload, stream)


def capture_rng():
    return {"python": random.getstate(), "numpy": np.random.get_state(),
            "torch": torch.get_rng_state()}


def restore_rng(state):
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"])


def shaper_state(shaper):
    return {"class": type(shaper).__module__ + "." + type(shaper).__qualname__,
            "state": deepcopy(vars(shaper))}


def restore_shaper(shaper, state, *, validate=True):
    if not isinstance(state, dict) or state.get("class") != type(shaper).__module__ + "." + type(shaper).__qualname__:
        raise ValueError("resume reward shaper class is incompatible")
    values = state.get("state")
    expected = vars(shaper)
    if not isinstance(values, dict) or values.keys() != expected.keys():
        raise ValueError("resume reward shaper state structure is invalid")
    if validate:
        for key, value in values.items():
            if key in {"raw", "shaped"}:
                array = np.asarray(value)
                if (not isinstance(value, list) or array.dtype.kind not in "fiu" or
                        array.ndim != 1 or not np.isfinite(array).all()):
                    raise ValueError("resume reward shaper records must be finite scalar lists")
            elif value is None:
                if key != "ema" or values.get("raw"):
                    raise ValueError("resume reward shaper scalar state cannot be missing")
            else:
                try:
                    scalar = np.asarray(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("resume reward shaper scalar state is invalid") from exc
                if scalar.dtype.kind not in "fiu" or scalar.shape != () or not np.isfinite(scalar):
                    raise ValueError("resume reward shaper scalar state must be finite")
            if key not in {"raw", "shaped", "ema"}:
                dynamic_reference = key == "reference" and "raw" not in expected
                if not dynamic_reference and value != expected[key]:
                    raise ValueError("resume reward shaper parameters disagree with configuration")
        if "raw" in values and len(values["raw"]) != len(values["shaped"]):
            raise ValueError("resume reward shaper record lengths disagree")
    vars(shaper).clear()
    vars(shaper).update(deepcopy(values))


def restore_normalizer(normalizer, checkpoint, prefix, shape):
    """Validate and copy one running mean/variance/count before backend startup."""
    try:
        values = {key: np.asarray(checkpoint[f"{prefix}_{key}"])
                  for key in ("mean", "var", "count")}
        if any(value.dtype.kind not in "fiu" for value in values.values()):
            raise ValueError("normalizer values must be numeric")
        if (values["mean"].shape != shape or values["var"].shape != shape or
                values["count"].shape != () or
                any(not np.isfinite(value).all() for value in values.values()) or
                (values["var"] < 0).any() or values["count"] <= 0):
            raise ValueError("normalizer shape, variance, or count is invalid")
        normalizer.mean = values["mean"].astype(np.float64, copy=True)
        normalizer.var = values["var"].astype(np.float64, copy=True)
        normalizer.count = float(values["count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"resume {prefix} normalization state is invalid") from exc


def restore_optimizer(optimizer, payload, *, learning):
    """Validate Adam groups and moment buffers, then load the optimizer state."""
    try:
        template = optimizer.state_dict()
        groups, states = payload["param_groups"], payload["state"]
        if not isinstance(groups, list) or not isinstance(states, dict) or len(groups) != len(template["param_groups"]):
            raise ValueError("invalid optimizer structure")
        parameters = {}
        for group, expected, live in zip(groups, template["param_groups"], optimizer.param_groups):
            if (not isinstance(group, dict) or group.keys() != expected.keys() or
                    not isinstance(group["params"], list) or group["params"] != expected["params"] or
                    any(group[key] != expected[key] for key in expected if key != "params")):
                raise ValueError("optimizer groups disagree with configuration")
            for identifier, parameter in zip(group["params"], live["params"]):
                if identifier in parameters:
                    raise ValueError("duplicate optimizer parameter")
                parameters[identifier] = parameter
        if not set(states) <= parameters.keys() or (learning and set(states) != parameters.keys()):
            raise ValueError("optimizer state does not belong to model parameters")
        for identifier, state in states.items():
            required = {"step", "exp_avg", "exp_avg_sq"}
            if any(group.get("amsgrad") for group in groups):
                required.add("max_exp_avg_sq")
            if not isinstance(state, dict) or state.keys() != required:
                raise ValueError("invalid optimizer parameter state")
            for key, tensor in state.items():
                if not isinstance(tensor, torch.Tensor) or not tensor.is_floating_point() or not torch.isfinite(tensor).all():
                    raise ValueError("optimizer state must contain finite floating tensors")
                if key == "step":
                    if tensor.shape != () or tensor.item() <= 0 or tensor.item() != int(tensor.item()):
                        raise ValueError("optimizer step is invalid")
                elif tensor.shape != parameters[identifier].shape:
                    raise ValueError("optimizer moment shape does not match parameter")
                if key in {"exp_avg_sq", "max_exp_avg_sq"} and (tensor < 0).any():
                    raise ValueError("optimizer squared moments must be nonnegative")
        optimizer.load_state_dict(payload)
        for state in optimizer.state.values():
            if any(not torch.isfinite(value).all() for value in state.values() if isinstance(value, torch.Tensor)):
                raise ValueError("optimizer state overflow after loading")
    except (KeyError, TypeError, ValueError, RuntimeError, OverflowError) as exc:
        raise ValueError("resume optimizer state is invalid or incompatible") from exc


def validate_resume(checkpoint, config, source_identity):
    """Resume is a new physical episode; only the target iteration budget may grow."""
    recovery = checkpoint.get("recovery")
    if not isinstance(recovery, dict) or recovery.get("schema") != SCHEMA:
        raise ValueError("resume requires validated software recovery metadata")
    required = {"actor", "critic", "optimizer", "obs_mean", "obs_var", "obs_count",
                "ret_mean", "ret_var", "ret_count", "iteration", "history", "run", "cfg"}
    if not required <= checkpoint.keys():
        raise ValueError("resume requires a full training checkpoint")
    previous = canonical(checkpoint["cfg"])
    target = canonical(config)
    old_budget, new_budget = previous.pop("iters", None), target.pop("iters", None)
    if (previous != target or not isinstance(old_budget, int) or new_budget < old_budget):
        raise ValueError("resume effective configuration is incompatible; only iters may increase")
    run = checkpoint["run"]
    if (run.get("schema") != SCHEMA or run.get("status") not in {"running", "completed"} or
            run.get("source_identity") != source_identity or
            run.get("experiment_id") != experiment_identity(checkpoint["cfg"], source_identity)):
        raise ValueError("resume source/input identity or experiment configuration is incompatible")
    iteration = checkpoint["iteration"]
    if not isinstance(iteration, int) or not 0 < iteration < config["iters"]:
        raise ValueError("resume requires remaining training iterations")
    if checkpoint["history"].get("cfg") != checkpoint["cfg"]:
        raise ValueError("resume history configuration does not belong to checkpoint")
    if checkpoint["history"].get("run") != run:
        raise ValueError("resume history does not belong to checkpoint run")
    if any(not isinstance(checkpoint["history"].get(key), list) or
           len(checkpoint["history"][key]) != iteration for key in HISTORY_FIELDS):
        raise ValueError("resume history does not match completed iteration budget")
    steps = iteration * config["n_steps"]
    if (not isinstance(checkpoint.get("obs_dim"), int) or checkpoint["obs_dim"] <= 0 or
            not isinstance(checkpoint.get("state_dim"), int) or checkpoint["state_dim"] <= 0 or
            checkpoint["state_dim"] % checkpoint["obs_dim"]):
        raise ValueError("resume observation/state dimensions are invalid")
    if (recovery.get("control_steps") != steps or run.get("control_steps") != steps or
            recovery.get("agent_steps") != steps * checkpoint.get("state_dim", 0) // checkpoint.get("obs_dim", 1) or
            run.get("agent_steps") != recovery.get("agent_steps") or
            not isinstance(recovery.get("next_episode"), int) or recovery["next_episode"] < 0 or
            recovery.get("mode") != "new_episode_segment"):
        raise ValueError("resume recovery counters or episode boundary are invalid")
    segment_first = run.get("segment_start_iteration", 1)
    discarded = recovery.get("discarded_episode_steps")
    episodes = checkpoint["history"]["episodes"][segment_first - 1:]
    if (not isinstance(discarded, int) or discarded < 0 or
            any(not isinstance(count, int) or count < 0 for count in episodes) or
            recovery["next_episode"] != run.get("start_episode", 0) + sum(episodes) + bool(discarded)):
        raise ValueError("resume recovery episode index does not match segment history")
    if not {"rng", "reward_shaper"} <= recovery.keys():
        raise ValueError("resume recovery requires RNG and reward shaper state")
    # Validate all recoverable state before the physics backend can be constructed.
    saved = capture_rng()
    try:
        restore_rng(recovery["rng"])
    except Exception as exc:
        raise ValueError("resume recovery RNG state is invalid") from exc
    finally:
        restore_rng(saved)
    return deepcopy(recovery)


def find_completed_run(runs_dir, config, source_identity):
    """Return a belonging completed history/checkpoint pair with the exact budget."""
    identity = experiment_identity(config, source_identity)
    for path in sorted(Path(runs_dir).glob("*_hist.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("hist"), dict):
                continue
            hist = data["hist"]
            run = hist.get("run")
            if not isinstance(run, dict):
                continue
            if (hist.get("cfg") != config or run.get("schema") != SCHEMA or
                    run.get("experiment_id") != identity or run.get("status") != "completed" or
                    run.get("recovery_mode") != "fresh" or
                    run.get("source_identity") != source_identity or
                    run.get("control_steps") != config["iters"] * config["n_steps"] or
                    any(not isinstance(hist.get(key), list) or len(hist[key]) != config["iters"]
                        for key in HISTORY_FIELDS)):
                continue
            checkpoint = Path(run["checkpoint"])
            if (not checkpoint.is_file() or checkpoint.stat().st_size == 0 or
                    data.get("checkpoint_sha256") != file_digest(checkpoint)):
                continue
            ckpt = torch.load(checkpoint, map_location="cpu", weights_only=False)
            if not isinstance(ckpt, dict):
                continue
            if (ckpt.get("cfg") != config or ckpt.get("run") != run or
                    json.dumps(ckpt.get("history"), sort_keys=True) != json.dumps(hist, sort_keys=True) or
                    ckpt.get("iteration") != config["iters"] or
                    not {"actor", "critic", "optimizer"} <= ckpt.keys()):
                continue
            recovery = ckpt.get("recovery", {})
            if not isinstance(recovery, dict):
                continue
            if (recovery.get("control_steps") != run["control_steps"] or
                    recovery.get("agent_steps") != run["agent_steps"] or
                    run["agent_steps"] != run["control_steps"] * ckpt["state_dim"] // ckpt["obs_dim"]):
                continue
            return path
        except (OSError, ValueError, KeyError, TypeError, EOFError, RuntimeError, ZeroDivisionError, pickle.UnpicklingError):
            continue
    return None
