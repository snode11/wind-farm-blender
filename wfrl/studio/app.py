"""Studio 主窗口：场景 → 通道 → 训练 → 约束，四个面板围着一个 3D 视图。

这是 D2 的收口。前面三层各自能单独验收：

    scene/       一份 YAML 决定算例（probe_scene_runtime）
    studio/view  同一份 YAML 决定画面（probe_scene_view / probe_scene_live）
    safety       指令约束与事件（probe_safety）
    trainer      训练跑后台线程（probe_trainer）

主窗口只做接线，不含物理，也不含算法。它的全部职责是：把工作线程的 `Snapshot`
搬到 3D 与四个面板上，并保证搬运过程不阻塞事件循环。

线程规矩（违反就是白屏或段错误）：
    工作线程   跑 FAST.Farm 与 PPO，只写 `Trainer._latest`（纯值）
    主线程     只读快照，只改 actor

两个定时器各跑各的：`_tick`（120 ms）取快照喂面板与位姿，`_render`（33 ms）按
真实流逝时间推进转子相位。合成一个的话，帧率会被 3 s 的控制步钉死在 0.4 fps。

运行：
    python -m wfrl.studio.app --scene scenes/turb3_row.yaml
    python -m wfrl.studio.app --scene scenes/turb3_row.yaml --train --iters 4
"""
from __future__ import annotations

import os
import sys
import time
from typing import Optional

import numpy as np

from qtpy import QtCore, QtGui, QtWidgets
from pyvistaqt import QtInteractor

from wfrl.channels.base import Fidelity
from wfrl.safety import SafetyLimiter
from wfrl.scene.schema import Scene, load_scene
from wfrl.studio.trainer import Trainer
from wfrl.studio.view import SceneView


def _use_system_fixed_font(widget):
    """Use Qt's platform font instead of a Windows/macOS family name."""
    widget.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))


# ---------------------------------------------------------------------------
# 面板
# ---------------------------------------------------------------------------
class ScenePanel(QtWidgets.QWidget):
    """场景树：布局、来流、控制量，以及**没有算进物理的东西**。

    最后那一段（`scene.notes`）不是装饰。地形只进渲染、风向在 FAST.Farm 后端
    不可设 —— 看图的人不会自己知道，写在这儿才不会把渲染效果当成物理结论。
    """

    def __init__(self, scene: Scene):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        tree = QtWidgets.QTreeWidget()
        tree.setHeaderLabels(["项", "值"])
        tree.setColumnWidth(0, 130)

        def group(title, rows):
            it = QtWidgets.QTreeWidgetItem(tree, [title, ""])
            it.setExpanded(True)
            f = it.font(0); f.setBold(True); it.setFont(0, f)
            for k, v in rows:
                QtWidgets.QTreeWidgetItem(it, [str(k), str(v)])
            return it

        group("场景", [("名称", scene.name), ("后端", scene.backend),
                       ("机型", scene.turbine), ("控制步", f"{scene.dt:g} s"),
                       ("来源", os.path.basename(scene.source_path or "内存构造"))])
        group("布局", [(t.id, f"({t.x:.0f}, {t.y:.0f}) m") for t in scene.turbines])
        group("来流", [("风速", f"{scene.wind_speed:g} m/s"),
                       ("风向", f"{scene.wind_direction:g}°"),
                       ("湍流盒", scene.turbulence or "无（均匀来流）")])
        group("控制量", [(c, "启用") for c in scene.controls])
        notes = group("未计入物理", [(f"{i+1}", s)
                                     for i, s in enumerate(scene.notes)])
        for i in range(notes.childCount()):
            notes.child(i).setForeground(1, QtGui.QColor(170, 110, 30))
        lay.addWidget(tree)


class ChannelPanel(QtWidgets.QWidget):
    """通道订阅：勾选状态直接落到运行时的采样开关与 3D 可见性。

    保真度用颜色分三档（直读/导出/合成）。这不是 UI 美化 —— DIRECT 的数进奖励
    和约束是合法的，SYNTH 的只能进感知层，混用会让结论不成立。悬停显示 provenance。
    """

    def __init__(self, on_toggle):
        super().__init__()
        self._on_toggle = on_toggle
        lay = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["通道", "保真度", "订阅"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 110)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)
        self.hint = QtWidgets.QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#555; font-size:11px;")
        lay.addWidget(self.hint)

    def populate(self, runtime):
        rows = runtime.channel_table()
        self._fill(rows)

    def populate_from_sensors(self, sensors, enabled=None):
        """没有 runtime（如 demo 模式）时，直接从已建好的 sensor 列表铺通道表。

        channel_table 只需要 topic/type/fidelity/provenance —— 全在 sensor 上，
        不依赖 MPI/driver。enabled 缺省全部订阅。
        """
        enabled = enabled or {}
        rows = []
        for s in sensors:
            for t in s.topics:
                rows.append((t, s.type_name, s.fidelity.value,
                             enabled.get(t, True), s.provenance))
        self._fill(rows)

    def _fill(self, rows):
        self.table.setRowCount(len(rows))
        for r, (topic, stype, fid, on, prov) in enumerate(rows):
            it = QtWidgets.QTableWidgetItem(topic)
            it.setToolTip(f"{stype}\n{prov}")
            self.table.setItem(r, 0, it)
            fi = QtWidgets.QTableWidgetItem(fid)
            fi.setForeground(QtGui.QColor(*Fidelity(fid).color))
            fi.setToolTip(prov)
            self.table.setItem(r, 1, fi)
            cb = QtWidgets.QCheckBox()
            cb.setChecked(bool(on))
            cb.stateChanged.connect(
                lambda s, t=topic: self._on_toggle(t, bool(s)))
            w = QtWidgets.QWidget()
            wl = QtWidgets.QHBoxLayout(w)
            wl.addWidget(cb); wl.setAlignment(QtCore.Qt.AlignCenter)
            wl.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(r, 2, w)
        self.hint.setText(
            "取消订阅 = 该传感器**停止采样**（省下 lidar 视线插值、camera 离屏"
            "渲染等真实计算），不只是不画。")


