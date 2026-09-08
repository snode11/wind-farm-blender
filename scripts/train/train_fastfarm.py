"""
把 FAST.Farm **动态（气弹）后端**接入 MARL 训练：真 MAPPO（集中式 critic，CTDE），
可选 GRU 时序 critic —— 因为动态后端有尾流传播时延（不同于 FLORIS 稳态），带记忆的
集中式 critic 才是它相对 IPPO 应当拉开差距的场景。

复用 mappo_central.py 里已验证的 Actor / RunningMeanStd / GAE，只替换采样器为
FastFarmDriver（真实 FAST.Farm，MPI 驱动）。产出：
  - results/checkpoints/mappo_fastfarm_<env>.pt
  - results/runs/fastfarm_<env>_curve.png     训练曲线（reward / 全场功率 / explained_var）

运行（必须 mpiexec）：
    "/c/Program Files/Microsoft MPI/Bin/mpiexec.exe" -n 1 \
        "C:/Users/s1155/.conda/envs/wfcrl/python.exe" train_fastfarm.py \
        --env Dec_Turb3_Row1_Fastfarm --iters 6 --n-steps 64 [--recurrent]

注意：FAST.Farm 每个控制步 ~0.6s，且回合是 SC_DLL 在 t=0 收到的固定迭代预算，所以
本脚本用**单个长生命周期** FAST.Farm 进程跑完整个训练（max_steps 设为总步数+余量），
避免反复 8s 重启 / 重新 spawn 子进程。
"""
import argparse
import os

import numpy as np
import torch
import torch.nn as nn

from wfrl.fastfarm_driver import FastFarmDriver
from wfrl.mappo_central import Actor, Critic, RunningMeanStd
from wfrl.rewards import CHOICES as REWARD_CHOICES, make_shaper

from wfrl import paths, windcond

DEVICE = torch.device("cpu")
CKPT_DIR = paths.CKPT
RUNS_DIR = paths.RUNS

# 训练用的本地观测键（3 维，和 FLORIS 版 actor 对齐，便于口径一致对照）
OBS_KEYS = ["yaw", "wind_speed", "wind_direction"]


# ---------------------------------------------------------------------------
# GRU 时序 critic（可选）：吃全局状态序列，输出每步 V(s_t)
# ---------------------------------------------------------------------------
class RecurrentCritic(nn.Module):
    def __init__(self, state_dim, hidden=128):
        super().__init__()
        self.gru = nn.GRU(state_dim, hidden, batch_first=True)
        self.head = nn.Linear(hidden, 1)

    def forward(self, s_seq, h0=None):
        # s_seq: (B, T, state_dim)
        out, hn = self.gru(s_seq, h0)
        return self.head(out).squeeze(-1), hn        # (B, T), h


