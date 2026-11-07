"""机舱相机传感器的数值检查：配置、位姿、视锥与覆盖关系。

不创建渲染窗口或启动仿真；末行 CAMERA_OK / CAMERA_FAILED。
"""
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


from wfrl.channels import registry                                # noqa: E402
from wfrl.scene.schema import Scene, SensorSpec, Turbine          # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def scene(params=None):
    sensors = [SensorSpec(type="camera", turbines="T1", params=params or {})]
    return Scene(name="pc", backend="fastfarm", dt=3.0,
                 turbines=[Turbine("T1", 0, 0), Turbine("T2", 504, 0)],
                 wind_speed=8.0, wind_direction=270.0,
                 controls=["yaw"], sensors=sensors).validate()


def build_cam(params=None):
    sc = scene(params)
    sensors = registry.build(sc)
    cam = next(s for s in sensors if s.type_name == "camera")
    return sc, cam


def main():
    print("== 1. registry.build + params 默认/覆盖 ==", flush=True)
    _sc, cam = build_cam()
    check("camera 传感器被 build 出来", cam.type_name == "camera")
    check("默认 fov 与配置一致", cam._p("fov") == cam.DEFAULTS["fov"], f"{cam._p('fov')}")
    check("默认 pitch 与配置一致", cam._p("pitch") == cam.DEFAULTS["pitch"], f"{cam._p('pitch')}")
    _sc2, cam2 = build_cam({"fov": 15, "pitch": -60, "mount": "hub"})
    check("显式 params 覆盖", cam2._p("fov") == 15 and cam2._p("pitch") == -60
          and cam2._p("mount") == "hub")

    print("\n== 2. camera_pose 机舱下俯视 ==", flush=True)
    ctx0 = {"yaw": np.array([0.0])}
    pos, focal, up, fov = cam.camera_pose(ctx0, 0, scale=1.0)
    check("position 在轮毂高度附近", abs(pos[2] - cam.HUB_H) < 10.0,
          f"z={pos[2]:.1f} (hub {cam.HUB_H})")
    check("焦点朝下（focal.z < pos.z）", focal[2] < pos[2],
          f"focal.z={focal[2]:.1f} < pos.z={pos[2]:.1f}")
    # yaw=0 时转子在 −x（上风向）。相机看本机叶片 ⇒ 焦点应在轮毂的 −x 侧，
    # 不能在 +x（那是机舱尾部/下风向，会看向下一台）。
    check("焦点在转子侧（−x，本机叶片），不是尾部", focal[0] < pos[0],
          f"focal.x={focal[0]:.1f} < pos.x={pos[0]:.1f}")
    check("挂载点在转子侧或轮毂上方（不在尾部 +x）", pos[0] <= 0.5,
          f"pos.x={pos[0]:.2f}")
    check("fov 与配置一致", abs(fov - cam.DEFAULTS["fov"]) < 1e-6, f"{fov}")

    print("\n== 3. 随机组偏航转 ==", flush=True)
    p0, f0, _u, _fov = cam.camera_pose({"yaw": np.array([0.0])}, 0, scale=1.0)
    p30, f30, _u, _fov = cam.camera_pose({"yaw": np.array([30.0])}, 0, scale=1.0)
    check("偏航 30° 后相机位姿改变（跟着机舱转）",
          not np.allclose(p0, p30) or not np.allclose(f0, f30),
          f"Δfocal={np.linalg.norm(f0 - f30):.1f} m")

    print("\n== 4. pitch 更负 ⇒ 焦点更低 ==", flush=True)
    _s, cam_shallow = build_cam({"pitch": -15})
    _s, cam_steep = build_cam({"pitch": -70})
    fl_sh = cam_shallow.camera_pose(ctx0, 0)[1][2]
    fl_st = cam_steep.camera_pose(ctx0, 0)[1][2]
    check("俯仰 -70° 焦点比 -15° 更低", fl_st < fl_sh,
          f"-70°→z={fl_st:.0f}  -15°→z={fl_sh:.0f}")

    print("\n== 5. 视锥几何 ==", flush=True)
    pts0 = cam._frustum_points({"yaw": np.array([0.0])}, 0, scale=1.0)
    pts1 = cam._frustum_points({"yaw": np.array([40.0])}, 0, scale=1.0)
    check("视锥有 5 个点（顶点+底面四角）", pts0.shape == (5, 3), f"{pts0.shape}")
    check("偏航后视锥跟着转", not np.allclose(pts0, pts1))

    print("\n== 6. 覆盖单调：窄 fov 看局部、宽 fov 看更大范围 ==", flush=True)
    def half_width(fov):
        _s, c = build_cam({"fov": fov})
        p, f, _u, _fv = c.camera_pose(ctx0, 0, scale=1.0)
        rng = np.linalg.norm(f - p)          # 焦点距离（对着转子平面，≈0.9R）
        return np.tan(np.deg2rad(fov) / 2.0) * rng
    w15, w40, w70 = half_width(15), half_width(40), half_width(70)
    check("视野半宽随 fov 单调增（短焦↔长焦）", w15 < w40 < w70,
          f"15°→{w15:.0f}m  40°→{w40:.0f}m  70°→{w70:.0f}m")
    check("窄 fov(15°) 只覆盖叶片局部（半宽 < 1/3 叶片）", w15 < 63.0 / 3,
          f"{w15:.0f}m")
    check("宽 fov(70°) 覆盖范围显著大于窄 fov（≥3×）", w70 > 3 * w15,
          f"70°/15° = {w70 / max(w15, 1e-6):.1f}×")

    print()
    if FAILS:
        print(f"CAMERA_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("CAMERA_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("CAMERA_FAILED — 未捕获异常")
        sys.exit(1)
