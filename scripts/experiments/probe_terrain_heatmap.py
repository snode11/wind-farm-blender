"""地形 + 风况热力图 + schema terrain 字段的离线验收（离屏渲染，不起事件循环）。

看不到"像不像山"（要真机 GPU 目视），但能证明几何/开关/schema 逻辑不退化。

  1 schema：Scene(terrain=...) 合法值过、非法值报 SceneError、to_dict/from_dict 往返
  2 地形：scene.terrain 起的 SceneView 有 terrain actor；顶点高度 > 0；机位处压平≈0；
    切面覆盖内不超 h_far；set_terrain 换/移
  3 热力图：set_heatmap(False) 后 wake_actor 不可见；再开恢复

末行 TERRAIN_OK / TERRAIN_FAIL。
"""
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import pyvista as pv                                              # noqa: E402

from wfrl.scene.schema import (Scene, SceneError, Turbine,        # noqa: E402
                               from_dict)
from wfrl.studio import SceneView                                 # noqa: E402
from wfrl.viz import terrain as terr                              # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def scene(terrain="mountains"):
    return Scene(name="pt", backend="fastfarm", dt=3.0,
                 turbines=[Turbine("T1", 0, 0), Turbine("T2", 504, 0),
                           Turbine("T3", 1008, 0)],
                 wind_speed=8.0, wind_direction=270.0,
                 controls=["yaw"], sensors=[], terrain=terrain).validate()


def main():
    print("== 1. schema terrain 字段 ==", flush=True)
    check("合法 terrain=mountains 过", scene("mountains").terrain == "mountains")
    check("terrain=None 过", scene(None).terrain is None)
    try:
        scene("river")
        check("非法 terrain 报错", False)
    except SceneError:
        check("非法 terrain 报 SceneError", True)
    sc = scene("gobi")
    check("to_dict 带 terrain", sc.to_dict().get("terrain") == "gobi")
    check("from_dict 往返保 terrain", from_dict(sc.to_dict()).terrain == "gobi")

    print("\n== 2. 地形几何 ==", flush=True)
    pl = pv.Plotter(off_screen=True, window_size=(500, 400))
    view = SceneView(pl, scene("mountains"), wake=False)
    check("scene.terrain 起的 view 有 terrain actor",
          view._terrain_actor is not None)
    mesh = view._terrain_actor.mapper.dataset
    z = np.asarray(mesh.points)[:, 2]
    check("地形有起伏（高度 > 0）", z.max() > 10.0, f"z_max={z.max():.0f} m")
    hub_z, _r = view.draw_dims()
    h_far = 0.5 * hub_z
    # 机位附近应被压平到≈0（PAD_R 内）
    pts = np.asarray(mesh.points)
    near = np.hypot(pts[:, 0], pts[:, 1]) < terr.PAD_R
    if near.any():
        check("机位处压平到≈0", np.abs(pts[near, 2]).max() < 5.0,
              f"塔基附近 z_max={np.abs(pts[near, 2]).max():.1f} m")
    # 切面覆盖范围（CORE_R）内不超过 h_far
    cx, cy = 504.0, 0.0
    core = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) < terr.CORE_R
    check("切面覆盖内不超 h_far（不穿尾流面）",
          pts[core, 2].max() <= h_far + 1.0,
          f"核心区 z_max={pts[core, 2].max():.0f} vs h_far={h_far:.0f}")

    print("\n== 3. set_terrain 换 / 移 ==", flush=True)
    a_mtn = view._terrain_actor
    view.set_terrain("gobi")
    check("换戈壁：actor 换了", view._terrain_actor is not a_mtn
          and view._terrain_actor is not None)
    z_gobi = np.asarray(view._terrain_actor.mapper.dataset.points)[:, 2]
    check("戈壁比山地平坦", z_gobi.max() < z.max(),
          f"戈壁 {z_gobi.max():.0f} < 山地 {z.max():.0f}")
    view.set_terrain(None)
    check("切无：actor 移除", view._terrain_actor is None)

    print("\n== 4. 风况热力图开关 ==", flush=True)
    pl2 = pv.Plotter(off_screen=True, window_size=(500, 400))
    view2 = SceneView(pl2, scene("mountains"), wake=True)
    if view2.wake_actor is None:
        check("wake_actor 建起来了（FLORIS proxy 可用）", False,
              "proxy 没建起来，跳过热力图检查")
    else:
        check("默认热力图开（wake_actor 可见）",
              view2.wake_actor.GetVisibility() == 1)
        view2.set_heatmap(False)
        check("关闭后 wake_actor 不可见",
              view2.wake_actor.GetVisibility() == 0)
        view2.set_heatmap(True)
        check("重开后 wake_actor 可见",
              view2.wake_actor.GetVisibility() == 1)

    print()
    if FAILS:
        print(f"TERRAIN_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("TERRAIN_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("TERRAIN_FAILED — 未捕获异常")
        sys.exit(1)
