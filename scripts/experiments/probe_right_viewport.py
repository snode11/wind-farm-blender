"""离屏诊断：右视口（renderer[1]）里到底有没有建出那台机组。

不需要 GL 显示 —— 只数 renderer 里的 actor。右视口"没有画面"最可能是机组根本
没进 renderer[1]（build_turbine 建到了 renderer[0]，或 subplot 状态没切过去）。
这个纯拓扑问题，本机 offscreen 就能查。

复刻 StudioWindow 建双视口 + 右视口建真尺度机组的那几步，然后：
  · 数 renderer[0] / renderer[1] 各有几个 actor
  · 确认相机 camera_pose 没有 NaN、机组在相机视野方向上
"""
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import pyvista as pv                                              # noqa: E402

from wfrl.scene.schema import load_scene                          # noqa: E402
from wfrl.channels import registry                                # noqa: E402
from wfrl.viz.animate import build_turbine, turbine_actors        # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def n_actors(renderer):
    c = renderer.GetActors()
    c.InitTraversal()
    n = 0
    while True:
        a = c.GetNextActor()
        if a is None:
            break
        n += 1
    return n


def main():
    sc = load_scene("scenes/turb3_ctrl3.yaml")
    pl = pv.Plotter(off_screen=True, shape=(1, 2), window_size=(800, 400))

    print("== 复刻 StudioWindow：左视口建主场景（放大 5×）==", flush=True)
    pl.subplot(0, 0)
    # 简化：左视口只放三台放大机组（够代表 SceneView 的 actor 都在左）
    left = [build_turbine(pl, t.x, t.y, sc.hub_height, scale=5.0)
            for t in sc.turbines]
    nL_before = n_actors(pl.renderers[0])
    nR_before = n_actors(pl.renderers[1])
    print(f"    左建完：renderer[0]={nL_before}  renderer[1]={nR_before}",
          flush=True)

    print("\n== 右视口建真尺度机组（camera 传感器挂载的那台）==", flush=True)
    cams = [s for s in registry.build(sc)
            if getattr(s, "type_name", None) == "camera"]
    check("场景有 camera 传感器", bool(cams))
    sensor = cams[0]
    si = 0
    ti = sensor.indices[si]
    pl.subplot(0, 1)                       # 关键：切到右 renderer 再 build
    right = build_turbine(pl, sc.turbines[ti].x, sc.turbines[ti].y,
                          sc.hub_height, scale=1.0)
    pl.subplot(0, 0)
    nL_after = n_actors(pl.renderers[0])
    nR_after = n_actors(pl.renderers[1])
    print(f"    右建完：renderer[0]={nL_after}  renderer[1]={nR_after}",
          flush=True)

    check("右视口机组的 actor 进了 renderer[1]（不是 [0]）",
          nR_after > nR_before,
          f"renderer[1] {nR_before}→{nR_after}")
    check("左视口没被右建污染（renderer[0] 数量不变）",
          nL_after == nL_before,
          f"renderer[0] {nL_before}→{nL_after}")
    check("右视口 actor 数 == 一台机组的 actor 数",
          nR_after - nR_before == len(turbine_actors(right)),
          f"新增 {nR_after - nR_before}，一台 {len(turbine_actors(right))}")

    print("\n== 相机位姿有效性 ==", flush=True)
    ctx = {"yaw": np.array([0.0])}
    pos, focal, up, fov = sensor.camera_pose(ctx, si, scale=1.0)
    check("相机 position 全有限（无 NaN）", np.all(np.isfinite(pos)), str(pos))
    check("相机 focal 全有限", np.all(np.isfinite(focal)), str(focal))
    # 机组在相机视线前方：focal 应落在机组附近（转子平面）
    hub = np.array([sc.turbines[ti].x, sc.turbines[ti].y, sc.hub_height])
    check("焦点落在转子平面附近（对着叶片，不是 95m 外空处）",
          np.linalg.norm(focal - hub) < 1.5 * sensor.TIP_R,
          f"|focal-hub|={np.linalg.norm(focal - hub):.0f} m (TIP_R {sensor.TIP_R})")

    # 把右视口相机真设一遍，看会不会把机组框出画外
    cam = pl.renderers[1].camera
    cam.SetParallelProjection(False)
    cam.view_angle = float(fov)
    cam.position = tuple(pos)
    cam.focal_point = tuple(focal)
    cam.up = tuple(up)
    pl.renderers[1].ResetCameraClippingRange()
    b = right["blades"][0].GetBounds() if "blades" in right else None
    print(f"    右视口叶片 bounds: {None if b is None else [round(x,1) for x in b]}",
          flush=True)

    print()
    if FAILS:
        print(f"RIGHTVP_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("RIGHTVP_OK — 右视口拓扑正确；若真机仍无画面，问题在相机取景/视口尺寸")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("RIGHTVP_FAILED — 未捕获异常")
        sys.exit(1)