class TrainPanel(QtWidgets.QWidget):
    """训练：按钮、进度、逐轮统计。曲线用 QPainter 直接画，不引 matplotlib ——
    嵌 FigureCanvas 到 Qt 里会和 VTK 抢 OpenGL 上下文。"""

    def __init__(self, on_start, on_pause, on_stop):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("▶ 开始训练")
        self.btn_pause = QtWidgets.QPushButton("⏸ 暂停")
        self.btn_stop = QtWidgets.QPushButton("⏹ 停止")
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(on_start)
        self.btn_pause.clicked.connect(on_pause)
        self.btn_stop.clicked.connect(on_stop)
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            row.addWidget(b)
        lay.addLayout(row)

        self.status = QtWidgets.QLabel("未开始")
        _use_system_fixed_font(self.status)
        lay.addWidget(self.status)

        # demo 模式：当前阶段中文提示（"变桨 90°→0° 启动" / "偏航 0°→30°"），
        # 大字醒目，讲解时观众一眼看到画面里正在发生什么。非 demo 时隐藏。
        self.demo_hint = QtWidgets.QLabel("")
        self.demo_hint.setWordWrap(True)
        self.demo_hint.setAlignment(QtCore.Qt.AlignCenter)
        self.demo_hint.setStyleSheet(
            "font-size:15px; font-weight:bold; color:#0a58a0;"
            " background:#eef4fb; border:1px solid #cfe0f2;"
            " border-radius:4px; padding:6px;")
        self.demo_hint.setVisible(False)
        lay.addWidget(self.demo_hint)

        # 曲线切换按钮：分风机显示（T1/T2/T3/全部），每台可单独看 yaw/pitch/rpm/power。
        # 非 demo（训练模式）时整行隐藏，只画 power/reward。
        self.curve_btns = QtWidgets.QWidget()
        brow = QtWidgets.QHBoxLayout(self.curve_btns)
        brow.setContentsMargins(0, 0, 0, 0)
        self._curve_group = QtWidgets.QButtonGroup(self)
        self._curve_group.setExclusive(True)
        self._curve_filter_idx = None  # None=全部；0/1/2=T1/T2/T3
        for i, name in enumerate(("T1", "T2", "T3", "全部")):
            b = QtWidgets.QPushButton(name)
            b.setCheckable(True)
            if name == "全部":
                b.setChecked(True)
            idx = None if name == "全部" else i
            b.clicked.connect(lambda _c, ix=idx: self._on_curve_btn(ix))
            self._curve_group.addButton(b, i)
            brow.addWidget(b)
        self.curve_btns.setVisible(False)
        lay.addWidget(self.curve_btns)

        self.curve = _Curve()
        lay.addWidget(self.curve, 1)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["轮", "功率MW", "奖励", "vloss", "ev"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)

    def set_running(self, on):
        self.btn_start.setEnabled(not on)
        self.btn_pause.setEnabled(on)
        self.btn_stop.setEnabled(on)

    def _on_curve_btn(self, idx):
        """曲线切换按钮：只显示选中风机的曲线（或全部）。idx=None 全部，0/1/2=T1/T2/T3。"""
        self._curve_filter_idx = idx
        self.curve.set_turbine_filter(idx)

    def update_snapshot(self, snap, trainer):
        if snap is None:
            return
        phase = {"idle": "空闲", "warmup": "启动 FAST.Farm / 热身",
                 "sampling": "采样", "updating": "PPO 更新",
                 "done": "已结束"}.get(snap.phase, snap.phase)
        self.status.setText(
            f"{phase}   步 {snap.step}   轮 {snap.iters_done}/{trainer.iters}\n"
            f"全场功率 {snap.farm_power:.3f} MW   奖励 {snap.reward:+.4f}")
        # demo 阶段提示 + 曲线按钮：只在 demo 模式（snap.demo_label 非空）露出。
        label = getattr(snap, "demo_label", "") or ""
        if label:
            self.demo_hint.setText(label)
            self.demo_hint.setVisible(True)
            self.curve_btns.setVisible(True)
        hs = trainer.history
        if len(hs) != self.table.rowCount():
            self.table.setRowCount(len(hs))
            for r, h in enumerate(hs):
                for c, v in enumerate([h["iter"], f"{h['mean_power']:.3f}",
                                       f"{h['mean_reward']:+.4f}",
                                       f"{h['vloss']:.4g}",
                                       f"{h['explained_var']:+.3f}"]):
                    self.table.setItem(r, c, QtWidgets.QTableWidgetItem(str(v)))
            # demo 模式：history 里有 yaw/pitch/rpm/power 数组，画分机组曲线
            if hs and isinstance(hs[0].get("yaw"), (list, np.ndarray)):
                self.curve.set_per_turbine_series(hs)
            else:
                # 训练模式：退回 power/reward 两条
                self.curve.set_series([h["mean_power"] for h in hs],
                                      [h["mean_reward"] for h in hs])


class _Curve(QtWidgets.QWidget):
    """两条曲线（全场功率 / 平均奖励）共用 x 轴，各自独立纵向自适应。

    共用纵轴是不行的：功率是 5 MW 量级、归一化奖励在 ±3，画在一起奖励会压成
    一条直线。两条各自缩放，图例标出各自量程。
    """

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(120)
        self._a, self._b = [], []
        # 命名曲线：demo 模式用（yaw/pitch/power 各一条，各自颜色+量程标注）。
        # 空 dict 时退回 _a/_b 两条的旧行为（训练模式）。
        self._named = {}
        # 分机组曲线：demo 新模式，history 每步存 yaw/pitch/rpm/power 数组(n,)
        self._per_turbine = []  # list of dicts: [{yaw,pitch,rpm,power}, ...]
        # 曲线过滤：None=全部显示；否则只画 label 在此集合里的曲线（切换按钮控制）。
        self._visible = None
        # 分机组过滤：None=全部；0/1/2=只画T1/T2/T3
        self._turbine_idx = None

    def set_series(self, a, b):
        self._a, self._b = list(a), list(b)
        self._named = {}
        self._per_turbine = []
        self.update()

    def set_named_series(self, series_dict):
        """demo 模式：多条命名曲线。series_dict = {label: (values, color)}"""
        self._named = {k: (list(v[0]), v[1]) for k, v in series_dict.items()}
        self._per_turbine = []
        self.update()

    def set_per_turbine_series(self, history):
        """demo 新模式：分机组曲线。history[i] = {yaw:[...], pitch:[...], rpm:[...], power:[...]}"""
        self._per_turbine = list(history)
        self._named = {}
        self.update()

    def set_visible(self, labels):
        """曲线过滤：labels=None 显示全部，否则只显示集合内的 label。"""
        self._visible = labels
        self.update()

    def set_turbine_filter(self, idx):
        """分机组过滤：idx=None 全部，0/1/2=T1/T2/T3。"""
        self._turbine_idx = idx
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor("white"))
        w, h = self.width(), self.height()
        p.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200)))
        p.drawRect(0, 0, w - 1, h - 1)

        # 分机组曲线：demo 新模式，每台画 yaw/pitch/power 三条
        if self._per_turbine:
            if len(self._per_turbine) < 2:
                p.setPen(QtGui.QColor(120, 120, 120))
                p.drawText(self.rect(), QtCore.Qt.AlignCenter, "等待数据…")
                return

            # 确定要画哪几台：None=全部，0/1/2=单台
            indices = [self._turbine_idx] if self._turbine_idx is not None else [0, 1, 2]
            n_turbines = len(np.asarray(self._per_turbine[0]["yaw"]))
            indices = [i for i in indices if i < n_turbines]

            # 每台三条曲线：yaw(蓝) pitch(绿) power(红)，各自独立缩放
            colors = {
                "yaw": QtGui.QColor(20, 80, 140),
                "pitch": QtGui.QColor(30, 140, 60),
                "power": QtGui.QColor(170, 40, 40),
            }
            off = 4
            for idx in indices:
                turb_label = f"T{idx+1}"
                for key, color in colors.items():
                    series = [float(h[key][idx]) if isinstance(h[key], (list, np.ndarray))
                              else float(h[key]) for h in self._per_turbine]
                    if len(series) < 2:
                        continue
                    lo, hi = min(series), max(series)
                    span = (hi - lo) or 1.0
                    pts = [QtCore.QPointF(
                        float(8 + i * (w - 16) / (len(series) - 1)),
                        float(h - 8 - (v - lo) / span * (h - 60)))
                        for i, v in enumerate(series)]
                    p.setPen(QtGui.QPen(color, 2 if self._turbine_idx == idx else 1))
                    for i in range(len(pts) - 1):
                        p.drawLine(pts[i], pts[i + 1])
                    # 图例：只在选中单台或全部模式下显示该台该指标的量程
                    if self._turbine_idx == idx or self._turbine_idx is None:
                        label = f"{turb_label}-{key}: {lo:.1f}~{hi:.1f}"
                        p.setPen(color)
                        p.drawText(10, off + 8, label)
                        off += 14
            return

        # demo 旧模式：命名曲线（已弃用，保留兼容）
        if self._named:
            valid = {k: v for k, v in self._named.items() if len(v[0]) >= 2}
            if self._visible is not None:
                valid = {k: v for k, v in valid.items() if k in self._visible}
            if not valid:
                p.setPen(QtGui.QColor(120, 120, 120))
                p.drawText(self.rect(), QtCore.Qt.AlignCenter, "等待数据…")
                return
            off = 4
            for label, (series, color) in valid.items():
                lo, hi = min(series), max(series)
                span = (hi - lo) or 1.0
                pts = [QtCore.QPointF(
                    8 + i * (w - 16) / (len(series) - 1),
                    h - 8 - (v - lo) / span * (h - 40))
                    for i, v in enumerate(series)]
                p.setPen(QtGui.QPen(color, 2))
                for i in range(len(pts) - 1):
                    p.drawLine(pts[i], pts[i + 1])
                p.setPen(color)
                p.drawText(10, off + 8, f"{label}: {lo:.2f} ~ {hi:.2f}")
                off += 14
            return

        # 训练模式：power/reward 两条
        if len(self._a) < 2:
            p.setPen(QtGui.QColor(120, 120, 120))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "等待第一轮更新")
            return
        for series, color, off in ((self._a, QtGui.QColor(20, 80, 140), 4),
                                   (self._b, QtGui.QColor(170, 40, 40), 18)):
            lo, hi = min(series), max(series)
            span = (hi - lo) or 1.0
            pts = [QtCore.QPointF(
                8 + i * (w - 16) / (len(series) - 1),
                h - 8 - (v - lo) / span * (h - 30))
                for i, v in enumerate(series)]
            p.setPen(QtGui.QPen(color, 2))
            for i in range(len(pts) - 1):
                p.drawLine(pts[i], pts[i + 1])
            p.setPen(color)
            p.drawText(10, off + 8, f"{lo:.3f} ~ {hi:.3f}")


