"""X100 replay controls and 3D overlay redraw scheduling."""
import sys
from types import SimpleNamespace

import pytest
from wfrl_blender.radar_x100 import runtime


class Scene(dict):
    wfrl_x100_fov = True
    def as_pointer(self): return id(self)


def _failing_replay_session():
    session = runtime.Session.__new__(runtime.Session)
    session.playing, session.mode, session.replay = True, 'REPLAY', object()
    def update():
        raise ValueError('回放路径已改变，请应用 X100 配置')
    session.update = update
    return session


def test_pause_stops_playback_even_when_position_update_fails():
    session = _failing_replay_session()
    with pytest.raises(ValueError):
        session.toggle_playback()
    assert session.playing is False


def test_replay_operators_report_unapplied_edits_instead_of_raising(frontend_module, monkeypatch):
    panels = frontend_module('wfrl_blender.panels.radar_x100')
    session = _failing_replay_session()
    session.replay = SimpleNamespace(rows=[0, 1, 2])
    session.selected_replay_index = lambda: 0
    session.seek_frame = lambda index: session.update()
    monkeypatch.setattr(frontend_module('wfrl_blender.radar_x100.runtime'), 'session_for', lambda scene: session)
    for operator in (panels.WFRL_OT_X100Play(), panels.WFRL_OT_X100Step()):
        scene, reports = Scene(), []
        operator.direction = 1
        operator.report = lambda kind, message: reports.append((kind, message))
        assert operator.execute(SimpleNamespace(scene=scene)) == {'CANCELLED'}
        assert reports == [({'ERROR'}, '回放路径已改变，请应用 X100 配置')]
        assert scene['wfrl_x100_error'] == '回放路径已改变，请应用 X100 配置'
        assert session.playing is False


def test_3d_views_redraw_only_when_overlay_inputs_change(monkeypatch):
    scene, other = Scene(), Scene()
    draws, other_draws = [], []
    windows = [SimpleNamespace(scene=scene, screen=SimpleNamespace(areas=[
                   SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: draws.append(1)),
                   SimpleNamespace(type='IMAGE_EDITOR', tag_redraw=lambda: draws.append('image'))])),
               SimpleNamespace(scene=other, screen=SimpleNamespace(areas=[
                   SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: other_draws.append(1))]))]
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(context=SimpleNamespace(
        window_manager=SimpleNamespace(windows=windows))))
    session = SimpleNamespace(frame=object(), gpu_batch=object(), display_valid=True, closing=False)
    monkeypatch.setattr(runtime, '_SESSIONS', {scene.as_pointer(): session})
    monkeypatch.setattr(runtime, '_DRAWN', {})
    alive = {scene.as_pointer(): scene, other.as_pointer(): other}

    def tick():
        before = len(draws)
        runtime._redraw_changed_overlays(alive)
        return len(draws) - before

    assert tick() == 1                     # first overlay appears
    assert tick() == 0                     # paused: nothing changed
    session.frame = object()
    assert tick() == 1                     # new frame
    session.gpu_batch = None
    assert tick() == 1                     # display resources dropped
    scene.wfrl_x100_fov = False
    assert tick() == 1                     # FOV overlay switched off
    session.closing = True
    assert tick() == 1                     # overlay removed once on close
    assert tick() == 0
    assert other_draws == [] and 'image' not in draws
