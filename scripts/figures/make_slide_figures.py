"""为 introduction_slides.tex 生成图件（全部来自真实计算，不含示意性伪数据）。

产出到 figs/：
  fig_wake_steering.png   FLORIS 水平尾流切面：贪心 vs 尾流偏转
  fig_yaw_power.png       偏航-功率权衡：单机组扫描 + (g1,g2) 联合功率曲面
  fig_reward_design.png   奖励设计：u^3 归一化的作用 + 功率/载荷帕累托权衡
  fig_wind_scenarios.png  风况模拟：Weibull x Normal 采样(Sc.II) + 回合内时序(Sc.III)
  fig_training.png        静态 vs 动态 vs GRU 训练曲线（读 runs/*_hist.json）

用法：
    "C:/Users/s1155/.conda/envs/wfcrl/python.exe" make_slide_figures.py
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from wfrl import paths

FIGS = paths.FIGS
RUNS = "runs"
ENV_ID = "Dec_Turb3_Row1_Floris"

plt.rcParams.update({
    "font.size": 11, "axes.grid": True, "grid.alpha": 0.3,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 150,
})


# ---------------------------------------------------------------------------
def _floris():
    from wfcrl import environments as envs
    from wfrl.viz.field import get_floris
    return get_floris(envs.make(ENV_ID))


def _yaw(fi, angles):
    """(n_turb,) -> FLORIS (1,1,n_turb)。"""
    return np.asarray(angles, float).reshape(1, 1, -1)


def _powers(fi, angles):
    """给定偏航角，返回各机组功率 (MW)。"""
    fi.calculate_wake(yaw_angles=_yaw(fi, angles))
    return np.ravel(fi.get_turbine_powers()) / 1e6


def _load_proxy(fi, angles, n_pts=9):
    """FLORIS 载荷代理（基准式(5)的简化）：每台机组 TI + 转子面速度标准差。

    式(5) 用 9x M 个转子平面网格点上的湍流强度与 u,v,w 速度标准差之和；
    FLORIS 3.5 的点采样只回传 u，故这里用 TI + sigma(u)，趋势一致。
    """
    fi.calculate_wake(yaw_angles=_yaw(fi, angles))
    ti = np.ravel(fi.floris.flow_field.turbulence_intensity_field)
    lx, ly = np.asarray(fi.layout_x, float), np.asarray(fi.layout_y, float)
    hub = float(np.ravel(fi.floris.farm.hub_heights)[0])
    D = float(np.ravel(fi.floris.farm.rotor_diameters)[0])
    off = np.linspace(-D / 2, D / 2, int(np.sqrt(n_pts)))
    sig = []
    for i in range(len(lx)):
        ys, zs = np.meshgrid(ly[i] + off, hub + off)
        xs = np.full(ys.size, lx[i] - 1.0)      # 转子平面上游侧：来流条件决定载荷
        u = np.ravel(fi.sample_flow_at_points(xs, np.ravel(ys), np.ravel(zs)))
        sig.append(float(np.std(u)))
    return ti + np.asarray(sig)


# ---------------------------------------------------------------------------
def fig_wake_steering():
    from wfrl.viz.field import extract_wake_plane
    cases = [("Greedy: all yaws = 0°", [0.0, 0.0, 0.0]),
             ("Wake steering: yaws = (25°, 15°, 0°)", [25.0, 15.0, 0.0])]
    fig, axes = plt.subplots(2, 1, figsize=(9.5, 5.6), sharex=True, sharey=True,
                             constrained_layout=True)
    p0 = None
    for ax, (title, ang) in zip(axes, cases):
        fi = _floris()
        hub = float(np.ravel(fi.floris.farm.hub_heights)[0])
        p = _powers(fi, ang)
        if p0 is None:
            p0 = p.sum()
            extra = ""
        else:
            extra = f"  (+{100 * (p.sum() / p0 - 1):.1f}%)"
        X, Y, _, U = extract_wake_plane(fi, hub, _yaw(fi, ang))
        im = ax.pcolormesh(X[:, :, 0], Y[:, :, 0], U[:, :, 0],
                           cmap="turbo", vmin=3.5, vmax=8.2, shading="auto")
        for xi, yi, pi, a in zip(fi.layout_x, fi.layout_y, p, ang):
            ax.plot([xi, xi], [yi - 63, yi + 63], "k-", lw=3, solid_capstyle="butt")
            ax.annotate(f"{pi:.2f} MW\n$\\gamma$={a:.0f}°", (xi, yi + 100),
                        ha="center", fontsize=9,
                        bbox=dict(fc="white", ec="none", alpha=0.8, pad=1.5))
        ax.set_title(f"{title}   —   farm total = {p.sum():.3f} MW{extra}",
                     fontsize=11)
        ax.set_ylabel("y [m]"); ax.set_ylim(-265, 265); ax.grid(False)
    axes[-1].set_xlabel("x [m]   (inflow $\\rightarrow$)")
    fig.colorbar(im, ax=axes, label="wind speed u [m/s]", aspect=32, shrink=0.9)
    _save(fig, "fig_wake_steering.png", tight=False)


def fig_yaw_power():
    fi = _floris()
    base = _powers(fi, [0, 0, 0])
    # (a) 只扫上游偏航
    g = np.arange(-40, 40.5, 1.0)                                # env 的 yaw 上下界 ±40°
    P = np.array([_powers(fi, [gi, 0, 0]) for gi in g])          # (n,3)
    # (b) (g1,g2) 联合曲面
    gg = np.arange(-40, 41, 2.5)
    Z = np.array([[_powers(fi, [a, b, 0]).sum() for b in gg] for a in gg])

    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.2))
    for k, (lab, c) in enumerate(zip(["T1 (upstream)", "T2 (middle)", "T3 (downstream)"],
                                     ["tab:red", "tab:orange", "tab:green"])):
        ax[0].plot(g, P[:, k], color=c, label=lab, lw=1.8)
    ax[0].plot(g, P.sum(1), "k-", lw=2.6, label="farm total")
    i = int(np.argmax(P.sum(1)))
    ax[0].axvline(g[i], color="tab:blue", ls="--", lw=1.2)
    ax[0].annotate(f"optimum $\\gamma_1$={g[i]:.0f}°\n+{100*(P.sum(1)[i]/base.sum()-1):.1f}% vs greedy",
                   (g[i], P.sum(1)[i]), xytext=(g[i] + 3, P.sum(1)[i] * 0.86),
                   fontsize=9, arrowprops=dict(arrowstyle="->", lw=1))
    ax[0].set_xlabel("upstream turbine yaw $\\gamma_1$ [deg]")
    ax[0].set_ylabel("power [MW]")
    ax[0].set_title("(a) Sacrifice locally, gain globally", fontsize=11)
    ax[0].legend(fontsize=8.5, loc="center left")

    im = ax[1].contourf(gg, gg, Z.T, levels=18, cmap="viridis")
    j = np.unravel_index(np.argmax(Z), Z.shape)
    ax[1].plot(gg[j[0]], gg[j[1]], "r*", ms=15, label=f"joint opt ({gg[j[0]]:.0f}°,{gg[j[1]]:.0f}°)")
    ax[1].plot(0, 0, "wo", ms=8, mec="k", label="greedy (0°,0°)")
    ax[1].set_xlabel("$\\gamma_1$ [deg]"); ax[1].set_ylabel("$\\gamma_2$ [deg]")
    ax[1].set_title("(b) Farm power surface — the coordination problem", fontsize=11)
    ax[1].legend(fontsize=8.5, loc="lower left"); ax[1].grid(False)
    fig.colorbar(im, ax=ax[1], label="farm total power [MW]")
    _save(fig, "fig_yaw_power.png")


def fig_reward_design():
    fi = _floris()
    # (a) u^3 归一化
    us = np.arange(4, 12.1, 0.5)
    tot, rp = [], []
    for u in us:
        fi.reinitialize(wind_speeds=[float(u)])
        p = _powers(fi, [0, 0, 0])
        tot.append(p.sum())
        rp.append(p.sum() * 1e3 / len(p) / u ** 3)          # kW 口径，式(4)
    fi.reinitialize(wind_speeds=[8.0])
    # (b) 功率 vs 载荷代理（逐机组）
    g = np.arange(0, 40.5, 2.0)
    P = np.array([_powers(fi, [gi, 0, 0]).sum() for gi in g])
    Lt = np.array([_load_proxy(fi, [gi, 0, 0]) for gi in g])      # (n,3)

    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.2))
    a2 = ax[0].twinx()
    ax[0].plot(us, tot, "o-", color="tab:blue", ms=3.5, label="raw farm power [MW]")
    a2.plot(us, rp, "s-", color="tab:red", ms=3.5, label="$r^P$ (normalized by $u_\\infty^3$)")
    ax[0].set_xlabel("free-stream wind speed $u_\\infty$ [m/s]")
    ax[0].set_ylabel("farm power [MW]", color="tab:blue")
    a2.set_ylabel("$r^P$  [kW / (m/s)$^3$]", color="tab:red")
    a2.set_ylim(0, max(rp) * 1.6); a2.grid(False)
    ax[0].set_title("(a) Why divide by $u_\\infty^3$", fontsize=11)
    ax[0].legend(loc="upper left", fontsize=8.5); a2.legend(loc="lower right", fontsize=8.5)

    for k, (lab, c) in enumerate(zip(["T1 (free stream)", "T2 (in wake)", "T3 (in wake)"],
                                     ["tab:red", "tab:orange", "tab:green"])):
        ax[1].plot(g, Lt[:, k], "-", color=c, lw=1.9, label=f"load proxy $r^L$: {lab}")
    ax[1].set_xlabel("upstream yaw $\\gamma_1$ [deg]")
    ax[1].set_ylabel("FLORIS load proxy  (TI + $\\sigma(u)$)")
    b2 = ax[1].twinx()
    b2.plot(g, P, "k--", lw=2, label="farm power [MW]")
    b2.set_ylabel("farm total power [MW]"); b2.grid(False)
    ax[1].annotate("T1 is the turbine being yawed, yet its\nproxy stays flat: FLORIS has no structural\n"
                   "model $\\Rightarrow$ yaw-induced blade load is invisible",
                   xy=(30, Lt[15, 0]), xytext=(6.5, 0.86), fontsize=8.2,
                   arrowprops=dict(arrowstyle="->", lw=1),
                   bbox=dict(fc="#fff6d5", ec="#c8a900", alpha=0.95, pad=2.5))
    ax[1].set_title("(b) Load proxy on FLORIS: aligned with power, not in tension",
                    fontsize=11)
    h1, l1 = ax[1].get_legend_handles_labels(); h2, l2 = b2.get_legend_handles_labels()
    ax[1].legend(h1 + h2, l1 + l2, fontsize=8, loc="upper right")
    _save(fig, "fig_reward_design.png")


def fig_wind_scenarios():
    rng = np.random.default_rng(0)
    u_bar, lam, phi_bar, sig = 8.0, 2.0, 270.0, 10.0      # 式(1)
    n = 4000
    u = u_bar * rng.weibull(lam, n)
    phi = rng.normal(phi_bar, sig, n)

    fig = plt.figure(figsize=(12.5, 3.6))
    gs = fig.add_gridspec(1, 4, wspace=0.42)

    ax = fig.add_subplot(gs[0])
    xs = np.linspace(0.1, 22, 300)
    pdf = (lam / u_bar) * (xs / u_bar) ** (lam - 1) * np.exp(-(xs / u_bar) ** lam)
    ax.hist(u, bins=45, density=True, color="tab:blue", alpha=0.45)
    ax.plot(xs, pdf, "k-", lw=2)
    ax.set_title(f"$u_\\infty\\sim\\mathcal{{W}}(\\bar u={u_bar},\\lambda={lam})$", fontsize=10)
    ax.set_xlabel("wind speed [m/s]"); ax.set_ylabel("density")

    ax = fig.add_subplot(gs[1])
    ax.hist(phi, bins=45, density=True, color="tab:orange", alpha=0.5)
    xs = np.linspace(phi_bar - 4 * sig, phi_bar + 4 * sig, 300)
    ax.plot(xs, np.exp(-0.5 * ((xs - phi_bar) / sig) ** 2) / (sig * np.sqrt(2 * np.pi)),
            "k-", lw=2)
    ax.set_title(f"$\\phi_\\infty\\sim\\mathcal{{N}}(\\bar\\phi={phi_bar:.0f}°,\\sigma={sig:.0f}°)$",
                 fontsize=10)
    ax.set_xlabel("wind direction [deg]")

    ax = fig.add_subplot(gs[2])
    ax.plot(phi, u, ".", ms=1.6, color="tab:green", alpha=0.35)
    ax.set_xlabel("direction [deg]"); ax.set_ylabel("speed [m/s]")
    ax.set_title("Scenario II: one draw per episode", fontsize=10)

    # 三种风况场景的时间结构（示意图：说明"什么时候重新采样"）
    ax = fig.add_subplot(gs[3])
    t = np.linspace(0, 3, 600)
    ep = np.clip(np.floor(t).astype(int), 0, 2)
    sc1 = np.full_like(t, 8.0)
    sc2 = np.array([8.0, 6.2, 9.4])[ep]                       # 每回合开始重采样
    sc3 = 8.0 + 1.6 * np.sin(2 * np.pi * 1.7 * t) + 0.5 * np.sin(2 * np.pi * 5.3 * t)
    for y, s, c, lab in [(0, sc1, "tab:blue", "Sc. I: constant"),
                         (7, sc2, "tab:orange", "Sc. II: resampled per episode"),
                         (14, sc3, "tab:green", "Sc. III: time series within episode")]:
        ax.plot(t, s - 8.0 + y, color=c, lw=1.8)
        ax.text(0.03, y + 2.6, lab, fontsize=8.2, color=c, fontweight="bold")
    for b in [1, 2]:
        ax.axvline(b, color="gray", ls=":", lw=1)
    ax.text(1.0, -4.2, "episode boundaries", fontsize=7.5, color="gray", ha="center")
    ax.set_xlabel("time [episodes]"); ax.set_yticks([])
    ax.set_ylim(-5, 19); ax.set_ylabel("$u_\\infty$  (offset per scenario)")
    ax.set_title("Where the randomness enters\n(schematic)", fontsize=9.5)
    _save(fig, "fig_wind_scenarios.png", tight=False)


def fig_training():
    runs = [("Static MAPPO (FLORIS)", "tab:blue",
             "fastfarm_Dec_Turb3_Row1_Floris_mappo_hist.json"),
            ("Dynamic MAPPO (FAST.Farm)", "tab:red",
             "fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_hist.json"),
            ("Dynamic GRU-MAPPO", "tab:purple",
             "fastfarm_Dec_Turb3_Row1_Fastfarm_gru-mappo_hist.json")]
    keys = [("power_mw", "farm total power [MW]"),
            ("explained_var", "critic explained variance"),
            ("reward", "shaped reward (per step)")]
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.9))
    for lab, c, fn in runs:
        p = os.path.join(RUNS, fn)
        if not os.path.exists(p):
            print(f"[skip] {p}"); continue
        h = json.load(open(p, encoding="utf-8"))["hist"]
        it = np.arange(1, len(h["reward"]) + 1)
        for j, (k, _) in enumerate(keys):
            ax[j].plot(it, h[k], "-o", color=c, ms=3, lw=1.6, label=lab, alpha=0.9)
            if len(it) >= 5:
                ax[j].hlines(np.mean(h[k][-5:]), it[-5], it[-1], color=c,
                             ls="--", lw=1.2, alpha=0.7)
    for j, (_, t) in enumerate(keys):
        ax[j].set_xlabel("iteration"); ax[j].set_title(t, fontsize=11)
    ax[1].set_ylim(-1.5, 1.05)                       # 早期大负值裁掉，便于看收敛段
    ax[1].text(0.98, 0.03, "early values < -1.5 clipped", transform=ax[1].transAxes,
               ha="right", fontsize=7.5, color="gray")
    ax[0].legend(fontsize=8.5)
    # 曾在此标 "≈2.5×" 作为后端效应。已撤回：两次 run 各自抽了一次风况，
    # 而 P ∝ u^3，单后端内部的抽签噪声就能产生同量级的间距
    # （exp_null_distribution.py: P(比值≥2.43)=0.26）。改成明确的警示。
    ax[0].annotate("", xy=(18, 5.69), xytext=(18, 2.34),
                   arrowprops=dict(arrowstyle="<->", color="#aa2828", lw=1.4))
    ax[0].annotate("gap NOT attributable\n(inflow not pinned)", (18.3, 3.6),
                   fontsize=9, color="#aa2828", fontweight="bold")
    fig.suptitle("Same layout (Turb3_Row1), same algorithm, same hyper-parameters — "
                 "but inflow was re-sampled per run, so absolute levels are NOT comparable"
                 "   (dashed = last-5-iter mean)", fontsize=10.5)
    _save(fig, "fig_training.png")


# ---------------------------------------------------------------------------
def _save(fig, name, tight=True):
    os.makedirs(FIGS, exist_ok=True)
    if tight:
        fig.tight_layout()
    out = os.path.join(FIGS, name)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[fig] -> {out}", flush=True)


if __name__ == "__main__":
    fig_wind_scenarios()
    fig_training()
    fig_wake_steering()
    fig_yaw_power()
    fig_reward_design()
    print("done.")