class SafetyPanel(QtWidgets.QWidget):
    """约束：本场景的可持续量 + 规则计数 + 最近事件。

    最近事件按严重级上色，block 用红 —— "偏航被占空比清零"必须一眼看见，
    否则策略学的是一串从未执行过的动作，而训练曲线上什么都看不出来。
    """

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        self.notes = QtWidgets.QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color:#333; font-size:11px;")
        lay.addWidget(self.notes)
        lay.addWidget(QtWidgets.QLabel("<b>规则触发计数</b>"))
        self.counts = QtWidgets.QTableWidget(0, 2)
        self.counts.setHorizontalHeaderLabels(["规则", "次数"])
        self.counts.horizontalHeader().setStretchLastSection(True)
        self.counts.setColumnWidth(0, 170)
        self.counts.verticalHeader().setVisible(False)
        self.counts.setMaximumHeight(130)
        lay.addWidget(self.counts)
        lay.addWidget(QtWidgets.QLabel("<b>最近事件</b>"))
        self.recent = QtWidgets.QListWidget()
        _use_system_fixed_font(self.recent)
        self.recent.setStyleSheet("font-size: 11px;")
        lay.addWidget(self.recent, 1)

    def set_notes(self, lines):
        self.notes.setText("\n".join(f"· {s}" for s in lines))

    def update_from(self, limiter: SafetyLimiter):
        smry = limiter.summary()
        if smry != getattr(self, "_last_smry", None):
            self._last_smry = smry
            self.counts.setRowCount(len(smry))
            for r, (k, v) in enumerate(smry):
                self.counts.setItem(r, 0, QtWidgets.QTableWidgetItem(k))
                self.counts.setItem(r, 1, QtWidgets.QTableWidgetItem(str(v)))
        evs = limiter.recent(40)
        if len(limiter.events) == getattr(self, "_last_n", -1):
            return
        self._last_n = len(limiter.events)
        self.recent.clear()
        color = {"block": QtGui.QColor(190, 40, 40),
                 "warn": QtGui.QColor(170, 110, 30),
                 "info": QtGui.QColor(70, 70, 70)}
        for e in reversed(evs):
            it = QtWidgets.QListWidgetItem(str(e))
            it.setForeground(color.get(e.severity, QtGui.QColor("black")))
            it.setToolTip(e.detail)
            self.recent.addItem(it)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class ControlPanel(QtWidgets.QWidget):
    """手动摆姿态（纯几何，不进物理）：选一台机组，拖偏航/桨距/转速。

    这是「验证挂机方案 / 看相机视野」用的，不是控制策略：滑块直接改画面里的
    机组姿态，转速会让叶片真的转起来。锁定的量在训练时也不被仿真覆盖，方便
    边训边手动摆某台看细节。「释放」把该机组交还给仿真。
    """

    def __init__(self, scene: Scene, on_set, on_clear):
        super().__init__()
        self._on_set = on_set
        self._on_clear = on_clear
        self.scene = scene
        lay = QtWidgets.QVBoxLayout(self)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("机组"))
        self.pick = QtWidgets.QComboBox()
        self.pick.addItems([t.id for t in scene.turbines])
        self.pick.currentIndexChanged.connect(self._sync_labels)
        row.addWidget(self.pick, 1)
        lay.addLayout(row)

        # (通道, 显示名, 最小, 最大, ×10 精度?, 单位)
        # 手动摆位是纯几何演示，范围按物理行程给：桨距 0–90°（顺桨到 90°），
        # 不受仿真的 STATE_BOUND(0,45) 约束 —— 那条是训练时约束层的事，这里只画。
        specs = [("yaw", "偏航", -40, 40, False, "°"),
                 ("pitch", "桨距", 0, 90, False, "°"),
                 ("rpm", "转速", 0, 20, True, "rpm")]
        self.sliders, self.vlabels = {}, {}
        for topic, name, lo, hi, deci, unit in specs:
            box = QtWidgets.QVBoxLayout()
            head = QtWidgets.QHBoxLayout()
            head.addWidget(QtWidgets.QLabel(f"<b>{name}</b> {unit}"))
            vl = QtWidgets.QLabel("—")
            _use_system_fixed_font(vl)
            head.addStretch(1)
            head.addWidget(vl)
            box.addLayout(head)
            sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            mul = 10 if deci else 1
            sl.setRange(int(lo * mul), int(hi * mul))
            sl.setValue(int(lo * mul))
            sl.valueChanged.connect(
                lambda v, t=topic, m=mul: self._changed(t, v / m))
            box.addWidget(sl)
            lay.addLayout(box)
            self.sliders[topic] = (sl, mul)
            self.vlabels[topic] = (vl, unit)

        btns = QtWidgets.QHBoxLayout()
        self.btn_release = QtWidgets.QPushButton("释放该机组")
        self.btn_release.clicked.connect(self._release)
        self.btn_release_all = QtWidgets.QPushButton("全部释放")
        self.btn_release_all.clicked.connect(lambda: self._on_clear(None, None))
        btns.addWidget(self.btn_release)
        btns.addWidget(self.btn_release_all)
        lay.addLayout(btns)

        self.hint = QtWidgets.QLabel(
            "滑块直接摆画面里的机组姿态，纯几何、不进物理；设了转速叶片会真的转。"
            "锁定的量在训练时也不被仿真盖掉，「释放」交还给仿真。")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#555; font-size:11px;")
        lay.addWidget(self.hint)
        lay.addStretch(1)

    @property
    def idx(self):
        return self.pick.currentIndex()

    def _changed(self, topic, value):
        vl, unit = self.vlabels[topic]
        vl.setText(f"{value:.1f} {unit}")
        self._on_set(topic, self.idx, value)

    def _release(self):
        self._on_clear(None, self.idx)

    def _sync_labels(self):
        # 切换机组时把标签显示成 "—"，值本身留在各机组的状态里（view 侧保存）
        for topic, (vl, unit) in self.vlabels.items():
            vl.setText("—")


