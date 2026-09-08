"""D1 收口：一份场景文件同时驱动仿真与 3D 画面。

必须经官方 mpiexec 启动：
    & "C:\\Program Files\\Microsoft MPI\\Bin\\mpiexec.exe" -n 1 `
      "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" `
      scripts/experiments/probe_scene_live.py

前两个验收各证一半（probe_scene_runtime 证仿真、probe_scene_view 证画面），
这里把它们接到**同一个 Scene 对象**上跑一遍，证的是那句完整的话：
改 YAML 里的机位，3D 和仿真同时变。

判据：
  1. 画面上机组的位置 == 算例里的坐标 == YAML 里的坐标（三者同一串数）
  2. 真实 FAST.Farm 的偏航/转速传到画面，机组姿态跟着动
  3. lidar 点云由真 driver 的数据更新（有湍流盒时读盒子）
  4. 关掉通道 ⇒ 停止采样 + 画面像素变少（同一次运行里前后两张图对比）
  5. 收尾无孤儿进程
"""
import os
import subprocess
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import pyvista as pv                                              # noqa: E402

from wfrl.scene import SceneRuntime                               # noqa: E402
from wfrl.scene.schema import (Scene, SensorSpec, Turbine)        # noqa: E402
from wfrl.studio import SceneView                                 # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "results", "runs")
FAILS = []


def fastfarm_processes():
    """Return FAST.Farm processes on both Windows and Unix hosts."""
    cmd = (["tasklist"] if os.name == "nt"
           else ["pgrep", "-f", "[F]AST.Farm"])
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""
STEPS = 5


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def ink(img):
    a = np.asarray(img)[..., :3].astype(int)
    return int((a.sum(axis=-1) < 720).sum())


def custom_scene():
    """非预注册布局：5D 间距 + 横向错开 126 m。"""
    return Scene(
        name="probe_live", backend="fastfarm", dt=3.0,
        turbines=[Turbine("W1", 0.0, 0.0),
                  Turbine("W2", 630.0, 126.0),
                  Turbine("W3", 1260.0, -126.0)],
        wind_speed=8.0, wind_direction=270.0, controls=["yaw"],
        sensors=[SensorSpec(type="power"), SensorSpec(type="actuator"),
                 SensorSpec(type="bladeload"), SensorSpec(type="rotorspeed"),
                 SensorSpec(type="lidar", turbines="W1")],
    ).validate()


def center(act):
    b = act.GetBounds()
    return np.array([(b[0] + b[1]) / 2, (b[2] + b[3]) / 2])