# ---------------------------------------------------------------------------
# FAST.Farm 采样器 —— 自己管回合生命周期
# ---------------------------------------------------------------------------
class FastFarmSampler:
    """把训练切成**真回合**：每回合重起一个 FAST.Farm 进程。

    阶段 3 之前这里是 `max_steps = total_steps + 8`，`done` 永不触发 —— 1280 步
    的训练实际是单回合、单次风况抽签，且开头的尾流建立瞬态整段漏进第 1 轮统计
    （虚高 3.38 vs 2.7 MW）。现在：

      - `episode_steps > 0`：每回合 spawn 一个新进程，按 `wind_schedule` 换来流，
        回合结束 `close(purge=True)` 排空迭代预算并删掉算例目录；
      - `warmup_steps`：每次 spawn 后先用零动作空转 W 步，**不进训练数据也不进
        统计**，把建立瞬态挡在回合之外；
      - `episode_steps == 0`：退回旧的单长回合口径，只为和历史结果对照。

    代价：每回合多一次 spawn（~25 s）＋ 2 步排空。E=128 时约占 20% 墙钟。
    """

    def __init__(self, env_id, episode_steps, warmup_steps=8, total_steps=None,
                 obs_keys=OBS_KEYS, load_coef=1.0, reward_shaper=None,
                 wind_schedule=None, wind_time_series=None, purge=True,
                 obs_duty=False, duty_penalty=0.0, scene_path=None, progress=None):
        self.progress = progress
        self.env_id = env_id
        self.scene_path = scene_path
        self._rt = None
        self.ep_steps = int(episode_steps)
        self.warmup = int(warmup_steps)
        self.obs_keys = list(obs_keys)
        # D5：把执行机构占空比约束接入学习回路（默认关，关闭时逐位复现旧行为）。
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
        self.episode = -1
        self.n_spawns = 0
        self.drv = None
        self._spawn()
        self.obs_dim = len(self.obs_keys) + (1 if self.obs_duty else 0)
        self.act_dim = 1
        self.act_low, self.act_high = self.drv.act_low, self.drv.act_high
        self.state_dim = self.n * self.obs_dim

    # ------------------------------------------------------------------
    def _pack(self, m):
        """driver 测量 dict -> (n, obs_dim) 本地观测矩阵。

        obs_duty 开启时追加一列"占空比余量"：(0.1 − duty)/0.1 裁到 [0,1]，
        1 = 还能自由发、0 = 再发就被清零。让策略看得见自己的动作会不会被吃 ——
        这正是 stage3 学不出偏航的盲点（duty 清零对策略原本完全不可见）。
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
        """回合边界：排空旧进程（否则卡在 MPI_RECV 变孤儿）再起新的。"""
        self._close_driver()
        return self._spawn()

    def close(self):
        self._close_driver()

    def _close_driver(self):
        # scene 模式经 SceneRuntime.close（它会 purge 算例目录）；否则直接关 driver
        if getattr(self, "_rt", None) is not None:
            self._rt.close(purge=self.purge)
            self._rt = None
        elif self.drv is not None:
            self.drv.close(purge=self.purge)


# ---------------------------------------------------------------------------
# GAE —— 时间截断版
# ---------------------------------------------------------------------------
def compute_gae_trunc(rews, vals, next_vals, dones, gamma=0.99, lam=0.95):
    """回合边界是**时间截断**，不是终止状态。

    偏航控制这个任务没有真正的终止状态：回合结束只是"预算走完了"，此后世界照常
    存在。所以 δ 里必须照常 bootstrap `V(s_{t+1})`（用截断**前**那个观测，不是
    reset 之后的），只把 GAE 的递推链在边界处断开。`mappo_central.compute_gae`
    是把 `mask` 同时乘进两处的通用版，在这里会把边界那步的回报硬压成 r_t，
    系统性低估回合末端的价值。
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
def train(env_id, iters, n_steps, n_epochs=10, mb_steps=64,
          gamma=0.99, lam=0.95, clip=0.2, lr=3e-4, ent_coef=0.005,
          vf_coef=0.5, max_grad=0.5, recurrent=False, seed=0,
          wind_speed=None, wind_direction=None,
          episode_steps=0, warmup_steps=8, wind_sched=None,
          reward="level", reward_ref=None, reward_alpha=0.01,
          load_coef=1.0, policy="learn", purge=True, tag_extra="",
          obs_duty=False, duty_penalty=0.0, scene=None,
          save_interval=0, resume_from=None, training_progress=None, run_id=None):
    # Validate resume provenance before launching any physics process.
    progress = None
    if training_progress:
        from wfrl.blender_bridge.training_progress import TrainingProgressWriter, resume_counts
        import uuid
        counts = (0, 0)
        if resume_from:
            checkpoint = torch.load(resume_from, map_location=DEVICE, weights_only=False)
            counts = resume_counts(checkpoint)
            required = {'actor', 'critic', 'optimizer', 'obs_mean', 'obs_var'}
            if not required <= checkpoint.keys():
                raise ValueError('Observability resume requires a full training checkpoint')
            if checkpoint['iteration'] >= iters:
                raise ValueError('Observability resume requires remaining training iterations')
        progress = TrainingProgressWriter(training_progress, run_id or uuid.uuid4().hex, *counts)
        progress.iteration = checkpoint['iteration'] + 1 if resume_from else 1
    torch.manual_seed(seed)
    np.random.seed(seed)
    total_steps = iters * n_steps

    # 来流调度：显式给了 --wind-speed 就等价于 pin:U（保持旧命令行兼容）
    if wind_sched is None:
        wind_sched = f"pin:{wind_speed:g}" if wind_speed is not None else "none"
    schedule = windcond.make_wind_schedule(wind_sched, seed=seed)
    shaper = make_shaper(reward, reference=reward_ref, alpha=reward_alpha)

    smp = FastFarmSampler(env_id, episode_steps=episode_steps,
                          warmup_steps=warmup_steps, total_steps=total_steps,
                          load_coef=load_coef, reward_shaper=shaper,
                          wind_schedule=schedule, purge=purge,
                          obs_duty=obs_duty, duty_penalty=duty_penalty,
                          scene_path=scene, progress=progress)
    n = smp.n

    actor = Actor(smp.obs_dim, smp.act_dim).to(DEVICE)
    critic = (RecurrentCritic(smp.state_dim) if recurrent
              else Critic(smp.state_dim)).to(DEVICE)
    opt = torch.optim.Adam(list(actor.parameters()) + list(critic.parameters()),
                           lr=lr)
    obs_rms = RunningMeanStd((smp.obs_dim,))
    ret_rms = RunningMeanStd(())
    ret_acc = 0.0

    # 恢复训练（如果给了 checkpoint）
    start_iter = 1
    if resume_from:
        if os.path.exists(resume_from):
            ckpt = torch.load(resume_from, map_location=DEVICE, weights_only=False)
            actor.load_state_dict(ckpt["actor"])
            critic.load_state_dict(ckpt["critic"])
            opt.load_state_dict(ckpt["optimizer"])
            obs_rms.mean = ckpt["obs_mean"]
            obs_rms.var = ckpt["obs_var"]
            obs_rms.count = ckpt.get("obs_count", 1e4)
            ret_rms.mean = ckpt.get("ret_mean", 0.0)
            ret_rms.var = ckpt.get("ret_var", 1.0)
            ret_rms.count = ckpt.get("ret_count", 1e4)
            start_iter = ckpt.get("iteration", 0) + 1
            hist = ckpt.get("history", {"reward": [], "power_mw": [], "power_norm": [],
                                        "u_inf": [], "explained_var": [], "pg": [],
                                        "vloss": [], "episodes": [], "raw_reward": []})
            print(f"[resume] 从 {resume_from} 恢复，继续从 iter {start_iter}/{iters}", flush=True)
        else:
            print(f"[resume] 警告：{resume_from} 不存在，从头开始", flush=True)
            hist = {"reward": [], "power_mw": [], "power_norm": [], "u_inf": [],
                    "explained_var": [], "pg": [], "vloss": [], "episodes": [],
                    "raw_reward": []}
    else:
        hist = {"reward": [], "power_mw": [], "power_norm": [], "u_inf": [],
                "explained_var": [], "pg": [], "vloss": [], "episodes": [],
                "raw_reward": []}

    obs = smp.obs()
    # tag 要能唯一标识这次实验的**全部**设定：多 seed / 换整形器 / 换回合长度的
    # 产物必须能共存，否则后跑的静默覆盖先跑的，做误差棒时拿到的其实是同一次
    tag = ("zero" if policy == "zero"
           else ("gru-mappo" if recurrent else "mappo")) + f"_s{seed}"
    tag += f"_{reward}"
    tag += f"_E{episode_steps}" if episode_steps > 0 else "_E0"
    tag += "_" + wind_sched.replace(":", "").replace(",", "-")
    if tag_extra:
        tag += f"_{tag_extra}"
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
    if "cfg" not in hist:
        hist["cfg"] = {"episode_steps": episode_steps, "warmup_steps": warmup_steps,
                       "wind_sched": wind_sched, "reward": reward,
                       "reward_ref": reward_ref, "load_coef": load_coef,
                       "policy": policy, "seed": seed, "recurrent": recurrent,
                       "iters": iters, "n_steps": n_steps}
    global_step = len(hist["reward"]) * n_steps  # 恢复时的全局步数

    def critic_value(state_np):
        """非循环：吃单步 (state_dim,)；循环：吃 (1,1,state_dim) 取末步。"""
        st = torch.as_tensor(state_np, device=DEVICE)
        if recurrent:
            v, _ = critic(st.view(1, 1, -1))
            return float(v.view(-1)[0])
        return float(critic(st))

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
        B_power = np.zeros(n_steps, np.float32)
        B_u = np.zeros(n_steps, np.float32)
        ep_at_start = smp.episode

        for t in range(n_steps):
            raw_obs = obs                                    # 决策所用的原始观测
            obs_n = obs_rms.norm(obs).astype(np.float32)
            state_n = obs_n.reshape(-1)
            with torch.no_grad():
                ot = torch.as_tensor(obs_n, device=DEVICE)
                dist = actor.dist(ot)
                a = dist.sample()
                logp = dist.log_prob(a).sum(-1)
                v = critic_value(state_n)
            a_np = a.cpu().numpy()
            # 动作 = 连续偏航增量，裁剪到边界；零偏航基线走同一条采样路径，
            # 只把动作换成 0 —— 这样基线与策略的回合结构/来流/统计口径完全一致
            a_clip = (np.zeros_like(a_np) if policy == "zero"
                      else np.clip(a_np, smp.act_low, smp.act_high))
            obs, r, trunc, meas = smp.step(a_clip)

            ret_acc = ret_acc * gamma + r
            ret_rms.update(np.array([ret_acc]))
            r_norm = r / np.sqrt(ret_rms.var + 1e-8)

            # 截断处的 bootstrap 必须用**截断前**那个观测，所以在 reset 之前算
            with torch.no_grad():
                B_nextv[t] = critic_value(
                    obs_rms.norm(obs).astype(np.float32).reshape(-1))

            B_obs[t], B_act[t] = obs_n, a_np
            B_logp[t] = logp.cpu().numpy()
            B_state[t], B_val[t] = state_n, v
            B_rew[t], B_done[t] = r_norm, float(trunc)
            B_raw[t] = float(np.mean(smp.shaper.raw[-1:]) if
                             getattr(smp.shaper, "raw", None) else np.nan)
            B_power[t] = float(np.sum(meas["power"]))       # 全场总功率 MW
            B_u[t] = smp.u_inf                              # 本步的自由来流
            obs_rms.update(raw_obs)
            global_step += n
            if progress is not None:
                progress.step += 1
                progress.agent_step += n
            if trunc:
                ret_acc = 0.0
                obs = smp.new_episode()

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
                # 时序 critic：整段序列一次前向（保持时间顺序），actor 仍可按步更新
                dist = actor.dist(obs_t)                      # (T,n,act)
                new_logp = dist.log_prob(act_t).sum(-1)      # (T,n)
                ratio = (new_logp - logp_t).exp()
                A = adv_t.unsqueeze(-1).expand_as(ratio)
                A = (A - A.mean()) / (A.std() + 1e-8)
                pg = -torch.min(ratio * A,
                                torch.clamp(ratio, 1 - clip, 1 + clip) * A).mean()
                ent = dist.entropy().sum(-1).mean()
                v_seq, _ = critic(state_t.unsqueeze(0))      # (1,T)
                vloss = ((v_seq.squeeze(0) - ret_t) ** 2).mean()
                loss = pg - ent_coef * ent + vf_coef * vloss
                opt.zero_grad(); loss.backward()
                nn.utils.clip_grad_norm_(
                    list(actor.parameters()) + list(critic.parameters()), max_grad)
                opt.step()
                pg_log.append(float(pg)); v_log.append(float(vloss))
                ent_log.append(float(ent))
            else:
                idx = np.arange(n_steps); np.random.shuffle(idx)
                for s in range(0, n_steps, mb_steps):
                    mb = torch.as_tensor(idx[s:s + mb_steps], device=DEVICE)
                    dist = actor.dist(obs_t[mb])
                    new_logp = dist.log_prob(act_t[mb]).sum(-1)
                    ratio = (new_logp - logp_t[mb]).exp()
                    A = adv_t[mb].unsqueeze(-1).expand_as(ratio)
                    A = (A - A.mean()) / (A.std() + 1e-8)
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
                    pg_log.append(float(pg)); v_log.append(float(vloss))
                    ent_log.append(float(ent))

        ev = 1.0 - np.var(ret - B_val) / (np.var(ret) + 1e-8)
        mpg = float(np.mean(pg_log)) if pg_log else float("nan")
        mvl = float(np.mean(v_log)) if v_log else float("nan")
        ment = float(np.mean(ent_log)) if ent_log else float("nan")
        if progress is not None:
            progress.iteration_stats(it, B_power.mean(), B_rew.mean(),
                                     mvl if v_log else None, ev)
        hist["reward"].append(float(B_rew.mean()))
        hist["raw_reward"].append(float(np.nanmean(B_raw)))
        hist["power_mw"].append(float(B_power.mean()))
        # 逐步归一化再平均（而非用均值 u），回合中途换来流时才正确
        pnorm = float(np.mean(B_power * 1e3 / np.maximum(B_u, 1e-6) ** 3))
        hist["power_norm"].append(pnorm)
        hist["u_inf"].append(float(B_u.mean()))
        hist["explained_var"].append(float(ev))
        hist["pg"].append(mpg)
        hist["vloss"].append(mvl)
        hist["episodes"].append(int(smp.episode - ep_at_start))
        print(f"[{tag}] it {it}/{iters}  steps {global_step}  "
              f"rew(1step) {B_rew.mean():+.4f}  raw {np.nanmean(B_raw):.3f}  "
              f"farm_power {B_power.mean():.2f}MW  "
              f"P/u^3 {pnorm:.3f}  u {B_u.mean():.2f}  ep+{smp.episode-ep_at_start}  "
              f"pg {mpg:+.4f}  vloss {mvl:.4f}  "
              f"ent {ment:.3f}  expl_var {ev:.3f}", flush=True)

        # 定期保存 checkpoint
        if save_interval > 0 and it % save_interval == 0:
            os.makedirs(CKPT_DIR, exist_ok=True)
            safe = env_id.replace("/", "_")
            ckpt_path = os.path.join(CKPT_DIR, f"mappo_fastfarm_{safe}_{tag}_iter{it}.pt")
            torch.save({"actor": actor.state_dict(),
                        "critic": critic.state_dict(),
                        "optimizer": opt.state_dict(),
                        "obs_mean": obs_rms.mean, "obs_var": obs_rms.var,
                        "obs_count": obs_rms.count,
                        "ret_mean": ret_rms.mean, "ret_var": ret_rms.var,
                        "ret_count": ret_rms.count,
                        "obs_dim": smp.obs_dim, "act_dim": smp.act_dim,
                        "state_dim": smp.state_dim, "obs_keys": smp.obs_keys,
                        "obs_duty": smp.obs_duty, "duty_penalty": smp.duty_penalty,
                        "recurrent": recurrent, "env_id": env_id,
                        "iteration": it, "history": hist,
                        **({"training_progress": {"step": progress.step, "agent_step": progress.agent_step}}
                           if progress is not None else {})}, ckpt_path)
            print(f"[{tag}] 定期保存 iter {it} -> {ckpt_path}", flush=True)

    smp.close()
    hist["shaper_stats"] = smp.shaper.stats() if hasattr(smp.shaper, "stats") else {}
    hist["n_spawns"] = smp.n_spawns

    os.makedirs(CKPT_DIR, exist_ok=True)
    safe = env_id.replace("/", "_")
    model_path = os.path.join(CKPT_DIR, f"mappo_fastfarm_{safe}_{tag}.pt")
    torch.save({"actor": actor.state_dict(),
                "obs_mean": obs_rms.mean, "obs_var": obs_rms.var,
                "obs_dim": smp.obs_dim, "act_dim": smp.act_dim,
                "state_dim": smp.state_dim, "obs_keys": smp.obs_keys,
                "obs_duty": smp.obs_duty, "duty_penalty": smp.duty_penalty,
                "recurrent": recurrent, "env_id": env_id}, model_path)
    print(f"[{tag}] saved -> {model_path}", flush=True)

    _dump_hist(hist, env_id, tag)
    _plot_curves(hist, env_id, tag)
    if progress is not None:
        progress.progress("done", iters)
        progress.close()
    return hist


