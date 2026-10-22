"""在**真环境**上验证占空比清零，并量它对可达偏航的封顶。

`probe_duty_dynamics.py` 是单机组闭式递推；这里用 wfcrl 的 FLORIS 后端真跑。
第一版对拍**失败**，追下去发现两处环境行为与朴素递推不同，都值得写下来：

1 **acc 累加的是动作空间裁剪后的量，不含状态边界。** mdp.py:299-318 先
  `clip(Δ, ±5°)` 累加进 `_actuation_accumulator`，再 `clip(state+Δ, ±40°)`
  写状态。偏航贴到 ±40° 之后指令不再改变状态，但 acc 照样涨 —— 也就是说
  饱和之后每一步都在为"没有发生的动作"付占空比。

2 **accumulator 对非末位 agent 是先读后写，逐机组不同步。**
  multiagent_env.py:246-249 在 `is_last()` 分支**之后**才把 mdp 的累计值回抄
  给当前 agent；而 mdp 的累计值只在 `is_last()` 那一支的 take_action 里更新。
  于是遍历序里第一个 agent 抄到的是"上一步 take_action 后"的值，末位 agent
  抄到的是"本步 take_action 后"的值 —— 前者比后者滞后一整步。

  后果：同样的指令序列，W1 会比 W3 多拿到一次执行机会。占空比这条约束在
  机组之间**不是等价的**，遍历序靠前的机组约束更松。这不是我们能改的上游
  行为，但策略共享参数、观测里又没有 duty，等于在学一个自己看不见且逐机组
  不一致的门控。

所以这里的模型不再写闭式，改成忠实复刻环境的那个循环（`EnvModel`），再逐
机组比对偏航轨迹。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from wfcrl import environments as envs                        # noqa: E402
from wfrl.safety import DUTY_LIMIT                            # noqa: E402

PASS, FAIL = [], []


def chk(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class EnvModel:
    """逐位复刻 multiagent_env.step + mdp.get_controlled_state_transition。

    要点全在时序上：
      · 每个 agent 自己的 k 自增在检查**之前**（:179）
      · 检查用的是该 agent **自己存的** acc（:198），不是 mdp 的当前值
      · acc 回抄发生在 take_action **之后**（:246），所以非末位 agent 滞后一步
      · acc 累加的是动作空间裁剪后的量，状态边界吃掉的部分照算（mdp.py:318）
    """

    def __init__(self, n, dt, rate, step_bound, state_bound):
        self.n, self.dt, self.rate = n, float(dt), float(rate)
        self.sb, self.stb = float(step_bound), float(state_bound)
        self.yaw = np.zeros(n)
        self.mdp_acc = np.zeros(n)        # mdp._actuation_accumulator
        self.agent_acc = np.zeros(n)      # accumulated_actions[agent]
        self.k = np.zeros(n, int)

    def step(self, req):
        """一轮 n 个 agent 的调用。返回 (本步各机组执行量, 各机组是否被清零)。"""
        applied, zeroed = np.zeros(self.n), np.zeros(self.n, bool)
        for i in range(self.n):
            self.k[i] += 1
            frac = self.agent_acc[i] / self.rate / self.k[i] / self.dt
            a = 0.0 if frac >= DUTY_LIMIT else float(
                np.clip(req[i], -self.sb, self.sb))
            zeroed[i], applied[i] = (a == 0.0 and req[i] != 0.0), a
            if i == self.n - 1:                    # is_last ⇒ take_action
                self.mdp_acc += np.abs(applied)    # 累加**动作空间裁剪后**的量
                self.yaw = np.clip(self.yaw + applied, -self.stb, self.stb)
            self.agent_acc[i] = self.mdp_acc[i]    # 回抄，在 take_action 之后
        return applied, zeroed


def main():
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    env = envs.make("Dec_Turb3_Row1_Floris")
    env.reset()
    u = env.unwrapped
    agents = list(env.possible_agents)
    n = len(agents)
    dt = float(u.farm_case.dt)
    rate = u.mdp.ACTUATORS_RATE["yaw"]
    hi = float(np.ravel(env.action_space(agents[0])["yaw"].high)[0])
    stb = float(np.ravel(u.mdp.state_space["yaw"].high)[0])
    sustain = DUTY_LIMIT * rate * dt
    print(f"env=Dec_Turb3_Row1_Floris  n={n}  dt={dt:g}s  rate={rate:g}°/s  "
          f"动作 ±{hi:g}°  状态 ±{stb:g}°")
    print(f"稳态可持续量 = 0.1×{rate:g}×{dt:g} = {sustain:g} °/步  "
          f"(动作空间的 {hi / sustain:.1f} 分之一)\n")

    mdl = EnvModel(n, dt, rate, hi, stb)
    req = np.full(n, hi)                       # 每步都请求满幅
    real, pred, zpred = [], [], []
    for _k in range(K):
        for _ag in env.agent_iter(max_iter=n):
            env.step({"yaw": np.array([hi], np.float32)})
        real.append([float(np.ravel(env.observe(a)["yaw"])[0]) for a in agents])
        _a, z = mdl.step(req)
        pred.append(mdl.yaw.copy())
        zpred.append(z.copy())
    real, pred, zpred = np.array(real), np.array(pred), np.array(zpred)

    dev = np.abs(real - pred).max()
    chk("忠实模型与环境的偏航轨迹逐机组、逐步一致", dev < 1e-4,
        f"{K} 步 × {n} 机组，最大偏差 {dev:.2e}°")

    nz = zpred.sum(axis=0)
    # 清零比例不是常数，由 动作幅度/可持续量 定：稳态下每 step_bound/sustain
    # 步才允许发一次满幅，故清零占比 ≈ 1 − sustain/step_bound。
    # FLORIS 算例 dt=60 ⇒ 可持续 1.8°，只比 ±5° 小 2.8 倍 ⇒ 约 64%；
    # FAST.Farm dt=3 ⇒ 可持续 0.09°，小 55.6 倍 ⇒ 约 98%。
    exp_frac = 1.0 - sustain / hi
    got_frac = nz.mean() / K
    chk("清零比例符合 1 − 可持续量/动作幅度", abs(got_frac - exp_frac) < 0.08,
        f"实测 {got_frac:.0%}，理论 {exp_frac:.0%}（dt={dt:g}s 下可持续 "
        f"{sustain:g}° vs 动作 ±{hi:g}°）；逐机组 "
        + ", ".join(f"{agents[i]}={nz[i]}/{K}" for i in range(n)))

    chk("占空比在机组间不等价（遍历序靠前的更松）", nz[0] < nz[-1],
        f"{agents[0]} 清零 {nz[0]} 次 < {agents[-1]} 清零 {nz[-1]} 次，"
        f"差 {nz[-1] - nz[0]} 次执行机会")

    fired = {agents[i]: [k + 1 for k in range(K) if not zpred[k, i]]
             for i in range(n)}
    # 生效间隔应当趋近 step_bound/sustain 步
    period = hi / sustain
    gaps = [g for v in fired.values() for g in np.diff(v)]
    med = float(np.median(gaps)) if gaps else 0.0
    chk("生效间隔趋近 动作幅度/可持续量 步", abs(med - period) <= 1.5,
        f"中位间隔 {med:.1f} 步，理论 {period:.1f} 步；"
        + "; ".join(f"{a}: {v[:6]}" for a, v in fired.items()))

    ceiling = sustain * K + hi
    chk("K 步可达偏航被封在 0.1·rate·dt·K + 首步免费 以内",
        real[-1].max() <= min(ceiling, stb) + 1e-6,
        f"实测末端 {real[-1].max():.2f}°，封顶 min({ceiling:.1f}, "
        f"状态边界 {stb:g})，名义 {hi * K:.0f}° 的 "
        f"{real[-1].max() / (hi * K):.1%}")

    # 饱和之后 acc 仍在涨 —— 为没有发生的动作付占空比
    if real[-1].max() >= stb - 1e-6:
        chk("偏航饱和后 acc 仍在累加（为未发生的动作付占空比）",
            mdl.mdp_acc.max() > stb,
            f"末端偏航 {real[-1].max():.0f}°，但 acc 已累到 "
            f"{mdl.mdp_acc.max():.0f}°")

    print(f"\n偏航轨迹 {agents[0]}（每 {max(1, K // 12)} 步）: " + " ".join(
        f"{real[i, 0]:.1f}" for i in range(0, K, max(1, K // 12))))
    print(f"偏航轨迹 {agents[-1]}（每 {max(1, K // 12)} 步）: " + " ".join(
        f"{real[i, -1]:.1f}" for i in range(0, K, max(1, K // 12))))

    print(f"\n{len(PASS)} 项通过, {len(FAIL)} 项失败")
    print("DUTY_ENV_OK" if not FAIL else "DUTY_ENV_FAIL: " + "; ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
