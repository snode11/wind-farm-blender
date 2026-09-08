"""阶段 3 的结果聚合 —— 成对的「零偏航基线 / 学习策略」按 seed 出误差棒。

和 `compare_pinned_seeds.py` 的区别有两点：

  1. **不靠文件名正则认 run**，读 hist 里的 `cfg` 块（回合长度 / 来流调度 /
     奖励整形器 / policy / seed 全在里面）。文件名早晚会不够用。
  2. **基线是同一批次里 `--policy zero` 跑出来的**，不是从别的实验里搬一个常数。
     `compare_pinned_seeds.py` 用的 `ZERO_YAW = {"Fastfarm": 2.7488}` 来自
     `matched_inflow.json`，那是另一套回合结构、没有 warmup 丢弃 —— 拿它当基线
     等于把回合结构的改动一起算进"策略增益"里。

判据（README §8 第 6 条）：**增益的间隙 > max(seed 标准差)**。n=3 下"区间恰好
不重叠"不算证据。

    python scripts/analysis/compare_stage3.py [--tag stage3] [--last 5]
"""
import argparse
import glob
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                          # noqa: E402
import numpy as np                                       # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from wfrl import paths                                   # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 分组时必须一致的设定：任何一项不同都不是同一个对照
KEYS = ("episode_steps", "warmup_steps", "wind_sched", "reward", "load_coef",
        "n_steps", "recurrent")


def load(tag):
    runs = []
    for p in sorted(glob.glob(os.path.join(paths.RUNS, "*_hist.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            continue
        h = d.get("hist", {})
        cfg = h.get("cfg")
        if not cfg:                                      # 阶段 3 之前的老产物
            continue
        if tag and tag not in d.get("tag", ""):
            continue
        runs.append({"path": p, "tag": d["tag"], "cfg": cfg, "hist": h})
    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="stage3", help="只看 tag 里含这段的 run")
    ap.add_argument("--last", type=int, default=5,
                    help="策略取末 N 轮的平均（避开早期探索）")
    args = ap.parse_args()

    runs = load(args.tag)
    if not runs:
        print(f"没有带 cfg 的 hist（tag 含 {args.tag!r}）。先跑 "
              f"scripts/train/run_stage3.py")
        return 1

    groups = {}
    for r in runs:
        key = tuple(r["cfg"].get(k) for k in KEYS)
        groups.setdefault(key, []).append(r)

    for key, rs in groups.items():
        cfg = dict(zip(KEYS, key))
        print(f"\n=== 回合 {cfg['episode_steps']} 步 / warmup {cfg['warmup_steps']} "
              f"/ {cfg['wind_sched']} / 奖励 {cfg['reward']} "
              f"/ load_coef {cfg['load_coef']} "
              f"/ {'GRU' if cfg['recurrent'] else 'MLP'} critic ===")
        by_seed = {}
        for r in rs:
            by_seed.setdefault(r["cfg"]["seed"], {})[r["cfg"]["policy"]] = r
        gains, base_v, pol_v = [], [], []
        for sd in sorted(by_seed):
            pair = by_seed[sd]
            if "zero" not in pair or "learn" not in pair:
                print(f"  seed {sd}: 缺 "
                      f"{'零偏航基线' if 'zero' not in pair else '学习策略'}，跳过")
                continue
            b = np.asarray(pair["zero"]["hist"]["power_norm"], dtype=float)
            p = np.asarray(pair["learn"]["hist"]["power_norm"], dtype=float)
            bm = float(b.mean())
            pm = float(p[-args.last:].mean())
            g = (pm - bm) / bm * 100.0
            gains.append(g); base_v.append(bm); pol_v.append(pm)
            print(f"  seed {sd}:  基线 P/u³ {bm:.3f}（{len(b)} 轮）  "
                  f"策略末{args.last}轮 {pm:.3f}  →  {g:+.2f}%")
        if len(gains) < 2:
            print("  seed 不足 2 个，不做判据")
            continue
        g = np.asarray(gains)
        # 判据：增益均值要跑赢 seed 之间的散布，否则就是抽签
        print(f"  增益 {g.mean():+.2f}% ± {g.std():.2f}%（n={len(g)}）  "
              f"最小 {g.min():+.2f}%  最大 {g.max():+.2f}%")
        verdict = ("通过：增益均值 > seed 标准差" if g.mean() > g.std()
                   else "**不通过**：增益均值落在 seed 散布之内，还不能归因于策略")
        print(f"  判据 → {verdict}")

    # 曲线：每个 seed 一条，基线画成横带
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for r in runs:
        h, c = r["hist"], r["cfg"]
        y = h["power_norm"]
        if c["policy"] == "zero":
            ax.axhline(float(np.mean(y)), ls="--", lw=1, alpha=0.5,
                       color="tab:gray")
        else:
            ax.plot(np.arange(1, len(y) + 1), y, "-o", ms=2.5,
                    label=f"seed {c['seed']}")
    ax.set_xlabel("iter"); ax.set_ylabel(r"$P_{farm}/u_\infty^3$  [kW/(m/s)$^3$]")
    ax.set_title("阶段 3：真回合结构下的训练曲线（虚线 = 同批次零偏航基线）")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=8)
    fig.tight_layout()
    out = os.path.join(paths.RUNS, f"stage3_{args.tag}_curves.png")
    fig.savefig(out, dpi=130)
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