class TurbineWindow(QtWidgets.QMainWindow):
    """单风机弹出窗口：只画选中的那台，贴身镜头看叶片扭角/变桨/变形。

    PyVista 的 actor 绑在创建它的 render window 上，不能跨窗口共享 —— 所以这里
    **自己建一份**该机组的几何，每帧从主窗口的共享姿态（_yaw/_pitch/_spin）读值
    重绘。它不持有任何物理，纯画面。
    """

    def __init__(self, parent, scene: Scene, view, idx: int):
        super().__init__(parent)
        self.scene = scene
        self.src_view = view          # 主窗口的 SceneView，姿态的唯一事实来源
        self.idx = int(idx)
        self.turb = None
        self._ready = False           # GL 上下文可用（窗口已 exposed）才置真
        self.setWindowTitle(f"单风机视图 — {scene.turbines[self.idx].id}")
        self.resize(720, 720)

        self.plotter = QtInteractor(self)
        self.setCentralWidget(self.plotter.interactor)

        pick = QtWidgets.QComboBox()
        pick.addItems([tt.id for tt in scene.turbines])
        pick.setCurrentIndex(self.idx)
        pick.currentIndexChanged.connect(self._switch)
        tb = self.addToolBar("t")
        tb.addWidget(QtWidgets.QLabel(" 机组 "))
        tb.addWidget(pick)
        self._pick = pick

        # 相机下拉：自由观察 + 场景里每一路 camera 传感器。选传感器相机就把视口
        # 切到它的安装位姿+FOV —— 看到的是这台相机拍到的实时叶片。相机项存
        # (sensor, 本窗口机组在该 sensor.indices 里的下标)。
        self._cams = self._collect_cameras(scene, view)
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" 相机 "))
        self._cam_pick = QtWidgets.QComboBox()
        self._cam_pick.addItem("自由观察")
        for label, _s, _si in self._cams:
            self._cam_pick.addItem(label)
        self._cam_pick.currentIndexChanged.connect(self._on_cam_changed)
        tb.addWidget(self._cam_pick)

        # FOV / 俯仰：自由观察时是观察镜头参数；选了传感器相机时微调该相机的
        # fov/pitch（短焦↔长焦、对准叶尖一眼可比）。
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" FOV "))
        self._fov = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._fov.setRange(5, 90)           # 度；5°≈长焦看局部，90°≈广角看整叶
        self._fov.setValue(30)
        self._fov.setFixedWidth(130)
        self._fov.valueChanged.connect(self._set_fov)
        tb.addWidget(self._fov)
        self._fov_lbl = QtWidgets.QLabel(" 30° ")
        tb.addWidget(self._fov_lbl)
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" 俯仰 "))
        self._pitch = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._pitch.setRange(-90, 30)       # 度；负=向下俯视叶片
        self._pitch.setValue(-30)
        self._pitch.setFixedWidth(130)
        self._pitch.valueChanged.connect(self._set_pitch)
        tb.addWidget(self._pitch)
        self._pitch_lbl = QtWidgets.QLabel(" -30° ")
        tb.addWidget(self._pitch_lbl)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._refresh)

    def showEvent(self, ev):
        """几何/镜头/定时器全部推迟到窗口真正显示之后再建。

        Windows 上在 .show() 之前往 QtInteractor 的 OpenGL 上下文里画东西，
        会 `wglMakeCurrent failed ... 句柄无效` —— 那时 GL 句柄还没生效。
        exposed 之后 GL 才可用，所以第一次 showEvent 里才建几何、起定时器。
        """
        super().showEvent(ev)
        if self._ready:
            return
        self._ready = True
        try:
            self.plotter.set_background("white")
            self._build(self.idx)
            self.plotter.add_axes(line_width=3)
        except Exception as e:                                # noqa: BLE001
            print(f"[turbine-win] 初始化渲染失败: {e!r}", flush=True)
        self.timer.start(33)

    def _build(self, idx):
        """为第 idx 台建一份**真实尺度**几何并摆好镜头。actor 不跨窗口共享。

        真尺度（scale=1.0）而不是主视图的 TURB_SCALE=5：机舱 90 m、叶片 63 m、
        叶尖 0.5 m 都是真值 —— 这样"相机能不能拍到 0.5 m 叶尖"才是可信的验证。
        """
        from wfrl.viz.animate import build_turbine
        self.idx = int(idx)
        t = self.scene.turbines[self.idx]
        self.turb = build_turbine(self.plotter, t.x, t.y, self.scene.hub_height,
                                  scale=1.0)
        self._apply_view()
        self.setWindowTitle(f"单风机视图 — {t.id}")

    def _collect_cameras(self, scene, view):
        """场景里所有 camera 传感器 × 挂载机组 → 下拉项列表。

        直接从场景 build 相机传感器（相机是纯几何、不需要 runtime/训练），不依赖
        view.sensors 是否已 attach —— 否则不点开始训练就列不出相机，正是这条 bug。
        返回 [(label, sensor, si)]，si 是机组在 sensor.indices 里的下标。
        """
        out = []
        try:
            from wfrl.channels import registry
            sensors = [s for s in registry.build(scene)
                       if getattr(s, "type_name", None) == "camera"]
        except Exception as e:                              # noqa: BLE001
            print(f"[turbine-win] 相机传感器 build 失败: {e!r}", flush=True)
            sensors = []
        for s in sensors:
            for si, ti in enumerate(s.indices):
                tid = scene.turbines[ti].id
                mount = s.params.get("mount", "nacelle_down")
                mname = {"nacelle_down": "机舱下俯视",
                         "nacelle_front": "机舱前视",
                         "hub": "轮毂",
                         "custom": "自定义点位"}.get(mount, mount)
                out.append((f"{tid}·{mname}", s, si))
        return out

    def _current_cam(self):
        """当前选中的相机 (sensor, si)，自由观察时返回 (None, None)。"""
        i = self._cam_pick.currentIndex() - 1     # 0 是"自由观察"
        if 0 <= i < len(self._cams):
            _label, s, si = self._cams[i]
            return s, si
        return None, None

    def _apply_view(self):
        """按当前相机选择摆镜头：传感器相机 → 切到它的位姿+FOV；否则自由观察。"""
        s, si = self._current_cam()
        if s is not None:
            self._apply_camera_pose(s, si)
        else:
            self._place_free_camera()

    def _apply_camera_pose(self, sensor, si):
        """把视口切到传感器相机的安装位姿+FOV（真实尺度）。看到实时叶片。"""
        from wfrl.viz.animate import update_turbine
        ti = sensor.indices[si]
        ctx = {"yaw": np.array([float(self.src_view._yaw[ti])])}
        # 滑块微调覆盖 sensor 的 fov/pitch，实时可比
        sensor.params["fov"] = float(self._fov.value())
        sensor.params["pitch"] = float(self._pitch.value())
        pos, focal, up, fov = sensor.camera_pose(ctx, si, scale=1.0)
        cam = self.plotter.camera
        cam.SetParallelProjection(False)
        cam.view_angle = float(fov)
        cam.position = tuple(pos)
        cam.focal_point = tuple(focal)
        cam.up = tuple(up)
        self.plotter.reset_camera_clipping_range()
        update_turbine(self.turb, float(self.src_view._yaw[ti]), 0.0, 0.0,
                       flex_scale=self.src_view.flex_scale)

    def _place_free_camera(self):
        """自由观察贴身镜头（真实尺度）：看点取轮毂，机位偏南抬高，FOV 按滑块。"""
        from wfrl.viz.animate import update_turbine
        hub_z = self.scene.hub_height
        r = 63.0                                  # 真实转子半径
        t = self.scene.turbines[self.idx]
        fx, fy = float(t.x), float(t.y)
        cam = self.plotter.camera
        cam.SetParallelProjection(False)
        cam.view_angle = float(self._fov.value())
        dirv = np.array([-2.2, -3.4, 1.1])
        dirv = dirv / np.linalg.norm(dirv)
        d = 4.0                                   # 4R 距离，看清整机
        cam.focal_point = (fx, fy, hub_z)
        cam.position = (fx + dirv[0] * d * r, fy + dirv[1] * d * r,
                        hub_z + dirv[2] * d * r)
        cam.up = (0.0, 0.0, 1.0)
        self.plotter.reset_camera_clipping_range()
        update_turbine(self.turb, 0.0, 0.0, 0.0,
                       flex_scale=self.src_view.flex_scale)

    def _place_camera(self):
        """兼容旧名：等价 _apply_view。"""
        self._apply_view()

    def _on_cam_changed(self, _i):
        s, _si = self._current_cam()
        # 切到传感器相机时把滑块同步成它的当前 fov/pitch，便于从真值起调
        if s is not None:
            self._fov.blockSignals(True)
            self._pitch.blockSignals(True)
            self._fov.setValue(int(s.params.get("fov", 40)))
            self._pitch.setValue(int(s.params.get("pitch", -30)))
            self._fov.blockSignals(False)
            self._pitch.blockSignals(False)
            self._fov_lbl.setText(f" {self._fov.value()}° ")
            self._pitch_lbl.setText(f" {self._pitch.value()}° ")
        if self._ready and self.turb is not None:
            try:
                self._apply_view()
                self.plotter.interactor.repaint()
            except Exception:                        # noqa: BLE001
                pass

    def _set_fov(self, deg):
        """FOV → view_angle，顺带显示等效焦距（35mm 全画幅示意）。"""
        f = 18.0 / max(np.tan(np.deg2rad(deg) / 2.0), 1e-3)   # 示意焦距 mm
        self._fov_lbl.setText(f" {deg}° ≈{f:.0f}mm ")
        if self._ready and self.turb is not None:
            try:
                self._apply_view()
                self.plotter.interactor.repaint()
            except Exception:                        # noqa: BLE001
                pass

    def _set_pitch(self, deg):
        self._pitch_lbl.setText(f" {deg}° ")
        if self._ready and self.turb is not None:
            try:
                self._apply_view()
                self.plotter.interactor.repaint()
            except Exception:                        # noqa: BLE001
                pass

    def _switch(self, i):
        """换一台：重建几何（换 x/y），重摆镜头。窗口没就绪时只记下选择。"""
        if not self._ready or self.turb is None:
            self.idx = int(i)
            return
        from wfrl.viz.animate import turbine_actors
        try:
            for a in turbine_actors(self.turb):
                self.plotter.remove_actor(a)
            self._build(int(i))
        except Exception as e:                               # noqa: BLE001
            print(f"[turbine-win] 切换机组失败: {e!r}", flush=True)

    def _refresh(self):
        """从主窗口共享姿态重绘这台。姿态是主窗口那份的唯一事实。

        坏上下文（窗口正在关、GL 句柄失效）时静默跳过 —— 不能让渲染异常在
        定时器里反复抛，那会把事件循环拖住，连关闭按钮都点不动。但第一次异常
        要打印出来（`_warned` 只报一次），否则 try/except 会把真正的 bug 吞掉，
        表现就是"画面不刷新只有滚轮能出图"。
        """
        if not self._ready or self.turb is None:
            return
        from wfrl.viz.animate import update_turbine
        v = self.src_view
        i = self.idx
        try:
            update_turbine(self.turb, float(v._yaw[i]), float(v._spin[i]),
                           float(v._pitch[i]), flex_scale=v.flex_scale)
            # 传感器相机随机组偏航转 —— 机舱转了，装在上面的相机跟着转。
            s, si = self._current_cam()
            if s is not None:
                ctx = {"yaw": np.array([float(v._yaw[i])])}
                pos, focal, up, fov = s.camera_pose(ctx, si, scale=1.0)
                cam = self.plotter.camera
                cam.position = tuple(pos)
                cam.focal_point = tuple(focal)
                cam.up = tuple(up)
                self.plotter.reset_camera_clipping_range()
            # 刷新的**唯一可靠路径**：QVTKRenderWindowInteractor 基类是 QWidget（不是
            # QOpenGLWidget），VTK 画到它自己的原生子窗口（HWND）。主动 render() /
            # render_window.Render() 都没触发这个第二原生窗口的重绘（内容静止时没有
            # 外部 paint 事件），只有滚轮走 VTK 交互器直接画。repaint() 是 QWidget 的
            # **同步强制重绘**，走 paintEvent ⇒ VTK 画到正确的原生窗口。update() 是
            # 异步排队、会被主窗口事件循环挤掉，所以之前不生效。
            self.plotter.render()            # 更新场景/相机状态
            self.plotter.interactor.repaint()   # 强制把它画到本窗口
            if not getattr(self, "_diag", False):
                self._diag = True
                it = self.plotter.interactor
                print(f"[turbine-win] interactor={type(it).__name__} "
                      f"visible={it.isVisible()} size={it.width()}x{it.height()}",
                      flush=True)
        except Exception as e:                               # noqa: BLE001
            if not getattr(self, "_warned", False):
                self._warned = True
                import traceback
                print(f"[turbine-win] _refresh 异常（只报一次）:\n"
                      f"{traceback.format_exc()}", flush=True)

    def closeEvent(self, ev):
        # 先停定时器再做别的：即便下面出错，也不能留一个还在 render 的定时器，
        # 否则窗口关不掉（正是坏上下文反复抛异常时的表现）。
        self._ready = False
        self.timer.stop()
        try:
            self.plotter.close()
        except Exception:                                    # noqa: BLE001
            pass
        p = self.parent()
        if p is not None and hasattr(p, "_on_turbine_window_closed"):
            p._on_turbine_window_closed()
        super().closeEvent(ev)