def main():
    os.makedirs(OUT, exist_ok=True)
    sc = custom_scene()
    print(f"== 场景 {sc.name}: x={sc.xcoords} y={sc.ycoords} ==", flush=True)

    rt = SceneRuntime(sc, max_steps=STEPS + 5, warmup_steps=2)
    pl = pv.Plotter(off_screen=True, window_size=(1100, 700))
    # 尾流平面开着：它也是从场景生成的 FLORIS proxy，同样不查预注册表
    view = SceneView(pl, sc, wake=True)

    print("\n== 1. 三串坐标必须是同一串 ==", flush=True)
    frame = rt.reset()
    fc = rt.driver.env.mdp.farm_case
    drawn = np.array([center(t["tower"]) for t in view.turbines])
    check("YAML == 算例", list(fc.xcoords) == sc.xcoords
          and list(fc.ycoords) == sc.ycoords, f"算例 x={list(fc.xcoords)}")
    check("YAML == 画面", np.allclose(drawn[:, 0], sc.xcoords, atol=1.0)
          and np.allclose(drawn[:, 1], sc.ycoords, atol=1.0),
          f"画面 x={np.round(drawn[:, 0], 0)}")
    check("不是预注册的 Turb3_Row1",
          not np.allclose(drawn[:, 0], [0.0, 504.0, 1008.0], atol=1.0))
    print(f"  u_inf = {rt.u_inf:.3f} m/s", flush=True)
    # proxy 建不起来只打印一行就跳过，画面上就少一张尾流图而不报错 —— 必须断言
    check("尾流 proxy 已建（同一份场景生成，不查预注册表）",
          view.sgrid is not None)
    if view.fi is not None:
        check("proxy 布局 == 场景布局",
              np.allclose(view.fi.layout_x, sc.xcoords)
              and np.allclose(view.fi.layout_y, sc.ycoords),
              f"{np.round(view.fi.layout_x, 0)}")
        # 切面按物理 90 m 解，但机组按 turb_scale 画 —— 不抬 z 就铺在塔基上
        zw = float(np.median(view.sgrid.points[:, 2]))
        check("尾流切面抬到画面轮毂高度",
              abs(zw - sc.hub_height * view.turb_scale) < 1.0,
              f"切面 z={zw:.0f} m，画面轮毂 {sc.hub_height * view.turb_scale:.0f} m")

    view.attach_sensors(rt)
    check("lidar 在画面上有点云", view.has_actors("lidar"))

    print("\n== 2. 真实 FAST.Farm 驱动画面 ==", flush=True)
    poses = []
    for k in range(STEPS):
        # 给一点偏航，才看得出画面是被真实测量驱动的而不是钉死的
        frame = rt.step(np.full(sc.n, 4.0))
        view.on_frame(frame, rt.last_measure, ctx=rt.last_ctx)
        view.tick(0.2)
        poses.append(np.array(view.turbines[0]["nac"].user_matrix).copy())
        print(f"  step {k+1}: 全场 {frame['_meta']['farm_power']:.3f} MW  "
              f"yaw={np.round(rt.last_measure['yaw'], 1)}  "
              f"rpm={np.round(rt.last_measure['rotor_speed'], 2)}", flush=True)
    check("偏航累加到画面（机舱变换逐步变化）",
          not np.allclose(poses[0], poses[-1]),
          f"最大差 {np.abs(poses[-1] - poses[0]).max():.3f}")
    check("画面偏航 == 仿真偏航",
          np.allclose(view._yaw, rt.last_measure["yaw"], atol=1e-9),
          f"{np.round(view._yaw, 2)}")
    check("转速非零（叶片在真转）",
          bool(np.all(np.asarray(view._rpm)[np.isfinite(view._rpm)] > 1.0)),
          f"{np.round(view._rpm, 2)}")

    print("\n== 3. 通道开关：停采样 + 画面变化 ==", flush=True)
    pl.camera_position = "xz"
    pl.reset_camera(bounds=view.bounds())
    pl.render()
    on_png = os.path.join(OUT, "_scene_live_on.png")
    pl.screenshot(on_png)
    i_on = ink(pl.screenshot(return_img=True))
    lidar = rt.sensor_of("lidar")
    k0 = lidar._k
    view.set_channel("lidar", False, runtime=rt)
    f2 = rt.step(np.zeros(sc.n))
    view.on_frame(f2, rt.last_measure, ctx=rt.last_ctx)
    view.tick(0.2)
    pl.render()
    off_png = os.path.join(OUT, "_scene_live_off.png")
    pl.screenshot(off_png)
    i_off = ink(pl.screenshot(return_img=True))
    check("lidar 不在 frame 里", "lidar" not in f2)
    check("lidar 传感器停止采样", lidar._k == k0, f"{k0} → {lidar._k}")
    check("画面像素变少", i_off < i_on, f"{i_on} → {i_off}（差 {i_on - i_off}）")
    print(f"  截图: {on_png}\n        {off_png}", flush=True)

    print("\n== 4. 收尾 ==", flush=True)
    case_dir = rt.driver.case_dir
    pl.close()
    rt.close(purge=True)
    if case_dir:
        check("算例目录已清", not os.path.exists(case_dir), case_dir)
    out = fastfarm_processes()
    check("无 FAST.Farm 孤儿进程", "FAST.Farm" not in out)

    print()
    if FAILS:
        print(f"SCENE_LIVE_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("SCENE_LIVE_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("SCENE_LIVE_FAILED — 未捕获异常")
        sys.exit(1)
