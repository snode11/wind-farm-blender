"""Camera controls respect Blender's single player and its scene owner."""
from types import SimpleNamespace as NS
import sys

import pytest

from wfrl_blender import playback
from test_native_camera_lifecycle import Scene, area, runtime


@pytest.fixture
def controls(runtime):
    context = runtime.bpy.context
    scene_b = Scene('B')
    scene_b.name = 'B'
    runtime.scene.name = 'A'
    window_b = NS(as_pointer=lambda: 30, scene=scene_b,
                  screen=NS(areas=[area(31)], is_animation_playing=False))
    runtime.windows.append(window_b)
    runtime.source_area.regions = [NS(type='WINDOW')]
    context.window, context.screen = runtime.view, runtime.view.screen
    context.area = runtime.camera_area
    calls, reports = [], []

    def set_player(owner, live=True):
        playback.on_load_pre()
        if owner is not None:
            with context.temp_override(window=runtime.source if owner is runtime.scene else window_b):
                playback.animation_started(owner)
        for window in runtime.windows:
            window.screen.is_animation_playing = live

    def cancel(**_kwargs):
        calls.append(('cancel', context.window.scene, playback.owner_scene()))
        playback.animation_stopped(playback.owner_scene())
        for window in runtime.windows:
            window.screen.is_animation_playing = False
        return {'PASS_THROUGH'}

    def play():
        calls.append(('play', context.window.scene, playback.owner_scene()))
        playback.animation_started(context.window.scene)
        for window in runtime.windows:
            window.screen.is_animation_playing = True
        return {'FINISHED'}

    runtime.bpy.ops.screen = NS(animation_cancel=cancel, animation_play=play)
    operator = NS(report=lambda levels, message: reports.append((levels, message)))
    return NS(runtime=runtime, context=context, scene_b=scene_b, calls=calls,
              reports=reports, operator=operator, set_player=set_player)


@pytest.mark.parametrize('owner,live', [('B', True), ('unknown', True), ('B', False)])
def test_direct_camera_play_cannot_stop_or_replace_foreign_or_unknown_player(controls, owner, live):
    c, r = controls, controls.runtime
    owner_scene = c.scene_b if owner == 'B' else None
    c.set_player(owner_scene, live)
    before = [(window.scene.frame_current, window.screen.is_animation_playing) for window in r.windows]
    assert r.native.WFRL_OT_NativePlay.execute(c.operator, c.context) == {'CANCELLED'}
    assert c.calls == []
    assert playback.owner_scene() is owner_scene
    assert [(window.scene.frame_current, window.screen.is_animation_playing) for window in r.windows] == before
    assert c.reports and c.reports[-1][0] == {'WARNING'}
    assert ('B' in c.reports[-1][1]) if owner == 'B' else ('归属' in c.reports[-1][1])


class HeaderLayout:
    def __init__(self, calls, enabled=True):
        self.calls, self.enabled = calls, enabled

    def row(self, **_kwargs):
        return HeaderLayout(self.calls, self.enabled)

    def operator(self, identifier, **kwargs):
        button = NS(identifier=identifier, enabled=self.enabled, **kwargs)
        self.calls.append(button)
        return button


@pytest.mark.parametrize('owner,live,enabled,playing', [
    ('idle', False, True, False), ('A', True, True, True),
    ('B', True, False, False), ('unknown', True, False, False), ('B', False, False, False),
])
def test_header_uses_owner_without_disabling_space_dispatch(controls, owner, live, enabled, playing):
    c, r = controls, controls.runtime
    owner_scene = r.scene if owner == 'A' else c.scene_b if owner == 'B' else None
    c.set_player(owner_scene, live)
    assert r.native.WFRL_OT_NativePlay.poll(c.context), 'Space must not fall through to the global native player'
    buttons = []
    r.native.header(NS(layout=HeaderLayout(buttons)), c.context)
    button = next(button for button in buttons if button.identifier == 'wfrl.native_camera_play')
    step = next(button for button in buttons if button.identifier == 'wfrl.farm_transport')
    assert button.enabled is enabled and step.enabled is enabled
    assert button.text == ('播放器占用中' if not enabled else '暂停' if playing else '播放')
    assert button.icon == ('PAUSE' if playing else 'PLAY')
    assert step.action == 'STEP'


