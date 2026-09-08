"""为 slides 截取 rviz_app 界面（真实窗口，含 RViz 式侧栏/遥测/工具条）。

截屏做法：Qt 控件用 QWidget.grab() 拿（面板、表格、工具条都对），3D 视口用
VTK 自己的 screenshot 拿（避开 OpenGL 子窗口 BitBlt 发黑的老问题），再把后者
贴进前者。这样窗口被遮挡/不在前台也能出图。

用法：
    "C:/Users/s1155/.conda/envs/wfcrl/python.exe" capture_rviz.py            # FLORIS 三视图
    "C:/Users/s1155/.conda/envs/wfcrl/python.exe" capture_rviz.py --fastfarm # 追加动态后端一张
"""
import argparse
import os

import numpy as np
from PIL import Image
from qtpy import QtCore, QtWidgets

from wfrl import paths

FIGS = paths.FIGS


# ---------------------------------------------------------------------------
def _pump(app, n=8):
    for _ in range(n):
        app.processEvents()


def grab(win, name):
    """合成截图：Qt 控件层 + VTK 视口层。"""
    app = QtWidgets.QApplication.instance()
    win.plotter.render()
    _pump(app)

    tmp = os.path.join(FIGS, "_tmp_chrome.png")
    win.grab().save(tmp)
    base = Image.open(tmp).convert("RGB")

    p = win.plotter
    shot = p.screenshot(None, return_img=True)
    vp = Image.fromarray(np.asarray(shot)[..., :3])

    dpr = base.width / max(win.width(), 1)
    tl = p.mapTo(win, QtCore.QPoint(0, 0))
    vp = vp.resize((max(int(p.width() * dpr), 1), max(int(p.height() * dpr), 1)))
    base.paste(vp, (int(tl.x() * dpr), int(tl.y() * dpr)))

    out = os.path.join(FIGS, name)
    base.save(out)
    os.remove(tmp)
    print(f"[rviz-cap] -> {out}  ({base.width}x{base.height})", flush=True)


ROW_DEG = -15.5          # Ablaincourt 机组连线方向 atan2(-533, 1915)


def _oblique(p, src, elev_deg=50.0, scale_f=0.75):
    """高斜视 + 正交投影：整场尾流拓扑一览，塔筒仍保持竖直。

    不用 view_xy() + camera.elevation：那样 viewup 会跟着转，塔筒在画面里
    集体歪倒。这里直接给相机位置并把 viewup 钉死在 +z。
    方位取机组连线的法向 → 机列横贯画面，串列尾流的逐级加深看得最清楚。
    """
    p.enable_parallel_projection()
    b = p.bounds
    fp = np.array([(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2])
    r = max(b[1] - b[0], b[3] - b[2]) * 2.0
    e, a = np.deg2rad(elev_deg), np.deg2rad(ROW_DEG - 90.0)
    pos = fp + r * np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a),
                             np.sin(e)])
    p.camera_position = [tuple(pos), tuple(fp), (0, 0, 1)]
    p.reset_camera()
    # 拾取地面比机场大得多，reset_camera 按它取景会把机组缩成一小条。
    # 正交投影下直接按机组包围盒定 parallel_scale（= 视口半高，世界单位）。
    # 留够余量：拖拽那张里 T1 会移出原机列，取景太紧会把它裁掉。
    p.camera.parallel_scale = scale_f * max(src.lx.ptp(), src.ly.ptp())


def advance(win, n):
    """手动推进 n 个控制步（定时器已停，保证截图可复现）。"""
    app = QtWidgets.QApplication.instance()
    for _ in range(n):
        win._tick()
        _pump(app, 2)


