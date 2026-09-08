"""零成本增益可达性扫描：某个布局在 FLORIS 稳态下，偏航到底有没有正增益可拿。

在投入几小时 FAST.Farm 训练之前，先用 FLORIS（秒级、无需 MPI）扫一遍：给上游
机组施加不同偏航角，看全场功率相对零偏航基线能不能显著上涨。若 FLORIS 下都没有
正增益，气动弹性后端更不会有 —— 直接别训。

用法：
    python scripts/experiments/probe_gain_reachable.py scenes/turb6_grid.yaml
    python scripts/experiments/probe_gain_reachable.py scenes/turb3_ctrl3.yaml  # 对照
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfrl.scene.schema import load_scene                          # noqa: E402
from wfrl.viz.field import get_floris                             # noqa: E402


def farm_power(fi, yaws_deg):
    ya = fi.floris.farm.yaw_angles * 0.0
    ya[..., :] = np.asarray(yaws_deg, float)
    fi.calculate_wake(yaw_angles=ya)
    return float(np.sum(fi.get_turbine_powers()))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "scenes/turb6_grid.yaml"
    sc = load_scene(path)
    sc.backend = "floris"; sc.turbulence = None; sc.controls = ["yaw"]
    sc.sensors = []
    env = sc.make_env(max_iter=10_000, log=False)
    env.reset()
    fi = get_floris(env)
    fi.reinitialize(wind_speeds=[float(sc.wind_speed)], wind_directions=[270.0])
    n = sc.n
    xs = np.asarray(sc.xcoords); ys = np.asarray(sc.ycoords)
    print(f"场景 {sc.name}: n={n}, 风向 270°(尾流沿 +x)")
    for i, t in enumerate(sc.turbines):
        print(f"  {t.id}: x={t.x:.0f} y={t.y:.0f}")

    p0 = farm_power(fi, np.zeros(n))
    print(f"\n零偏航基线全场功率: {p0/1e6:.4f} MW")

    # 上游机组 = x 最小的那些（每行/每列的最前一台）；给它们同一个偏航扫一遍
    x_upstream = xs.min()
    up = np.where(xs <= x_upstream + 1.0)[0]
    print(f"上游机组（x={x_upstream:.0f}）: {[sc.turbines[i].id for i in up]}")
    print("\n偏航角 → 全场功率 → 相对基线增益：")
    best = (0.0, p0)
    for yaw in (0, 5, 10, 15, 20, 25, 30):
        yaws = np.zeros(n); yaws[up] = yaw
        p = farm_power(fi, yaws)
        gain = (p - p0) / p0 * 100
        mark = "  <<< 峰" if p > best[1] else ""
        if p > best[1]:
            best = (yaw, p)
        print(f"  上游偏 {yaw:>3}° → {p/1e6:.4f} MW → {gain:+.2f}%{mark}")

    bg = (best[1] - p0) / p0 * 100
    print(f"\n最优上游偏航 {best[0]}° → 增益 {bg:+.2f}%")
    if bg > 1.0:
        print(f"GAIN_REACHABLE_OK — 该构型有 {bg:.1f}% 正增益可拿，值得训练")
    else:
        print(f"GAIN_NONE — FLORIS 下最大增益仅 {bg:+.2f}%，训练大概率拿不到正增益")
    return 0


if __name__ == "__main__":
    sys.exit(main())
