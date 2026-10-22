"""Deterministic SYNTH-only transport fixture, never a physics backend."""
import threading
import time
from types import SimpleNamespace


class FakeTrainer:
    def __init__(self, scene, on_snapshot, **options):
        self.scene, self.callback = scene, on_snapshot
        self._stop = threading.Event()
        self.error = None
    def start(self):
        def run():
            step = 0
            while not self._stop.is_set():
                n = len(self.scene.turbines)
                self.callback(SimpleNamespace(step=step, ctx={'t':step*.1},
                    measure={'yaw':[step%30]*n, 'pitch':[2.]*n, 'rotor_speed':[8.]*n,
                             'power':[2.]*n}, farm_power=2.*n, reward=.5, events=[]))
                step += 1
                self._stop.wait(.1)
        self._thread=threading.Thread(target=run, name='wfrl-fake', daemon=True)
        self._thread.start()
    def stop(self, timeout=120):
        self._stop.set(); self._thread.join(timeout)