# ---------------------------------------------------------------------------
def capture_floris(app):
    from wfrl.viz.rviz_app import FarmSource, MainWindow

    src = FarmSource(env_id="Ablaincourt_Floris", policy="auto")
    # env 每次 reset 都会重采风速**和风向**（实测 238°/293°/264°），不固定住的话
    # 每张图的尾流拓扑都不一样，前后对照就没意义了。
    # 285.5° = 机组连线方向 atan2(-533, 1915) 的来流角（FLORIS: 270°→+x），
    # 来流正好沿排布方向 → 完整的串列尾流级联，也正对应理论部分的尾流 DAG。
    src.fi.reinitialize(wind_speeds=[8.0], wind_directions=[285.5])
    src._solve()
    print(f"[rviz-cap] Ablaincourt: {src.n} 台机组", flush=True)
    win = MainWindow(src, allow_drag=True, tick_ms=120)
    win.timer.stop()                      # 由脚本手动驱动
    win.show()
    _pump(app, 20)

    advance(win, 14)                      # 让自动偏航摆出可见的尾流偏转

    # (1) 斜俯视总览
    p = win.plotter
    p.reset_camera()
    p.camera.zoom(1.7)
    win.status.setText("  step 14 · FLORIS 稳态 · 自动偏航演示  ")
    grab(win, "fig_rviz_overview.png")

    # (2) 高角度斜视：整场尾流叠加 —— 与理论里的尾流 DAG 直接对应。
    # 不用正俯视：从正上方看，竖直的转子平面退化成一条线，机组像倒下的棍子。
    _oblique(p, src)
    cam = (p.camera_position, p.camera.parallel_scale)   # (3) 复用，前后严格同视角
    win.status.setText("  高斜视：整场尾流叠加结构（对应尾流 DAG）  ")
    grab(win, "fig_rviz_topdown.png")
    p_before = np.ravel(src.fi.get_turbine_powers()) / 1e6
    print(f"[rviz-cap] 拖拽前功率 {np.round(p_before,3)}  合计 {p_before.sum():.3f} MW"
          f"  yaw {np.round(src.yaws,1)}", flush=True)

    # (3) 拖拽重定位：走 app 真实的 release 回填路径，与 (2) 用同一相机 → 前后对照
    # 位移不宜过大：FLORIS 水平切面固定 200x200 网格，域一撑大尾流就被抹平
    i = int(np.argmin(src.lx))            # 最上风向那台
    dy_move = 260.0
    win._drag_idx = i
    src.lx[i], src.ly[i] = float(src.lx[i]), float(src.ly[i]) + dy_move
    t = win.turbines[i]
    dx, dy = src.lx[i] - t["x"], src.ly[i] - t["y"]
    for key in ("tower", "nac", "rotor"):
        a = t[key]
        a.position = (a.position[0] + dx, a.position[1] + dy, a.position[2])
    t["x"], t["y"] = float(src.lx[i]), float(src.ly[i])
    t["hub_c"] = t["hub_c"] + np.array([dx, dy, 0.0])
    win._on_release(None, None)           # 回填 FLORIS + 换网格 + 刷尾流
    win._redraw_turbines()                # 姿态矩阵要用新的旋转中心重建（yaw 不变）
    # 只重发功率/载荷，不推进策略 —— 偏航与 (2) 完全一致，前后严格受控
    from wfrl import multimodal as mm
    p_after = np.ravel(src.fi.get_turbine_powers()) / 1e6
    win.hub.publish("power", p_after)
    win.hub.publish("vibration", mm.vibration_features(src.fi))
    print(f"[rviz-cap] 拖拽后功率 {np.round(p_after,3)}  合计 {p_after.sum():.3f} MW"
          f"  ({100*(p_after.sum()/p_before.sum()-1):+.1f}%)", flush=True)

    p.camera_position, p.camera.parallel_scale = cam
    win.status.setText(
        f"  T{i+1} 已拖拽重定位 (+0, +{dy_move:.0f}) m —— FLORIS 已重算尾流与功率  ")
    grab(win, "fig_rviz_drag.png")

    win.timer.stop()
    win.close()


def capture_fastfarm(app):
    from wfrl.viz.rviz_app import FastFarmSource, MainWindow

    print("[rviz-cap] 启动 FAST.Farm（约 10 秒）…", flush=True)
    src = FastFarmSource(env_id="Dec_Turb3_Row1_Fastfarm", policy="auto",
                         max_steps=60)
    win = MainWindow(src, allow_drag=False, tick_ms=200)
    win.timer.stop()
    win.show()
    _pump(app, 20)

    advance(win, 12)                      # FAST.Farm ~0.6 s/步
    p = win.plotter
    p.camera_position = "yz"
    p.camera.azimuth = -62
    p.camera.elevation = 24
    p.reset_camera()
    p.camera.zoom(1.15)
    win.status.setText("  FAST.Farm 动态后端 · step 12 · 尾流为 FLORIS proxy  ")
    grab(win, "fig_rviz_fastfarm.png")

    win.close()
    _pump(app, 10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fastfarm", action="store_true",
                    help="额外截一张 FAST.Farm 动态后端（慢，需 mpiexec）")
    ap.add_argument("--only-fastfarm", action="store_true")
    args = ap.parse_args()

    os.makedirs(FIGS, exist_ok=True)
    app = QtWidgets.QApplication([])

    if not args.only_fastfarm:
        capture_floris(app)
    if args.fastfarm or args.only_fastfarm:
        capture_fastfarm(app)
    print("done.")


if __name__ == "__main__":
    main()
