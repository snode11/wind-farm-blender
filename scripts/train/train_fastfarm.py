"""Feedforward actors with centralized feedforward or recurrent critics.

FAST.Farm training uses per-episode processes, optional warmup, time-limit GAE,
and content-identified checkpoints. Recovery starts a separately identified
physical episode segment; it does not restore in-flight simulator state.
"""
import argparse
import os
import copy
import inspect
import random

import numpy as np
import torch
import torch.nn as nn

from wfrl.mappo_central import Actor, Critic, RunningMeanStd
from wfrl.policy_actions import PolicyActionContract, load_action_contract
from wfrl.rewards import CHOICES as REWARD_CHOICES, make_shaper

from wfrl import paths, windcond
from wfrl.training_integrity import (
    SCHEMA, canonical, source_input_identity as _source_input_identity,
    new_run, artifact_tag, save_checkpoint_exclusive, capture_rng, restore_rng,
    shaper_state, restore_shaper, validate_resume, file_digest,
    restore_normalizer, restore_optimizer,
)

DEVICE = torch.device("cpu")
CKPT_DIR = paths.CKPT
RUNS_DIR = paths.RUNS

# 训练用的本地观测键（3 维，和 FLORIS 版 actor 对齐，便于口径一致对照）
OBS_KEYS = ["yaw", "wind_speed", "wind_direction"]


def FastFarmDriver(*args, **kwargs):
    """Load the physics dependency only when a real sampler is constructed."""
    from wfrl.fastfarm_driver import FastFarmDriver as driver_type
    driver = driver_type.__new__(driver_type)
    driver.case_dir = None
    try:
        driver.__init__(*args, **kwargs)
    except BaseException as exc:
        try:
            driver.close(strict=True, purge=True)
        except BaseException as cleanup_error:
            cleanup_error.driver = driver
            raise cleanup_error from exc
        raise
    return driver


# ---------------------------------------------------------------------------
# GRU 时序 critic（可选）：吃全局状态序列，输出每步 V(s_t)
# ---------------------------------------------------------------------------
class RecurrentCritic(nn.Module):
    def __init__(self, state_dim, hidden=128):
        super().__init__()
        self.gru = nn.GRU(state_dim, hidden, batch_first=True)
        self.head = nn.Linear(hidden, 1)

    def forward(self, s_seq, h0=None, episode_starts=None):
        """Evaluate contiguous states, resetting hidden state before episode starts."""
        if episode_starts is None:
            out, hn = self.gru(s_seq, h0)
            return self.head(out).squeeze(-1), hn
        starts = torch.as_tensor(episode_starts, dtype=torch.bool, device=s_seq.device)
        if starts.shape != s_seq.shape[:2]:
            raise ValueError("episode_starts must have shape (batch, time)")
        hidden = h0
        values = []
        for t in range(s_seq.shape[1]):
            if hidden is not None:
                hidden = hidden * (~starts[:, t]).to(s_seq.dtype).view(1, -1, 1)
            out, hidden = self.gru(s_seq[:, t:t + 1], hidden)
            values.append(self.head(out).squeeze(-1))
        return torch.cat(values, dim=1), hidden


