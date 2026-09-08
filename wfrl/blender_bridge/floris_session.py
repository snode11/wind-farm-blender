"""Headless FLORIS backend demonstration; no policy or training is performed."""
import threading
from types import SimpleNamespace

import numpy as np


class FlorisDemoSession:
    """Trainer lifecycle interface, reusing FastFarmDriver's FLORIS stepping.

    Only registered layouts matching the scene are accepted. ``yaw_delta`` is a
    fixed demonstration command in degrees per control step, not a learned policy.
    Snapshot values are direct steady-state model outputs, not field measurements.
    """
    def __init__(self, scene, on_snapshot=None, demo=True, replay=False,
                 env_id='Dec_Turb3_Row1_Floris', max_steps=120,
                 yaw_delta=0.0, frame_interval=0.1, driver_factory=None):
        if not demo or replay or scene.backend != 'floris':
            raise ValueError('FLORIS adapter supports backend demo only')
        if list(scene.controls) != ['yaw']:
            raise ValueError('FLORIS backend demo supports yaw control only')
        if 'floris' not in env_id.lower():
            raise ValueError('FLORIS backend demo requires a FLORIS env_id')
        if int(max_steps) < 1 or not np.isfinite(frame_interval) or frame_interval < 0:
            raise ValueError('max_steps must be positive; frame_interval must be nonnegative')
        if not np.isfinite(yaw_delta):
            raise ValueError('yaw_delta must be finite')
        self.scene, self.on_snapshot = scene, on_snapshot
        self.env_id, self.max_steps = env_id, int(max_steps)
        self.yaw_delta, self.frame_interval = float(yaw_delta), float(frame_interval)
        self._driver_factory = driver_factory
        self._thread = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self.error = None
        self._latest = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def latest(self):
        return self._latest

    def start(self):
        if self.running:
            return
        self.error = None
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, name='wfrl-floris-demo', daemon=True)
        self._thread.start()

    def pause(self, on=True):
        self._paused.set() if on else self._paused.clear()

    def stop(self, timeout=120):
        self._stop.set()
        self._paused.clear()
        if self.running and threading.current_thread() is not self._thread:
            self._thread.join(timeout)

    def _run(self):
        driver = None
        try:
            factory = self._driver_factory
            if factory is None:
                from wfrl.fastfarm_driver import FastFarmDriver
                factory = FastFarmDriver
            driver = factory(self.env_id, max_steps=self.max_steps, controls=('yaw',))
            if (driver.n != self.scene.n or
                    not np.allclose(driver.xcoords, self.scene.xcoords) or
                    not np.allclose(driver.ycoords, self.scene.ycoords)):
                raise ValueError('Scene layout does not match registered FLORIS env_id')
            driver.reset(wind_speed=self.scene.wind_speed,
                         wind_direction=self.scene.wind_direction)
            while not self._stop.is_set() and not driver.done:
                if self._paused.is_set():
                    self._stop.wait(0.02)
                    continue
                m = driver.step(self.yaw_delta)
                # The driver has placeholder loads and inferred drivetrain channels;
                # FLORIS cannot supply those. Omit them so the wire marks unsupported.
                measure = {key: m[key] for key in
                           ('yaw', 'power', 'wind_speed', 'wind_direction')}
                snap = SimpleNamespace(
                    step=int(m['step']), iters_done=0, frame={}, measure=measure,
                    ctx={'t': m['step'] * driver.dt, 'backend': 'floris',
                         'backend_demo': True},
                    farm_power=float(np.sum(m['power'])), reward=float(m['reward']),
                    events=[], phase='done' if driver.done else 'sampling',
                    demo_label='FLORIS steady-state backend demo (no training)')
                self._latest = snap
                if self.on_snapshot is not None:
                    self.on_snapshot(snap)
                if self._stop.wait(self.frame_interval):
                    break
        except Exception as exc:
            self.error = str(exc)
        finally:
            if driver is not None:
                try:
                    driver.close(drain_cap=0)
                except Exception as exc:
                    self.error = 'FLORIS cleanup failed: ' + str(exc)
