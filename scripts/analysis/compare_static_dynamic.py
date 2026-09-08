"""叠加"静态 MAPPO(FLORIS 稳态)" vs "动态 MAPPO(FAST.Farm 气弹)"训练曲线。

读 train_fastfarm.py 落的 runs/*_hist.json，画三联对比图 + 打印收敛数值摘要。
用法：
    python compare_static_dynamic.py \
        --static runs/fastfarm_Dec_Turb3_Row1_Floris_mappo_hist.json \
        --dynamic runs/fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_hist.json \
        [--dynamic-gru runs/fastfarm_Dec_Turb3_Row1_Fastfarm_gru-mappo_hist.json]
"""
import argparse
import json
import os

import numpy as np

RUNS_DIR = "runs"


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _tail_mean(xs, k=5):
    xs = np.asarray(xs, dtype=float)
    return float(xs[-k:].mean()) if len(xs) else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", required=True)
    ap.add_argument("--dynamic", required=True)
    ap.add_argument("--dynamic-gru", default=None)
    ap.add_argument("--out", default=os.path.join(RUNS_DIR,
                    "compare_static_vs_dynamic.png"))
    args = ap.parse_args()

    runs = [("静态 MAPPO (FLORIS)", "tab:blue", _load(args.static)),
            ("动态 MAPPO (FAST.Farm)", "tab:red", _load(args.dynamic))]
    if args.dynamic_gru and os.path.exists(args.dynamic_gru):
        runs.append(("动态 GRU-MAPPO", "tab:purple", _load(args.dynamic_gru)))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:                                            # noqa: BLE001
        pass

    metrics = [("reward", "shaped reward (1-step avg)"),
               ("power_mw", "farm total power (MW, avg)"),
               ("explained_var", "critic explained_var")]
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.5))
    for label, color, d in runs:
        h = d["hist"]
        its = np.arange(1, len(h["reward"]) + 1)
        for j, (key, _) in enumerate(metrics):
            ax[j].plot(its, h[key], "-o", color=color, label=label, ms=4)
    for j, (_, title) in enumerate(metrics):
        ax[j].set_title(title); ax[j].set_xlabel("iter")
        ax[j].grid(True, alpha=0.3); ax[j].legend(fontsize=8)
    fig.suptitle("静态 vs 动态 —— 真 MAPPO（同布局 Turb3_Row1，同算法同超参）")
    fig.tight_layout()
    os.makedirs(RUNS_DIR, exist_ok=True)
    fig.savefig(args.out, dpi=120)
    print(f"[compare] 对比图 -> {args.out}", flush=True)

    # 数值摘要（末 5 iter 均值）
    print("\n== 收敛摘要（末 5 iter 均值）==")
    print(f"{'run':<26}{'reward':>10}{'power(MW)':>12}{'expl_var':>10}")
    for label, _, d in runs:
        h = d["hist"]
        print(f"{label:<26}{_tail_mean(h['reward']):>10.3f}"
              f"{_tail_mean(h['power_mw']):>12.2f}"
              f"{_tail_mean(h['explained_var']):>10.3f}")


if __name__ == "__main__":
    main()
