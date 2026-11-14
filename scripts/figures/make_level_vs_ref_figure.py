"""Compare saved level/reference yaw and power using counted valid samples.

Missing measurements remain gaps; boxplots exclude invalid samples.
Output: slides/figs/fig_level_vs_ref.png.
"""
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.figures.plot_wake_steering import (  # noqa: E402
    describe_stats, farm_total, masked_data, statistics,
)

LEVEL_NPZ = 'results/runs/stagE400_rollout.npz'
REF_NPZ = 'results/runs/stagE400ref_rollout.npz'
OUT = 'slides/figs/fig_level_vs_ref.png'


def build_figure(level, ref):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    yaw_level = masked_data(level, "yaw")  # (400, 3)
    pow_level = masked_data(level, "power")  # (400, 3)
    yaw_ref = masked_data(ref, "yaw")
    pow_ref = masked_data(ref, "power")

    if yaw_level.shape != pow_level.shape or yaw_ref.shape != pow_ref.shape:
        raise ValueError('yaw and power must have matching shapes in each archive')
    steps = np.arange(len(yaw_level))
    ref_steps = np.arange(len(yaw_ref))

    fig, ax = plt.subplots(1, 3, figsize=(15, 4.5))

    # (a) T1 偏航轨迹对比
    ax[0].plot(steps, yaw_level[:, 0], "-", lw=1.8, color="#1f77b4", label="level 奖励", alpha=0.85)
    ax[0].plot(ref_steps, yaw_ref[:, 0], "-", lw=1.8, color="#d62728", label="reference 奖励")
    ax[0].axhline(20, ls="--", color="#2ca02c", lw=1.2, alpha=0.7)
    ax[0].text(320, 21.5, "FLORIS 最优 ~20°", fontsize=8, color="#2ca02c")
    ax[0].axhline(36, ls=":", color="#888", lw=1, alpha=0.6)
    ax[0].text(320, 37.5, "占空比上限 ~36°", fontsize=7, color="#555")
    ax[0].fill_between(ref_steps, 0, yaw_ref[:, 0], color="#d62728", alpha=0.08)
    ax[0].set_title("(a) 上游机组(T1)偏航轨迹\n缺测处保留断点", fontsize=10)
    ax[0].set_xlabel("时间步"); ax[0].set_ylabel("T1 偏航角 (°)")
    ax[0].legend(loc="upper left", fontsize=9)
    ax[0].grid(alpha=0.3)
    ax[0].set_xlim(0, 400); ax[0].set_ylim(-2, 40)

    # (b) 瞬时全场功率对比
    farm_pow_level = farm_total(pow_level)
    farm_pow_ref = farm_total(pow_ref)
    baseline = 3.11  # 零偏航基线 (从 reference 训练日志)

    ax[1].plot(steps, farm_pow_level, "-", lw=1.4, color="#1f77b4", label="level", alpha=0.8)
    ax[1].plot(ref_steps, farm_pow_ref, "-", lw=1.6, color="#d62728", label="reference")
    ax[1].axhline(baseline, ls="--", color="#888", lw=1.2)
    ax[1].text(10, baseline - 0.03, f"零偏航基线 {baseline:.2f} MW", fontsize=8, color="#555")
    ax[1].fill_between(ref_steps, baseline, farm_pow_ref, where=(farm_pow_ref > baseline),
                        color="#d62728", alpha=0.12, label="reference 增益区")
    level_stats, ref_stats = statistics(farm_pow_level[-50:]), statistics(farm_pow_ref[-50:])
    ax[1].set_title('(b) 瞬时全场功率（末50步统计）\nreference: ' + describe_stats(ref_stats, 'MW')
                    + '\nlevel: ' + describe_stats(level_stats, 'MW'), fontsize=9)
    ax[1].set_xlabel("时间步"); ax[1].set_ylabel("全场功率 (MW)")
    ax[1].legend(loc="lower right", fontsize=9)
    ax[1].grid(alpha=0.3)
    ax[1].set_xlim(0, 400); ax[1].set_ylim(2.9, 3.4)

    # (c) 三机组末50步偏航分布（箱线图）
    last50_level = yaw_level[-50:, :]
    last50_ref = yaw_ref[-50:, :]

    positions_level = [1, 2, 3]
    positions_ref = [1.3, 2.3, 3.3]

    for label, values, positions, color in (
            ('level', last50_level, positions_level, '#1f77b4'),
            ('reference', last50_ref, positions_ref, '#d62728')):
        for i, position in enumerate(positions):
            samples = values[:, i][np.isfinite(values[:, i])]
            stats = statistics(samples)
            detail = describe_stats(stats, '°')
            if samples.size:
                ax[2].boxplot([samples], positions=[position], widths=.25, patch_artist=True,
                              boxprops=dict(facecolor=color, alpha=.6),
                              medianprops=dict(color='#000', lw=1.5),
                              whiskerprops=dict(color=color), capprops=dict(color=color))
                detail += f"; median={stats['median']:.2f}°, range=[{stats['min']:.2f}, {stats['max']:.2f}]°"
            ax[2].text(position, .98 if label == 'level' else .88,
                       f'n={samples.size}' if samples.size else '无有效数据\nn=0',
                       transform=ax[2].get_xaxis_transform(), ha='center', va='top', fontsize=6, color=color)
            print(f'  {label} T{i + 1}: {detail}')

    ax[2].axhline(0, ls=":", color="#aaa", lw=1)
    ax[2].set_xticks([1.15, 2.15, 3.15])
    ax[2].set_xticklabels(["T1 (上游)", "T2 (中游)", "T3 (下游)"])
    ax[2].set_title("(c) 末50步偏航分布（三机组）\n仅统计有效样本", fontsize=10)
    ax[2].set_ylabel("偏航角 (°)")
    ax[2].legend([Patch(facecolor="#1f77b4"), Patch(facecolor="#d62728")], ["level", "reference"],
                 loc="upper left", fontsize=9)
    ax[2].grid(alpha=0.3, axis="y")
    ax[2].set_ylim(-6, 38)

    t1_ref = statistics(last50_ref[:, 0])
    if t1_ref['count']:
        ax[2].text(1.5, t1_ref['median'], f"{t1_ref['median']:.1f}°", fontsize=8,
                   color='#d62728', va='center', fontweight='bold')

    fig.suptitle("Level vs Reference 奖励：偏航与功率比较",
                 fontsize=13, y=1.00, fontweight="bold")
    for label, stats in (('Level', level_stats), ('Reference', ref_stats)):
        gain = (f"{(stats['mean'] - baseline) / baseline * 100:+.2f}%"
                if stats['count'] else '无有效数据')
        print(f"{label} 全场功率: {describe_stats(stats, 'MW')} ({gain})")
    delta = (f"{(ref_stats['mean'] - level_stats['mean']) * 1000:.1f} kW"
             if ref_stats['count'] and level_stats['count'] else '无有效数据')
    print(f"增益: {delta}（有效样本数: level={level_stats['count']}, reference={ref_stats['count']}）")
    return fig


def main(level_path=LEVEL_NPZ, ref_path=REF_NPZ, output_path=OUT):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.rcParams['font.family'] = 'Microsoft YaHei'
    plt.rcParams['axes.unicode_minus'] = False
    with np.load(level_path) as level, np.load(ref_path) as ref:
        fig = build_figure(level, ref)
    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140, bbox_inches='tight')
    plt.close(fig)
    print(f'saved -> {Path(output_path).resolve()}')


if __name__ == '__main__':
    main()
