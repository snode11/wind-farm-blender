"""尾流圆环几何的离线验收（不起事件循环，用离屏渲染）。

看不到"像不像扩散的尾流管"（要真机 GPU 目视），但能证明几何与动画逻辑不退化。

  1 点数、RGBA 数组尺寸正确
  2 沿下游铺开、半径随下游扩张、每个环是垂直下游的正圆
  3 透明度沿下游渐隐（两端趋 0，中段高）
  4 相位推进 ⇒ 环整体向下游滚动（点位变）
  5 每台机组颜色不同
  6 SceneView 开关；tick 里相位随 rpm 累进（转得快滚得快）

末行 RINGS_OK / RINGS_FAIL。
"""
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import pyvista as pv                                              # noqa: E402

from wfrl.scene.schema import Scene, Turbine                      # noqa: E402
from wfrl.studio import SceneView                                 # noqa: E402
from wfrl.viz import wake_rings as wr                             # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def main():
    hub = np.array([0.0, 0.0, 450.0])
    r_tip = 63.0 * 5.0
    scale = 5.0

    print("== 1. 点数 / RGBA 尺寸 ==", flush=True)
    pts = wr.ring_points(hub, r_tip, 270.0, scale, phase=0.0)
    rgba = wr.ring_rgba((0.15, 0.85, 0.25), phase=0.0)
    check("点数 = n_rings × n_seg", pts.shape == (wr.N_RINGS * wr.N_SEG, 3),
          f"{pts.shape}")
    check("RGBA = (N, 4) uint8",
          rgba.shape == (wr.N_RINGS * wr.N_SEG, 4) and rgba.dtype == np.uint8,
          f"{rgba.shape} {rgba.dtype}")

    print("\n== 2. 下游铺开 / 扩张 / 正圆 ==", flush=True)
    ring0 = pts[:wr.N_SEG]
    rad0 = np.linalg.norm(ring0 - ring0.mean(axis=0), axis=1)
    ringL = pts[(wr.N_RINGS - 1) * wr.N_SEG:]
    radL = np.linalg.norm(ringL - ringL.mean(axis=0), axis=1)
    check("首环正圆且≈转子半径",
          rad0.std() < 0.01 * rad0.mean() and abs(rad0.mean() - r_tip) < 0.05 * r_tip,
          f"{rad0.mean():.0f}±{rad0.std():.2f}")
    check("末环因扩张更大", radL.mean() > rad0.mean() * 1.3,
          f"末 {radL.mean():.0f} vs 首 {rad0.mean():.0f}")
    check("环平面垂直下游（x 厚度≈0）", ring0[:, 0].std() < 1e-6)

    print("\n== 3. 透明度沿下游渐隐 ==", flush=True)
    a = wr._alpha(wr._fracs(0.0))
    check("两端透明度趋 0", a[0] < 0.15 and a[-1] < 0.15,
          f"首 {a[0]:.2f} 末 {a[-1]:.2f}")
    check("中段透明度高于两端", a.max() > max(a[0], a[-1]) * 2,
          f"峰 {a.max():.2f} @ frac={np.argmax(a) / wr.N_RINGS:.2f}")

    print("\n== 4. 相位推进 ⇒ 向下游滚动 ==", flush=True)
    p_a = wr.ring_points(hub, r_tip, 270.0, scale, phase=0.0)
    p_b = wr.ring_points(hub, r_tip, 270.0, scale, phase=0.1)
    check("相位变了点位跟着变", not np.allclose(p_a, p_b),
          f"最大位移 {np.abs(p_a - p_b).max():.0f}")

    print("\n== 5. 每台颜色不同 ==", flush=True)
    c0, c1, c2 = (wr.turbine_color(i, 3) for i in range(3))
    check("三台颜色互不相同", c0 != c1 and c1 != c2 and c0 != c2,
          f"{np.round(c0, 2)} {np.round(c1, 2)} {np.round(c2, 2)}")

    print("\n== 6. SceneView 开关 + 相位随 rpm 累进 ==", flush=True)
    sc = Scene(name="pr", backend="fastfarm", dt=3.0,
               turbines=[Turbine("T1", 0, 0), Turbine("T2", 504, 0)],
               wind_speed=8.0, wind_direction=270.0,
               controls=["yaw"], sensors=[]).validate()
    pl = pv.Plotter(off_screen=True, window_size=(500, 400))
    view = SceneView(pl, sc, wake=False)
    check("默认关闭", not view._rings_on and not view._rings)
    view.set_wake_rings(True)
    check("开启后每台建了一套环", len(view._rings) == sc.n)
    # 两台不同 rpm，tick 后相位不同（转得快滚得快）
    view._rpm = np.array([12.0, 6.0])
    view._rings_on = True
    view.tick(0.1)
    check("相位随 rpm 累进（快台相位更大）",
          view._ring_phase[0] > view._ring_phase[1] > 0.0,
          f"T1 {view._ring_phase[0]:.4f}  T2 {view._ring_phase[1]:.4f}")
    view.set_wake_rings(False)
    check("关闭后 actor 不可见",
          all(not h["actor"].GetVisibility() for h in view._rings))

    print()
    if FAILS:
        print(f"RINGS_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("RINGS_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("RINGS_FAILED — 未捕获异常")
        sys.exit(1)
