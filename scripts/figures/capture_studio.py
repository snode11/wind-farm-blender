"""为 slides 截取 Studio 主窗口（四面板 + 3D 视图）。

与 `capture_rviz.py` 同一套合成做法：Qt 控件层用 `QWidget.grab()`，3D 视口用
VTK 自己的 `screenshot()`，再把后者贴进前者 —— 直接抓整窗会让 OpenGL 子窗口
发黑。这里复用 capture_rviz 的 `grab()`，不重写一遍。

要跑真训练（截图里的功率、约束事件必须是真的，不是摆拍），所以要 mpiexec：

    & "C:\\Program Files\\Microsoft MPI\\Bin\\mpiexec.exe" -n 1 `
      "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" `
      scripts/figures/capture_studio.py

出两张：
    fig_studio_overview.png   训练进行中：3D + 四面板
    fig_studio_safety.png     约束面板特写：占空比清零的事件流
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from qtpy import QtCore, QtWidgets                                   # noqa: E402

from wfrl import paths                                       # noqa: E402
from wfrl.scene.schema import Scene, SensorSpec, Turbine     # noqa: E402
from wfrl.studio.app import StudioWindow                     # noqa: E402

# scripts/ 不是包（没有 __init__.py），按路径加载同目录的 capture_rviz.grab
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from capture_rviz import grab                                # noqa: E402

FIGS = paths.FIGS


def scene():
    return Scene(
        name="Turb3 Row · FAST.Farm", backend="fastfarm", dt=3.0,
        turbines=[Turbine("W1", 0.0, 0.0), Turbine("W2", 630.0, 126.0),
                  Turbine("W3", 1260.0, -126.0)],
        wind_speed=8.0, wind_direction=270.0, controls=["yaw"],
        sensors=[SensorSpec(type="power"), SensorSpec(type="actuator"),
                 SensorSpec(type="rotorspeed"), SensorSpec(type="bladeload"),
                 SensorSpec(type="lidar", turbines="W1")],
    ).validate()


def pump(app, seconds, until=None):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.02)
        if until is not None and until():
            return True
    return until() if until else True


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StudioWindow(scene(), wake=True, train_kw=dict(
        iters=3, n_steps=12, warmup_steps=3, mb_steps=6, n_epochs=4))
    win.show()
    pump(app, 1.0)
    win.train_panel.btn_start.click()

    # 等到曲线上有两轮（一轮只画一个点，画不出线段），最多等 6 分钟
    ok = pump(app, 360, until=lambda: len(win.trainer.history) >= 2)
    snap = win.trainer.latest()
    print(f"[studio-cap] history={len(win.trainer.history)} "
          f"step={getattr(snap, 'step', None)} "
          f"约束事件={len(win.limiter.events)}", flush=True)
    if not ok:
        print("[studio-cap] 没等到两轮更新，曲线会只有一个点", flush=True)

    p = win.plotter
    # 不用 "xz"：侧视下尾流水平切面正好侧对镜头，退化成一条线，图上什么都
    # 看不到。斜俯视 + 正交投影：尾流铺开、塔筒仍竖直（viewup 钉死 +z，不能
    # 靠 camera.elevation 转，那样塔筒会集体歪倒）。
    b = win.view.bounds()
    fp = np.array([(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2])
    r = max(b[1] - b[0], b[3] - b[2]) * 2.0
    e, a = np.deg2rad(38.0), np.deg2rad(-115.0)
    p.enable_parallel_projection()
    p.camera_position = [tuple(fp + r * np.array([np.cos(e) * np.cos(a),
                                                  np.cos(e) * np.sin(a),
                                                  np.sin(e)])),
                         tuple(fp), (0, 0, 1)]
    p.reset_camera()
    p.camera.parallel_scale = 0.62 * max(b[1] - b[0], b[3] - b[2])
    # 状态栏还停在启动那句上，截图里会是过期信息
    h = win.trainer.history
    win.statusBar().showMessage(
        f"训练中 · 步 {snap.step} · 已完成 {len(h)} 轮 PPO 更新 · "
        f"约束事件 {len(win.limiter.events)} 条")
    pump(app, 1.0)
    grab(win, "fig_studio_overview.png")

    # 约束面板特写：单独 grab 那个 dock，不再合成 VTK 层。
    # 先加宽——默认宽度会把事件行截在"占空比 0." 上，而那个数值正是这张图要给的。
    dock = next(d for d in win.findChildren(QtWidgets.QDockWidget)
                if d.windowTitle() == "物理与安全约束")
    win.resizeDocks([dock], [720], QtCore.Qt.Horizontal)
    pump(app, 0.5)
    out = os.path.join(FIGS, "fig_studio_safety.png")
    dock.grab().save(out)
    print(f"[studio-cap] -> {out}", flush=True)

    win.close()
    pump(app, 2.0)


if __name__ == "__main__":
    main()
