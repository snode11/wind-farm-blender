"""Studio 场景、通道、训练、约束与手动控制面板。"""
from __future__ import annotations

import os

import numpy as np
from qtpy import QtCore, QtGui, QtWidgets

from wfrl.channels.base import Fidelity
from wfrl.safety import SafetyLimiter
from wfrl.scene.schema import Scene


def _use_system_fixed_font(widget):
    """Use Qt's platform font instead of a Windows/macOS family name."""
    widget.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))


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
            "取消订阅 = 该传感器**停止采样**（省下 lidar 合成采样、camera 离屏"
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
        # 空 dict 时使用 _a/_b 两条训练曲线。
        self._named = {}
        # 分机组曲线：history 每步存 yaw/pitch/rpm/power 数组(n,)
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
        """分机组曲线。history[i] = {yaw:[...], pitch:[...], rpm:[...], power:[...]}"""
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

        # 分机组曲线：每台画 yaw/pitch/power 三条
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

        # 命名曲线：按标签选择显示内容。
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
