"""最小复现：pyvistaqt 双视口 + QTimer 能不能刷新右视口。

不牵扯 Studio 的任何代码 —— 就一个 shape=(1,2) 窗口，左右各一个立方体，QTimer
每 33ms 转它们、按几种方式触发重绘。目的：把"右视口不刷新"这件事从我们的 app
里隔离出来，确认是 pyvistaqt/VTK 版本本身的问题，还是 app 里挡住了。

跑（真机，有显示器）：
    & "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" scripts/experiments/probe_dualview_min.py

看窗口：左右两个立方体**都应该在转**。
  · 两个都转           → pyvistaqt 双视口刷新没问题，bug 在我们 app（我据此改）
  · 只有左边转、右边不转 → 就是 pyvistaqt/VTK 版本的双视口+定时器刷新缺陷，
                           得换刷新机制或换库（我据此换方案，不再瞎试）
关窗退出。控制台会打印用的刷新方式和 pyvista 版本。

可选参数：--mode 选刷新方式（render/repaint/interactor_render），默认三种都试
（每 3 秒切一种，控制台报当前用哪种，好看出哪种能刷右边）。
"""
import sys
import time

import numpy as np
import pyvista as pv
from qtpy import QtCore, QtWidgets
from pyvistaqt import QtInteractor


class Win(QtWidgets.QMainWindow):
    def __init__(self, mode):
        super().__init__()
        self.setWindowTitle("双视口刷新最小复现 —— 左右立方体都该转")
        self.resize(900, 450)
        self.mode = mode
        self.plotter = QtInteractor(self, shape=(1, 2))
        self.setCentralWidget(self.plotter.interactor)
        for r in self.plotter.renderers:
            r.SetBackground(1.0, 1.0, 1.0)

        self.plotter.subplot(0, 0)
        self.cubeL = pv.Cube()
        self.actL = self.plotter.add_mesh(self.cubeL, color="tomato")
        self.plotter.add_text("左视口 L", position="upper_left", font_size=10,
                              color="black")

        self.plotter.subplot(0, 1)
        self.cubeR = pv.Cube()
        self.actR = self.plotter.add_mesh(self.cubeR, color="seagreen")
        self.plotter.add_text("右视口 R", position="upper_left", font_size=10,
                              color="black")

        self.plotter.subplot(0, 0)
        self._ang = 0.0
        self._modes = ([mode] if mode != "all"
                       else ["render", "repaint", "interactor_render"])
        self._mi = 0
        self._t0 = time.time()
        print(f"pyvista {pv.__version__}  刷新方式: "
              f"{self._modes if mode == 'all' else mode}", flush=True)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    def _rot(self, act, ang):
        from vtkmodules.vtkCommonTransforms import vtkTransform
        tf = vtkTransform()
        tf.RotateZ(ang)
        act.SetUserTransform(tf)

    def _tick(self):
        self._ang = (self._ang + 3.0) % 360.0
        self._rot(self.actL, self._ang)
        self._rot(self.actR, -self._ang)
        # all 模式：每 3 秒换一种刷新方式，控制台报
        if len(self._modes) > 1 and time.time() - self._t0 > 3.0:
            self._t0 = time.time()
            self._mi = (self._mi + 1) % len(self._modes)
            print(f"  切换刷新方式 -> {self._modes[self._mi]}", flush=True)
        m = self._modes[self._mi]
        try:
            if m == "render":
                self.plotter.render()
            elif m == "repaint":
                self.plotter.interactor.repaint()
            elif m == "interactor_render":
                self.plotter.interactor.GetRenderWindow().Render()
        except Exception as e:                              # noqa: BLE001
            print("  刷新出错:", e, flush=True)


def main():
    mode = "all"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = Win(mode)
    w.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
