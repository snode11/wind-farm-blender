"""
真 MAPPO（集中式 critic）—— 自写轻量实现，FLORIS 后端，与 train_mappo.py 的
参数共享 IPPO 做对照。

与 IPPO 的唯一本质区别在 critic 的输入：
  - actor  π(a_i | o_i)      : 共享，只吃单台机组本地观测 o_i (3 维)     → 去中心化执行
  - critic V(s)              : 集中式，吃全局状态 s = 7 台 o_i 拼接 (21 维) → 训练时上帝视角

风场奖励是全局共享的（整场功率代理，7 台同值），所以用单个 V(s) 预测这份团队回报，
优势 A_t 每步一个标量、广播给 7 台各自的 (o_i, a_i) 做策略梯度。这就是教科书 MAPPO
（参数共享 + 集中式 critic + 团队奖励 CTDE）。

Usage:
    python mappo_central.py --timesteps 50000     # 与 IPPO 50k 同量级对照
    python mappo_central.py --eval-only
"""
import argparse
import os

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Normal
from pettingzoo.utils.conversions import aec_to_parallel

from wfcrl import environments as envs

from wfrl import paths

ENV_ID = "Dec_Ablaincourt_Floris"
MAX_STEPS = 200
CKPT_DIR = paths.CKPT
MODEL_PATH = os.path.join(CKPT_DIR, "mappo_central_ablaincourt.pt")

DEVICE = torch.device("cpu")   # 网络极小，CPU 即可；瓶颈是 FLORIS 求解


# ---------------------------------------------------------------------------
# 运行均值/方差（Welford），用于观测归一化
# ---------------------------------------------------------------------------
class RunningMeanStd:
    def __init__(self, shape):
        self.mean = np.zeros(shape, np.float64)
        self.var = np.ones(shape, np.float64)
        self.count = 1e-4

    def update(self, x):                       # x: (N, *shape)
        bm, bv, bc = x.mean(0), x.var(0), x.shape[0]
        d = bm - self.mean
        tot = self.count + bc
        self.mean += d * bc / tot
        M2 = self.var * self.count + bv * bc + d ** 2 * self.count * bc / tot
        self.var = M2 / tot
        self.count = tot

    def norm(self, x):
        return (x - self.mean) / np.sqrt(self.var + 1e-8)


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def mlp(sizes, act=nn.Tanh):
    layers = []
    for i in range(len(sizes) - 1):
        layers.append(nn.Linear(sizes[i], sizes[i + 1]))
        if i < len(sizes) - 2:
            layers.append(act())
    return nn.Sequential(*layers)


class Actor(nn.Module):
    def __init__(self, obs_dim, act_dim, hidden=(128, 128)):
        super().__init__()
        self.mu = mlp([obs_dim, *hidden, act_dim])
        self.log_std = nn.Parameter(-0.5 * torch.ones(act_dim))

    def dist(self, o):
        return Normal(self.mu(o), self.log_std.exp())


class Critic(nn.Module):
    def __init__(self, state_dim, hidden=(128, 128)):
        super().__init__()
        self.v = mlp([state_dim, *hidden, 1])

    def forward(self, s):
        return self.v(s).squeeze(-1)


# ---------------------------------------------------------------------------
# 环境采样器：直接驱动 PettingZoo parallel env（不需要 VecEnv）
# ---------------------------------------------------------------------------
class Sampler:
    def __init__(self):
        raw = envs.make(ENV_ID, controls=["yaw"], max_num_steps=MAX_STEPS)
        self.par = aec_to_parallel(raw)
        self.agents = self.par.possible_agents
        self.n = len(self.agents)
        self.obs_keys = list(self.par.observation_space(self.agents[0]).keys())
        self.obs_dim = len(self.obs_keys)                 # 3: yaw/ws/wd
        a = self.par.action_space(self.agents[0])["yaw"]
        self.act_dim = int(a.shape[0])                    # 1
        self.act_low, self.act_high = float(a.low[0]), float(a.high[0])
        self.state_dim = self.n * self.obs_dim            # 21
        self._obs = None

    def _pack(self, obs_dict):
        """{agent:{key:val}} -> (n, obs_dim) 本地观测矩阵。"""
        return np.stack([
            np.array([np.ravel(obs_dict[ag][k])[0] for k in self.obs_keys],
                     dtype=np.float32)
            for ag in self.agents
        ])

    def reset(self):
        obs_dict, _ = self.par.reset()
        self._obs = self._pack(obs_dict)
        return self._obs

    def step(self, actions):                              # actions: (n, act_dim)
        clipped = np.clip(actions, self.act_low, self.act_high)
        act_dict = {ag: {"yaw": clipped[i].astype(np.float32)}
                    for i, ag in enumerate(self.agents)}
        obs_dict, rew, term, trunc, _ = self.par.step(act_dict)
        self._obs = self._pack(obs_dict)
        r = float(np.ravel(rew[self.agents[0]])[0])       # 共享团队奖励，取一个即可
        done = bool(term[self.agents[0]] or trunc[self.agents[0]])
        if done:
            self._obs = self.reset()
        return self._obs, r, done