# ---------------------------------------------------------------------------
# FAST.Farm 采样器 —— 自己管回合生命周期
# ---------------------------------------------------------------------------
class FastFarmSampler:
    """Manage physical episodes, wind scheduling, warmup, and observation packing."""

    def __init__(self, env_id, episode_steps, warmup_steps=8, total_steps=None,
                 obs_keys=OBS_KEYS, load_coef=1.0, reward_shaper=None,
                 wind_schedule=None, wind_time_series=None, purge=True,
                 obs_duty=False, duty_penalty=0.0, scene_path=None, progress=None,
                 start_episode=0):
        self.progress = progress
        self.env_id = env_id
        self.scene_path = scene_path
        self._rt = None
        self.ep_steps = int(episode_steps)
        self.warmup = int(warmup_steps)
        self.obs_keys = list(obs_keys)
        # 可选占空比观测和动作惩罚。
        #  obs_duty     —— obs 追加"占空比余量"列，策略从此看得见自己还能发多大；
        #  duty_penalty —— 动作被占空比清零时按被吃掉的幅度给负奖励（λ 权重）。
        self.obs_duty = bool(obs_duty)
        self.duty_penalty = float(duty_penalty)
        self.load_coef = float(load_coef)
        # 整形器**一个实例贯穿整个训练**：Centered 的 EMA 基线要跨回合保留，
        # 每回合清零等于给每个回合开头塞一段与策略无关的大信号。
        self.shaper = reward_shaper
        self.wind_schedule = wind_schedule or (lambda ep: (None, None))
        self.wind_time_series = wind_time_series
        self.purge = bool(purge)
        # 单回合预算：+2 步余量留给 close() 排空，保证训练数据里不出现被
        # 截断轮清空 rewards 的那一步（`multiagent_env.py:241` _clear_rewards）
        self.budget = (self.warmup + self.ep_steps + 2 if self.ep_steps > 0
                       else int(total_steps) + self.warmup + 8)
        self.episode = int(start_episode) - 1
        self.n_spawns = 0
        self.drv = None
        try:
            self._spawn()
            self.obs_dim = len(self.obs_keys) + (1 if self.obs_duty else 0)
            self.act_dim = 1
            self.act_low, self.act_high = self.drv.act_low, self.drv.act_high
            self.state_dim = self.n * self.obs_dim
        except BaseException as exc:
            self._cleanup_failed_spawn(exc)
            raise

    def _cleanup_failed_spawn(self, cause):
        try:
            self._close_driver()
        except BaseException as cleanup_error:
            cleanup_error.sampler = self
            raise cleanup_error from cause

    # ------------------------------------------------------------------
    def _pack(self, m):
        """driver 测量 dict -> (n, obs_dim) 本地观测矩阵。

        obs_duty 开启时追加一列"占空比余量"：(0.1 − duty)/0.1 裁到 [0,1]，
        1 = 还能自由发、0 = 再发就被清零。策略可从此列判断执行约束。
        """
        cols = [np.asarray(m[k], dtype=np.float32) for k in self.obs_keys]
        if self.obs_duty:
            duty = np.asarray((m.get("duty") or {}).get(
                "yaw", np.zeros(self.n)), np.float32).ravel()
            headroom = np.clip((0.1 - duty) / 0.1, 0.0, 1.0).astype(np.float32)
            cols.append(headroom)
        return np.stack(cols, axis=1)                # (n, obs_dim)

    def _spawn(self):
        if self.progress is not None: self.progress.progress("warmup")
        self.episode += 1
        ws, wd = self.wind_schedule(self.episode)
        kw = {}
        if self.wind_time_series is not None:
            kw["wind_time_series"] = self.wind_time_series
        if self.scene_path is not None:
            # 错列等自定义布局：走 SceneRuntime 起 driver（预注册 env 只有正对一列）。
            # SceneRuntime 内部建 FastFarmDriver（已带滑动窗口占空比），warmup 也在
            # 它 reset 里做；我们读 rt.last_measure 当作 driver 的测量 dict m。
            from wfrl.scene.schema import load_scene
            from wfrl.scene.runtime import SceneRuntime
            sc = load_scene(self.scene_path)
            self._rt = SceneRuntime(sc, max_steps=self.budget,
                                    warmup_steps=self.warmup,
                                    load_coef=self.load_coef,
                                    reward_shaper=self.shaper)
            self._rt.reset()                     # 内部含 warmup
            self.drv = self._rt.driver
            self.n = sc.n
            m = self._rt.last_measure
        else:
            self.drv = FastFarmDriver(self.env_id, max_steps=self.budget,
                                      load_coef=self.load_coef,
                                      reward_shaper=self.shaper, **kw)
            self.n = self.drv.n
            m = self.drv.reset(wind_speed=ws, wind_direction=wd)
            for _ in range(self.warmup):             # 建立瞬态：空转、丢弃
                m = self.drv.step(np.zeros(self.n))
        self._m = m
        self._t = 0
        self.u_inf = self.drv.u_inf
        self.n_spawns += 1
        print(f"[sampler] 回合 {self.episode} 起：u_inf={self.u_inf:.3f} m/s  "
              f"warmup={self.warmup} 步已丢弃  预算={self.budget}", flush=True)
        if self.progress is not None: self.progress.progress("sampling")
        return self._pack(m)

    def obs(self):
        return self._pack(self._m)

    def step(self, yaw_delta):                       # yaw_delta: (n,1) or (n,)
        req = np.asarray(yaw_delta).ravel().astype(float)
        yaw_before = np.asarray(self._m["yaw"], float).ravel()
        m = self.drv.step(req)
        r = float(m["reward"])
        if self.duty_penalty > 0.0:
            # 被吃掉的动作幅度 = 请求量 − 实际偏航变化（被占空比清零或状态边界裁掉）。
            # 按被吃掉的量给负奖励：发"会被清零的大动作"从此在回报上劣于"发可持续
            # 的小动作"，逼策略学会控制在占空比余量之内。
            yaw_after = np.asarray(m["yaw"], float).ravel()
            eaten = np.abs(req) - np.abs(yaw_after - yaw_before)
            eaten = np.clip(eaten, 0.0, None)
            r -= self.duty_penalty * float(np.mean(eaten))
        self._m = m
        self._t += 1
        trunc = bool(m["done"]) or (self.ep_steps > 0
                                    and self._t >= self.ep_steps)
        return self._pack(m), r, trunc, m

    def new_episode(self):
        """Drain the completed physical episode before spawning the next one."""
        state = shaper_state(self.shaper) if self.shaper is not None else None
        self._close_driver()
        if state is not None:
            restore_shaper(self.shaper, state, validate=False)
        try:
            return self._spawn()
        except BaseException as exc:
            self._cleanup_failed_spawn(exc)
            raise

    def close(self):
        self._close_driver()

    def _close_driver(self):
        # scene 模式经 SceneRuntime.close（它会 purge 算例目录）；否则直接关 driver
        if getattr(self, "_rt", None) is not None:
            self._rt.close(purge=self.purge, strict=True)
            self._rt = None
        elif self.drv is not None:
            self.drv.close(purge=self.purge, strict=True)
        self.drv = None


