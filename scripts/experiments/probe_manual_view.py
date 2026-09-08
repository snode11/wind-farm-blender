"""手动摆位 + 单风机窗口的离线验收（不起 Qt 事件循环，用离屏渲染）。

第二批「手动调姿 + 单风机窗口」的纯逻辑部分。要证的都不依赖事件循环：

  1 set_manual 真的改了机组姿态（user_matrix 变），并立即反映到画面
  2 手动锁定的机组，on_frame（仿真快照）不再覆盖它 —— 否则手动摆好的角度
    会被下一个控制步顶回去；未锁定的机组仍照常被快照更新
  3 clear_manual 解锁后 on_frame 重新接管
  4 转速走 _rpm，tick 会推进自转（叶片方位角变）
  5 弹出窗口能为单台独立 build_turbine（actor 不跨 render window 共享），
    且从主 view 的共享姿态读值渲染

末行 MANUAL_VIEW_OK / MANUAL_VIEW_FAIL。
"""
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import pyvista as pv                                              # noqa: E402

from wfrl.scene.schema import Scene, SensorSpec, Turbine          # noqa: E402
from wfrl.studio import SceneView                                 # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def scene():
    return Scene(
        name="probe_manual", backend="fastfarm", dt=3.0,
        turbines=[Turbine("W1", 0.0, 0.0), Turbine("W2", 630.0, 0.0),
                  Turbine("W3", 1260.0, 0.0)],
        wind_speed=8.0, wind_direction=270.0, controls=["yaw"],
        sensors=[SensorSpec(type="power")],
    ).validate()


def measure(sc, yaw, pitch=None, rpm=None):
    n = sc.n
    return {"yaw": np.asarray(yaw, float),
            "pitch_meas": np.zeros(n) if pitch is None
            else np.asarray(pitch, float),
            "rotor_speed": np.full(n, 9.0) if rpm is None
            else np.asarray(rpm, float),
            "m_flap": None, "_meta": {"u_inf": sc.wind_speed}}


def main():
    sc = scene()
    pl = pv.Plotter(off_screen=True, window_size=(700, 520))
    view = SceneView(pl, sc, wake=False)

    print("== 1. set_manual 改姿态 ==", flush=True)
    m0 = np.array(view.turbines[1]["nac"].user_matrix).copy()
    view.set_manual("yaw", 1, 25.0)
    m1 = np.array(view.turbines[1]["nac"].user_matrix)
    check("W2 手动偏航 25° 改了机舱变换矩阵", not np.allclose(m0, m1))
    check("view._yaw[1] == 25", abs(view._yaw[1] - 25.0) < 1e-9,
          f"{view._yaw[1]}")
    check("W2 被标记为手动锁定", view.is_manual("yaw", 1))
    check("W1 未被锁定", not view.is_manual("yaw", 0))

    print("\n== 2. 锁定的机组不被仿真快照覆盖 ==", flush=True)
    # 一帧快照，所有机组偏航都想改成 0：W2 已锁定应保持 25，W1/W3 归 0
    view.on_frame({"_meta": {"u_inf": 8.0}},
                  measure(sc, yaw=[0.0, 0.0, 0.0]))
    check("W2 锁定，仍是 25°", abs(view._yaw[1] - 25.0) < 1e-9,
          f"{view._yaw[1]}")
    check("W1 未锁定，被快照更新（此处本就是 0）",
          abs(view._yaw[0] - 0.0) < 1e-9, f"{view._yaw[0]}")
    # 再来一帧把 W1 推到 10：W1 应跟随，W2 仍守 25
    view.on_frame({"_meta": {"u_inf": 8.0}},
                  measure(sc, yaw=[10.0, 0.0, 5.0]))
    check("W1 跟随快照到 10°", abs(view._yaw[0] - 10.0) < 1e-9,
          f"{view._yaw[0]}")
    check("W2 仍守手动值 25°", abs(view._yaw[1] - 25.0) < 1e-9,
          f"{view._yaw[1]}")
    check("W3 跟随快照到 5°", abs(view._yaw[2] - 5.0) < 1e-9, f"{view._yaw[2]}")

    print("\n== 3. clear_manual 解锁后重新接管 ==", flush=True)
    view.clear_manual("yaw", 1)
    check("W2 解锁", not view.is_manual("yaw", 1))
    view.on_frame({"_meta": {"u_inf": 8.0}},
                  measure(sc, yaw=[10.0, 3.0, 5.0]))
    check("W2 解锁后跟随快照到 3°", abs(view._yaw[1] - 3.0) < 1e-9,
          f"{view._yaw[1]}")

    print("\n== 4. 手动转速驱动自转 ==", flush=True)
    view.set_manual("rpm", 0, 12.0)
    check("_rpm[0] == 12", abs(view._rpm[0] - 12.0) < 1e-9, f"{view._rpm[0]}")
    spin0 = float(view._spin[0])
    view.tick(1.0)                       # 1 s：12 rpm ×6 = 72°/s
    check("tick 后 W1 方位角推进（叶片真的转）",
          abs(view._spin[0] - spin0) > 1.0,
          f"Δspin={view._spin[0] - spin0:.1f}° （期望 ~72°）")

    print("\n== 5. 多通道并存锁定 ==", flush=True)
    view.set_manual("pitch", 2, 8.0)
    view.set_manual("yaw", 2, -15.0)
    locks = view.manual_locks()
    check("W3 的 pitch 与 yaw 同时锁定",
          ("pitch", 2) in locks and ("yaw", 2) in locks, str(sorted(locks)))
    view.on_frame({"_meta": {"u_inf": 8.0}},
                  measure(sc, yaw=[0, 0, 0], pitch=[0, 0, 0]))
    check("W3 pitch 守 8°", abs(view._pitch[2] - 8.0) < 1e-9, f"{view._pitch[2]}")
    check("W3 yaw 守 -15°", abs(view._yaw[2] + 15.0) < 1e-9, f"{view._yaw[2]}")
    view.clear_manual()
    check("clear_manual() 全解", not view.manual_locks(), str(view.manual_locks()))

    print("\n== 6. 单风机窗口能独立建单台并从共享姿态渲染 ==", flush=True)
    # 不起 QMainWindow（要事件循环），直接验证它依赖的两件事：
    #   a) 能在另一个 plotter 上为单台 build_turbine
    #   b) update_turbine 用主 view 的共享姿态能改这台的矩阵
    from wfrl.viz.animate import build_turbine, turbine_actors, update_turbine
    pl2 = pv.Plotter(off_screen=True, window_size=(400, 400))
    idx = 1
    t = sc.turbines[idx]
    turb = build_turbine(pl2, t.x, t.y, sc.hub_height, scale=view.turb_scale)
    check("弹出窗口独立建出单台（actor 不共享主窗口）",
          len(turbine_actors(turb)) >= 2 and turb is not view.turbines[idx],
          f"{len(turbine_actors(turb))} 个 actor")
    view._yaw[idx] = 30.0
    view._pitch[idx] = 5.0
    mb0 = np.array(turb["nac"].user_matrix).copy()
    update_turbine(turb, float(view._yaw[idx]), float(view._spin[idx]),
                   float(view._pitch[idx]), flex_scale=view.flex_scale)
    mb1 = np.array(turb["nac"].user_matrix)
    check("单风机窗口按主 view 的共享姿态重绘（矩阵随之变）",
          not np.allclose(mb0, mb1))

    print()
    if FAILS:
        print(f"MANUAL_VIEW_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("MANUAL_VIEW_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("MANUAL_VIEW_FAILED — 未捕获异常")
        sys.exit(1)
