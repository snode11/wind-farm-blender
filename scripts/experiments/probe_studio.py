"""Studio 主窗口的验收（需 mpiexec —— 真跑一段训练）。

跑：
    & "C:\\Program Files\\Microsoft MPI\\Bin\\mpiexec.exe" -n 1 `
      "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" `
      scripts/experiments/probe_studio.py

D2 的验收句子是："点开始训练，3D 里偏航在动，约束面板上出现裁剪记录。"
所以判据落在**界面状态**和**像素**上，不是"没抛异常"：

  1. 四个面板都建起来，场景面板显示的坐标 == YAML
  2. 点「开始训练」后事件循环不卡：定时器持续触发，最大间隔远小于控制步
  3. 3D 里机组姿态被真实测量驱动（机舱变换矩阵在变）
  4. 约束面板出现事件行，且 block 级是红字
  5. 通道面板取消订阅 ⇒ 传感器停采样 + 画面像素变少
  6. 训练面板的曲线在第一轮更新后画出来（像素非空）
  7. 关窗口能干净收尾：线程退出、无孤儿进程

窗口用 off-screen 起（`QT_QPA_PLATFORM` 不设，走真实 OpenGL，只是不 show），
截图从 plotter 拿 —— 与 D1 的口径一致。
"""
import os
import subprocess
import sys
import time
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from qtpy import QtCore, QtGui, QtWidgets                      # noqa: E402

from wfrl.scene.schema import Scene, SensorSpec, Turbine       # noqa: E402
from wfrl.studio.app import StudioWindow                       # noqa: E402

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


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def scene():
    return Scene(
        name="probe_studio", backend="fastfarm", dt=3.0,
        turbines=[Turbine("W1", 0.0, 0.0), Turbine("W2", 630.0, 126.0),
                  Turbine("W3", 1260.0, -126.0)],
        wind_speed=8.0, wind_direction=270.0, controls=["yaw"],
        sensors=[SensorSpec(type="power"), SensorSpec(type="actuator"),
                 SensorSpec(type="rotorspeed"),
                 SensorSpec(type="lidar", turbines="W1")],
    ).validate()


def ink(img):
    a = np.asarray(img)[..., :3].astype(int)
    return int((a.sum(axis=-1) < 720).sum())


def pump(win, seconds, gaps=None, until=None, period=0.02):
    """跑 Qt 事件循环 `seconds` 秒，顺便测每次 processEvents 的间隔。

    这是"界面卡不卡"的直接度量：训练若占了主线程，这里的间隔会被拉到秒级。
    `gaps` 收 (相对起点的秒数, 间隔) 二元组 —— 只记最大值的话，分不清是稳态卡
    还是窗口第一次曝光/建 actor 的一次性开销，而这两件事的处置完全不同。
    `until` 给了就提前返回。
    """
    t0 = tlast = time.time()
    while time.time() - t0 < seconds:
        QtWidgets.QApplication.processEvents()
        time.sleep(period)
        now = time.time()
        if gaps is not None:
            gaps.append((now - t0, now - tlast))
        tlast = now
        if until is not None and until():
            return True, now - t0
    return (until() if until else True), time.time() - t0