# ---------------------------------------------------------------------------
# GAE —— 时间截断版
# ---------------------------------------------------------------------------
def compute_gae_trunc(rews, vals, next_vals, dones, gamma=0.99, lam=0.95):
    """回合边界是**时间截断**，不是终止状态。

    偏航控制这个任务没有真正的终止状态：回合结束只是"预算走完了"，此后世界照常
    存在。所以 δ 里必须照常 bootstrap `V(s_{t+1})`（用截断**前**那个观测，不是
    reset 之后的），只把 GAE 的递推链在边界处断开。`next_vals` 为各步在
    reset 前计算的下一状态价值，`dones` 只控制优势递推是否跨过回合边界。
    """
    T = len(rews)
    adv = np.zeros(T, dtype=np.float32)
    gae = 0.0
    for t in reversed(range(T)):
        delta = rews[t] + gamma * next_vals[t] - vals[t]
        gae = delta + gamma * lam * (1.0 - dones[t]) * gae
        adv[t] = gae
    return adv, adv + vals


# ---------------------------------------------------------------------------
# 训练
# ---------------------------------------------------------------------------
def experiment_config(env_id, iters, n_steps, **kwargs):
    """Resolve training defaults and aliases into the effective experiment config."""
    bound = inspect.signature(train).bind(env_id, iters, n_steps, **kwargs)
    bound.apply_defaults()
    config = dict(bound.arguments)
    for key in ("purge", "tag_extra", "save_interval", "resume_from", "training_progress", "run_id"):
        config.pop(key)
    speed = config.pop("wind_speed")
    config["wind_sched"] = (config["wind_sched"] or
                            (f"pin:{speed:g}" if speed is not None else "none")).strip()
    if "floris" not in env_id.lower():
        config["wind_direction"] = None
    head, _, argument = config["wind_sched"].partition(":")
    head = head.lower()
    if head == "pin":
        config["wind_sched"] = f"pin:{float(argument):g}"
    elif head == "cycle":
        config["wind_sched"] = "cycle:" + ",".join(f"{float(x):g}" for x in argument.split(",") if x.strip())
    elif head == "weibull":
        config["wind_sched"] = f"weibull:{int(argument) if argument else int(config['seed'])}"
    else:
        config["wind_sched"] = head + (":" + argument if argument else "")
    if config["scene"] is not None:
        from wfrl.scene.schema import load_scene
        config["scene"] = load_scene(config["scene"]).to_dict()
        config["wind_sched"] = "scene"
        config["wind_direction"] = None
    for key in ("iters", "n_steps", "n_epochs", "mb_steps"):
        if config[key] <= 0:
            raise ValueError(f"{key} must be positive")
    if config["episode_steps"] < 0 or config["warmup_steps"] < 0:
        raise ValueError("episode_steps and warmup_steps must be nonnegative")
    return canonical(config)


def training_wind_schedule(config):
    """Use the same backend and explicit direction as experiment identity."""
    if config["scene"] is not None:
        return lambda episode: (None, None)
    backend = "floris" if "floris" in config["env_id"].lower() else "fastfarm"
    schedule = windcond.make_wind_schedule(config["wind_sched"], seed=config["seed"], backend=backend)
    direction = config["wind_direction"]
    if direction is None:
        return schedule
    return lambda episode: (schedule(episode)[0], direction)


def source_input_identity(scene=None):
    return _source_input_identity(paths.ROOT, scene)


def display_label(config, extra=""):
    algorithm = "zero" if config["policy"] == "zero" else ("gru-mappo" if config["recurrent"] else "mappo")
    return (f"{algorithm}_s{config['seed']}_{config['reward']}_E{config['episode_steps']}_"
            f"{config['wind_sched'].replace(':', '').replace(',', '-')}" +
            (f"_{extra}" if extra else ""))


def _training_components(obs_dim, act_dim, state_dim, recurrent, lr, checkpoint=None, learning=True):
    """Build software state; checkpoint validation completes before physics starts."""
    saved_rng = capture_rng() if checkpoint is not None else None
    try:
        actor = Actor(obs_dim, act_dim).to(DEVICE)
        critic = (RecurrentCritic(state_dim) if recurrent else Critic(state_dim)).to(DEVICE)
        optimizer = torch.optim.Adam(list(actor.parameters()) + list(critic.parameters()), lr=lr)
        obs_rms, ret_rms = RunningMeanStd((obs_dim,)), RunningMeanStd(())
        if checkpoint is not None:
            try:
                actor.load_state_dict(checkpoint["actor"])
                critic.load_state_dict(checkpoint["critic"])
                if any(not torch.isfinite(value).all() for model in (actor, critic)
                       for value in model.state_dict().values()):
                    raise ValueError("model parameters must be finite")
                with torch.no_grad():
                    distribution = actor.dist(torch.zeros((1, obs_dim), device=DEVICE))
                    value = (critic(torch.zeros((1, 1, state_dim), device=DEVICE))[0]
                             if recurrent else critic(torch.zeros((1, state_dim), device=DEVICE)))
                    if (not torch.isfinite(distribution.mean).all() or
                            not torch.isfinite(distribution.stddev).all() or
                            not (distribution.stddev > 0).all() or not torch.isfinite(value).all()):
                        raise ValueError("model evaluation must be finite")
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                raise ValueError("resume actor/critic state is invalid or incompatible") from exc
            restore_optimizer(optimizer, checkpoint["optimizer"], learning=learning)
            restore_normalizer(obs_rms, checkpoint, "obs", (obs_dim,))
            restore_normalizer(ret_rms, checkpoint, "ret", ())
        return actor, critic, optimizer, obs_rms, ret_rms
    finally:
        if saved_rng is not None:
            restore_rng(saved_rng)


