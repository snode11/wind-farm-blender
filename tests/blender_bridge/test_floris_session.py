"""New FLORIS adapter tests; independent of historical suites."""
from types import SimpleNamespace
import threading

import numpy as np

from wfrl.blender_bridge.floris_session import FlorisDemoSession


def scene():
    return SimpleNamespace(backend='floris', controls=['yaw'], n=3,
                           xcoords=[0, 504, 1008], ycoords=[0, 0, 0],
                           wind_speed=8, wind_direction=270)


class Driver:
    instances = []
    def __init__(self, env_id, max_steps, controls):
        self.n, self.dt, self.done, self.k = 3, 3, False, 0
        self.xcoords, self.ycoords = scene().xcoords, scene().ycoords
        self.limit, self.closed = max_steps, False
        self.instances.append(self)
    def reset(self, **kw):
        self.wind = kw
    def step(self, delta):
        self.k += 1
        self.done = self.k >= self.limit
        return dict(step=self.k, power=np.array([1., 2., 3.]),
                    yaw=np.full(3, delta), wind_speed=np.full(3, 8.),
                    wind_direction=np.full(3, 270.), reward=.2)
    def close(self, **kw):
        self.closed = True


def test_snapshots_preserve_modeled_values_without_inventing_drivetrain():
    snapshots = []
    tr = FlorisDemoSession(scene(), snapshots.append, max_steps=2,
                          frame_interval=0, driver_factory=Driver)
    tr.start()
    tr._thread.join(3)
    assert not tr.running and tr.error is None
    assert [s.step for s in snapshots] == [1, 2]
    assert snapshots[-1].farm_power == 6
    assert snapshots[-1].ctx['backend_demo'] is True
    assert set(snapshots[-1].measure) == {'power', 'yaw', 'wind_speed', 'wind_direction'}
    assert Driver.instances[-1].closed


def test_layout_mismatch_fails_and_closes_backend():
    s = scene()
    s.xcoords = [0, 600, 1200]
    tr = FlorisDemoSession(s, driver_factory=Driver)
    tr.start()
    tr._thread.join(3)
    assert 'layout' in tr.error
    assert Driver.instances[-1].closed
    assert tr.latest() is None


def test_stop_unblocks_paused_demo_and_retains_thread_handle():
    ready = threading.Event()
    def receive(snapshot):
        tr.pause()
        ready.set()
    tr = FlorisDemoSession(scene(), receive, max_steps=100,
                          frame_interval=0, driver_factory=Driver)
    tr.start()
    assert ready.wait(3)
    tr.stop(timeout=3)
    assert tr._thread is not None and not tr.running
    assert Driver.instances[-1].closed
