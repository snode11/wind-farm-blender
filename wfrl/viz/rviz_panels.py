"""RViz 遥测表格与数据通道开关。"""
import numpy as np
from qtpy import QtWidgets

from wfrl.viz.geometry import CALIB


# ---------------------------------------------------------------------------
# 遥测表格面板（订阅 yaw/power/vibration）
# ---------------------------------------------------------------------------
def _finite_mean(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    return float(values.mean()) if values.size else np.nan


def _finite_min(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    return float(values.min()) if values.size else np.nan


class TelemetryPanel(QtWidgets.QTableWidget):
    # 列 = (表头, 订阅的通道, 取值方式, 格式)
    COLS = [
        ("机组", None, None, None),
        ("yaw°", "yaw", lambda d, i: d[i], "%+.1f"),
        # 桨距分两列：指令是外部策略累加的 PitchRef（下界语义），实测是
        # avrSWAP(4) 回读。大风档基线自己顺桨，两列会明显分开 —— 这正是要看的。
        ("桨距指令°", "pitch", lambda d, i: d[i], "%.2f"),
        ("桨距实测°", "pitch_meas", lambda d, i: d[i], "%.2f"),
        ("转矩kNm", "torque", lambda d, i: d[i] / 1e3, "%.1f"),
        ("转速rpm", "rotor_speed", lambda d, i: d[i], "%.2f"),
        ("功率MW", "power", lambda d, i: d[i], "%.2f"),
        ("TI", "vibration", lambda d, i: d[i, 0], "%.3f"),
        ("1P载荷", "vibration", lambda d, i: d[i, 4], "%.3f"),
        # 叶尖挠度给**真值**（不含 flex_scale 显示放大），三片取均值。
        # 弯矩→挠度用 geometry.CALIB 的实测标定。
        ("挠度m", "m_flap",
         lambda d, i: CALIB["flap"][0] * _finite_mean(d[i]) + CALIB["flap"][1],
         "%+.2f"),
        # 扫塔净空：三片叶掠塔时的最小间隙，同样是 **1× 真值**（不含显示放大）。
        # 叶片有预弯 + 锥角 + 悬伸，8 m/s 下净空 4.4 m，只有风速大到叶尖挠度
        # 翻倍才会逼近塔筒 —— 所以低于 CLR_WARN 才染色。
        ("净空m", "clearance", lambda d, i: _finite_min(d[i]), "%.2f"),
    ]
    DUTY_LIMIT = 0.1        # multiagent_env.py:206，超过环境会把该动作清零
    CLR_WARN = 1.5          # 净空低于此值染黄（设计态 4.5 m，1.5 m 已是异常工况）

    def __init__(self, n, hub):
        super().__init__(n, len(self.COLS))
        self.setHorizontalHeaderLabels([c[0] for c in self.COLS])
        self.verticalHeader().setVisible(False)
        # 不撑开的话 dock 只给两列的宽度，后面几列会被截掉
        self.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Stretch)
        self.setMinimumWidth(560)
        for i in range(n):
            self.setItem(i, 0, QtWidgets.QTableWidgetItem(f"T{i+1}"))
            for c in range(1, len(self.COLS)):
                self.setItem(i, c, QtWidgets.QTableWidgetItem("-"))
        self._buf = {}
        self._duty = {}
        for _, topic, _, _ in self.COLS:
            if topic:
                hub.subscribe(topic, lambda d, t=topic: self._set(t, d))
        hub.subscribe("duty", lambda d: self._set_duty(d))

    def _set(self, k, d):
        self._buf[k] = d
        self._refresh()

    def _set_duty(self, d):
        """执行机构占空比：逼近 10% 的格子染黄，说明"下一步会被环境清零"。"""
        self._duty = d or {}
        self._refresh()

    def _refresh(self):
        from qtpy.QtGui import QColor
        warn = QColor(255, 235, 150)
        plain = QColor(255, 255, 255)
        for i in range(self.rowCount()):
            for c, (_, topic, get, fmt) in enumerate(self.COLS):
                if topic is None:
                    continue
                d = self._buf.get(topic)
                if d is None:
                    continue
                try:
                    v = float(get(d, i))
                except (IndexError, TypeError, ValueError):
                    continue
                self.item(i, c).setText("-" if not np.isfinite(v) else fmt % v)
            for c, key in ((1, "yaw"), (2, "pitch")):
                du = self._duty.get(key)
                hot = du is not None and i < len(du) and du[i] >= self.DUTY_LIMIT
                self.item(i, c).setBackground(warn if hot else plain)
            # 净空告警：黄=接近塔筒，红=已扫塔
            cl = self._buf.get("clearance")
            col = len(self.COLS) - 1
            if cl is not None and i < len(cl):
                v = float(np.nanmin(cl[i]))
                bg = plain if not np.isfinite(v) or v >= self.CLR_WARN else (
                    QColor(255, 170, 170) if v <= 0 else warn)
                self.item(i, col).setBackground(bg)


# ---------------------------------------------------------------------------
# 通道开关面板（像 RViz 左侧 Displays 勾选框）
# ---------------------------------------------------------------------------
class ChannelPanel(QtWidgets.QWidget):
    def __init__(self, hub, topics):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("<b>数据通道 (Channels)</b>"))
        for t in topics:
            cb = QtWidgets.QCheckBox(t)
            cb.setChecked(True)
            cb.stateChanged.connect(
                lambda s, name=t: hub.enabled.__setitem__(name, bool(s)))
            lay.addWidget(cb)
        lay.addStretch(1)