def train(env_id, iters, n_steps, n_epochs=10, mb_steps=64,
          gamma=0.99, lam=0.95, clip=0.2, lr=3e-4, ent_coef=0.005,
          vf_coef=0.5, max_grad=0.5, recurrent=False, seed=0,
          wind_speed=None, wind_direction=None,
          episode_steps=0, warmup_steps=8, wind_sched=None,
          reward="level", reward_ref=None, reward_alpha=0.01,
          load_coef=1.0, policy="learn", purge=True, tag_extra="",
          obs_duty=False, duty_penalty=0.0, scene=None,
          save_interval=0, resume_from=None, training_progress=None, run_id=None):
    config = experiment_config(**{key: value for key, value in locals().items()})
    wind_sched = config["wind_sched"]
    recovery = None
    policy_scene = None
    if scene is not None:
        from wfrl.scene.schema import load_scene
        policy_scene = load_scene(scene)
        if list(policy_scene.controls) != ["yaw"]:
            raise ValueError("train_fastfarm requires scene controls ['yaw']; use Studio for other controls")
    source_identity = source_input_identity(scene)
    ckpt = None
    action_contract = None
    if resume_from:
        ckpt = torch.load(resume_from, map_location=DEVICE, weights_only=False)
        action_contract = load_action_contract(
            ckpt, controls=["yaw"],
            n_agents=policy_scene.n if policy_scene is not None else None,
            scene=policy_scene.to_dict() if policy_scene is not None else None,
            env_id=env_id)
        if action_contract.transform != "clip":
            raise ValueError("train_fastfarm resume requires the clip action contract")
        if ckpt.get("obs_duty") != bool(obs_duty) or ckpt.get("recurrent") != bool(recurrent):
            raise ValueError("resume obs_duty/recurrent settings must match the checkpoint")
        if not {"actor", "critic", "optimizer"} <= ckpt.keys():
            raise ValueError("resume requires a full training checkpoint with actor/critic/optimizer")
    components = None
    if ckpt is not None:
        recovery = validate_resume(ckpt, config, source_identity)
        expected_obs_dim = len(OBS_KEYS) + int(obs_duty)
        if (ckpt["obs_dim"] != expected_obs_dim or ckpt.get("act_dim") != 1 or
                ckpt.get("obs_keys") != OBS_KEYS or
                (policy_scene is not None and ckpt["state_dim"] != policy_scene.n * expected_obs_dim)):
            raise ValueError("resume dimension/observation metadata is incompatible")
        components = _training_components(ckpt["obs_dim"], ckpt["act_dim"], ckpt["state_dim"],
                                          recurrent, lr, checkpoint=ckpt, learning=policy != "zero")
    run = new_run(config, source_identity, display_label(config, tag_extra),
                  parent=ckpt, progress_run_id=run_id)
    if recovery is not None:
        run["segment_start_iteration"] = ckpt["iteration"] + 1
        run["segment_start_control_steps"] = recovery["control_steps"]
        run["start_episode"] = recovery["next_episode"]
        run["parent_checkpoint_sha256"] = file_digest(resume_from)
    tag = artifact_tag(run)
    progress = None
    if training_progress:
        from wfrl.blender_bridge.training_progress import TrainingProgressWriter, resume_counts
        import uuid
        counts = (0, 0)
        if resume_from:
            checkpoint = ckpt
            counts = resume_counts(checkpoint)
            required = {'actor', 'critic', 'optimizer', 'obs_mean', 'obs_var'}
            if not required <= checkpoint.keys():
                raise ValueError('Observability resume requires a full training checkpoint')
            if checkpoint['iteration'] >= iters:
                raise ValueError('Observability resume requires remaining training iterations')
        progress = TrainingProgressWriter(training_progress, run_id or uuid.uuid4().hex, *counts)
        progress.iteration = checkpoint['iteration'] + 1 if resume_from else 1
    random.seed(seed)
    torch.manual_seed(seed)
    np.random.seed(seed)
    total_steps = iters * n_steps

    # 来流调度由归一化后的有效配置定义。
    schedule = training_wind_schedule(config)
    shaper = make_shaper(reward, reference=reward_ref, alpha=reward_alpha)
    if recovery is not None:
        restore_shaper(shaper, recovery["reward_shaper"])
        restore_rng(recovery["rng"])

    smp = None
    sampler_closed = False
    try:
        smp = FastFarmSampler(env_id, episode_steps=episode_steps,
                              warmup_steps=warmup_steps,
                              total_steps=(iters - ckpt["iteration"]) * n_steps if ckpt else total_steps,
                              load_coef=load_coef, reward_shaper=shaper,
                              wind_schedule=schedule, purge=purge,
                              obs_duty=obs_duty, duty_penalty=duty_penalty,
                              scene_path=scene, progress=progress,
                              start_episode=recovery["next_episode"] if recovery else 0)
        n = smp.n
        bounds = {"yaw": (smp.act_low, smp.act_high)}
        if action_contract is None:
            action_contract = PolicyActionContract.from_bounds(["yaw"], bounds, "clip")
        else:
            load_action_contract(
                ckpt, controls=["yaw"], n_agents=n,
                scene=policy_scene.to_dict() if policy_scene is not None else None,
                env_id=env_id)
            action_contract.validate_runtime(["yaw"], bounds)
        policy_metadata = {"controls": ["yaw"], "policy_source": "train_fastfarm",
                           "action_contract": action_contract.to_dict()}
        if policy_scene is not None:
            policy_metadata["scene"] = policy_scene.to_dict()

        if ckpt is not None and (smp.obs_dim, smp.act_dim, smp.state_dim) != (
                ckpt["obs_dim"], ckpt["act_dim"], ckpt["state_dim"]):
            raise ValueError("resume sampler dimensions do not match validated software state")
        actor, critic, opt, obs_rms, ret_rms = (components if components is not None else
            _training_components(smp.obs_dim, smp.act_dim, smp.state_dim, recurrent, lr))
        ret_acc = 0.0
        start_iter = 1
        if ckpt is not None:
            start_iter = ckpt["iteration"] + 1
            hist = copy.deepcopy(ckpt["history"])
            print(f"[resume] 从 {resume_from} 恢复，继续从 iter {start_iter}/{iters}", flush=True)
        else:
            hist = {"reward": [], "power_mw": [], "power_norm": [], "u_inf": [],
                    "explained_var": [], "pg": [], "vloss": [], "episodes": [],
                    "raw_reward": []}

        obs = smp.obs()
        print(f"[{tag}] env={env_id}  n={n}  obs_dim={smp.obs_dim}  "
              f"state_dim={smp.state_dim}  {iters} iters x {n_steps} steps "
              f"(~{total_steps} control steps)\n"
              f"[{tag}] 回合={episode_steps or '单长回合'} 步  warmup={warmup_steps} 步  "
              f"来流调度={wind_sched}  奖励={reward}  load_coef={load_coef}  "
              f"策略={policy}", flush=True)
        if save_interval > 0:
            print(f"[{tag}] 定期保存：每 {save_interval} 轮保存一次 checkpoint", flush=True)

        # power_norm = P/u_inf^3，跨来流可比的功率口径；u_inf 逐步记录，
        # 因为回合边界会换来流。
        hist["cfg"] = config
        hist["run"] = run
        control_steps = recovery["control_steps"] if recovery else 0
        global_step = recovery["agent_steps"] if recovery else 0
        critic_hidden = None
        episode_start = True
        pending_episode = False
        episode_control_steps = 0

        def critic_value(state_np, hidden=None, start=False):
            st = torch.as_tensor(state_np, device=DEVICE)
            if recurrent:
                v, next_hidden = critic(st.view(1, 1, -1), hidden,
                                        episode_starts=[[start]])
                return float(v.view(-1)[0]), next_hidden
            return float(critic(st)), None

        def checkpoint_payload(iteration):
            step_in_episode = episode_control_steps
            next_episode = smp.episode + (1 if pending_episode or step_in_episode else 0)
            run["control_steps"], run["agent_steps"] = control_steps, global_step
            return {"actor": actor.state_dict(), "critic": critic.state_dict(),
                    "optimizer": opt.state_dict(),
                    "obs_mean": obs_rms.mean, "obs_var": obs_rms.var, "obs_count": obs_rms.count,
                    "ret_mean": ret_rms.mean, "ret_var": ret_rms.var, "ret_count": ret_rms.count,
                    "obs_dim": smp.obs_dim, "act_dim": smp.act_dim, "state_dim": smp.state_dim,
                    "obs_keys": smp.obs_keys, "obs_duty": smp.obs_duty,
                    "duty_penalty": smp.duty_penalty, "recurrent": recurrent, "env_id": env_id,
                    **policy_metadata, "iteration": iteration, "history": hist,
                    "cfg": config, "run": run,
                    "recovery": {"schema": SCHEMA, "mode": "new_episode_segment",
                                 "next_episode": next_episode,
                                 "discarded_episode_steps": step_in_episode if not pending_episode else 0,
                                 "control_steps": control_steps, "agent_steps": global_step,
                                 "reward_shaper": shaper_state(shaper), "rng": capture_rng()},
                    **({"training_progress": {"step": progress.step, "agent_step": progress.agent_step}}
                       if progress is not None else {})}

        for it in range(start_iter, iters + 1):
            if progress is not None: progress.progress("sampling", it)
            B_obs = np.zeros((n_steps, n, smp.obs_dim), np.float32)
            B_act = np.zeros((n_steps, n, smp.act_dim), np.float32)
            B_logp = np.zeros((n_steps, n), np.float32)
            B_state = np.zeros((n_steps, smp.state_dim), np.float32)
            B_val = np.zeros(n_steps, np.float32)
            B_nextv = np.zeros(n_steps, np.float32)
            B_rew = np.zeros(n_steps, np.float32)
            B_raw = np.zeros(n_steps, np.float32)
            B_done = np.zeros(n_steps, np.float32)
            B_episode_start = np.zeros(n_steps, bool)
            rollout_hidden = critic_hidden.detach().clone() if critic_hidden is not None else None
            B_power = np.zeros(n_steps, np.float32)
            B_u = np.zeros(n_steps, np.float32)

            for t in range(n_steps):
                if pending_episode:
                    obs = smp.new_episode()
                    pending_episode = False
                    episode_control_steps = 0
                B_episode_start[t] = episode_start
                raw_obs = obs                                    # 决策所用的原始观测
                obs_n = obs_rms.norm(obs).astype(np.float32)
                state_n = obs_n.reshape(-1)
                with torch.no_grad():
                    ot = torch.as_tensor(obs_n, device=DEVICE)
                    dist = actor.dist(ot)
                    a = dist.sample()
                    logp = dist.log_prob(a).sum(-1)
                    v, critic_hidden = critic_value(state_n, critic_hidden, episode_start)
                episode_start = False
                a_np = a.cpu().numpy()
                # 动作 = 连续偏航增量，裁剪到边界；零偏航基线走同一条采样路径，
                # 只把动作换成 0 —— 这样基线与策略的回合结构/来流/统计口径完全一致
                a_clip = (np.zeros_like(a_np) if policy == "zero"
                          else action_contract.commands(a_np, n)["yaw"][:, None])
                obs, r, trunc, meas = smp.step(a_clip)

                ret_acc = ret_acc * gamma + r
                ret_rms.update(np.array([ret_acc]))
                r_norm = r / np.sqrt(ret_rms.var + 1e-8)

                # Bootstrap 与下一次决策使用同一份统计；截断仍在 reset 前估值。
                obs_rms.update(raw_obs)
                with torch.no_grad():
                    B_nextv[t], _ = critic_value(
                        obs_rms.norm(obs).astype(np.float32).reshape(-1), critic_hidden)

                B_obs[t], B_act[t] = obs_n, a_np
                B_logp[t] = logp.cpu().numpy()
                B_state[t], B_val[t] = state_n, v
                B_rew[t], B_done[t] = r_norm, float(trunc)
                B_raw[t] = float(np.mean(smp.shaper.raw[-1:]) if
                                 getattr(smp.shaper, "raw", None) else np.nan)
                B_power[t] = float(np.sum(meas["power"]))       # 全场总功率 MW
                B_u[t] = smp.u_inf                              # 本步的自由来流
                global_step += n
                control_steps += 1
                episode_control_steps += 1
                if progress is not None:
                    progress.step += 1
                    progress.agent_step += n
                if trunc:
                    ret_acc = 0.0
                    critic_hidden = None
                    episode_start = True
                    pending_episode = True

            if critic_hidden is not None:
                critic_hidden = critic_hidden.detach()
            if progress is not None: progress.progress("updating", it)
            adv, ret = compute_gae_trunc(B_rew, B_val, B_nextv, B_done, gamma, lam)

            obs_t = torch.as_tensor(B_obs, device=DEVICE)
            act_t = torch.as_tensor(B_act, device=DEVICE)
            logp_t = torch.as_tensor(B_logp, device=DEVICE)
            state_t = torch.as_tensor(B_state, device=DEVICE)
            adv_t = torch.as_tensor(adv, device=DEVICE)
            ret_t = torch.as_tensor(ret, device=DEVICE)

            # policy="zero" 是零偏航基线：走完全相同的采样路径，但不更新网络
            pg_log, v_log, ent_log = [], [], []
            for _ in range(0 if policy == "zero" else n_epochs):
                if recurrent:
                    # 时序 critic 保持采样顺序，并在回合起点清空隐藏状态。
                    dist = actor.dist(obs_t)                      # (T,n,act)
                    new_logp = dist.log_prob(act_t).sum(-1)      # (T,n)
                    ratio = (new_logp - logp_t).exp()
                    A = adv_t.unsqueeze(-1).expand_as(ratio)
                    std = A.std() if A.numel() > 1 else A.new_zeros(())
                    A = (A - A.mean()) / (std + 1e-8)
                    pg = -torch.min(ratio * A,
                                    torch.clamp(ratio, 1 - clip, 1 + clip) * A).mean()
                    ent = dist.entropy().sum(-1).mean()
                    v_seq, _ = critic(state_t.unsqueeze(0), rollout_hidden,
                                      episode_starts=B_episode_start[None, :])
                    vloss = ((v_seq.squeeze(0) - ret_t) ** 2).mean()
                    loss = pg - ent_coef * ent + vf_coef * vloss
                    opt.zero_grad(); loss.backward()
                    nn.utils.clip_grad_norm_(
                        list(actor.parameters()) + list(critic.parameters()), max_grad)
                    opt.step()
                    pg_log.append(float(pg.detach())); v_log.append(float(vloss.detach()))
                    ent_log.append(float(ent.detach()))
                else:
                    idx = np.arange(n_steps); np.random.shuffle(idx)
                    for s in range(0, n_steps, mb_steps):
                        mb = torch.as_tensor(idx[s:s + mb_steps], device=DEVICE)
                        dist = actor.dist(obs_t[mb])
                        new_logp = dist.log_prob(act_t[mb]).sum(-1)
                        ratio = (new_logp - logp_t[mb]).exp()
                        A = adv_t[mb].unsqueeze(-1).expand_as(ratio)
                        std = A.std() if A.numel() > 1 else A.new_zeros(())
                        A = (A - A.mean()) / (std + 1e-8)
                        pg = -torch.min(ratio * A,
                                        torch.clamp(ratio, 1 - clip, 1 + clip) * A).mean()
                        ent = dist.entropy().sum(-1).mean()
                        v = critic(state_t[mb])
                        vloss = ((v - ret_t[mb]) ** 2).mean()
                        loss = pg - ent_coef * ent + vf_coef * vloss
                        opt.zero_grad(); loss.backward()
                        nn.utils.clip_grad_norm_(
                            list(actor.parameters()) + list(critic.parameters()), max_grad)
                        opt.step()
                        pg_log.append(float(pg.detach())); v_log.append(float(vloss.detach()))
                        ent_log.append(float(ent.detach()))

            ev = 1.0 - np.var(ret - B_val) / (np.var(ret) + 1e-8)
            mpg = float(np.mean(pg_log)) if pg_log else float("nan")
            mvl = float(np.mean(v_log)) if v_log else float("nan")
            ment = float(np.mean(ent_log)) if ent_log else float("nan")
            if progress is not None:
                progress.iteration_stats(it, B_power.mean(), B_rew.mean(),
                                         mvl if v_log else None, ev)
            raw_mean = float(np.nanmean(B_raw)) if np.isfinite(B_raw).any() else float("nan")
            hist["reward"].append(float(B_rew.mean()))
            hist["raw_reward"].append(raw_mean)
            hist["power_mw"].append(float(B_power.mean()))
            # 逐步归一化再平均（而非用均值 u），回合中途换来流时才正确
            pnorm = float(np.mean(B_power * 1e3 / np.maximum(B_u, 1e-6) ** 3))
            hist["power_norm"].append(pnorm)
            hist["u_inf"].append(float(B_u.mean()))
            hist["explained_var"].append(float(ev))
            hist["pg"].append(mpg)
            hist["vloss"].append(mvl)
            hist["episodes"].append(int(np.count_nonzero(B_done)))
            print(f"[{tag}] it {it}/{iters}  steps {global_step}  "
                  f"rew(1step) {B_rew.mean():+.4f}  raw {raw_mean:.3f}  "
                  f"farm_power {B_power.mean():.2f}MW  "
                  f"P/u^3 {pnorm:.3f}  u {B_u.mean():.2f}  ep+{np.count_nonzero(B_done)}  "
                  f"pg {mpg:+.4f}  vloss {mvl:.4f}  "
                  f"ent {ment:.3f}  expl_var {ev:.3f}", flush=True)

            # 定期保存 checkpoint
            if save_interval > 0 and it % save_interval == 0:
                os.makedirs(CKPT_DIR, exist_ok=True)
                safe = env_id.replace("/", "_")
                ckpt_path = os.path.join(CKPT_DIR, f"mappo_fastfarm_{safe}_{tag}_iter{it}.pt")
                save_checkpoint_exclusive(checkpoint_payload(it), ckpt_path)
                print(f"[{tag}] 定期保存 iter {it} -> {ckpt_path}", flush=True)

        hist["shaper_stats"] = smp.shaper.stats() if hasattr(smp.shaper, "stats") else {}
        hist["n_spawns"] = smp.n_spawns
        os.makedirs(CKPT_DIR, exist_ok=True)
        safe = env_id.replace("/", "_")
        model_path = os.path.abspath(os.path.join(CKPT_DIR, f"mappo_fastfarm_{safe}_{tag}.pt"))
        run["status"] = "completed"
        run["checkpoint"] = model_path
        payload = checkpoint_payload(iters)
        # Closing drains physics; recovery state belongs to the last training update.
        sampler_closed = True
        smp.close()
        save_checkpoint_exclusive(payload, model_path)
        print(f"[{tag}] saved -> {model_path}", flush=True)

        _dump_hist(hist, env_id, tag)
        _plot_curves(hist, env_id, tag)
        if progress is not None:
            progress.progress("done", iters)
        return hist
    finally:
        try:
            if smp is not None and not sampler_closed:
                smp.close()
        finally:
            if progress is not None:
                progress.close()


