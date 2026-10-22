import threading
from types import SimpleNamespace

from wfrl.studio.trainer import Trainer


def test_stop_timeout_keeps_live_thread_visible():
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    trainer = Trainer.__new__(Trainer)
    trainer._thread = thread
    trainer._stop = threading.Event()
    trainer._paused = threading.Event()
    thread.start()
    try:
        trainer.stop(timeout=0)
        assert trainer.running, 'A timed-out worker must remain visible and block restart'
        assert trainer._thread is thread
    finally:
        release.set()
        thread.join(1)
