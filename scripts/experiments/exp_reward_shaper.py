"""奖励整形器的来流污染判据 —— 纯数值，不起 FAST.Farm。

## 要回答的问题

README §6 阻塞项 3:"`StepPercentage` 在变风况训练之前必须先换掉"。换成什么、
凭什么说新的更好,需要一个判据。这里用**环境真实的奖励管线**做解析复现:

    multiagent_env.py:220-227   raw = mean(P_i)·1e3 / u_up³ − load_coef·mean|load|
    multiagent_env.py:228       self._state = next_state     ← 在算完 reward **之后**

所以分母是**上一控制步**的上游测速。功率本身 ∝ u_t³,于是

    raw_t = 3.3 · η_t · (u_t / u_{t−1})³

其中 3.3 是实测的 mean(P/u³) 量级、η_t 是**策略能改变的那部分效率**(偏航对准、
尾流转向带来的增益)。定常来流下 (u_t/u_{t−1})³ = 1,raw 只反映 η —— 这正是
README 说的"u³ 精确抵消";来流一变,那个比值就是纯粹的来流噪声。

## 判据

两个情景,各自减掉"什么都没发生"的对照,再按 γ 折现求和:

  - **来流情景**:η 恒定,u 从 8 线性升到 8.5(+6%)。真实收益 **0** —— 风变大不是
    策略的功劳。
  - **策略情景**:u 恒定,η 阶跃 +5% 并**保持**。这是策略真正学到东西的样子。

实测下来"污染比 = |Σ来流| / |Σ策略|"对所有整形器都是 ≈0.04 —— 因为两条路径经过
**同一个**非线性,整形器同比例地缩放二者,这个指标不区分谁好谁坏。区分它们的是
另外两件事:

  1. **持续增益换不换得来持续奖励。** 差分型的 `step` 只在 η 改变的**那一步**
     给一次奖励,之后归零 —— 策略把偏航保持在更好的位置,得到的折现回报比
     `level` 小两个量级。
  2. **信噪比。** 湍流让 u 逐步抖动,而分母滞后一步 ⇒ 每步都注入
     (u_t/u_{t−1})³ − 1 的纯噪声。用同一串噪声去量各整形器的 std,再拿持续增益
     去比,得到每步 SNR。

    python scripts/experiments/exp_reward_shaper.py
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfrl import paths                                    # noqa: E402
from wfrl.rewards import make_shaper                      # noqa: E402

RAW_BASE = 3.3          # 实测 mean(P/u^3) 量级（8 m/s、零偏航、三机组）
GAMMA = 0.99
T = 240
T_EVENT = 60            # 事件发生的步
RAMP = 10               # 来流爬升用多少步
U0, U1 = 8.0, 8.5       # +6.25%
ETA_GAIN = 0.05         # 策略带来的持续效率提升
TI = 0.09               # 湍流强度：90m_08mps.bts 实测 TI≈9%


def series(kind):
    """(u_t, eta_t)。kind ∈ {control, wind, policy}。"""
    u = np.full(T, U0)
    eta = np.ones(T)
    if kind == "wind":
        for k in range(RAMP):
            u[T_EVENT + k] = U0 + (U1 - U0) * (k + 1) / RAMP
        u[T_EVENT + RAMP:] = U1
    elif kind == "policy":
        eta[T_EVENT:] = 1.0 + ETA_GAIN
    return u, eta


def raw_reward(u, eta):
    """环境交给整形器的量：分母滞后一个控制步（multiagent_env.py:226 vs 228）。"""
    u_prev = np.concatenate([[u[0]], u[:-1]])
    return RAW_BASE * eta * (u / u_prev) ** 3


def shaped_series(name, u, eta, **kw):
    sh = make_shaper(name, **kw)
    return np.array([sh(r) for r in raw_reward(u, eta)], dtype=float)


def discounted(x):
    return float(np.sum(x * GAMMA ** np.arange(len(x))))


def main():
    specs = [("level", {}),
             ("centered", {"alpha": 0.01}),
             ("centered", {"alpha": 0.05}),
             ("reference", {"reference": RAW_BASE}),
             ("step", {})]

    u_c, e_c = series("control")
    # 湍流噪声：同一串抽样喂给所有整形器，差别才归因得到整形器本身
    u_noisy = u_c * (1.0 + TI * np.random.default_rng(0).standard_normal(T))
    rows = []
    for name, kw in specs:
        base = shaped_series(name, u_c, e_c, **kw)
        d_wind = shaped_series(name, *series("wind"), **kw) - base
        d_pol = shaped_series(name, *series("policy"), **kw) - base
        sw, sp = discounted(d_wind), discounted(d_pol)
        # 事件之后 50 步的平均增量：持续的改进有没有换来持续的奖励
        tail = slice(T_EVENT + RAMP + 10, T_EVENT + RAMP + 60)
        sig = float(d_pol[tail].mean())
        noise = float(shaped_series(name, u_noisy, e_c, **kw).std())
        rows.append({
            "shaper": name + (f"(α={kw['alpha']})" if "alpha" in kw else ""),
            "sum_wind": sw, "sum_policy": sp,
            "contam": abs(sw) / max(abs(sp), 1e-12),
            "tail_wind": float(d_wind[tail].mean()),
            "tail_policy": sig,
            "noise_std": noise,
            "snr": abs(sig) / max(noise, 1e-12),
        })

    w = max(len(r["shaper"]) for r in rows)
    print(f"\n  γ={GAMMA}  T={T}  事件在第 {T_EVENT} 步  "
          f"来流 {U0}→{U1} m/s（{RAMP} 步爬升）  策略 η +{ETA_GAIN:.0%} 并保持  "
          f"噪声 TI={TI:.0%}\n")
    print(f"  {'整形器':<{w}}  {'Σγᵗ来流':>9}  {'Σγᵗ策略':>9}  {'污染比':>7}  "
          f"{'尾段策略':>9}  {'噪声std':>8}  {'每步SNR':>8}")
    print("  " + "-" * (w + 62))
    for r in rows:
        print(f"  {r['shaper']:<{w}}  {r['sum_wind']:>9.3f}  "
              f"{r['sum_policy']:>9.3f}  {r['contam']:>7.3f}  "
              f"{r['tail_policy']:>9.4f}  {r['noise_std']:>8.4f}  "
              f"{r['snr']:>8.4f}")

    out = os.path.join(paths.RUNS, "reward_shaper_sensitivity.json")
    os.makedirs(paths.RUNS, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"gamma": GAMMA, "T": T, "t_event": T_EVENT, "ramp": RAMP,
                   "u": [U0, U1], "eta_gain": ETA_GAIN, "ti": TI, "rows": rows},
                  f, ensure_ascii=False, indent=1)
    print(f"\n  → {out}")

    ok = True

    def chk(label, cond, detail=""):
        nonlocal ok
        ok = bool(cond) and ok
        print(f"  {'v' if cond else 'X'} {label}"
              + (f" — {detail}" if detail else ""), flush=True)

    print()
    by = {r["shaper"]: r for r in rows}
    st, lv = by["step"], by["level"]
    contam = [r["contam"] for r in rows]
    chk("污染比不是判据：各整形器落在同一量级",
        max(contam) / max(min(contam), 1e-12) < 1.5,
        f"{min(contam):.3f} ~ {max(contam):.3f}")
    chk("step 对持续改进只给一次性奖励（尾段增量≈0）",
        abs(st["tail_policy"]) < 0.02 * abs(lv["tail_policy"]),
        f"step {st['tail_policy']:.5f} vs level {lv['tail_policy']:.5f}")
    chk("step 的折现回报比 level 小两个量级",
        abs(st["sum_policy"]) < 0.01 * abs(lv["sum_policy"]),
        f"{st['sum_policy']:.3f} vs {lv['sum_policy']:.3f}")
    chk("level 把持续改进变成持续奖励",
        lv["tail_policy"] > 0.9 * ETA_GAIN * RAW_BASE,
        f"{lv['tail_policy']:.4f}（理论 {ETA_GAIN*RAW_BASE:.4f}）")
    chk("level 尾段不残留来流增益（u³ 抵消干净）",
        abs(lv["tail_wind"]) < 1e-9, f"{lv['tail_wind']:.3e}")
    chk("湍流下 level 的每步 SNR 高于 step 至少 10 倍",
        lv["snr"] > 10 * st["snr"],
        f"level {lv['snr']:.4f} vs step {st['snr']:.4f}")
    print("REWARD_SHAPER_OK" if ok else "REWARD_SHAPER_FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