def _dump_hist(hist, env_id, tag):
    """把训练历史落 JSON，供 compare_static_dynamic.py 叠加对比。"""
    import json
    os.makedirs(RUNS_DIR, exist_ok=True)
    safe = env_id.replace("/", "_")
    out = os.path.join(RUNS_DIR, f"fastfarm_{safe}_{tag}_hist.json")
    with open(out, "x", encoding="utf-8") as f:
        json.dump({"env_id": env_id, "tag": tag, "hist": hist,
                   "checkpoint_sha256": file_digest(hist["run"]["checkpoint"])}, f)
    print(f"[{tag}] hist -> {out}", flush=True)


def _plot_curves(hist, env_id, tag):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(RUNS_DIR, exist_ok=True)
    its = np.arange(1, len(hist["reward"]) + 1)
    fig, ax = plt.subplots(1, 3, figsize=(15, 4))
    ax[0].plot(its, hist["reward"], "-o", color="tab:blue")
    ax[0].set_title("shaped reward (1-step avg)"); ax[0].set_xlabel("iter")
    ax[1].plot(its, hist["power_mw"], "-o", color="tab:green", label="raw MW")
    ax[1].set_title("farm total power (MW, avg)"); ax[1].set_xlabel("iter")
    if hist.get("power_norm"):
        # 原始 MW 会被来流抽签整体缩放，跨 run 比较必须看归一化的那条
        ax1b = ax[1].twinx()
        ax1b.plot(its, hist["power_norm"], "--s", color="tab:olive", ms=3,
                  label=r"$P/u_\infty^3$")
        ax1b.set_ylabel(r"$P/u_\infty^3$  [kW/(m/s)$^3$]", color="tab:olive")
    ax[2].plot(its, hist["explained_var"], "-o", color="tab:red")
    ax[2].set_title("critic explained_var"); ax[2].set_xlabel("iter")
    for a in ax:
        a.grid(True, alpha=0.3)
    fig.suptitle(f"FAST.Farm {tag}  —  {env_id}")
    fig.tight_layout()
    safe = env_id.replace("/", "_")
    out = os.path.join(RUNS_DIR, f"fastfarm_{safe}_{tag}_curve.png")
    fig.savefig(out, dpi=120)
    print(f"[{tag}] curve -> {out}", flush=True)


