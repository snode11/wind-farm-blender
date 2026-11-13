"""
Real PPO training on WFCRL's centralized wind-farm env (FLORIS backend).

Env : Ablaincourt_Floris  — 7 turbines, yaw control, steady-state FLORIS wake solver
Algo: PPO (Stable-Baselines3), MultiInputPolicy for the Dict observation
      + VecNormalize on observations AND returns.

Why VecNormalize: the raw Dict obs channels live on very different scales
(wind_direction ~289, wind_speed ~4-8, yaw ~0-40) and the reward is un-normalized.
Without normalization the value net can't fit (explained_variance ~ 0) and PPO's
advantages are noise. VecNormalize fixes both.

Usage:
    python scripts/train/train_ppo.py                       # train from scratch
    python scripts/train/train_ppo.py --timesteps 200000    # longer run
    python scripts/train/train_ppo.py --resume              # continue training
    python scripts/train/train_ppo.py --eval-only           # evaluate saved model

Monitoring:
    tensorboard --logdir results/logs/ppo_wf
    (episode reward mean = ep_rew_mean; watch train/explained_variance climb > 0)
"""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import pickle
from uuid import uuid4

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.utils import check_for_correct_spaces
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

from wfcrl import environments as envs
from wfrl.sb3_wrapper import WFCRLSB3Wrapper
from wfrl.sb3_checkpoint import validate_model_normalization
from wfrl.training_integrity import (atomic_write, capture_rng, restore_rng, file_digest,
                                     read_checkpoint_manifest, validate_checkpoint_location)

from wfrl import paths

ENV_ID = "Ablaincourt_Floris"
LOG_DIR = os.path.join(paths.LOGS, "ppo_wf")
CKPT_DIR = paths.CKPT
MODEL_PATH = os.path.join(CKPT_DIR, "ppo_ablaincourt")
VECNORM_PATH = os.path.join(CKPT_DIR, "vecnormalize.pkl")
MAX_STEPS = 200  # steps per episode (FLORIS is fast, so we can afford long episodes)


class CheckpointPair:
    """Path-specific SB3 generations using the mappo_sb3 manifest contract."""

    def __init__(self, model_path, normalization_path, *, norm_keys=None):
        self.model_path = Path(model_path)
        self.normalization_path = Path(normalization_path)
        self.manifest = Path(str(model_path) + ".manifest.json")
        self.norm_keys = norm_keys

    def _generation_paths(self, generation):
        norm = self.normalization_path
        return (Path(f"{self.model_path}.{generation}.zip"),
                norm.with_name(f"{norm.stem}.{generation}{norm.suffix}"))

    def resolve(self):
        """Pin one validated generation; use legacy paths only without a manifest."""
        document = read_checkpoint_manifest(self.manifest)
        if document is None:
            return Path(str(self.model_path) + ".zip"), self.normalization_path
        generation = document.get("generation") if isinstance(document, dict) else None
        if (not isinstance(generation, str) or len(generation) != 32
                or any(c not in "0123456789abcdef" for c in generation)
                or document.get("schema") != 1):
            raise ValueError("Invalid checkpoint manifest")
        pair = self._generation_paths(generation)
        for key, filename in zip(("model", "normalization"), pair):
            if not filename.is_file():
                raise FileNotFoundError(f"Required checkpoint file is missing: {filename}")
            validate_checkpoint_location(filename)
            if (document.get(key) != filename.name
                    or document.get(key + "_sha256") != file_digest(filename)):
                raise ValueError(f"Checkpoint manifest does not match {key}")
        return pair

    def require(self):
        pair = self.resolve()
        for filename in pair:
            if not filename.is_file():
                raise FileNotFoundError(f"Required checkpoint file is missing: {filename}")
        return pair

    def save(self, model, venv):
        """Publish the manifest only after both immutable artifacts pass validation."""
        generation = uuid4().hex
        model_path, norm_path = self._generation_paths(generation)
        old_ids = [obj.__dict__.get("_wfrl_checkpoint_id") for obj in (model, venv)]
        rng = capture_rng()
        committed = False
        try:
            model._wfrl_checkpoint_id = venv._wfrl_checkpoint_id = generation
            restored = {}

            def validate_model(path):
                restored["model"] = type(model).load(path, device="cpu")
                if restored["model"].__dict__.get("_wfrl_checkpoint_id") != generation:
                    raise ValueError("Model has an invalid checkpoint identity")

            def validate_norm(path):
                with path.open("rb") as stream:
                    normalization = pickle.load(stream)
                validate_model_normalization(restored["model"], normalization,
                                             norm_keys=self.norm_keys)

            atomic_write(model_path, model.save, validate_model, exclusive=True)
            atomic_write(norm_path, venv.save, validate_norm, exclusive=True)
            document = dict(schema=1, generation=generation,
                            model=model_path.name, normalization=norm_path.name,
                            model_sha256=file_digest(model_path),
                            normalization_sha256=file_digest(norm_path))

            def write_manifest(path):
                path.write_text(json.dumps(document, allow_nan=False) + "\n", encoding="utf-8")

            def validate_manifest(path):
                if json.loads(path.read_text(encoding="utf-8")) != document:
                    raise ValueError("Checkpoint manifest failed validation")

            atomic_write(self.manifest, write_manifest, validate_manifest)
            committed = True
            return model_path, norm_path
        finally:
            # An interruption immediately after replacement must retain the published pair.
            if not committed and self.manifest.is_file():
                try:
                    committed = json.loads(self.manifest.read_text(encoding="utf-8")).get("generation") == generation
                except (OSError, ValueError, AttributeError):
                    pass
            if not committed:
                model_path.unlink(missing_ok=True)
                norm_path.unlink(missing_ok=True)
                for obj, identity in zip((model, venv), old_ids):
                    if identity is None:
                        obj.__dict__.pop("_wfrl_checkpoint_id", None)
                    else:
                        obj._wfrl_checkpoint_id = identity
            restore_rng(rng)