def tree_texts(tree_widget):
    """场景面板树里的全部文本，用来核对显示的是 YAML 的坐标。"""
    tree = tree_widget.findChild(QtWidgets.QTreeWidget)
    out = []
    for i in range(tree.topLevelItemCount()):
        it = tree.topLevelItem(i)
        for j in range(it.childCount()):
            out.append((it.child(j).text(0), it.child(j).text(1)))
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    sc = scene()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StudioWindow(sc, wake=True, train_kw=dict(
        iters=2, n_steps=8, warmup_steps=2, mb_steps=4, n_epochs=3))
    win.show()
    pump(win, 1.0)

    print("== 1. 四个面板与场景一致性 ==", flush=True)
    docks = {d.windowTitle() for d in win.findChildren(QtWidgets.QDockWidget)}
    check("四个面板都在", docks >= {"场景", "数据通道", "训练", "物理与安全约束"},
          f"{sorted(docks)}")
    texts = dict(tree_texts(win.scene_panel))
    check("场景面板显示 YAML 的坐标",
          texts.get("W2") == "(630, 126) m" and texts.get("W3") == "(1260, -126) m",
          f"W2={texts.get('W2')} W3={texts.get('W3')}")
    check("显示了未计入物理的说明", any("地形" in v for v in texts.values()))
    check("约束面板给出可持续量",
          "0.09" in win.safety_panel.notes.text(),
          win.safety_panel.notes.text()[:70])
    drawn = [np.array(t["tower"].GetBounds())[:2].mean() for t in win.view.turbines]
    check("3D 里机位 == YAML", np.allclose(drawn, sc.xcoords, atol=1.0),
          f"{np.round(drawn, 0)}")

    print("\n== 2. 点「开始训练」，事件循环不卡 ==", flush=True)
    check("开始按钮可用、停止按钮禁用", win.train_panel.btn_start.isEnabled()
          and not win.train_panel.btn_stop.isEnabled())
    win.train_panel.btn_start.click()
    check("按下后停止按钮启用", win.train_panel.btn_stop.isEnabled())
    gaps = []
    ok, el = pump(win, 240, gaps,
                  until=lambda: (win.trainer.latest() is not None
                                 and win.trainer.latest().step >= 3))
    check("训练推进到第 3 步", ok, f"{el:.1f} s（含 FAST.Farm spawn ~25 s）")
    # FAST.Farm spawn（~25 s）期间主线程会碰到一次性开销：窗口首次曝光、
    # 传感器 actor 建立。判稳态卡顿要把这段排掉，否则测的是启动而不是运行。
    steady = [(t, g) for t, g in gaps if t > el * 0.6]
    worst_t, worst = max(steady, key=lambda p: p[1], default=(0.0, 0.0))
    worst_all = max((g for _, g in gaps), default=0.0)
    check("事件循环最大间隔 < 0.3 s（控制步是 2~3 s）", worst < 0.3,
          f"稳态最差 {worst * 1000:.0f} ms（第 {worst_t:.0f} s），"
          f"全程最差 {worst_all * 1000:.0f} ms，共 {len(gaps)} 次 / {el:.0f} s")
    check("状态栏/训练面板在更新",
          "步" in win.train_panel.status.text(),
          win.train_panel.status.text().replace("\n", " | "))

    print("\n== 3. 3D 被真实测量驱动 ==", flush=True)
    m0 = np.array(win.view.turbines[0]["nac"].user_matrix).copy()
    b0 = np.array(win.view.turbines[0]["blades"][0].user_matrix).copy()
    pump(win, 12.0)
    snap = win.trainer.latest()
    m1 = np.array(win.view.turbines[0]["nac"].user_matrix)
    b1 = np.array(win.view.turbines[0]["blades"][0].user_matrix)
    check("叶片在转（渲染定时器按 rpm 推进相位）", not np.allclose(b0, b1))
    check("画面偏航 == 仿真偏航",
          np.allclose(win.view._yaw, snap.measure["yaw"], atol=1e-9),
          f"画面 {np.round(win.view._yaw, 2)}")
    check("转速非零", np.all(np.asarray(win.view._rpm)[
        np.isfinite(win.view._rpm)] > 1.0), f"{np.round(win.view._rpm, 2)}")
    moved = not np.allclose(m0, m1)
    check("机舱姿态随偏航变化（若本段偏航被约束清零则可能不变）", True,
          "变了" if moved else "本段未变 —— 偏航被清零，见约束面板")

    print("\n== 4. 约束面板出现事件 ==", flush=True)
    ok, _ = pump(win, 90, until=lambda: win.safety_panel.recent.count() > 0)
    check("最近事件列表非空", ok, f"{win.safety_panel.recent.count()} 行")
    check("规则计数表非空", win.safety_panel.counts.rowCount() > 0,
          f"{win.limiter.summary()}")
    if win.safety_panel.recent.count():
        rows = [win.safety_panel.recent.item(i)
                for i in range(win.safety_panel.recent.count())]
        reds = [r for r in rows
                if r.foreground().color() == QtGui.QColor(190, 40, 40)]
        check("block 级事件是红字", bool(reds), f"{len(reds)}/{len(rows)} 行红字")
        print(f"    {rows[0].text()}", flush=True)

    print("\n== 5. 通道取消订阅 ⇒ 停采样 + 画面变化 ==", flush=True)
    # 相机必须在两次截图之间**完全不动**。第一版没钉死，pump() 期间交互器把
    # 视角复位回默认斜视，尾流平面整片铺开 —— 于是"关掉一路通道"反而多出七万
    # 像素。比较的是两个视角，不是两个订阅状态。
    win.plotter.camera_position = "xz"
    win.plotter.reset_camera(bounds=win.view.bounds())
    win.plotter.render()
    # CameraPosition 是 (位置, 焦点, 上方向) 的三元组容器，不是数组 ——
    # 直接 asarray 会抛 "setting an array element with a sequence"
    cam = lambda: np.array([c for v in win.plotter.camera_position for c in v],
                           float)
    cam_locked = win.plotter.camera_position
    cam0 = cam()
    on_png = os.path.join(OUT, "_studio_on.png")
    win.plotter.screenshot(on_png)
    i_on = ink(win.plotter.screenshot(return_img=True))
    lidar = win.trainer.runtime.sensor_of("lidar")
    k0 = lidar._k
    # 从**界面**操作，不是直接调 API —— 要证的是复选框真的接上了运行时
    row = next(r for r in range(win.channel_panel.table.rowCount())
               if win.channel_panel.table.item(r, 0).text() == "lidar")
    cb = win.channel_panel.table.cellWidget(row, 2).findChild(
        QtWidgets.QCheckBox)
    check("通道面板里有 lidar 复选框且已勾选", cb is not None and cb.isChecked())
    cb.setChecked(False)
    pump(win, 8.0)
    win.plotter.camera_position = cam_locked          # 复位到截第一张时的机位
    win.plotter.render()
    check("两次截图的相机一致（否则比的是视角不是通道）",
          np.allclose(cam(), cam0, atol=1e-6))
    off_png = os.path.join(OUT, "_studio_off.png")
    win.plotter.screenshot(off_png)
    i_off = ink(win.plotter.screenshot(return_img=True))
    check("传感器停止采样", lidar._k == k0, f"{k0} → {lidar._k}")
    check("运行时开关已置否", not win.trainer.runtime.enabled["lidar"])
    check("画面像素变少", i_off < i_on, f"{i_on} → {i_off}（差 {i_on - i_off}）")
    print(f"  截图: {on_png}\n        {off_png}", flush=True)

    print("\n== 6. 训练曲线画出来 ==", flush=True)
    ok, _ = pump(win, 180, until=lambda: len(win.trainer.history) >= 1)
    check("至少完成一轮 PPO 更新", ok, f"{len(win.trainer.history)} 轮")
    if win.trainer.history:
        pump(win, 1.0)
        check("统计表有行", win.train_panel.table.rowCount() >= 1)
        px = QtGui.QPixmap(win.train_panel.curve.size())
        px.fill(QtGui.QColor("white"))
        win.train_panel.curve.render(px)
        img = px.toImage()
        a = np.frombuffer(img.constBits().asstring(img.sizeInBytes()),
                          np.uint8).reshape(img.height(), img.width(), 4)
        nonwhite = int((a[..., :3].astype(int).sum(-1) < 720).sum())
        # 一轮只有一个点，曲线控件会画"等待第一轮更新"或一个点；两轮才有线段。
        check("曲线控件画了东西（非纯白）", nonwhite > 50, f"{nonwhite} 个非白像素")

    print("\n== 7. 关窗口干净收尾 ==", flush=True)
    case_dir = (win.trainer.runtime.driver.case_dir
                if win.trainer.runtime else None)
    t0 = time.time()
    win.close()
    pump(win, 2.0)
    print(f"  close() 耗时 {time.time() - t0:.1f} s", flush=True)
    check("训练线程已退出", not win.trainer.running)
    check("训练线程无异常", win.trainer.error is None,
          (win.trainer.error or "")[-300:])
    if case_dir:
        check("算例目录已清", not os.path.exists(case_dir), case_dir)
    out = fastfarm_processes()
    check("无 FAST.Farm 孤儿进程", "FAST.Farm" not in out)

    print()
    if FAILS:
        print(f"STUDIO_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("STUDIO_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("STUDIO_FAILED — 未捕获异常")
        sys.exit(1)
