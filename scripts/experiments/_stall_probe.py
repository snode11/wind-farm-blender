"""3144 ms 卡顿定位：把主线程的每次循环拆成三段分别计时。

判据是互斥的，所以能直接定位而不用猜：

    t_pe    processEvents 的耗时     → 大 = 主线程自己在干活（_tick/_render）
    t_slp   time.sleep(0.02) 的超时  → 大 = GIL 被工作线程攥住不放
    余下                              → 调度延迟

同时把 `_tick` / `_render` 各自的耗时也记下来，若 t_pe 大，可以直接说出是哪个。

跑：
    mpiexec -n 1 python scripts/experiments/_stall_probe.py
    mpiexec -n 1 python scripts/experiments/_stall_probe.py --no-qt   # 纯线程基线
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


def run_qt(seconds):
    from qtpy import QtWidgets
    from wfrl.studio.app import StudioWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StudioWindow(scene(), wake=True, train_kw=dict(
        iters=2, n_steps=8, warmup_steps=2, mb_steps=4, n_epochs=3))
    win.show()

    # 给两个定时器回调套上计时器：卡在哪个上要能指名道姓
    t_tick, t_render = [], []
    raw_tick, raw_render = win._tick, win._render

    def tick():
        t0 = time.perf_counter()
        raw_tick()
        t_tick.append(time.perf_counter() - t0)

    def render():
        t0 = time.perf_counter()
        raw_render()
        t_render.append(time.perf_counter() - t0)

    win.timer.timeout.disconnect(); win.timer.timeout.connect(tick)
    win.rtimer.timeout.disconnect(); win.rtimer.timeout.connect(render)

    # `_tick`/`_render` 之外还有一段在 processEvents 里 —— VTK 的实际绘制发生在
    # 控件的 paintEvent，不在我们的回调里。分别计时才不会又指错人。
    t_paint, t_drain, t_redraw = [], [], []
    itr = win.plotter.interactor
    raw_paint = itr.paintEvent

    def paint(ev):
        t0 = time.perf_counter()
        raw_paint(ev)
        t_paint.append(time.perf_counter() - t0)

    itr.paintEvent = paint

    raw_drain, raw_redraw = win.view.drain_wake, win.view._redraw

    def drain():
        t0 = time.perf_counter()
        r = raw_drain()
        t_drain.append(time.perf_counter() - t0)
        return r

    t_deform = []
    raw_dd = win.view.drain_deform

    def redraw(**kw):
        t0 = time.perf_counter()
        raw_redraw(**kw)
        t_redraw.append(time.perf_counter() - t0)

    def drain_deform():
        t0 = time.perf_counter()
        r = raw_dd()
        t_deform.append(time.perf_counter() - t0)
        return r

    win.view.drain_wake = drain
    win.view.drain_deform = drain_deform
    win.view._redraw = redraw

    for _ in range(30):
        QtWidgets.QApplication.processEvents(); time.sleep(0.02)
    win.train_panel.btn_start.click()

    pe, slp, gap = [], [], []
    t0 = time.time()
    while time.time() - t0 < seconds:
        a = time.perf_counter()
        QtWidgets.QApplication.processEvents()
        b = time.perf_counter()
        time.sleep(0.02)
        c = time.perf_counter()
        pe.append(b - a); slp.append(c - b - 0.02); gap.append(c - a)

    print(f"\n== Qt 主窗口（{seconds:.0f} s，训练在跑）==", flush=True)
    report("QT", "总间隔      ", gap)
    report("QT", "processEvents", pe)
    report("QT", "sleep 超时  ", slp)
    report("QT", "_tick       ", t_tick)
    report("QT", "_render     ", t_render)
    report("QT", "paintEvent  ", t_paint)
    report("QT", "drain_wake  ", t_drain)
    report("QT", "_redraw     ", t_redraw)
    report("QT", "drain_deform", t_deform)
    snap = win.trainer.latest()
    print(f"  快照: step={getattr(snap, 'step', None)} "
          f"phase={getattr(snap, 'phase', None)}", flush=True)
    win.close()
    for _ in range(20):
        QtWidgets.QApplication.processEvents(); time.sleep(0.02)


def run_thread(seconds):
    """无 Qt 基线：同一个 Trainer，主线程只 sleep 轮询。"""
    import threading                                          # noqa: F401
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
    ap.add_argument("--no-qt", action="store_true")
    a = ap.parse_args()
    (run_thread if a.no_qt else run_qt)(a.seconds)
