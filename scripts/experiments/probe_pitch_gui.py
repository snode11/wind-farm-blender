"""FAST.Farm + GUI：实测桨距是否真的进了面板、并驱动叶片几何。

离线的 D 组用 FLORIS 喂数据，那条路根本没有 `pitch_meas` 通道；headless 又不
开窗口。两者都覆盖不到"大风档基线顺桨 → 画面上叶片跟着扭"这条链路，而它恰好
就是演示要走的那条。所以这里真起 FAST.Farm、真开窗口，跑几步后对内部状态下断言。

    mpiexec -n 1 python -X utf8 scripts/experiments/probe_pitch_gui.py [--wind strong]
"""
import argparse
import os
import sys

import numpy as np
import pyvista as pv
from qtpy import QtCore, QtWidgets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfrl import paths, windcond                      # noqa: E402
from wfrl.viz.rviz_app import FastFarmSource, MainWindow  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--wind", default="strong")
ap.add_argument("--seconds", type=float, default=45.0)
args = ap.parse_args()

ws, _, turb = windcond.resolve(args.wind, backend="fastfarm")
print(f"[probe] 风况 {args.wind} → wind_speed={ws} turb={turb}", flush=True)

pv.OFF_SCREEN = False
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
src = FastFarmSource(env_id="Dec_Turb3_Row1_Fastfarm", policy="auto",
                     max_steps=30, controls=("yaw", "pitch"),
                     wake_vtk=False, wind_time_series=turb, wind_speed=ws)
win = MainWindow(src, allow_drag=False, tick_ms=120, flex_scale=5.0,
                 render_ms=33, terrain_kind="mountains",
                 dual_view=True, focus=1)
win.resize(1280, 720)
win.show()
win.raise_()

st = {}


def finish():
    st["cmd"] = None if win._pitch_cmd is None else np.copy(win._pitch_cmd)
    st["meas"] = None if win._pitch_meas is None else np.copy(win._pitch_meas)
    st["used"] = None if win._pitch is None else np.copy(win._pitch)
    # 面板里那两列的**文本**——通道接没接上，看屏幕上的字最直接
    hdr = [win.telemetry.COLS[c][0] for c in range(len(win.telemetry.COLS))]
    ic, im = hdr.index("桨距指令°"), hdr.index("桨距实测°")
    st["txt"] = [(win.telemetry.item(i, ic).text(),
                  win.telemetry.item(i, im).text())
                 for i in range(win.telemetry.rowCount())]
    d = os.path.join(paths.ROOT, "results", "verify")
    os.makedirs(d, exist_ok=True)
    import imageio.v2 as iio
    iio.imwrite(os.path.join(d, f"pitch_gui_{args.wind}.png"),
                np.asarray(win.plotter.screenshot(return_img=True)))
    win.close()
    app.quit()


QtCore.QTimer.singleShot(int(1000 * args.seconds), finish)
app.exec_()
if hasattr(src, "close"):
    src.close()

ok = True


def chk(label, cond, detail=""):
    global ok
    ok = bool(cond) and ok
    print(f"  {'v' if cond else 'X'} {label}" + (f" — {detail}" if detail else ""),
          flush=True)


print(f"\n[probe] {args.wind} 结果", flush=True)
chk("拿到实测桨距", st["meas"] is not None and np.isfinite(st["meas"]).any(),
    str(np.round(st["meas"], 2) if st["meas"] is not None else None))
chk("拿到指令桨距", st["cmd"] is not None, str(np.round(st["cmd"], 2)
                                              if st["cmd"] is not None else None))
chk("叶片几何用的是实测那一路",
    st["used"] is not None and st["meas"] is not None
    and np.allclose(st["used"], st["meas"], equal_nan=True))
if st["meas"] is not None and st["cmd"] is not None:
    gap = float(np.nanmax(np.abs(st["meas"] - st["cmd"])))
    # strong/gale 档上游机组基线自己在顺桨，两列必然分开；calm 档基线≈0，
    # 两列只差一个控制步的滞后，所以这条只对大风档断言。
    if args.wind in ("strong", "gale"):
        chk("大风档两列确实分开（基线在顺桨，不是把指令抄了一遍）", gap > 3.0,
            f"max|实测-指令| = {gap:.2f}°")
    else:
        print(f"  · max|实测-指令| = {gap:.2f}°（小风档预期接近，不作断言）",
              flush=True)
bad = [t for t in st["txt"] if t[1] in ("", "-")]
chk("面板实测列每台机组都有读数", not bad, str(st["txt"]))
print("PITCH_GUI_OK" if ok else "PITCH_GUI_FAILED", flush=True)
sys.exit(0 if ok else 1)
