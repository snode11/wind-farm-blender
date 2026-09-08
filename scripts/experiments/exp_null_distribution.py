"""对照实验 B：抽签噪声的零假设分布。

问题：日报里"静态 5.686 MW vs 动态 2.337 MW = 2.43×"是两次单独训练的结果，
每次训练只 reset 一次 ⇒ 只抽一次风况。要判断 2.43 是否说明了什么，
必须先知道**同一后端内部**、仅凭 reset 抽签，两次跑出来的比值能有多大。

做法：FLORIS 零偏航（不含策略，排除算法影响），N 次独立 reset，
记录自由来流 u、来流方向、全场功率。然后看 |log 比值| 的分布：
    P(两次独立抽签的功率比 >= 2.43) = ?
若这个概率不小，则 2.43 这个观测值对"后端差异"没有证据力。

同时做方差分解：功率的对数方差里，风速贡献多少、风向贡献多少。

    "C:/Users/s1155/.conda/envs/wfcrl/python.exe" exp_null_distribution.py
"""
import os
import json

import numpy as np

from wfrl.fastfarm_driver import FastFarmDriver

from wfrl import paths

N = 200
SETTLE = 3            # FLORIS 稳态，3 步足够
OBSERVED_RATIO = 5.686 / 2.337


def main():
    drv = FastFarmDriver("Dec_Turb3_Row1_Floris", max_steps=SETTLE + 2)
    u, wd, tot = [], [], []
    for k in range(N):
        m = drv.reset()                                  # 不钉风 = benchmark 默认行为
        ff = drv.env.mdp.interface.fi.floris.flow_field
        u.append(float(np.ravel(ff.wind_speeds)[0]))
        wd.append(float(np.ravel(ff.wind_directions)[0]))
        for _ in range(SETTLE):
            m = drv.step(np.zeros(drv.n))
        tot.append(float(m["power"].sum()))
        if (k + 1) % 50 == 0:
            print(f"  {k+1}/{N} …", flush=True)
    drv.close()

    u = np.array(u); wd = np.array(wd); P = np.array(tot)

    print(f"\nN={N} 次独立 reset（FLORIS，零偏航，无策略）")
    print(f"  自由来流 u : {u.min():.3f} – {u.max():.3f} m/s  (均值 {u.mean():.3f})")
    print(f"  来流方向   : {wd.min():.2f} – {wd.max():.2f} °   (均值 {wd.mean():.2f})")
    print(f"  全场功率   : {P.min():.3f} – {P.max():.3f} MW  (均值 {P.mean():.3f}, "
          f"中位 {np.median(P):.3f})")
    print(f"  极差比     : {P.max()/P.min():.2f}×")

    # 两次独立抽签的比值分布（所有有序对）
    R = P[:, None] / P[None, :]
    R = R[~np.eye(N, dtype=bool)]
    for q in (50, 75, 90, 95, 99):
        print(f"  比值 P{q:<2d} = {np.percentile(R, q):.3f}×")
    p_ge = float((R >= OBSERVED_RATIO).mean())
    print(f"\n  P(随机两次的比值 >= 观测的 {OBSERVED_RATIO:.3f}×) = {p_ge:.3f}")

    # 方差分解：log P ~ 3 log u 能解释多少
    lp = np.log(P)
    b, a = np.polyfit(np.log(u), lp, 1)
    resid = lp - (a + b * np.log(u))
    r2_u = 1 - resid.var() / lp.var()
    # 剩余部分与风向偏离 270° 的关系
    dev = np.abs(((wd - 270 + 180) % 360) - 180)
    r2_wd = 1 - (resid - np.polyval(np.polyfit(dev, resid, 2), dev)).var() / resid.var()
    print(f"\n  log P 对 log u 的斜率 = {b:.2f}（理论 3，因尾流亏损随 u 变化而偏离）")
    print(f"  风速解释 log P 方差的 {100*r2_u:.1f}%；"
          f"剩余方差中风向偏角再解释 {100*r2_wd:.1f}%")

    json.dump({
        "n": N, "u": u.tolist(), "wind_direction": wd.tolist(),
        "power_total_MW": P.tolist(), "observed_ratio": OBSERVED_RATIO,
        "p_ratio_ge_observed": p_ge,
        "ratio_percentiles": {str(q): float(np.percentile(R, q))
                              for q in (50, 75, 90, 95, 99)},
        "r2_windspeed": float(r2_u), "r2_winddir_of_residual": float(r2_wd),
    }, open(os.path.join(paths.RUNS, "null_distribution.json"), "w"), indent=2)
    print("\n-> runs/null_distribution.json")


if __name__ == "__main__":
    main()