class AtomicCheckpointCallback(BaseCallback):
    """Commit a model/statistics generation at each configured callback interval."""

    def __init__(self, save_freq, save_path, name_prefix):
        super().__init__()
        self.save_freq = save_freq
        self.save_path = Path(save_path)
        self.name_prefix = name_prefix

    def _on_step(self):
        if self.n_calls % self.save_freq == 0:
            venv = self.model.get_vec_normalize_env()
            if venv is None:
                raise ValueError("Atomic checkpoints require VecNormalize statistics")
            stem = self.save_path / f"{self.name_prefix}_{self.num_timesteps}_steps"
            norm = self.save_path / f"{self.name_prefix}_vecnormalize_{self.num_timesteps}_steps.pkl"
            CheckpointPair(stem, norm).save(self.model, venv)
        return True


def _checkpoint_pair():
    return CheckpointPair(MODEL_PATH, VECNORM_PATH)


def save_checkpoint_pair(model, venv):
    return _checkpoint_pair().save(model, venv)


def make_single_env(max_steps: int = MAX_STEPS) -> Monitor:
    """One SB3-ready env instance (adapter + episode-stats Monitor). Raw reward."""
    raw = envs.make(ENV_ID, controls=["yaw"], max_num_steps=max_steps)
    try:
        return Monitor(WFCRLSB3Wrapper(raw))
    except BaseException:
        raw.close()
        raise


def _require_checkpoint_files():
    return _checkpoint_pair().require()


def _validate_normalization(venv):
    keys = set(venv.observation_space.spaces)
    if not venv.norm_obs or set(venv.norm_obs_keys or ()) != keys:
        raise ValueError("PPO checkpoint must normalize every observation key")
    if not isinstance(venv.obs_rms, dict) or set(venv.obs_rms) != keys:
        raise ValueError("PPO checkpoint has incompatible observation statistics")
    statistics = [(key, rms, venv.observation_space[key].shape)
                  for key, rms in venv.obs_rms.items()]
    statistics.append(("reward", venv.ret_rms, ()))
    for key, rms, shape in statistics:
        if (np.shape(rms.mean) != shape or np.shape(rms.var) != shape
                or not np.isfinite(rms.mean).all() or not np.isfinite(rms.var).all()
                or np.any(rms.var < 0) or np.shape(rms.count) != ()
                or not np.isfinite(rms.count) or rms.count <= 0):
            raise ValueError(f"PPO checkpoint has invalid {key} normalization statistics")


def _validate_pair(model, venv):
    check_for_correct_spaces(venv, model.observation_space, model.action_space)
    _validate_normalization(venv)
    if not np.isclose(model.gamma, venv.gamma, rtol=0, atol=1e-12):
        raise ValueError("PPO model and normalization discount factors do not match")
    if model.__dict__.get("_wfrl_checkpoint_id") != venv.__dict__.get("_wfrl_checkpoint_id"):
        raise ValueError("PPO model and normalization checkpoint identities do not match")


