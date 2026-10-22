"""FLORIS 选型指南针：扫"间距 × 横向偏移"，找偏航增益最大的布局。

前提认知（已由 FAST.Farm 实测确立）：FLORIS 稳态**系统性高估** wake steering 的
绝对增益（turb3/turb6 上高估 ~20×）。所以这里**只用它选型**（哪个布局的偏航增益
相对更大），不用它定论——选出的候选必须再上 FAST.Farm 确认绝对值。

扫描：3 机一列，变间距 D_spacing ∈ {3D,4D}，下游逐台横向偏移 offset ∈
{0, 0.5R, 1R, 1.5R}（R=63m 转子半径）。每个布局在 FLORIS 下测"上游偏航 0→25°"
的最大全场功率增益。列表按增益降序，指出最值得上 FAST.Farm 验证的 1~2 个。

秒级，无需 MPI。
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfcrl.environments.data_cases import FlorisCase              # noqa: E402
from wfcrl.interface import FlorisInterface                       # noqa: E402

R = 63.0
D = 126.0


def make_fi(xs, ys):
    case = FlorisCase(num_turbines=len(xs), xcoords=list(xs), ycoords=list(ys),
                      dt=60, buffer_window=1, t_init=0, max_iter=100)
    fi = FlorisInterface.from_case(case)
    fi.init(wind_speed=8.0, wind_direction=270.0)
    return fi.fi


def farm_power(fi, yaws):
    ya = fi.floris.farm.yaw_angles * 0.0
    ya[..., :] = np.asarray(yaws, float)
    fi.calculate_wake(yaw_angles=ya)
    return float(np.sum(fi.get_turbine_powers()))


def best_gain(xs, ys):
    fi = make_fi(xs, ys)
    n = len(xs)
    p0 = farm_power(fi, np.zeros(n))
    up = [0]                              # 上游最前一台
    best = p0
    for yaw in (5, 10, 15, 20, 25):
        y = np.zeros(n);
        for i in up: y[i] = yaw
        best = max(best, farm_power(fi, y))
    return (best - p0) / p0 * 100, p0 / 1e6


def main():
    print("FLORIS 选型指南针（只选型不定论，绝对值必上 FAST.Farm 复核）\n")
    print(f"{'布局':<28}{'零偏航MW':>10}{'偏航最大增益':>14}")
    rows = []
    for spacing_D in (3.0, 4.0):
        s = spacing_D * D
        for off_R in (0.0, 0.5, 1.0, 1.5):
            off = off_R * R
            xs = [0.0, s, 2 * s]
            ys = [0.0, off, 2 * off]      # 下游逐台横向累积偏移
            g, p0 = best_gain(xs, ys)
            name = f"{spacing_D:g}D间距, 偏移{off_R:g}R"
            rows.append((g, name, p0))
            print(f"{name:<28}{p0:>10.3f}{g:>13.2f}%")
    rows.sort(reverse=True)
    print("\n增益降序（FLORIS 相对趋势可信、绝对值高估）：")
    for g, name, p0 in rows[:3]:
        print(f"  {name}: {g:+.2f}%")
    print(f"\n最有希望：{rows[0][1]}（FLORIS {rows[0][0]:+.1f}%）")
    print("→ 下一步：把它写成场景 YAML，上 FAST.Farm 用 probe_gain_ff 确认真实增益")
    print("PICK_LAYOUT_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