# ---------------------------------------------------------------------------
# GAE
# ---------------------------------------------------------------------------
def compute_gae(rews, vals, dones, last_v, gamma=0.99, lam=0.95):
    T = len(rews)
    adv = np.zeros(T, dtype=np.float32)
    gae = 0.0
    for t in reversed(range(T)):
        mask = 1.0 - dones[t]
        next_v = last_v if t == T - 1 else vals[t + 1]
        delta = rews[t] + gamma * next_v * mask - vals[t]
        gae = delta + gamma * lam * mask * gae
        adv[t] = gae
    ret = adv + vals
    return adv, ret


# ---------------------------------------------------------------------------
# 训练
# ---------------------------------------------------------------------------
def train(total_timesteps, n_steps=1024, n_epochs=10, mb_steps=256,
          gamma=0.99, lam=0.95, clip=0.2, lr=3e-4,
          ent_coef=0.005, vf_coef=0.5, max_grad=0.5, seed=0):
    torch.manual_seed(seed)
    smp = Sampler()
    actor = Actor(smp.obs_dim, smp.act_dim).to(DEVICE)
    critic = Critic(smp.state_dim).to(DEVICE)
    opt = torch.optim.Adam(list(actor.parameters()) + list(critic.parameters()),
                           lr=lr)
    obs_rms = RunningMeanStd((smp.obs_dim,))

    smp.reset()
    n = smp.n
    total_iters = max(1, total_timesteps // (n_steps * n))
    print(f"[mappo-c] {total_iters} iters × {n_steps} steps × {n} agents "
          f"= {total_iters * n_steps * n} agent-transitions")

    # 回报归一化（等价 VecNormalize(norm_reward=True)）：用折扣回报的运行 std 归一 reward，
    # 让 value 目标保持 O(1)、value loss 不爆炸；与 IPPO 对照口径一致。
    ret_rms = RunningMeanStd(())
    ret_acc = 0.0

    global_step = 0
    for it in range(1, total_iters + 1):
        # ---- 采样 ----
        B_obs = np.zeros((n_steps, n, smp.obs_dim), np.float32)
        B_act = np.zeros((n_steps, n, smp.act_dim), np.float32)
        B_logp = np.zeros((n_steps, n), np.float32)
        B_state = np.zeros((n_steps, smp.state_dim), np.float32)
        B_val = np.zeros(n_steps, np.float32)
        B_rew = np.zeros(n_steps, np.float32)
        B_done = np.zeros(n_steps, np.float32)

        for t in range(n_steps):
            raw_obs = smp._obs                              # (n, obs_dim)
            obs_n = obs_rms.norm(raw_obs).astype(np.float32)
            state_n = obs_n.reshape(-1)                     # (state_dim,)
            with torch.no_grad():
                ot = torch.as_tensor(obs_n, device=DEVICE)
                dist = actor.dist(ot)
                a = dist.sample()
                logp = dist.log_prob(a).sum(-1)
                v = critic(torch.as_tensor(state_n, device=DEVICE))
            a_np = a.cpu().numpy()
            _, r, done = smp.step(a_np)

            # 回报归一化：先累加折扣回报、更新 std，再用它归一当前 reward
            ret_acc = ret_acc * gamma + r
            ret_rms.update(np.array([ret_acc]))
            r_norm = r / np.sqrt(ret_rms.var + 1e-8)
            if done:
                ret_acc = 0.0

            B_obs[t], B_act[t] = obs_n, a_np
            B_logp[t] = logp.cpu().numpy()
            B_state[t], B_val[t] = state_n, float(v)
            B_rew[t], B_done[t] = r_norm, float(done)
            obs_rms.update(raw_obs)
            global_step += n

        # bootstrap 最后一步的 value
        with torch.no_grad():
            last_state = obs_rms.norm(smp._obs).astype(np.float32).reshape(-1)
            last_v = float(critic(torch.as_tensor(last_state, device=DEVICE)))
        adv, ret = compute_gae(B_rew, B_val, B_done, last_v, gamma, lam)

        # ---- 更新 ----
        obs_t = torch.as_tensor(B_obs, device=DEVICE)        # (T,n,obs)
        act_t = torch.as_tensor(B_act, device=DEVICE)        # (T,n,act)
        logp_t = torch.as_tensor(B_logp, device=DEVICE)      # (T,n)
        state_t = torch.as_tensor(B_state, device=DEVICE)    # (T,state)
        adv_t = torch.as_tensor(adv, device=DEVICE)          # (T,)
        ret_t = torch.as_tensor(ret, device=DEVICE)          # (T,)

        pg_log, v_log, ent_log = [], [], []
        idx = np.arange(n_steps)
        for _ in range(n_epochs):
            np.random.shuffle(idx)
            for s in range(0, n_steps, mb_steps):
                mb = idx[s:s + mb_steps]
                mbt = torch.as_tensor(mb, device=DEVICE)
                dist = actor.dist(obs_t[mbt])                # (b,n,act)
                new_logp = dist.log_prob(act_t[mbt]).sum(-1) # (b,n)
                ratio = (new_logp - logp_t[mbt]).exp()
                A = adv_t[mbt].unsqueeze(-1).expand_as(ratio)  # 广播给 n 台
                A = (A - A.mean()) / (A.std() + 1e-8)          # 优势归一化
                pg = -torch.min(ratio * A,
                                torch.clamp(ratio, 1 - clip, 1 + clip) * A).mean()
                ent = dist.entropy().sum(-1).mean()
                v = critic(state_t[mbt])
                vloss = ((v - ret_t[mbt]) ** 2).mean()
                loss = pg - ent_coef * ent + vf_coef * vloss
                opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(
                    list(actor.parameters()) + list(critic.parameters()), max_grad)
                opt.step()
                pg_log.append(float(pg)); v_log.append(float(vloss))
                ent_log.append(float(ent))

        # explained variance（critic 质量）
        ev = 1.0 - np.var(ret - B_val) / (np.var(ret) + 1e-8)
        print(f"[mappo-c] it {it}/{total_iters}  steps {global_step}  "
              f"ep_rew(1step avg) {B_rew.mean():.3f}  "
              f"pg {np.mean(pg_log):+.4f}  vloss {np.mean(v_log):.4f}  "
              f"ent {np.mean(ent_log):.3f}  explained_var {ev:.3f}")

    os.makedirs(CKPT_DIR, exist_ok=True)
    torch.save({"actor": actor.state_dict(), "critic": critic.state_dict(),
                "obs_mean": obs_rms.mean, "obs_var": obs_rms.var,
                "obs_dim": smp.obs_dim, "act_dim": smp.act_dim,
                "state_dim": smp.state_dim}, MODEL_PATH)
    print(f"[mappo-c] saved -> {MODEL_PATH}")
    evaluate(actor, obs_rms, smp)
    return actor, critic, obs_rms


def evaluate(actor, obs_rms, smp=None, n_episodes=2):
    if smp is None:
        smp = Sampler()
    for ep in range(n_episodes):
        obs = smp.reset()
        ep_ret, done = 0.0, False
        while not done:
            obs_n = obs_rms.norm(obs).astype(np.float32)
            with torch.no_grad():
                mu = actor.mu(torch.as_tensor(obs_n, device=DEVICE))  # 确定性
            obs, r, done = smp.step(mu.cpu().numpy())
            ep_ret += r
        print(f"[eval] ep {ep + 1}: return = {ep_ret:.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timesteps", type=int, default=50000)
    ap.add_argument("--eval-only", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if args.eval_only:
        smp = Sampler()
        actor = Actor(smp.obs_dim, smp.act_dim)
        ckpt = torch.load(MODEL_PATH, map_location=DEVICE, weights_only=False)
        actor.load_state_dict(ckpt["actor"])
        rms = RunningMeanStd((smp.obs_dim,))
        rms.mean, rms.var = ckpt["obs_mean"], ckpt["obs_var"]
        evaluate(actor, rms, smp)
        return
    train(args.timesteps, seed=args.seed)


if __name__ == "__main__":
    main()
