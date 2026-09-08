"""聚合"钉死来流 + 多 seed"的 RL 对照，出误差棒。

对应日报 §1.4 的最后一块：物理层的受控结论已经有了，这里补含策略的那一层。
只吃文件名带 `_s{seed}_u{ws}` 的历史（不钉风的老 run 不混进来）。

    "C:/Users/s1155/.conda/envs/wfcrl/python.exe" compare_pinned_seeds.py
"""
import glob
import json
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from wfrl import paths

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

PAT = re.compile(r"fastfarm_Dec_Turb3_Row1_(\w+?)_(gru-mappo|mappo)_s(\d+)_u([\d.]+)_hist\.json$")
# 零偏航基线（runs/matched_inflow.json，u=8）：策略要跑赢的下限
ZERO_YAW = {"Floris": 2.3767, "Fastfarm": 2.7488}


def load():
    runs = {}
    for p in sorted(glob.glob(os.path.join(paths.RUNS, "*_hist.json"))):
        m = PAT.search(os.path.basename(p))
        if not m:
            continue
        backend, net, seed, u = m.group(1), m.group(2), int(m.group(3)), float(m.group(4))
        h = json.load(open(p, encoding="utf-8"))["hist"]
        runs.setdefault((backend, net, u), []).append((seed, h))
    return runs


def main():
    runs = load()
    if not runs:
        print("没有钉风的多 seed 历史，先跑 train_fastfarm.py --wind-speed ...")
        return

    print(f"{'后端':<10}{'网络':<11}{'u':>5}{'seeds':>7}"
          f"{'末5轮功率 (MW)':>24}{'零偏航基线':>10}{'策略增益':>10}")
    summary = {}
    for (backend, net, u), items in sorted(runs.items()):
        items.sort()
        # 每个 seed 取末 5 轮均值，再在 seed 之间求均值与极差
        pw = np.array([np.mean(h["power_mw"][-5:]) for _, h in items])
        pn = np.array([np.mean(h["power_norm"][-5:]) for _, h in items])
        base = ZERO_YAW.get(backend, float("nan"))
        summary[(backend, net, u)] = (pw, pn, items)
        print(f"{backend:<10}{net:<11}{u:>5.0f}{len(items):>7}"
              f"{pw.mean():>12.3f} ±{pw.std(ddof=0):.3f} "
              f"[{pw.min():.3f},{pw.max():.3f}]"
              f"{base:>10.3f}{100*(pw.mean()/base-1):>+9.1f}%")

    # 跨后端直接比绝对 MW 意义有限：两边的零偏航基线本来就不同(2.38 vs 2.75)。
    # 真正可比的是"策略相对各自基线拿到了多少"。
    keys = {b: v for (b, n, u), v in summary.items() if n == "mappo"}
    if "Floris" in keys and "Fastfarm" in keys:
        a, b = keys["Floris"][0], keys["Fastfarm"][0]
        gap = b.min() - a.max()
        print(f"\n后端对照(MLP critic, u=8): FLORIS {a.mean():.3f} vs "
              f"FAST.Farm {b.mean():.3f}  ->  {a.mean()/b.mean():.3f}×"
              f"  (零偏航物理对照是 {ZERO_YAW['Floris']/ZERO_YAW['Fastfarm']:.3f}×)")
        print(f"  seed 区间 FLORIS [{a.min():.3f},{a.max():.3f}] / "
              f"FAST.Farm [{b.min():.3f},{b.max():.3f}]  间隙 {gap:+.4f} MW")
        # n=3 下"区间恰好不重叠"是刀锋结论：要求间隙至少有一个 seed 标准差才算数
        need = max(a.std(ddof=0), b.std(ddof=0))
        print(f"  判据: 间隙需 > max(seed 标准差) = {need:.3f} MW  -> "
              f"{'差异可谈' if gap > need else '间隙过小，n=3 下不能宣称后端效应'}")
        print(f"  更可比的口径 —— 策略相对各自零偏航基线的增益: "
              f"FLORIS {100*(a.mean()/ZERO_YAW['Floris']-1):+.1f}%  vs  "
              f"FAST.Farm {100*(b.mean()/ZERO_YAW['Fastfarm']-1):+.1f}%")

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.0))
    for j, (key, ylab) in enumerate((("power_mw", "全场功率 (MW)"),
                                     ("power_norm", r"$P/u_\infty^3$ [kW/(m/s)$^3$]"))):
        for (backend, net, u), (_, _, items) in sorted(summary.items()):
            C = np.array([h[key] for _, h in items])
            it = np.arange(1, C.shape[1] + 1)
            c = "#aa2828" if backend == "Floris" else "#1e6e46"
            ls = "--" if net == "gru-mappo" else "-"
            ax[j].plot(it, C.mean(0), ls, color=c, lw=1.8,
                       label=f"{backend} · {net} (n={len(items)})")
            ax[j].fill_between(it, C.min(0), C.max(0), color=c, alpha=0.16)
        ax[j].set_xlabel("iteration"); ax[j].set_ylabel(ylab)
        ax[j].grid(alpha=0.25); ax[j].legend(fontsize=8)
    for backend, v in ZERO_YAW.items():
        ax[0].axhline(v, color="#aa2828" if backend == "Floris" else "#1e6e46",
                      ls=":", lw=1.2)
    ax[0].set_title("钉死 $u_\\infty=8$ m/s、270°；带状=seed 极差\n点线=零偏航基线",
                    fontsize=10)
    ax[1].set_title("归一化功率", fontsize=10)
    fig.tight_layout()
    out = os.path.join(paths.FIGS, "fig_pinned_seeds.png")
    fig.savefig(out, dpi=150)
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