class StudioWindow(QtWidgets.QMainWindow):
    def __init__(self, scene: Scene, train_kw: Optional[dict] = None,
                 tick_ms: int = 120, render_ms: int = 33, wake: bool = True):
        super().__init__()
        self.scene = scene
        self.setWindowTitle(
            f"WFRL Studio — {scene.name}  [{scene.backend} · "
            f"{scene.n} 台 · {'湍流盒' if scene.turbulence else '均匀来流'}]")
        self.resize(1560, 940)

        # shape=(1,2)：左视口是主场景，右视口留给相机视图。构造时就得定（QtInteractor
        # 建好后不能改 shape）。默认把右视口收成零宽 —— 只看到左边整场，和单视口一样；
        # 开「相机视口」时再把它拉出来。同一 render window ⇒ 不存在多窗口 GL 上下文
        # 那个刷不出的坑（独立弹窗在本机 VTK 版本下 repaint 都救不活）。
        self.plotter = QtInteractor(self, shape=(1, 2))
        self.setCentralWidget(self.plotter.interactor)
        for r in self.plotter.renderers:
            r.SetBackground(1.0, 1.0, 1.0)
        self.plotter.subplot(0, 0)          # SceneView 全建在左视口
        self.view = SceneView(self.plotter, scene, wake=wake)
        self.view.ensure_sensors()
        # 右视口是「相机传感器所见」——只由 FOV/俯仰滑块驱动，鼠标不该动它。
        # 关掉它的交互性：否则光标划过右视口时 trackball 会抓住 renderer[1] 拖动
        # 相机，而每帧 _apply_cam_pose 又把相机拽回传感器位姿，两边打架 ⇒ 拖动时
        # 在「拖出来的焦距」和「传感器焦距」之间反复闪。
        self.plotter.renderers[1].InteractiveOff()
        self._cam_on = False                # 右视口（相机视图）是否开
        self._cam_turb = None               # 右视口里那台真尺度机组的 actor 句柄
        self._cam_idx = 0
        self._cams = []                     # [(label, sensor, si)]
        self._set_right_viewport(0.0)       # 右视口收零，只显示左

        self.trainer = Trainer(scene, **(train_kw or {}))
        self.limiter = self.trainer.limiter
        self._sensors_attached = False
        self._pending = {}                 # topic -> 期望的订阅状态（训练起来前）
        self._demo_enabled = {}            # demo（无 runtime）下各通道的订阅状态

        self.scene_panel = ScenePanel(scene)
        self.channel_panel = ChannelPanel(self._toggle_channel)
        # 通道表不依赖 driver/MPI —— ensure_sensors() 已建好 view.sensors，
        # 先按它铺一份，demo 模式下也能立即勾选/取消相机、雷达等通道。
        if getattr(self.view, "sensors", None):
            self.channel_panel.populate_from_sensors(self.view.sensors)
        self.train_panel = TrainPanel(self._start, self._pause, self._stop)
        self.safety_panel = SafetyPanel()
        self.control_panel = ControlPanel(scene, self._manual_set,
                                          self._manual_clear)
        self.safety_panel.set_notes(
            self.limiter.notes(n_steps=self.trainer.episode_steps
                               or self.trainer.n_steps))
        self._dock("场景", self.scene_panel, QtCore.Qt.LeftDockWidgetArea)
        self._dock("数据通道", self.channel_panel, QtCore.Qt.LeftDockWidgetArea)
        self._dock("手动摆位", self.control_panel, QtCore.Qt.LeftDockWidgetArea)
        self._dock("训练", self.train_panel, QtCore.Qt.RightDockWidgetArea)
        self._dock("物理与安全约束", self.safety_panel,
                   QtCore.Qt.RightDockWidgetArea)

        self._build_menu()

        self.statusBar().showMessage("就绪 —— 点「开始训练」启动 FAST.Farm")
        self._paused = False
        self._last_step = -1
        self._clock = QtCore.QElapsedTimer()
        self._clock.start()
        self._last_ns = self._clock.nsecsElapsed()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(tick_ms)
        self.rtimer = QtCore.QTimer(self)
        self.rtimer.timeout.connect(self._render)
        self.rtimer.start(render_ms)

    def _dock(self, title, widget, area):
        d = QtWidgets.QDockWidget(title, self)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    # ---- 菜单栏 --------------------------------------------------------
    def _build_menu(self):
        mb = self.menuBar()
        vm = mb.addMenu("视图")
        self.act_vortex = QtGui.QAction("尾流圆环（扩散包络）", self,
                                        checkable=True)
        self.act_vortex.setChecked(self.view._rings_armed)  # 默认预备开，转起来才现
        self.act_vortex.toggled.connect(self._toggle_vortex)
        vm.addAction(self.act_vortex)
        self.act_heatmap = QtGui.QAction("风况热力图", self, checkable=True)
        self.act_heatmap.setChecked(self.view._heatmap_on)
        self.act_heatmap.toggled.connect(self._toggle_heatmap)
        vm.addAction(self.act_heatmap)
        # 地形子菜单：无/山地/戈壁（单选），初始选中场景 YAML 的 terrain
        tm = vm.addMenu("地形")
        self._terr_group = QtGui.QActionGroup(self)
        self._terr_group.setExclusive(True)
        for label, kind in (("无", None), ("山地", "mountains"),
                            ("戈壁", "gobi")):
            a = QtGui.QAction(label, self, checkable=True)
            a.setChecked(self.scene.terrain == kind)
            a.triggered.connect(lambda _=False, k=kind: self._set_terrain(k))
            self._terr_group.addAction(a)
            tm.addAction(a)
        vm.addSeparator()
        for name, cam in (("俯视", "top"), ("侧视", "side"),
                          ("斜视", "iso")):
            a = QtGui.QAction(f"主相机：{name}", self)
            a.triggered.connect(lambda _=False, c=cam: self._set_camera(c))
            vm.addAction(a)

        # 相机传感器功能全部收到一个开关后面：打开 = 视锥框 + 右分屏视口 + 相机控件；
        # 关掉 = 全部收起，不干扰主流程。右视口画面在本机 VTK 下还没跑通（见记录），
        # 视锥框与控件先可用。
        self.act_camera = QtGui.QAction("相机传感器", self, checkable=True)
        self.act_camera.toggled.connect(self._toggle_camera_master)
        vm.addSeparator()
        vm.addAction(self.act_camera)

        cm = mb.addMenu("控制")
        a = QtGui.QAction("全部释放手动摆位", self)
        a.triggered.connect(lambda: self._manual_clear(None, None))
        cm.addAction(a)

    def _toggle_camera_master(self, on):
        """相机总开关：视锥框（camera 通道可见性）+ 右分屏视口 + 相机控件一起开关。"""
        on = bool(on)
        # 1. 视锥框：camera 通道的可见性（走现有 set_channel）
        try:
            self.view.set_channel("camera", on, runtime=self.trainer.runtime)
        except Exception:                                    # noqa: BLE001
            pass
        # 2. 右分屏视口 + 控件
        self._toggle_camera_viewport(on)

    def _set_camera(self, kind):
        b = self.view.bounds()
        fp = ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2)
        cam = self.plotter.camera
        cam.focal_point = fp
        cam.up = (0.0, 0.0, 1.0)
        span = max(b[1] - b[0], b[3] - b[2], b[5] - b[4])
        if kind == "top":
            cam.position = (fp[0], fp[1], fp[2] + 2.0 * span)
            cam.up = (0.0, 1.0, 0.0)
        elif kind == "side":
            cam.position = (fp[0], fp[1] - 2.0 * span, fp[2])
        else:                                    # iso
            cam.position = (fp[0] - span, fp[1] - span, fp[2] + span)
        self.plotter.reset_camera_clipping_range()
        self.plotter.render()

    # ---- 手动摆位 ------------------------------------------------------
    def _manual_set(self, topic, i, value):
        self.view.set_manual(topic, i, value)

    def _manual_clear(self, topic, i):
        self.view.clear_manual(topic, i)

    def _toggle_vortex(self, on):
        self.view.set_wake_rings(bool(on))

    def _toggle_heatmap(self, on):
        self.view.set_heatmap(bool(on))

    def _set_terrain(self, kind):
        self.view.set_terrain(kind)

    # ---- 相机右视口（双视口，同一 render window ⇒ 稳定刷新）------------
    def _set_right_viewport(self, frac):
        """右视口占屏宽度比例 frac∈[0,1]。0=收零（只显示左）。"""
        f = float(np.clip(frac, 0.0, 1.0))
        self.plotter.renderers[0].SetViewport(0.0, 0.0, 1.0 - f, 1.0)
        self.plotter.renderers[1].SetViewport(1.0 - f, 0.0, 1.0, 1.0)
        # 只重算左（主）视口的裁剪范围。右视口相机贴着转子平面，自动重算会把机组
        # 裁没（见 _apply_cam_pose）—— 它的裁剪范围在 _apply_cam_pose 里钉死，别在这
        # 里覆盖掉。相机开着时随后会走 _apply_cam_pose 把范围重新钉好。
        self.plotter.renderers[0].ResetCameraClippingRange()
        if not getattr(self, "_cam_on", False):
            self.plotter.renderers[1].ResetCameraClippingRange()

    def _toggle_camera_viewport(self, on):
        self._cam_on = bool(on)
        if self._cam_on:
            self._collect_cams()
            if not self._cams:
                self.statusBar().showMessage(
                    "场景里没有 camera 传感器 —— 在 YAML 的 sensors 加一路 "
                    "{type: camera, ...}")
                self.act_camera.setChecked(False)
                self._cam_on = False
                return
            self._build_camera_toolbar()
            self._build_right_turbine()
            self._set_right_viewport(0.42)
            self.cam_toolbar.setVisible(True)
        else:
            self._set_right_viewport(0.0)
            if getattr(self, "cam_toolbar", None) is not None:
                self.cam_toolbar.setVisible(False)
        self.plotter.interactor.repaint()

    def _collect_cams(self):
        """场景里所有 camera 传感器 × 挂载机组 → [(label, sensor, si)]。"""
        from wfrl.channels import registry
        out = []
        try:
            sensors = [s for s in registry.build(self.scene)
                       if getattr(s, "type_name", None) == "camera"]
        except Exception as e:                              # noqa: BLE001
            print(f"[studio] 相机传感器 build 失败: {e!r}", flush=True)
            sensors = []
        for s in sensors:
            for si, ti in enumerate(s.indices):
                tid = self.scene.turbines[ti].id
                mount = s.params.get("mount", "nacelle_down")
                mname = {"nacelle_down": "机舱下俯视",
                         "nacelle_front": "机舱前视", "hub": "轮毂",
                         "custom": "自定义点位"}.get(
                             mount, mount)
                out.append((f"{tid}·{mname}", s, si))
        self._cams = out

    def _build_camera_toolbar(self):
        if getattr(self, "cam_toolbar", None) is not None:
            return
        tb = self.addToolBar("相机")
        self.cam_toolbar = tb
        tb.addWidget(QtWidgets.QLabel(" 相机 "))
        self._cam_pick = QtWidgets.QComboBox()
        for label, _s, _si in self._cams:
            self._cam_pick.addItem(label)
        self._cam_pick.currentIndexChanged.connect(self._on_cam_pick)
        tb.addWidget(self._cam_pick)
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" FOV "))
        self._cam_fov = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._cam_fov.setRange(5, 90)
        self._cam_fov.setFixedWidth(130)
        self._cam_fov.valueChanged.connect(self._on_cam_slider)
        tb.addWidget(self._cam_fov)
        self._cam_fov_lbl = QtWidgets.QLabel("  ")
        tb.addWidget(self._cam_fov_lbl)
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" 俯仰 "))
        self._cam_pitch = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._cam_pitch.setRange(-90, 30)
        self._cam_pitch.setFixedWidth(130)
        self._cam_pitch.valueChanged.connect(self._on_cam_slider)
        tb.addWidget(self._cam_pitch)
        self._cam_pitch_lbl = QtWidgets.QLabel("  ")
        tb.addWidget(self._cam_pitch_lbl)
        self._on_cam_pick(0)          # 同步滑块到首个相机的 fov/pitch

    def _build_right_turbine(self):
        """在右视口建一台**真实尺度**的选中机组（相机拍它，叶尖 0.5 m 真值）。"""
        from wfrl.viz.animate import build_turbine, turbine_actors
        self.plotter.subplot(0, 1)
        if self._cam_turb is not None:
            for a in turbine_actors(self._cam_turb):
                try:
                    self.plotter.renderers[1].RemoveActor(a)
                except Exception:                            # noqa: BLE001
                    pass
        _label, sensor, si = self._cams[self._cam_pick.currentIndex()] \
            if self._cams else (None, None, None)
        ti = sensor.indices[si] if sensor is not None else 0
        self._cam_idx = ti
        t = self.scene.turbines[ti]
        self._cam_turb = build_turbine(self.plotter, t.x, t.y,
                                       self.scene.hub_height, scale=1.0)
        self.plotter.subplot(0, 0)    # 交互焦点还给左视口
        self._apply_cam_pose()

    def _on_cam_pick(self, _i):
        if not self._cams:
            return
        _label, s, _si = self._cams[self._cam_pick.currentIndex()]
        self._cam_fov.blockSignals(True); self._cam_pitch.blockSignals(True)
        self._cam_fov.setValue(int(s.params.get("fov", 40)))
        self._cam_pitch.setValue(int(s.params.get("pitch", -30)))
        self._cam_fov.blockSignals(False); self._cam_pitch.blockSignals(False)
        self._cam_fov_lbl.setText(f" {self._cam_fov.value()}° ")
        self._cam_pitch_lbl.setText(f" {self._cam_pitch.value()}° ")
        if self._cam_on:
            self._build_right_turbine()
            self.plotter.interactor.repaint()

    def _on_cam_slider(self, _v):
        self._cam_fov_lbl.setText(f" {self._cam_fov.value()}° ")
        self._cam_pitch_lbl.setText(f" {self._cam_pitch.value()}° ")
        self._apply_cam_pose()
        self.plotter.interactor.repaint()

    def _apply_cam_pose(self):
        """把右视口相机设成选中传感器相机的位姿+FOV（真实尺度）。"""
        if not self._cams or self._cam_turb is None:
            return
        _label, s, si = self._cams[self._cam_pick.currentIndex()]
        ti = s.indices[si]
        s.params["fov"] = float(self._cam_fov.value())
        s.params["pitch"] = float(self._cam_pitch.value())
        ctx = {"yaw": np.array([float(self.view._yaw[ti])])}
        pos, focal, up, fov = s.camera_pose(ctx, si, scale=1.0)
        cam = self.plotter.renderers[1].camera
        cam.SetParallelProjection(False)
        cam.view_angle = float(fov)
        cam.position = tuple(pos)
        cam.focal_point = tuple(focal)
        cam.up = tuple(up)
        # 关键：**不要**用 ResetCameraClippingRange。相机贴着转子平面（离叶片
        # 才 1~3 m）却盯着 57 m 外的焦点，机组包围盒把相机整个裹在里面 —— VTK 自动
        # 算近/远面时会把近面推到叶片前/后，随俯仰角不同就把整台机组裁没了（拖动
        # 闪现、只有某些角度才稳）。改成钉死一个够宽的裁剪范围：近面 0.5 m 收住最近
        # 的叶尖，远面覆盖焦点外一段 + 塔筒到地面。TIP_R≈63，HUB_H≈90。
        tip_r = float(getattr(s, "TIP_R", 63.0))
        hub_h = float(getattr(s, "HUB_H", 90.0))
        far = 0.9 * tip_r + 3.0 * tip_r + hub_h
        cam.SetClippingRange(0.5, far)

    def _update_right_turbine(self):
        """渲染定时器每帧调：右视口机组姿态 + 相机随实时数据更新。"""
        if not self._cam_on or self._cam_turb is None:
            return
        from wfrl.viz.animate import update_turbine
        v = self.view
        ti = self._cam_idx
        update_turbine(self._cam_turb, float(v._yaw[ti]), float(v._spin[ti]),
                       float(v._pitch[ti]), flex_scale=v.flex_scale)
        self._apply_cam_pose()

    def _mark_right_dirty(self):
        """强制右 renderer 下次重绘。改了 user_matrix 后 VTK 不一定认为 renderer
        变脏（尤其相机没动时）—— 显式 Modified() 让 paintEvent 一定重画右视口。"""
        try:
            r = self.plotter.renderers[1]
            r.Modified()
            for a in r.GetActors():
                a.Modified()
        except Exception:                                # noqa: BLE001
            pass

    # ---- 按钮 ----------------------------------------------------------
    def _start(self):
        self.trainer.start()
        self.train_panel.set_running(True)
        self.statusBar().showMessage("启动 FAST.Farm（spawn 约 25 s）…")

    def _pause(self):
        self._paused = not self._paused
        self.trainer.pause(self._paused)
        self.train_panel.btn_pause.setText("▶ 继续" if self._paused else "⏸ 暂停")

    def _stop(self):
        # stop() 要等 driver 排空迭代预算（几十秒），压在按钮回调里界面会假死。
        # 先把定时器停掉再等，避免这期间还去读一个正在收尾的 runtime。
        self.statusBar().showMessage("停止中：排空 FAST.Farm 迭代预算…")
        QtWidgets.QApplication.processEvents()
        self.trainer.stop()
        self.train_panel.set_running(False)
        self.statusBar().showMessage("已停止")

    def _toggle_channel(self, topic, on):
        rt = self.trainer.runtime
        # rt 为 None（demo 脚本模式无 driver）时也能切：view.set_channel 在
        # runtime=None 下只跳过“停止采样”，actor 可见性照常切 —— 关相机/雷达即生效。
        self.view.set_channel(topic, on, runtime=rt)
        if rt is None:
            self._demo_enabled[topic] = on     # 记住状态，稍后有 runtime 再补采样

    # ---- 定时器 --------------------------------------------------------
    def _tick(self):
        """控制步节奏：取快照 → 面板 + 机组位姿。不做渲染。"""
        tr = self.trainer
        snap = tr.latest()
        if snap is None:
            return
        if not self._sensors_attached and tr.runtime is not None:
            self.view.attach_sensors(tr.runtime, snap.ctx)
            self.channel_panel.populate(tr.runtime)
            for t, on in {**self._demo_enabled, **self._pending}.items():
                self.view.set_channel(t, on, runtime=tr.runtime)
            self._pending.clear()
            self._sensors_attached = True
        self.train_panel.update_snapshot(snap, tr)
        self.safety_panel.update_from(self.limiter)
        if snap.step != self._last_step:
            self._last_step = snap.step
            self.view.on_frame(snap.frame, snap.measure, ctx=snap.ctx)
        if not tr.running and self.train_panel.btn_stop.isEnabled():
            self.train_panel.set_running(False)
            self.statusBar().showMessage(
                "训练结束" + (f"（异常，见控制台）" if tr.error else ""))

    def _render(self):
        """画面节奏：按真实流逝时间推进转子相位，1× 实时。

        repaint 放在 try/finally 的 finally 里：最小复现证明 repaint 能刷右视口，
        所以只要它每帧一定执行，右视口就该刷。若 _update_right_turbine / view.tick
        抛异常（双视口下 subplot 状态、相机 NaN 等），Qt 定时器会静默吞掉、导致
        repaint 永不执行 —— 这正是"右视口不刷新"最可能的原因。异常首帧打印一次。
        """
        now = self._clock.nsecsElapsed()
        dt = (now - self._last_ns) / 1e9
        self._last_ns = now
        try:
            self._update_right_turbine()
            self.view.tick(dt)
        except Exception:                                # noqa: BLE001
            if not getattr(self, "_render_warned", False):
                self._render_warned = True
                import traceback
                print(f"[studio] _render 异常（只报一次）:\n"
                      f"{traceback.format_exc()}", flush=True)
        finally:
            self.plotter.interactor.repaint()

    def closeEvent(self, ev):
        self.timer.stop()
        self.rtimer.stop()
        self.view.close()
        self.trainer.stop()
        super().closeEvent(ev)