DEFAULT_ENV = {"fastfarm": "Dec_Turb3_Row1_Fastfarm",
               "floris": "Dec_Turb3_Row1_Floris"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="fastfarm",
                    choices=["fastfarm", "floris"],
                    help="fastfarm=动态气弹(需 mpiexec)；floris=静态稳态(无需 MPI)")
    ap.add_argument("--env", default=None,
                    help="不填则按 backend 自动选同布局的 Turb3_Row1 环境")
    ap.add_argument("--iters", type=int, default=6)
    ap.add_argument("--n-steps", type=int, default=64)
    ap.add_argument("--recurrent", action="store_true",
                    help="用 GRU 时序 critic（动态后端时延场景）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--wind-speed", type=float, default=None,
                    help="钉死自由来流风速(m/s)。不给则走 benchmark 的随机抽样 —— "
                         "做后端/网络对照时**必须**给，否则比的是两次风况抽签"
                         "(功率 ~ u^3，实测单后端内部即可有 2.4x 跨度)")
    ap.add_argument("--wind-direction", type=float, default=None,
                    help="钉死来流方向(°)。FAST.Farm 侧无法设定,会被忽略;"
                         "因此跨后端对照请统一用 270(沿 +x,与机列同向)")

    # ---- 回合结构 ----
    ap.add_argument("--episode-steps", type=int, default=128,
                    help="每回合的控制步数。>0 时每回合重起一个 FAST.Farm 进程"
                         "(约 25 s)并按 --wind-sched 换来流;0 = 单长回合")
    ap.add_argument("--warmup-steps", type=int, default=8,
                    help="每次 spawn 后先用零动作空转的步数,这些步**不进训练数据"
                         "也不进训练统计**，用于丢弃尾流建立瞬态")
    ap.add_argument("--wind-sched", default=None,
                    help="逐回合来流调度:pin:U / preset:NAME / cycle:U1,U2,... / "
                         "weibull[:SEED] / none。不给则由 --wind-speed 推出 pin:U,"
                         "两者都没有就是 none")
    ap.add_argument("--no-purge", action="store_true",
                    help="回合结束后**不**删 __simul__ 下的算例目录(默认删)。"
                         "每回合一个 ~37 MB,长训练不删会撑爆磁盘")

    # ---- 奖励整形 ----
    ap.add_argument("--reward", default="level", choices=REWARD_CHOICES,
                    help="level=直接用已归一化的 P/u^3(默认);"
                         "centered=减 EMA 基线;reference=相对零偏航基线的百分比;"
                         "step=逐步相对差分")
    ap.add_argument("--reward-ref", type=float, default=None,
                    help="--reward reference 用的基线 r0,来自匹配来流下的零偏航"
                         "实测(先跑 --policy zero 拿 raw)")
    ap.add_argument("--reward-alpha", type=float, default=0.01,
                    help="--reward centered 的 EMA 更新率。要比策略改变功率的"
                         "时间尺度慢,否则基线会把增益一起吃掉")
    ap.add_argument("--load-coef", type=float, default=1.0,
                    help="载荷惩罚权重。实测 mean(P/u^3)=3.33、mean|load|=0.394 "
                         "⇒ 等权约需 8.4;默认 1.0 时载荷项只有功率项的 1/8")

    # ---- 执行机构约束 ----
    ap.add_argument("--obs-duty", action="store_true",
                    help="obs 追加占空比余量列，让策略看得见自己的动作会不会被清零")
    ap.add_argument("--duty-penalty", type=float, default=0.0,
                    help="动作被占空比清零时按被吃掉的幅度给负奖励的权重 λ（0=关）")

    # ---- 定期保存与恢复 ----
    ap.add_argument("--save-interval", type=int, default=0,
                    help="每 N 轮保存一次 checkpoint（0=关闭，只在最后保存）")
    ap.add_argument("--resume-from", type=str, default=None,
                    help="从完整 checkpoint 恢复软件状态，另起物理回合与 run；仅允许增加 iters")

    # ---- 基线 ----
    ap.add_argument("--policy", default="learn", choices=("learn", "zero"),
                    help="zero=零偏航基线:走完全相同的采样/回合/统计路径但不更新"
                         "网络。对照必须用它,别拿另一个脚本的数当基线")
    ap.add_argument("--tag", default="", help="附加到产物文件名的自定义后缀")
    ap.add_argument("--scene", default=None,
                    help="用场景 YAML 起 driver（错列等自定义布局，走 SceneRuntime）；"
                         "给了则忽略 --env/--backend 的预注册布局")
    ap.add_argument("--training-progress", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--run-id", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()
    env_id = args.env or DEFAULT_ENV[args.backend]
    train(env_id, args.iters, args.n_steps,
          scene=args.scene,
          recurrent=args.recurrent, seed=args.seed,
          wind_speed=args.wind_speed, wind_direction=args.wind_direction,
          episode_steps=args.episode_steps, warmup_steps=args.warmup_steps,
          wind_sched=args.wind_sched, reward=args.reward,
          reward_ref=args.reward_ref, reward_alpha=args.reward_alpha,
          load_coef=args.load_coef, policy=args.policy,
          purge=not args.no_purge, tag_extra=args.tag,
          obs_duty=args.obs_duty, duty_penalty=args.duty_penalty,
          save_interval=args.save_interval, resume_from=args.resume_from,
          training_progress=args.training_progress, run_id=args.run_id)


if __name__ == "__main__":
    main()
