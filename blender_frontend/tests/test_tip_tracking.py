"""Tip-trail geometry, fade and reset behavior with lightweight curve fakes."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender import tip_tracking as tracking


class Points:
    def __init__(self):
        self.count = 1
        self.coordinates = None
        self.writes = 0

    def add(self, count):
        self.count += count

    def foreach_set(self, name, values):
        assert name == 'co' and len(values) == self.count * 4
        self.coordinates = np.asarray(values, dtype=np.float32).reshape(-1, 4).copy()
        self.writes += 1


class Spline:
    def __init__(self):
        self.points = Points()
        self._level = 0
        self.material_writes = 0

    @property
    def material_index(self):
        return self._level

    @material_index.setter
    def material_index(self, level):
        self._level = level
        self.material_writes += 1


class Splines(list):
    def __init__(self):
        super().__init__()
        self.clears = 0
        self.builds = 0

    def clear(self):
        self.clears += 1
        super().clear()

    def new(self, kind):
        assert kind == 'POLY'
        self.builds += 1
        spline = Spline()
        self.append(spline)
        return spline


class Curve:
    def __init__(self):
        self.data = SimpleNamespace(splines=Splines())
        self.hidden = False
        self.hide_render = False

    def hide_set(self, hidden):
        self.hidden = hidden


class Scene(dict):
    def __init__(self):
        super().__init__()
        self.frame_current = 0
        self.time = 0.
        self.objects = {'WFRL.Turbine.T1.Rotor': SimpleNamespace(rotation_euler=SimpleNamespace(x=0.))}
        self.objects.update({f'WFRL.Turbine.T1.Blade{bid}': SimpleNamespace(tip=None)
                             for bid in (1, 2, 3)})


@pytest.fixture
def trails(monkeypatch):
    monkeypatch.setattr(tracking, '_ensure_curve', lambda *_: Curve())
    monkeypatch.setattr(tracking, '_simulation_time', lambda scene: scene.time)
    monkeypatch.setattr(tracking, '_tip_world', lambda obj, _depsgraph, **_kw: obj.tip)
    monkeypatch.setattr(tracking, '_is_playing', lambda scene: False)

    def make(max_points=tracking.MAX_POINTS):
        scene = Scene()
        trail = tracking.TipTrail(scene, max_points=max_points)

        def tick(time_s, angle=0., *, frame=None, point=None):
            scene.frame_current = scene.frame_current + 1 if frame is None else frame
            scene.time = time_s
            scene.objects['WFRL.Turbine.T1.Rotor'].rotation_euler.x = angle % math.tau
            for bid in (1, 2, 3):
                phase = angle + (bid - 1) * math.tau / 3
                scene.objects[f'WFRL.Turbine.T1.Blade{bid}'].tip = (
                    (time_s * .02, math.sin(phase), math.cos(phase)) if point is None else point)
            trail.update(scene)
        return scene, trail, tick
    return make


def capture_pair(tick):
    for i in range(161):
        tick(i * .05, i * math.tau / 80)


def geometry(trail):
    return {bid: [s.points.coordinates.tolist() for s in obj.data.splines]
            for bid, obj in trail.objects.items()}


def work(trail):
    return [(obj.data.splines.clears, obj.data.splines.builds,
             [(s.points.writes, s.material_writes) for s in obj.data.splines])
            for obj in trail.objects.values()]


def test_hold_does_not_rebuild_or_write_curves(trails):
    _scene, trail, tick = trails()
    capture_pair(tick)
    assert trail.compare_since == 8.
    assert all(len(lines) == 2 for lines in trail.completed.values())
    before, before_work = geometry(trail), work(trail)
    for t in (8.01, 8.5, 9., 9.9, 10.):
        tick(t)
        assert geometry(trail) == before
        assert work(trail) == before_work


def test_fade_updates_material_only_when_level_changes(trails):
    _scene, trail, tick = trails()
    capture_pair(tick)
    before = geometry(trail)
    splines = [s for obj in trail.objects.values() for s in obj.data.splines]
    counts = [(obj.data.splines.clears, obj.data.splines.builds) for obj in trail.objects.values()]
    tick(10.1)
    level = round(tracking.opacity(.1) * (tracking.FADE_LEVELS - 1))
    assert 0 < level < tracking.FADE_LEVELS - 1
    assert all(s.material_index == level and s.material_writes == 2 for s in splines)
    after_work = work(trail)
    tick(10.10001)
    assert work(trail) == after_work
    tick(10.3)
    assert all(s.material_index < level and s.material_writes == 3 for s in splines)
    assert all(s.points.writes == 1 for s in splines)
    assert counts == [(obj.data.splines.clears, obj.data.splines.builds) for obj in trail.objects.values()]
    assert splines == [s for obj in trail.objects.values() for s in obj.data.splines]
    assert geometry(trail) == before


def test_zero_fade_level_removes_geometry_then_capture_restarts(trails):
    scene, trail, tick = trails()
    capture_pair(tick)
    tick(10.749)
    assert all(not obj.data.splines for obj in trail.objects.values())
    before = work(trail)
    tick(10.7495)
    assert work(trail) == before
    tick(10.75)
    assert scene['wfrl_tip_trail_clear_reason'] == 'comparison_complete'
    assert trail.compare_since is None
    assert all(not lines for lines in trail.completed.values())
    assert all(len(points) == 1 for points in trail.points.values())
    tick(10.8)
    assert all(len(obj.data.splines) == 1 for obj in trail.objects.values())
    assert all(obj.data.splines[0].material_index == tracking.FADE_LEVELS - 1
               for obj in trail.objects.values())


def test_clear_forces_next_sample_to_redraw_even_at_same_time(trails):
    scene, trail, tick = trails()
    tick(0.)
    tick(.05)
    assert all(obj.data.splines for obj in trail.objects.values())
    trail.clear()
    assert all(not obj.data.splines for obj in trail.objects.values())
    clears = [obj.data.splines.clears for obj in trail.objects.values()]
    tick(.05, frame=scene.frame_current)
    assert [obj.data.splines.clears for obj in trail.objects.values()] == [n + 1 for n in clears]
    assert all(len(points) == 1 for points in trail.points.values())
    tick(.1)
    assert all(len(obj.data.splines) == 1 for obj in trail.objects.values())


def test_disable_enable_clears_draw_bookkeeping_and_restores_visibility(trails):
    _scene, trail, tick = trails()
    capture_pair(tick)
    trail.disable()
    assert all(not obj.data.splines and obj.hidden and obj.hide_render for obj in trail.objects.values())
    before = work(trail)
    tick(8.1)
    assert work(trail) == before
    trail.enable()
    tick(8.1)
    tick(8.15)
    assert all(not obj.hidden and not obj.hide_render for obj in trail.objects.values())
    assert all(len(obj.data.splines) == 1 for obj in trail.objects.values())


def test_unchanged_sample_in_same_bucket_skips_geometry_write(trails):
    _scene, trail, tick = trails()
    tick(0., point=(1., 2., 3.))
    tick(.025, point=(1., 2., 3.))
    before = work(trail)
    tick(.026, point=(1., 2., 3.))
    assert work(trail) == before
    tick(.027, point=(1., 2., 4.))
    assert all(after[1] == prior[1] + 1 for after, prior in zip(work(trail), before))
    for obj in trail.objects.values():
        np.testing.assert_array_equal(obj.data.splines[0].points.coordinates,
                                      [[1., 2., 3., 1.], [1., 2., 4., 1.]])


def test_paused_update_and_enable_preserve_capture(trails):
    _scene, trail, tick = trails()
    capture_pair(tick)
    before = geometry(trail), work(trail)
    trail.enable()
    tick(8.)
    assert (geometry(trail), work(trail)) == before


@pytest.mark.parametrize('seek', ['backward', 'forward'])
def test_timeline_seek_clears_comparison(trails, seek):
    scene, trail, tick = trails()
    capture_pair(tick)
    tick(2. if seek == 'backward' else 12., frame=scene.frame_current + 4)
    assert scene['wfrl_tip_trail_clear_reason'] == 'timeline_seek'
    assert all(not lines for lines in trail.completed.values())
    assert all(len(points) == 1 for points in trail.points.values())
    assert all(not obj.data.splines for obj in trail.objects.values())


@pytest.mark.parametrize('point', [None, (float('nan'), 0., 0.)])
def test_unavailable_tip_clears_all_blades(trails, point):
    scene, trail, tick = trails()
    tick(0.)
    tick(.05)
    scene.objects['WFRL.Turbine.T1.Blade2'].tip = point
    scene.time, scene.frame_current = .1, scene.frame_current + 1
    trail.update(scene)
    assert scene['wfrl_tip_trail_clear_reason'] == 'tip_unavailable'
    assert all(not obj.data.splines for obj in trail.objects.values())
    assert all(not points for points in trail.points.values())
    tick(.15)
    tick(.2)
    assert all(obj.data.splines for obj in trail.objects.values())


def test_slow_revolution_compaction_retains_start_boundary_and_point_limit(trails):
    _scene, trail, tick = trails(max_points=8)
    for i in range(131):
        tick(i * .05, i * math.tau / 130)
        assert all(len(points) <= trail.max_points for points in trail.points.values())
    assert all(len(lines) == 1 for lines in trail.completed.values())
    for bid, lines in trail.completed.items():
        assert lines[0][0][0] == 0.
        assert len(lines[0]) <= trail.max_points + 1
        assert tuple(trail.points[bid][0]) == lines[0][-1]
        coordinates = trail.objects[bid].data.splines[0].points.coordinates
        np.testing.assert_array_equal(coordinates[:, :3], np.asarray(lines[0], dtype=np.float32))
        np.testing.assert_array_equal(coordinates[:, 3], 1.)
