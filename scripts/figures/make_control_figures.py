"""对照实验作图：把"2.43x 是抽签噪声"这件事画清楚。

图 1 (fig_control_null.png)：零假设分布
    FLORIS 零偏航、无策略、200 次独立 reset 的全场功率直方图，
    标出原先当作结论的两个观测值 5.686 / 2.337 MW —— 它们都落在同一个
    单后端分布内部。右图是任取两次的比值分布，标出 2.43x 的分位。

图 2 (fig_control_matched.png)：钉死来流后的两后端对照
    同一组 u_inf 下零偏航的全场功率（原始 MW 与归一化 P/u^3）。

    "C:/Users/s1155/.conda/envs/wfcrl/python.exe" make_control_figures.py
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from wfrl import paths

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

FIGS = paths.FIGS
A_OBS, B_OBS = 5.686, 2.337          # 日报里被当作"静态 vs 动态"的两个数


def fig_null():
    d = json.load(open(os.path.join(paths.RUNS, "null_distribution.json")))
    P = np.array(d["power_total_MW"])
    ratio = d["observed_ratio"]

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.0))

    ax[0].hist(P, bins=32, color="#4878a8", alpha=0.85, edgecolor="white")
    ymax = ax[0].get_ylim()[1]
    ax[0].set_ylim(0, ymax * 1.22)
    for v, c, lab, side in ((A_OBS, "#aa2828", f"原“静态”\n{A_OBS:.2f} MW", "left"),
                            (B_OBS, "#1e6e46", f"原“动态”\n{B_OBS:.2f} MW", "right")):
        ax[0].axvline(v, color=c, lw=2.2, ls="--")
        ax[0].annotate(lab, (v, ymax * 1.14), color=c, fontsize=9, ha=side,
                       xytext=(6 if side == "left" else -6, 0),
                       textcoords="offset points", va="center")
    ax[0].set_xlabel("全场功率 (MW)")
    ax[0].set_ylabel("频数")
    ax[0].set_title("单一后端(FLORIS)、零偏航、无策略\n"
                    f"仅 reset 抽签 {d['n']} 次的功率分布", fontsize=10)
    ax[0].grid(alpha=0.25)

    # 观测量是"两次跑的功率差了 2.43 倍"，事先并不知道哪边大 ⇒ 零假设统计量
    # 取 max/min（而不是有序比），否则概率会被少算一半。
    R = P[:, None] / P[None, :]
    R = R[~np.eye(len(P), dtype=bool)]
    R = np.maximum(R, 1.0 / R)
    p_ge = float((R >= ratio).mean())
    ax[1].hist(R, bins=np.linspace(1, 8, 40), color="#8a8a8a", alpha=0.85,
               edgecolor="white")
    ax[1].axvline(ratio, color="#aa2828", lw=2.4)
    ax[1].text(ratio, ax[1].get_ylim()[1] * 0.85,
               f"  观测比值 {ratio:.2f}×\n  P(纯抽签 ≥ 此值) = {p_ge:.2f}",
               color="#aa2828", fontsize=9.5, va="top")
    ax[1].set_xlabel("任取两次抽签的功率比值 (大/小)")
    print(f"   零假设: P(max/min >= {ratio:.3f}) = {p_ge:.3f}  "
          f"中位比值 {np.median(R):.3f}  P90 {np.percentile(R,90):.3f}")
    ax[1].set_ylabel("频数")
    ax[1].set_title("比值的零假设分布（同后端、同算法）", fontsize=10)
    ax[1].grid(alpha=0.25)

    fig.tight_layout()
    out = os.path.join(FIGS, "fig_control_null.png")
    fig.savefig(out, dpi=150)
    print(f"-> {out}")


def fig_matched():
    d = json.load(open(os.path.join(paths.RUNS, "matched_inflow.json")))
    fl = [r for r in d["rows"] if r["backend"] == "floris"]
    ff = [r for r in d["rows"] if r["backend"] == "fastfarm"]
    if not ff:
        print("!! matched_inflow.json 里还没有 fastfarm 结果，跳过图 2")
        return

    u_fl = [r["u_inf"] for r in fl]; p_fl = [r["power_total_MW"] for r in fl]
    u_ff = [r["u_inf"] for r in ff]; p_ff = [r["power_total_MW"] for r in ff]
    n_fl = [r["P_over_u3"] for r in fl]; n_ff = [r["P_over_u3"] for r in ff]

    fig, ax = plt.subplots(1, 3, figsize=(15.5, 4.1))
    ax[0].plot(u_fl, p_fl, "-o", color="#aa2828", label="FLORIS 稳态")
    ax[0].plot(u_ff, p_ff, "-s", color="#1e6e46", label="FAST.Farm 气弹")
    ax[0].set_xlim(min(u_fl) - 0.35, max(u_fl) + 0.35)
    ax[0].set_xlabel(r"钉死的自由来流 $u_\infty$ (m/s)")
    ax[0].set_ylabel("全场功率 (MW)")
    ax[0].set_title("同一来流、零偏航：两后端功率", fontsize=10)
    ax[0].legend(); ax[0].grid(alpha=0.25)

    ax[1].plot(u_fl, n_fl, "-o", color="#aa2828", label="FLORIS 稳态")
    ax[1].plot(u_ff, n_ff, "-s", color="#1e6e46", label="FAST.Farm 气弹")
    ax[1].set_xlabel(r"钉死的自由来流 $u_\infty$ (m/s)")
    ax[1].set_ylabel(r"$P/u_\infty^3$  [kW/(m/s)$^3$]")
    ax[1].set_title("归一化功率（消掉 $u^3$ 的量级效应）", fontsize=10)
    ax[1].legend(); ax[1].grid(alpha=0.25)

    for U, a, b in zip(u_fl, p_fl, p_ff):
        ax[0].annotate(f"{a/max(b,1e-9):.2f}×", (U, (a + b) / 2), fontsize=8.5,
                       color="#555555", ha="center")

    # (c) 真正的静/动差异不在功率量级，而在**尾流建立的时延与分级**：
    #     FLORIS 稳态模型瞬间建立尾流，FAST.Farm 分两级建立。
    #     横轴用 step 而非仿真时间：t_init=25 s、dt=3 s ⇒ start_iter=9，reset 内预跑
    #     10 步，序列起点已对应仿真第 30 s，标 step 可避免绝对时刻的歧义。
    try:
        s = json.load(open(os.path.join(paths.RUNS, "settle_check.json")))
    except FileNotFoundError:
        s = None
    if s is not None:
        H = np.array(s["power_series"]); U = s["u_inf"]
        t = np.arange(len(H))                              # 控制步；dt = 3 s
        fl8 = next(r for r in fl if abs(r["u_inf"] - U) < 1e-6)
        cols = ("#4878a8", "#1e6e46", "#aa2828")
        # T3 画虚线：step 15-45 段 T2/T3 逐位重合（差 0.05%），实线会互相盖住，
        # 而这个重合正是"第一次跌落 = T1 尾流抵达"讲不通的依据，必须看得见。
        for i in range(H.shape[1]):
            kw = {"dashes": (4, 3)} if i == 2 else {}
            ax[2].plot(t, H[:, i], color=cols[i], lw=1.7,
                       label=f"T{i+1} · FAST.Farm", **kw)
            ax[2].axhline(fl8["power_per_turbine"][i], color=cols[i], lw=1.2,
                          ls=":", alpha=0.95)
        ax[2].plot([], [], color="#666666", ls=":", lw=1.2,
                   label="对应的 FLORIS 稳态值")
        # step 15：T2 这一跌与 T1 尾流抵达一致（504/8 = 63 s，实测 75 s）；T3 同步跌落
        # 讲不通（抵 T3 需 1008/8 = 126 s ≈ step 32），故只对 T2 写机理，T3 标待查。
        for tm, txt, yf in ((15, "$T_2$：$T_1$ 尾流抵达\nstep 15（$T_3$ 同步跌落待查）", 0.62),
                            (45, "$T_3$：$T_2$ 尾流抵达\nstep 45", 0.36)):
            ax[2].axvline(tm, color="#888888", lw=1.0, ls="--")
            ax[2].annotate(txt, (tm, H.max() * yf), fontsize=8.2, color="#555555",
                           xytext=(14, 0), textcoords="offset points", va="center")
        ax[2].set_xlabel("控制步 (step，$dt$ = 3 s)")
        ax[2].set_ylabel("单机功率 (MW)")
        ax[2].set_title(f"$u_\\infty$={U:.0f} m/s 零偏航的尾流建立过程（分两级）\n"
                        "FLORIS 在 $t=0$ 即为稳态值，无时延", fontsize=10)
        ax[2].legend(fontsize=8, loc="center right"); ax[2].grid(alpha=0.25)

    fig.tight_layout()
    out = os.path.join(FIGS, "fig_control_matched.png")
    fig.savefig(out, dpi=150)
    print(f"-> {out}")


if __name__ == "__main__":
    os.makedirs(FIGS, exist_ok=True)
    fig_null()
    fig_matched()