def _make_venv(max_steps, *, training, norm_path=None, normalization=None):
    model_path = None
    if norm_path is not None and os.path.abspath(norm_path) == os.path.abspath(VECNORM_PATH):
        model_path, norm_path = _checkpoint_pair().resolve()
    if norm_path is not None and not os.path.isfile(norm_path):
        raise FileNotFoundError(f"Required PPO normalization file is missing: {norm_path}")
    base = DummyVecEnv([lambda: make_single_env(max_steps)])
    try:
        if normalization is not None:
            # VecNormalize copies statistics without copying its live environment.
            venv = deepcopy(normalization)
            venv.set_venv(base)
        elif norm_path is not None:
            venv = VecNormalize.load(norm_path, base)
            venv._wfrl_normalization_path = os.path.abspath(norm_path)
            venv._wfrl_model_path = os.path.abspath(model_path) if model_path is not None else None
        else:
            venv = VecNormalize(base, norm_obs=True, clip_obs=10.0, gamma=0.99)
        check_for_correct_spaces(base, venv.observation_space, venv.action_space)
        _validate_normalization(venv)
        venv.training = training
        venv.norm_reward = training
        return venv
    except BaseException:
        base.close()
        raise


def make_train_venv(max_steps: int = MAX_STEPS, *, resume=False) -> VecNormalize:
    """Normalize observations and returns; resume restores their saved statistics."""
    return _make_venv(max_steps, training=True,
                      norm_path=VECNORM_PATH if resume else None)


def make_eval_venv(max_steps: int = MAX_STEPS, *, normalization=None) -> VecNormalize:
    """Freeze saved or explicitly supplied statistics and return raw rewards."""
    return _make_venv(max_steps, training=False, normalization=normalization,
                      norm_path=VECNORM_PATH if normalization is None else None)


def build_model(venv, resume: bool) -> PPO:
    if resume:
        model_path = venv.__dict__.get("_wfrl_model_path")
        if model_path is None:
            raise ValueError("Restore VecNormalize before loading a PPO checkpoint")
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"Required PPO checkpoint file is missing: {model_path}")
        print(f"[train] resuming from {model_path}")
        model = PPO.load(model_path, env=venv, tensorboard_log=LOG_DIR)
        _validate_pair(model, venv)
        return model

    print("[train] building fresh PPO model")
    return PPO(
        policy="MultiInputPolicy",   # handles the Dict observation space
        env=venv,
        learning_rate=3e-4,
        n_steps=1024,                # rollout length per update
        batch_size=128,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.005,              # small exploration bonus for continuous control
        vf_coef=0.5,
        max_grad_norm=0.5,
        policy_kwargs=dict(net_arch=[128, 128]),
        tensorboard_log=LOG_DIR,
        verbose=1,
    )


def evaluate(model: PPO, n_episodes: int = 3, max_steps: int = MAX_STEPS, *,
             venv=None, normalization=None):
    """Evaluate frozen observations and raw rewards; close owned environments."""
    owns_venv = venv is None
    if owns_venv:
        venv = make_eval_venv(max_steps, normalization=normalization)
    try:
        _validate_pair(model, venv)
        if venv.training or venv.norm_reward:
            raise ValueError("PPO evaluation requires training=False and norm_reward=False")
        returns = []
        for ep in range(n_episodes):
            obs = venv.reset()
            ep_ret, done = 0.0, False
            while not done:
                action, _ = model.predict(obs, deterministic=True)
                obs, reward, dones, _ = venv.step(action)
                ep_ret += float(reward[0])
                done = bool(dones[0])
            returns.append(ep_ret)
            print(f"[eval] episode {ep + 1}: raw return = {ep_ret:.4f}")
    finally:
        if owns_venv:
            venv.close()
    print(f"[eval] mean raw return over {n_episodes} eps = {np.mean(returns):.4f}")
    return float(np.mean(returns))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=100_000,
                        help="Additional control steps, rounded up to a complete rollout")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--eval-only", action="store_true")
    parser.add_argument("--progress-bar", action="store_true",
                        help="Show the optional progress bar (requires tqdm and rich)")
    args = parser.parse_args()

    if args.resume or args.eval_only:
        _require_checkpoint_files()
    if args.eval_only:
        venv = make_eval_venv()
        try:
            model = build_model(venv, resume=True)
            evaluate(model, venv=venv)
        finally:
            venv.close()
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CKPT_DIR, exist_ok=True)
    venv = make_train_venv(resume=args.resume)
    try:
        model = build_model(venv, resume=args.resume)

        print("\n[train] --- policy evaluation (before training) ---")
        evaluate(model, n_episodes=2, normalization=venv)

        ckpt_cb = AtomicCheckpointCallback(
            save_freq=20_000, save_path=CKPT_DIR, name_prefix="ppo_wf",
        )

        print(f"\n[train] --- training for {args.timesteps} timesteps ---")
        model.learn(total_timesteps=args.timesteps, callback=ckpt_cb,
                    progress_bar=args.progress_bar,
                    reset_num_timesteps=not args.resume)

        model_path, norm_path = save_checkpoint_pair(model, venv)
        print(f"[train] saved model to {model_path} and stats to {norm_path}")
    finally:
        venv.close()

    print("\n[train] --- final evaluation (after training) ---")
    evaluate(model)


if __name__ == "__main__":
    main()