def _dump_hist(hist, env_id, tag):
    """把训练历史落 JSON，供 compare_static_dynamic.py 叠加对比。"""
    import json
    os.makedirs(RUNS_DIR, exist_ok=True)
    safe = env_id.replace("/", "_")
    out = os.path.join(RUNS_DIR, f"fastfarm_{safe}_{tag}_hist.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"env_id": env_id, "tag": tag, "hist": hist}, f)
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

    # ---- 阶段 3：回合结构 ----
    ap.add_argument("--episode-steps", type=int, default=128,
                    help="每回合的控制步数。>0 时每回合重起一个 FAST.Farm 进程"
                         "(约 25 s)并按 --wind-sched 换来流;0 = 退回旧口径的"
                         "单长回合(done 永不触发,只为和历史结果对照)")
    ap.add_argument("--warmup-steps", type=int, default=8,
                    help="每次 spawn 后先用零动作空转的步数,这些步**不进训练数据"
                         "也不进统计** —— 挡住尾流建立瞬态(旧口径下它漏进第 1 轮,"
                         "把功率虚高到 3.38 MW)")
    ap.add_argument("--wind-sched", default=None,
                    help="逐回合来流调度:pin:U / preset:NAME / cycle:U1,U2,... / "
                         "weibull[:SEED] / none。不给则由 --wind-speed 推出 pin:U,"
                         "两者都没有就是 none")
    ap.add_argument("--no-purge", action="store_true",
                    help="回合结束后**不**删 __simul__ 下的算例目录(默认删)。"
                         "每回合一个 ~37 MB,长训练不删会撑爆磁盘")

    # ---- 阶段 3：奖励整形器 ----
    ap.add_argument("--reward", default="level", choices=REWARD_CHOICES,
                    help="level=直接用已归一化的 P/u^3(默认);"
                         "centered=减 EMA 基线;reference=相对零偏航基线的百分比;"
                         "step=旧的 StepPercentage(逐步相对差分,只为对照)")
    ap.add_argument("--reward-ref", type=float, default=None,
                    help="--reward reference 用的基线 r0,来自匹配来流下的零偏航"
                         "实测(先跑 --policy zero 拿 raw)")
    ap.add_argument("--reward-alpha", type=float, default=0.01,
                    help="--reward centered 的 EMA 更新率。要比策略改变功率的"
                         "时间尺度慢,否则基线会把增益一起吃掉")
    ap.add_argument("--load-coef", type=float, default=1.0,
                    help="载荷惩罚权重。实测 mean(P/u^3)=3.33、mean|load|=0.394 "
                         "⇒ 等权约需 8.4;默认 1.0 时载荷项只有功率项的 1/8")

    # ---- D5：把执行机构约束接入学习回路（默认关，关闭时复现旧行为）----
    ap.add_argument("--obs-duty", action="store_true",
                    help="obs 追加占空比余量列，让策略看得见自己的动作会不会被清零")
    ap.add_argument("--duty-penalty", type=float, default=0.0,
                    help="动作被占空比清零时按被吃掉的幅度给负奖励的权重 λ（0=关）")

    # ---- 定期保存与恢复 ----
    ap.add_argument("--save-interval", type=int, default=0,
                    help="每 N 轮保存一次 checkpoint（0=关闭，只在最后保存）")
    ap.add_argument("--resume-from", type=str, default=None,
                    help="从指定 checkpoint 恢复训练（路径）")

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
