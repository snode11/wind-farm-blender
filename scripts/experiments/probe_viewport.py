"""同一 render window 内改 renderer 的 viewport —— 能否做出"可拖分隔条"的效果？

QSplitter + 两个 QtInteractor 已验证不行：actor 的 OpenGL 资源绑在创建它的
render window 上，跨窗口共享的那个视口画出来是全白（probe_splitter.py: shot1 ink 0）。
"""
import sys

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor
from qtpy import QtCore, QtWidgets

app = QtWidgets.QApplication(sys.argv)
w = QtWidgets.QMainWindow()
p = QtInteractor(w, shape=(1, 2))
w.setCentralWidget(p)
w.resize(1200, 600)
p.set_background("white")
for r in p.renderers:
    r.SetBackground(1, 1, 1)

p.subplot(0, 0)
a = p.add_mesh(pv.Sphere(radius=5, center=(0, 0, 10)), color="red")
p.add_mesh(pv.Plane(i_size=40, j_size=40), color="tan")
p.renderers[1].AddActor(a)
for r in p.renderers:
    r.ResetCamera()
w.show()


def ink(img, x0, x1):
    sub = img[:, x0:x1]
    return int((sub.min(axis=2) < 235).sum())


def check():
    img = np.asarray(p.screenshot(return_img=True))
    W = img.shape[1]
    print("default viewports:",
          [tuple(np.round(r.GetViewport(), 2)) for r in p.renderers], flush=True)
    print("ink L/R @50%:", ink(img, 0, W // 2), ink(img, W // 2, W), flush=True)

    for frac in (0.25, 0.75):
        p.renderers[0].SetViewport(0.0, 0.0, frac, 1.0)
        p.renderers[1].SetViewport(frac, 0.0, 1.0, 1.0)
        for r in p.renderers:
            r.ResetCameraClippingRange()
        p.render()
        app.processEvents()
        im = np.asarray(p.screenshot(return_img=True))
        cut = int(frac * im.shape[1])
        print(f"frac={frac}: ink L={ink(im, 0, cut)} R={ink(im, cut, im.shape[1])}",
              flush=True)

    print("VIEWPORT_OK", flush=True)
    w.close()
    app.quit()


QtCore.QTimer.singleShot(2500, check)
app.exec_()