# ---------------------------------------------------------------------------
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="WFRL Studio")
    ap.add_argument("--scene", required=True, help="场景 YAML")
    ap.add_argument("--train", action="store_true", help="启动后立即开始训练")
    ap.add_argument("--iters", type=int, default=8)
    ap.add_argument("--n-steps", type=int, default=32)
    ap.add_argument("--warmup", type=int, default=4)
    ap.add_argument("--fast", action="store_true",
                    help="快迭代预设：iters=2 n_steps=16 warmup=2，单次 spawn 跑完，"
                         "只用于冲流程/看可视化，不出结论")
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--replay", action="store_true",
                    help="回放模式：加载 --ckpt 的策略，用确定性动作 (dist.mean) "
                         "跑一回合并可视化，不做训练更新")
    ap.add_argument("--replay-steps", type=int, default=400,
                    help="回放时跑多少步（默认 400）")
    ap.add_argument("--demo", action="store_true",
                    help="演示模式：预设启停循环（pitch 90↔0°）+ 偏航变化（0→30°），"
                         "不加载 checkpoint，用于演示 3D 可视化效果")
    ap.add_argument("--demo-cycles", type=int, default=2,
                    help="演示模式循环次数（默认 2）")
    ap.add_argument("--no-wake", action="store_true",
                    help="不建 FLORIS proxy 尾流平面（省一次稳态求解）")
    args = ap.parse_args(argv)

    # --fast 只是把三个训练超参压小，覆盖各自默认值（除非用户又显式给了值）。
    # 目的是把一次演示从几十分钟压到几分钟：一次 spawn、少采几步、少热身。
    if args.fast:
        argv_seen = set(argv if argv is not None else sys.argv[1:])
        if "--iters" not in argv_seen:
            args.iters = 2
        if "--n-steps" not in argv_seen:
            args.n_steps = 16
        if "--warmup" not in argv_seen:
            args.warmup = 1

    scene = load_scene(args.scene)
    app = QtWidgets.QApplication(sys.argv)
    win = StudioWindow(scene, wake=not args.no_wake, train_kw=dict(
        iters=args.iters, n_steps=args.n_steps, warmup_steps=args.warmup,
        ckpt_path=args.ckpt, replay=args.replay, replay_steps=args.replay_steps,
        demo=args.demo, demo_cycles=args.demo_cycles))
    win.show()
    if args.train or args.replay or args.demo:
        QtCore.QTimer.singleShot(300, win._start)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
