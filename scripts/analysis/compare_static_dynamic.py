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
from pathlib import Path
import tempfile

import numpy as np

RUNS_DIR = "runs"


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        hist, config = data['hist'], data['hist']['cfg']
        if (not isinstance(config, dict) or type(config['seed']) is not int
                or type(config['iters']) is not int or config['iters'] <= 0):
            raise ValueError('Expected seed and positive iteration budget in configuration')
        json.dumps(config, allow_nan=False)
        if 'run' in hist and hist['run'].get('status') != 'completed':
            raise ValueError('History run is not completed')
        for key in ('reward', 'power_mw', 'explained_var'):
            values = np.asarray(hist[key], dtype=float)
            if values.shape != (config['iters'],) or not np.isfinite(values).all():
                raise ValueError(f'{key} requires one finite value per iteration')
            with np.errstate(over='ignore', invalid='ignore'):
                if not np.isfinite([values.mean(), values[-5:].mean()]).all():
                    raise ValueError(f'{key} means must be finite')
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f'Invalid history {path}: {exc}') from exc


def _validate_comparison(runs, sources):
    reference = None
    for index, ((_, _, data), path) in enumerate(zip(runs, sources)):
        config = data['hist']['cfg']
        backend = 'Floris' if index == 0 else 'Fastfarm'
        if (config.get('env_id') != f'Dec_Turb3_Row1_{backend}'
                or data.get('env_id', config['env_id']) != config['env_id']
                or config.get('recurrent') is not (index == 2)):
            raise ValueError(f'Invalid backend/layout/network configuration: {path}')
        common = {k: v for k, v in config.items() if k not in {'env_id', 'recurrent'}}
        if reference is not None and common != reference:
            raise ValueError(f'Comparison configuration/seed mismatch: {sources[0]} vs {path}')
        reference = common


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
    sources = [args.static, args.dynamic]
    if args.dynamic_gru:
        runs.append(("动态 GRU-MAPPO", "tab:purple", _load(args.dynamic_gru)))
        sources.append(args.dynamic_gru)
    _validate_comparison(runs, sources)

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
    try:
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
        target = Path(args.out)
        if not target.suffix:
            target = target.with_suffix("." + plt.rcParams["savefig.format"])
        with tempfile.TemporaryDirectory(prefix=".analysis-plot-",
                                         dir=target.resolve().parent) as staging:
            staged = Path(staging) / target.name
            fig.savefig(staged, dpi=120)
            os.replace(staged, target)
        print(f"[compare] 对比图 -> {args.out}", flush=True)
    finally:
        plt.close(fig)

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