def test_camera_play_pauses_own_player(controls):
    c, r = controls, controls.runtime
    c.set_player(r.scene)
    assert r.native.WFRL_OT_NativePlay.execute(c.operator, c.context) == {'FINISHED'}
    assert c.calls == [('cancel', r.scene, r.scene)]
    assert not playback.any_playing() and playback.owner_scene() is None
    assert not c.reports


def test_camera_play_starts_from_surviving_source_window(controls):
    c, r = controls, controls.runtime
    c.set_player(None, False)
    assert r.native.WFRL_OT_NativePlay.execute(c.operator, c.context) == {'FINISHED'}
    assert c.calls == [('play', r.scene, None)]
    assert playback.is_playing(r.scene) and playback.owner_window() is r.source
    assert not c.reports


def test_camera_replay_uses_shared_restart_control_at_terminal(controls, monkeypatch):
    from wfrl_blender import clearance_replay
    c, r = controls, controls.runtime
    c.set_player(None, False)
    r.scene.frame_current = r.scene.frame_end = 121
    monkeypatch.setattr(clearance_replay, 'reader_for', lambda scene: object() if scene is r.scene else None)
    r.bpy.ops.wfrl = NS(clearance_playback=lambda: c.calls.append(('replay', c.context.window)) or {'FINISHED'})
    assert r.native.WFRL_OT_NativePlay.execute(c.operator, c.context) == {'FINISHED'}
    assert c.calls == [('replay', r.source)]


def test_delayed_close_resume_cannot_restart_after_a_new_player_was_paused(controls):
    c, r = controls, controls.runtime
    c.set_player(r.scene)
    r.native.shutdown()
    assert r.session.resume_scheduled and not playback.any_playing()
    with c.context.temp_override(window=r.source):
        r.bpy.ops.screen.animation_play()
        r.bpy.ops.screen.animation_cancel(restore_frame=False)
    before = list(c.calls)
    r.session.resume_after_close()
    assert c.calls == before and not playback.any_playing()
    assert r.session.lifecycle[-1]['reason'] == 'playback_state_changed'


def test_execute_rechecks_player_after_context_selection(controls, monkeypatch):
    c, r = controls, controls.runtime
    c.set_player(None, False)
    assert r.native.WFRL_OT_NativePlay.poll(c.context)
    original_context = r.session.playback_context

    def foreign_player_started():
        override = original_context()
        c.set_player(c.scene_b)
        return override

    monkeypatch.setattr(r.session, 'playback_context', foreign_player_started)
    assert r.native.WFRL_OT_NativePlay.execute(c.operator, c.context) == {'CANCELLED'}
    assert c.calls == [] and playback.is_playing(c.scene_b)
    assert c.reports[-1][0] == {'WARNING'}


@pytest.mark.parametrize('owner,live,expected', [
    ('A', True, '播放'), ('B', True, '暂停'),
    ('unknown', True, '暂停'), ('idle', False, '暂停'),
])
def test_camera_hud_reports_only_its_scene_playback(controls, monkeypatch, owner, live, expected):
    from wfrl_blender import custom_camera_preview, performance
    c, r = controls, controls.runtime
    c.set_player(r.scene if owner == 'A' else c.scene_b if owner == 'B' else None, live)
    r.scene['wfrl_clearance_time_s'] = 119.57
    r.session.entries = [dict(area=r.camera_area, slot=1, proxy=None)]
    c.context.workspace = r.view.workspace
    c.context.space_data = r.camera_area.spaces.active
    c.context.space_data.region_3d = NS()
    c.context.region = NS(width=1200, height=700)
    c.context.preferences = NS(system=NS(ui_scale=1.))
    labels = []
    shader = NS(bind=lambda: None, uniform_float=lambda *_args: None)
    monkeypatch.setitem(sys.modules, 'gpu', NS(shader=NS(from_builtin=lambda _name: shader)))
    monkeypatch.setitem(sys.modules, 'gpu_extras.batch', NS(batch_for_shader=lambda *_a, **_k: NS(draw=lambda _shader: None)))
    monkeypatch.setitem(sys.modules, 'blf', NS(size=lambda *_args: None, color=lambda *_args: None,
        position=lambda *_args: None, draw=lambda _font, text: labels.append(text), dimensions=lambda *_args: (100, 20)))
    monkeypatch.setattr(performance, 'count', lambda *_args: None)
    monkeypatch.setattr(custom_camera_preview, 'available_rectangle', lambda *_args: (0, 0, 1200, 700))
    r.session.draw()
    assert labels[-1] == f'共同 119.57 s · {expected}'
