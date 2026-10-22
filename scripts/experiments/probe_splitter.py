"""两个独立 QtInteractor 放进 QSplitter：actor 能否跨 render window 共享？"""
import sys

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor
from qtpy import QtCore, QtWidgets

app = QtWidgets.QApplication(sys.argv)
w = QtWidgets.QMainWindow()
sp = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
p0, p1 = QtInteractor(sp), QtInteractor(sp)
sp.addWidget(p0)
sp.addWidget(p1)
sp.setSizes([700, 500])
w.setCentralWidget(sp)
w.resize(1200, 600)

a = p0.add_mesh(pv.Sphere(radius=5, center=(0, 0, 10)), color="red")
p1.renderer.AddActor(a)              # 跨 render window 共享同一个 actor
p0.add_mesh(pv.Plane(i_size=40, j_size=40), color="tan")
for p in (p0, p1):
    p.reset_camera()
w.show()


def check():
    b0 = np.asarray(p0.renderer.ComputeVisiblePropBounds())
    b1 = np.asarray(p1.renderer.ComputeVisiblePropBounds())
    print("bounds r0", np.round(b0, 1), flush=True)
    print("bounds r1", np.round(b1, 1), flush=True)
    a.user_matrix = np.array([[1, 0, 0, 25], [0, 1, 0, 0],
                              [0, 0, 1, 0], [0, 0, 0, 1]], float)
    p0.render()
    p1.render()
    c0 = np.asarray(p0.renderer.ComputeVisiblePropBounds())
    c1 = np.asarray(p1.renderer.ComputeVisiblePropBounds())
    print("after dx=25 -> r0", np.round(c0[0], 1), " r1", np.round(c1[0], 1),
          flush=True)
    i0 = np.asarray(p0.screenshot(return_img=True))
    i1 = np.asarray(p1.screenshot(return_img=True))
    print("shot0", i0.shape, "ink", int((i0.min(axis=2) < 235).sum()), flush=True)
    print("shot1", i1.shape, "ink", int((i1.min(axis=2) < 235).sum()), flush=True)
    sp.setSizes([300, 900])
    app.processEvents()
    print("resized ->", sp.sizes(), flush=True)
    print("SPLITTER_OK", flush=True)
    w.close()
    app.quit()


QtCore.QTimer.singleShot(2500, check)
app.exec_()
