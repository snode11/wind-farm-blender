"""三控制量 action space 的 FAST.Farm 冒烟：确认 pitch/torque 真的被下发、
仿真真的有响应、三通道约束都进了回路。

probe_multictrl.py 已经把纯逻辑（维度派生、缩放、路由、约束）离线验证过；这里
只补最后一环 —— 起一次真的 FAST.Farm，看三通道命令到达仿真后**回读**有没有变。
判据不是"训练好"，是**接线通**：

  1 obs/act 维度在真 runtime 上是 5/3（不是回退到单控）
  2 pitch 指令下发后，实测桨距 pitch_meas 有非零响应（或被基线顶替并如实上报）
  3 torque 指令下发后，转矩回读随之变化、rotor_speed 有限
  4 三通道各自的约束事件都出现过（yaw 占空比 / torque 下限，pitch 视工况）
  5 快照里三通道位姿齐全，够 Studio 驱动叶片变桨/自转/尾流偏转

需 mpiexec：
    & "C:\\Program Files\\Microsoft MPI\\Bin\\mpiexec.exe" -n 1 `
      "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" `
      scripts/experiments/probe_multictrl_ff.py
"""
import os
import sys
import time
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfrl.scene.schema import Scene, SensorSpec, Turbine        # noqa: E402
from wfrl.studio.trainer import Snapshot, Trainer               # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def scene():
    return Scene(
        name="probe_ctrl3", backend="fastfarm", dt=3.0,
        turbines=[Turbine("W1", 0.0, 0.0), Turbine("W2", 630.0, 0.0),
                  Turbine("W3", 1260.0, 0.0)],
        wind_speed=8.0, wind_direction=270.0,
        controls=["yaw", "pitch", "torque"],
        sensors=[SensorSpec(type="power"), SensorSpec(type="actuator"),
                 SensorSpec(type="bladeload"), SensorSpec(type="rotorspeed")],
    ).validate()


def wait_step(tr, target, timeout, gaps=None):
    t0 = tlast = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.033)
        now = time.time()
        if gaps is not None:
            gaps.append(now - tlast)
        tlast = now
        if (tr.latest() or Snapshot()).step >= target:
            return True, now - t0
        if not tr.running:
            return (tr.latest() or Snapshot()).step >= target, now - t0
    return False, time.time() - t0


def main():
    sc = scene()
    print(f"== 三控场景 {sc.name}: controls={sc.controls} ==", flush=True)
    # 步数给足以让占空比累起来、让 torque 托底触发；不求训练效果
    tr = Trainer(sc, iters=1, n_steps=12, warmup_steps=2, mb_steps=4,
                 n_epochs=2)

    print("\n== 1. 维度在真 runtime 上是 5/3 ==", flush=True)
    tr.start()
    ok, el = wait_step(tr, 1, 240)
    check("等到第一帧", ok, f"{el:.1f} s（含 spawn）")
    check("obs_keys = yaw,pitch,torque,ws,wd",
          tr.obs_keys == ["yaw", "pitch", "torque",
                          "wind_speed", "wind_direction"], str(tr.obs_keys))
    # actor 的输入输出维度直接问网络
    check("actor 输入 5 维", tr.actor.mu[0].in_features == 5,
          f"{tr.actor.mu[0].in_features}")
    check("actor 输出 3 维", tr.actor.log_std.numel() == 3,
          f"{tr.actor.log_std.numel()}")

    print("\n== 2-3. 三通道命令下发后仿真有回读响应 ==", flush=True)
    # 收集若干步的回读，看 pitch_meas / torque / rotor_speed 是否在变
    pit, tq, rpm, yaw = [], [], [], []
    seen = set()
    deadline = time.time() + 180
    while tr.running and len(seen) < 8 and time.time() < deadline:
        s = tr.latest()
        if s and s.step not in seen and s.measure:
            seen.add(s.step)
            m = s.measure
            pit.append(np.ravel(m.get("pitch_meas", [np.nan]))[0])
            tq.append(np.ravel(m.get("torque", [np.nan]))[0])
            rpm.append(np.ravel(m.get("rotor_speed", [np.nan]))[0])
            yaw.append(np.ravel(m.get("yaw", [np.nan]))[0])
        time.sleep(0.1)

    tq = np.array(tq, float)
    rpm = np.array(rpm, float)
    pit = np.array(pit, float)
    check("采到多步回读", len(seen) >= 4, f"{len(seen)} 步: {sorted(seen)}")
    check("torque 回读有限且非零", np.any(np.isfinite(tq) & (np.abs(tq) > 1)),
          f"样本 {np.round(tq[np.isfinite(tq)][:5], 0)}")
    check("torque 回读随控制步变化（不是钉死常数）",
          np.nanstd(tq) > 1.0, f"std={np.nanstd(tq):.1f} N·m")
    check("rotor_speed 有限", np.any(np.isfinite(rpm)),
          f"样本 {np.round(rpm[np.isfinite(rpm)][:5], 2)} rpm")
    check("pitch_meas 有限（基线顶替时应为基线桨距，仍是有限数）",
          np.any(np.isfinite(pit)),
          f"样本 {np.round(pit[np.isfinite(pit)][:5], 3)}°")

    print("\n== 4. 三通道约束都进了回路 ==", flush=True)
    # 再等一会让占空比 / torque 下限触发
    deadline = time.time() + 90
    while tr.running and time.time() < deadline:
        if len(tr.limiter.counts) >= 1 and tr.latest() and tr.latest().step >= 6:
            break
        time.sleep(0.2)
    summ = dict(tr.limiter.summary())
    print(f"    约束计数: {summ}", flush=True)
    controls_hit = {e.control for e in tr.limiter.events}
    check("产生了约束事件", bool(summ), str(summ))
    check("yaw 通道有约束（占空比清零/越界）", "yaw" in controls_hit,
          str(sorted(controls_hit)))
    check("torque 通道有约束（下限托底/顶替语义）",
          "torque" in controls_hit,
          "torque 未触发（工况下转矩已在 60% 额定之上也算正常）"
          if "torque" not in controls_hit else str(sorted(controls_hit)))

    print("\n== 5. 快照位姿够 Studio 驱动 ==", flush=True)
    s = tr.latest()
    check("measure 带 yaw/pitch_meas/rotor_speed/m_flap",
          all(k in s.measure for k in
              ("yaw", "pitch_meas", "rotor_speed", "m_flap")),
          f"{sorted(s.measure)}")

    print("\n== 收尾 ==", flush=True)
    t0 = time.time()
    tr.stop(timeout=300)
    print(f"  stop() 耗时 {time.time() - t0:.1f} s", flush=True)
    check("线程干净退出", not tr.running)
    check("训练线程无异常", tr.error is None, (tr.error or "")[-400:])

    print()
    if FAILS:
        print(f"MULTICTRL_FF_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("MULTICTRL_FF_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("MULTICTRL_FF_FAILED — 未捕获异常")
        sys.exit(1)
