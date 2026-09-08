"""验证转矩双向包线在真 FAST.Farm 上确实防住失速（下游 T2/T3 不再停转）。

背景：未训练策略随机下发转矩，`GenTrq=TorqueRef` 完全顶替基线（DISCON.F90:440）。
修复前，转矩每步 +1000 无上限累加，下游在尾流里被刹到 TSR≈2、BEM 关闭、转子
失速停转（诊断实测 T2/T3 转速 7.7→逐步跌）。修复后，safety._torque_semantics
把绝对转矩锚定在基线 VS 曲线的 ±25% 带内，偏高即被托回 —— 转子不该再失速。

判据（跑一次真 FAST.Farm，三控制量场景）：
  1 三台转速全程 > 3 rpm（TSR 不塌到 BEM 关闭区）
  2 下游 T2/T3 不是单调衰减到停（末段转速 ≥ 前段的一半）
  3 约束层报出「转矩超运行上限（防失速）」事件（说明包线真在拦）

需 mpiexec。约几分钟（一次 spawn）。
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
        name="probe_tqenv", backend="fastfarm", dt=3.0,
        turbines=[Turbine("T1", 0.0, 0.0), Turbine("T2", 504.0, 0.0),
                  Turbine("T3", 1008.0, 0.0)],
        wind_speed=8.0, wind_direction=270.0,
        controls=["yaw", "pitch", "torque"],
        sensors=[SensorSpec(type="power"), SensorSpec(type="rotorspeed")],
    ).validate()


def main():
    sc = scene()
    # 步数给足以让"修复前会失速"的过程走完：诊断里 6 步就跌明显了，这里跑 ≥12
    tr = Trainer(sc, iters=1, n_steps=14, warmup_steps=2, mb_steps=4, n_epochs=1,
                 seed=0)
    tr.start()
    print("等第一帧（含 spawn）…", flush=True)
    rpm_hist = []                       # 每步 (T1,T2,T3) 转速
    seen = set()
    deadline = time.time() + 300
    while tr.running and time.time() < deadline:
        s = tr.latest()
        if s and s.step not in seen and s.measure:
            seen.add(s.step)
            r = np.ravel(s.measure.get("rotor_speed", []))
            if r.size >= 3:
                rpm_hist.append((s.step, float(r[0]), float(r[1]), float(r[2])))
                print(f"  step {s.step:2d}: T1={r[0]:5.2f}  T2={r[1]:5.2f}  "
                      f"T3={r[2]:5.2f} rpm", flush=True)
        if len(seen) >= 14:
            break
        time.sleep(0.1)

    print("\n收尾…", flush=True)
    tr.stop(timeout=300)

    print("\n== 判据 ==", flush=True)
    check("采到足够步数", len(rpm_hist) >= 8, f"{len(rpm_hist)} 步")
    arr = np.array([[a, b, c] for _s, a, b, c in rpm_hist])   # (steps, 3)
    if arr.size:
        mins = np.nanmin(arr, axis=0)
        check("三台转速全程 > 3 rpm（不失速）", np.all(mins > 3.0),
              f"各台最低 {np.round(mins, 2)}")
        # 下游末段不塌：后 1/3 的均值 ≥ 前 1/3 均值的一半
        k = max(1, len(arr) // 3)
        head = np.nanmean(arr[:k], axis=0)
        tail = np.nanmean(arr[-k:], axis=0)
        check("下游 T2/T3 末段没有塌到停",
              tail[1] > 0.5 * head[1] and tail[2] > 0.5 * head[2],
              f"T2 {head[1]:.2f}→{tail[1]:.2f}  T3 {head[2]:.2f}→{tail[2]:.2f}")

    summ = dict(tr.limiter.summary())
    print(f"    约束计数: {summ}", flush=True)
    check("约束层报出转矩包线事件（说明包线在拦）",
          any("转矩" in k for k in summ), str(summ))

    print()
    if FAILS:
        print(f"TORQUE_ENV_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("TORQUE_ENV_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("TORQUE_ENV_FAILED — 未捕获异常")
        sys.exit(1)
