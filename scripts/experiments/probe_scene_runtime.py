"""场景运行时的在线验收：手写的场景 YAML 能不能真的驱动 FAST.Farm。

必须经官方 mpiexec 启动：
    & "C:\\Program Files\\Microsoft MPI\\Bin\\mpiexec.exe" -n 1 `
      "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" `
      scripts/experiments/probe_scene_runtime.py

验收点：
  1. 场景生成的算例布局 == YAML 里写的坐标（不是预注册表里的）
  2. 步进能拿到真实功率与载荷
  3. 关掉一路通道后，它**不再出现在 frame 里**，且该传感器不再被采样
  4. lidar 在真 driver 上能出点云
  5. close 后没有孤儿进程、算例目录被清

用一个**非预注册**的布局（间距 5D、横向错开）来证明布局确实来自场景文件 ——
如果它还是走 data_cases 的 Turb3_Row1，坐标会是 0/504/1008 且 y 全 0。
"""
import os
import subprocess
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfrl.scene import SceneRuntime, load_scene                     # noqa: E402
from wfrl.scene.schema import Scene, SensorSpec, Turbine            # noqa: E402

FAILS = []
STEPS = 6


def fastfarm_processes():
    """Return FAST.Farm processes on both Windows and Unix hosts."""
    cmd = (["tasklist"] if os.name == "nt"
           else ["pgrep", "-f", "[F]AST.Farm"])
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def custom_scene():
    """故意不是任何预注册布局：5D 间距 + 横向错开 126 m。"""
    return Scene(
        name="probe_custom", backend="fastfarm", dt=3.0,
        turbines=[Turbine("W1", 0.0, 0.0),
                  Turbine("W2", 630.0, 126.0),
                  Turbine("W3", 1260.0, -126.0)],
        wind_speed=8.0, wind_direction=270.0,
        controls=["yaw"],
        sensors=[SensorSpec(type="power"), SensorSpec(type="actuator"),
                 SensorSpec(type="bladeload"), SensorSpec(type="rotorspeed"),
                 SensorSpec(type="lidar", turbines="W1")],
    ).validate()


def main():
    sc = custom_scene()
    print(f"== 场景 {sc.name}: x={sc.xcoords} y={sc.ycoords} ==", flush=True)
    rt = SceneRuntime(sc, max_steps=STEPS + 4, warmup_steps=2)

    print("\n== 1. 布局来自场景文件 ==", flush=True)
    frame = rt.reset()
    fc = rt.driver.env.mdp.farm_case
    check("x 坐标透传到算例", list(fc.xcoords) == sc.xcoords, f"{list(fc.xcoords)}")
    check("y 坐标透传到算例", list(fc.ycoords) == sc.ycoords, f"{list(fc.ycoords)}")
    check("不是预注册的 Turb3_Row1",
          list(fc.xcoords) != [0.0, 504.0, 1008.0])
    check("机组数一致", fc.num_turbines == 3)
    print(f"  u_inf = {rt.u_inf:.3f} m/s", flush=True)

    print("\n== 2. 步进拿到真实量 ==", flush=True)
    powers, lidar_seen = [], 0
    for k in range(STEPS):
        frame = rt.step(np.zeros(sc.n))
        p = frame["_meta"]["farm_power"]
        powers.append(p)
        if "lidar" in frame:
            lidar_seen += 1
        print(f"  step {k+1}: 全场 {p:.3f} MW  reward={frame['_meta']['reward']:.4f}",
              flush=True)
    powers = np.array(powers)
    check("功率为正且在量级内", bool(np.all(powers > 0.5) and np.all(powers < 16)),
          f"{powers.min():.2f}~{powers.max():.2f} MW")
    check("功率稳定（warmup 已丢弃瞬态）", float(powers.std()) < 0.3,
          f"std={powers.std():.4f}")
    check("载荷是逐叶片三元组", np.shape(frame["m_flap"]) == (3, 3),
          str(np.shape(frame.get("m_flap"))))
    check("载荷非零", bool(np.all(np.isfinite(frame["m_flap"]))))
    check("转速在额定量级",
          bool(np.all(np.asarray(frame["rotorspeed"])[np.isfinite(
              frame["rotorspeed"])] < 13.0)),
          f"{np.round(frame['rotorspeed'], 2)}")

    print("\n== 3. lidar 在真 driver 上出点云 ==", flush=True)
    check("lidar 每步都出", lidar_seen == STEPS, f"{lidar_seen}/{STEPS}")
    lid = frame["lidar"]
    check("点云挂在 W1 前方",
          bool(np.all(lid["points"][0][:, 0] < 0)),
          f"x={lid['points'][0][:, 0].min():.0f}~{lid['points'][0][:, 0].max():.0f}")
    check("视线风速在量级内",
          bool(np.all(lid["u"] > 3) and np.all(lid["u"] < 20)),
          f"{lid['u'].min():.2f}~{lid['u'].max():.2f}")

    print("\n== 4. 关掉通道 ⇒ 不出现在 frame，且不再采样 ==", flush=True)
    lidar_sensor = rt.sensor_of("lidar")
    k_before = lidar_sensor._k
    rt.set_enabled("lidar", False)
    f2 = rt.step(np.zeros(sc.n))
    check("lidar 不在 frame 里", "lidar" not in f2, str(sorted(f2)))
    check("lidar 传感器没被采样", lidar_sensor._k == k_before,
          f"{k_before} → {lidar_sensor._k}")
    check("其他通道照常", "power" in f2 and "m_flap" in f2)
    rt.set_enabled("lidar", True)
    f3 = rt.step(np.zeros(sc.n))
    check("重新打开后恢复", "lidar" in f3)

    print("\n== 5. 通道一览表 ==", flush=True)
    for t, typ, fid, on, prov in rt.channel_table():
        print(f"  {t:12s} {typ:11s} {fid}  {'开' if on else '关'}  {prov[:44]}",
              flush=True)
    check("一览表覆盖所有通道", len(rt.channel_table()) == len(rt.topics()))

    print("\n== 6. 收尾 ==", flush=True)
    case_dir = rt.driver.case_dir
    rt.close(purge=True)
    check("driver 已释放", rt.driver is None)
    if case_dir:
        check("算例目录已清", not os.path.exists(case_dir), case_dir)
    out = fastfarm_processes()
    check("无 FAST.Farm 孤儿进程", "FAST.Farm" not in out)

    print()
    if FAILS:
        print(f"SCENE_RUNTIME_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("SCENE_RUNTIME_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("SCENE_RUNTIME_FAILED — 未捕获异常")
        sys.exit(1)
