"""Qt event-loop stop confirmation using a controlled live worker."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import threading
import time
from types import SimpleNamespace as NS
import pytest
from qtpy import QtCore, QtWidgets, QtGui
from wfrl.studio.app import StudioWindow
from wfrl.studio.trainer import Trainer
from wfrl.studio.view import SceneView


@pytest.fixture
def window():
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    class SmallWindow(StudioWindow):
        def __init__(self):
            QtWidgets.QMainWindow.__init__(self)
            self._stopping=False;self._stop_error=None;self._close_pending=False
            self._stop_timer=QtCore.QTimer(self);self._stop_timer.setInterval(5)
            self._stop_timer.timeout.connect(self._poll_stop)
            self.timer=QtCore.QTimer(self);self.rtimer=QtCore.QTimer(self)
            self.train_panel=NS(set_running=lambda value: self.states.append(value),btn_start=NS(setEnabled=lambda value:None))
            self.states=[];self.view=NS(close=lambda **kw: True)
            self.trainer=object.__new__(Trainer)
            self.trainer._stop=threading.Event();self.trainer._paused=threading.Event()
            self.trainer.error=None
            self.trainer.runtime=None
    win=SmallWindow();release=threading.Event()
    win.trainer._thread=threading.Thread(target=release.wait,daemon=True);win.trainer._thread.start()
    watchdog=threading.Timer(.5,release.set);watchdog.start()
    yield win,release,app
    watchdog.cancel();release.set();win.trainer.stop(timeout=1);win._stop_timer.stop();win.deleteLater();app.processEvents()


def test_stop_returns_promptly_and_confirms_only_after_worker_exit(window):
    win,release,app=window
    started=time.monotonic();win._stop()
    assert time.monotonic()-started < .2
    assert win._stopping and win.trainer.running and not win.states
    assert '停止中' in win.statusBar().currentMessage()
    win._poll_stop();assert win._stopping
    release.set();win.trainer._thread.join(1)
    win._poll_stop()
    assert not win._stopping and win.states==[False]
    assert '已停止' in win.statusBar().currentMessage()


def test_close_is_ignored_until_live_worker_is_confirmed(window):
    win,release,app=window
    event=QtGui.QCloseEvent();started=time.monotonic();win.closeEvent(event)
    assert time.monotonic()-started < .2
    assert not event.isAccepted() and win._close_pending
    assert win.trainer.running


def test_qt_timer_keeps_dispatching_while_stop_is_pending(window):
    win,release,app=window
    pulses=[]
    timer=QtCore.QTimer();timer.setInterval(5);timer.timeout.connect(lambda:pulses.append(True));timer.start()
    win._stop();win._stop_started=time.monotonic()-121;win._poll_stop()
    assert '未确认' in win.statusBar().currentMessage() and win.trainer.running
    until=time.monotonic()+.05
    while time.monotonic()<until: app.processEvents();time.sleep(.001)
    timer.stop()
    assert len(pulses)>=2 and win._stopping


def test_failed_cleanup_remains_unconfirmed_after_thread_exit(window):
    win,release,app=window
    win.trainer.runtime=NS(driver=object())
    win.trainer.error='cleanup failed'
    win._stop();release.set();win.trainer._thread.join(1);win._poll_stop()
    assert win._stop_error and '未确认' in win.statusBar().currentMessage()
    event=QtGui.QCloseEvent();win.closeEvent(event)
    assert not event.isAccepted()


def test_view_close_retains_live_solve_worker_for_retry():
    view=object.__new__(SceneView);release=threading.Event()
    view._solve_stop=threading.Event();view._solve_evt=threading.Event()
    thread=threading.Thread(target=release.wait,daemon=True);view._solve_thread=thread;thread.start()
    try:
        started=time.monotonic();assert view.close(timeout=0) is False
        assert time.monotonic()-started < .2 and view._solve_thread is thread
        release.set();thread.join(1)
        assert view.close(timeout=0) is True and view._solve_thread is None
    finally: release.set();thread.join(1)
