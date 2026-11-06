"""Studio 训练启停、停止确认与快照和渲染定时器。"""
from __future__ import annotations

import logging
import time


_LOG = logging.getLogger("wfrl.studio.app")


def _start(self):
    if self._stopping or self.trainer.running or self.trainer.runtime is not None:
        return
    self._stop_error = None
    self._close_pending = False
    self.trainer.error = None
    self.trainer.start()
    self.train_panel.set_running(True)
    self.statusBar().showMessage("启动 FAST.Farm（spawn 约 25 s）…")


def _pause(self):
    self._paused = not self._paused
    self.trainer.pause(self._paused)
    self.train_panel.btn_pause.setText("▶ 继续" if self._paused else "⏸ 暂停")


def _stop(self):
    if self._stopping:
        return
    self._stopping = True
    self._stop_started = time.monotonic()
    self.statusBar().showMessage("停止中：排空 FAST.Farm 迭代预算…")
    self.trainer.stop(timeout=0)
    self._stop_timer.start()


def _poll_stop(self):
    if self.trainer.running:
        if time.monotonic() - self._stop_started >= 120:
            self.statusBar().showMessage("停止未确认：工作线程仍在收尾")
        return
    if self._stopping:
        self.trainer.stop(timeout=0)
        self._stopping = False
        self._stop_error = (self.trainer.error or '后端资源仍由 Trainer 持有') if self.trainer.runtime is not None else None
        self.train_panel.set_running(False)
        self.train_panel.btn_start.setEnabled(not bool(self._stop_error))
        self.statusBar().showMessage("停止未确认：" + str(self._stop_error) if self._stop_error
                                    else ("已停止（训练异常，见控制台）" if self.trainer.error else "已停止"))
    if self._close_pending and not self._stop_error:
        self.close()
    elif not self._stopping:
        self._stop_timer.stop()


def _tick(self):
    """控制步节奏：取快照 → 面板 + 机组位姿。不做渲染。"""
    if self._stopping or self._stop_error:
        return
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
            "训练结束" + ("（异常，见控制台）" if tr.error else ""))


def _render(self):
    """画面节奏：按真实流逝时间推进转子相位，1× 实时。

    场景更新失败仍尝试 repaint。更新与重绘异常均记录类型、信息和堆栈；
    连续相同异常只记录一次，异常变化或恢复后再次失败时重新记录。
    """
    now = self._clock.nsecsElapsed()
    dt = (now - self._last_ns) / 1e9
    self._last_ns = now
    try:
        try:
            self._update_right_turbine()
            self.view.tick(dt)
        finally:
            self.plotter.interactor.repaint()
    except Exception as e:                            # noqa: BLE001
        error = (type(e), str(e))
        if getattr(self, "_render_error", None) != error:
            self._render_error = error
            _LOG.exception("Studio 渲染失败")
    else:
        self._render_error = None
