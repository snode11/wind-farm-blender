"""后台 Trainer 运行时的主线程调度延迟探针。

报告 sleep 超时与轮询间隔；运行会启动 FAST.Farm。
    mpiexec -n 1 python scripts/experiments/_stall_probe.py --seconds 90
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfrl.scene.schema import Scene, SensorSpec, Turbine     # noqa: E402


def scene():
    return Scene(
        name="stall", backend="fastfarm", dt=3.0,
        turbines=[Turbine("W1", 0.0, 0.0), Turbine("W2", 630.0, 126.0),
                  Turbine("W3", 1260.0, -126.0)],
        wind_speed=8.0, wind_direction=270.0, controls=["yaw"],
        sensors=[SensorSpec(type="power"), SensorSpec(type="actuator"),
                 SensorSpec(type="rotorspeed"),
                 SensorSpec(type="lidar", turbines="W1")],
    ).validate()


def report(tag, name, arr):
    a = np.asarray(arr, float) * 1000.0
    if not len(a):
        print(f"  {tag:10s} {name}: 无样本")
        return
    print(f"  {tag:10s} {name}: n={len(a):5d}  max={a.max():7.0f} ms  "
          f"p99={np.percentile(a, 99):6.0f}  p50={np.median(a):5.1f}  "
          f">300ms={int((a > 300).sum())}", flush=True)


def run_thread(seconds):
    """训练在工作线程运行，主线程用 sleep 轮询。"""
    from wfrl.studio.trainer import Trainer

    tr = Trainer(scene(), iters=2, n_steps=8, warmup_steps=2,
                 mb_steps=4, n_epochs=3)
    tr.start()
    slp, gap = [], []
    t0 = time.time()
    while time.time() - t0 < seconds and tr.running:
        b = time.perf_counter()
        time.sleep(0.02)
        c = time.perf_counter()
        slp.append(c - b - 0.02); gap.append(c - b)
    print(f"\n== 纯线程基线（{seconds:.0f} s）==", flush=True)
    report("THREAD", "总间隔    ", gap)
    report("THREAD", "sleep 超时", slp)
    snap = tr.latest()
    print(f"  快照: step={getattr(snap, 'step', None)}", flush=True)
    tr.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=90.0)
    a = ap.parse_args()
    run_thread(a.seconds)
