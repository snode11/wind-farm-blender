"""Plot saved yaw and power with gaps for invalid samples and counted statistics."""
from pathlib import Path

import numpy as np


def masked_data(data, key):
    """Saved masks take precedence; archives without masks use finite samples."""
    values = np.asarray(data[key], float).copy()
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError(f'{key}: expected a (steps, 3) array')
    valid = np.isfinite(values)
    if f'{key}_valid' in data:
        declared = np.asarray(data[f'{key}_valid'])
        if declared.dtype.kind != 'b' or declared.shape != values.shape:
            raise ValueError(f'{key}_valid: expected boolean flags with shape {values.shape}')
        valid &= declared
    values[~valid] = np.nan
    return values


def farm_total(power):
    """A farm total requires every turbine at that time step."""
    complete = np.isfinite(power).all(axis=1)
    total = np.full(len(power), np.nan)
    total[complete] = power[complete].sum(axis=1)
    total[~np.isfinite(total)] = np.nan
    return total


def statistics(values):
    values = np.asarray(values, float)
    samples = values[np.isfinite(values)]
    return dict(count=int(samples.size),
                **{name: float(fn(samples)) if samples.size else None
                   for name, fn in (('mean', np.mean), ('median', np.median),
                                    ('min', np.min), ('max', np.max))})


def describe_stats(stats, unit):
    if not stats['count']:
        return '无有效数据（有效样本数=0）'
    return f"{stats['mean']:.3f} {unit}（有效样本数={stats['count']}）"


def build_figure(data):
    import matplotlib.pyplot as plt

    yaw = masked_data(data, "yaw")      # (400, 3)
    power = masked_data(data, "power")  # (400, 3)

    steps = np.arange(len(yaw))
    if yaw.shape != power.shape:
        raise ValueError('yaw and power must have matching shapes')
    farm_power = farm_total(power)
    yaw_stats = statistics(yaw[-50:, 0])
    power_stats = statistics(farm_power[-50:])

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

    # Plot 1: T1 yaw angle
    ax1.plot(steps, yaw[:, 0], 'b-', linewidth=2, label='T1 (upstream)')
    ax1.axhline(0, color='gray', linestyle='--', alpha=0.5)
    ax1.set_ylabel('Yaw Angle (deg)', fontsize=12)
    ax1.set_title('T1 Yaw: ' + describe_stats(yaw_stats, 'deg'), fontsize=12)
    ax1.legend()
    ax1.grid(alpha=0.3)

    # Plot 2: Individual turbine power
    ax2.plot(steps, power[:, 0], 'b-', label='T1 (upstream)', alpha=0.7)
    ax2.plot(steps, power[:, 1], 'g-', label='T2 (middle)', alpha=0.7)
    ax2.plot(steps, power[:, 2], 'r-', label='T3 (downstream)', alpha=0.7)
    ax2.set_ylabel('Power (MW)', fontsize=12)
    ax2.set_title('Individual Turbine Power', fontsize=14)
    ax2.legend()
    ax2.grid(alpha=0.3)

    # Plot 3: Farm total power vs baseline
    baseline = 3.116  # Zero-yaw baseline (MW)
    ax3.plot(steps, farm_power, 'k-', linewidth=2, label='RL Policy (learned)')
    ax3.axhline(baseline, color='r', linestyle='--', linewidth=2, label=f'Baseline (zero yaw): {baseline} MW')
    ax3.fill_between(steps, baseline, farm_power, where=(farm_power > baseline),
                     color='green', alpha=0.2, label='Gain region')
    ax3.set_xlabel('Time Step', fontsize=12)
    ax3.set_ylabel('Farm Power (MW)', fontsize=12)
    gain = ((power_stats['mean'] - baseline) / baseline * 100 if power_stats['count'] else None)
    gain_text = '无有效数据' if gain is None else f'{gain:+.1f}%'
    ax3.set_title('Farm Power: ' + describe_stats(power_stats, 'MW') + f'; Gain: {gain_text}', fontsize=12)
    ax3.legend()
    ax3.grid(alpha=0.3)

    print("\nKey results (last 50 steps):")
    print('  T1 yaw: ' + describe_stats(yaw_stats, 'deg'))
    for i in range(3):
        print(f'  T{i + 1} power: ' + describe_stats(statistics(power[-50:, i]), 'MW'))
    print('  Farm power: ' + describe_stats(power_stats, 'MW'))
    print(f"  Gain: {gain_text}（有效样本数={power_stats['count']}）")
    return fig


def main(input_path='results/runs/stagE400ref_rollout.npz',
         output_path='results/figures/wake_steering_demo.png'):
    import matplotlib.pyplot as plt

    plt.rcParams['font.sans-serif'] = ['SimHei']
    plt.rcParams['axes.unicode_minus'] = False
    with np.load(input_path) as data:
        fig = build_figure(data)
    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f'Figure saved: {output_path}')
    plt.show()
    plt.close(fig)


if __name__ == '__main__':
    main()
