"""Scene ownership and window targeting regressions without Blender UI."""
from contextlib import contextmanager
from types import SimpleNamespace
import sys

import pytest
import wfrl_blender
from wfrl_blender import clearance_replay, farm_flex, tip_tracking, playback


class Scene(dict):
    def __init__(self, name):
        super().__init__(name=name, wfrl_scene_kind='clearance_replay')
        self.name = name
        self.objects = {f'WFRL.Turbine.T1.{suffix}': object() for suffix in
                        ('YawRoot', 'Rotor', 'Tower', 'Blade1', 'Blade2', 'Blade3')}
        self.frames = []

    def as_pointer(self):
        return id(self)

    def frame_set(self, frame):
        self.frames.append(frame)
        farm_flex.update(self)


@pytest.fixture
def runtimes(monkeypatch):
    handlers = SimpleNamespace(frame_change_post=[], load_pre=[])
    bpy = SimpleNamespace(app=SimpleNamespace(handlers=handlers))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'bpy.app.handlers', SimpleNamespace(persistent=lambda fn: fn))
    monkeypatch.setitem(sys.modules, 'wfrl_blender.nrel_defects.editor',
                        SimpleNamespace(detach=lambda *a, **k: None,
                                        restore_saved=lambda *a: None,
                                        frame_changed=lambda *a: None))
    monkeypatch.setitem(sys.modules, 'wfrl_blender.panels.custom_cameras',
                        SimpleNamespace(_ACTIVE=None, cancel_on_load=lambda: None))
    monkeypatch.setattr(farm_flex, '_ACTIVES', {})
    monkeypatch.setattr(farm_flex, '_ACTIVE', None)
    monkeypatch.setattr(clearance_replay, '_READERS', {})
    monkeypatch.setattr(farm_flex, 'read_package', lambda path: ({'turbine_ids': ['T1']},))
    monkeypatch.setattr(tip_tracking, 'register', lambda: None)

    def clear(scene, reason):
        clearance_replay._READERS.pop(scene.as_pointer(), None)
        scene['wfrl_clearance_status'] = reason
    monkeypatch.setattr(clearance_replay, 'clear', clear)

    class Runtime:
        def __init__(self, scene, path):
            self.scene, self.enabled = scene, True
            self.readers = {'T1': object()}
            self.blades = [(scene.objects['WFRL.Turbine.T1.Blade1'],)]
            self.cache, self.comparison, self.updates = {}, None, []
            self.restored = False
            clearance_replay._READERS[scene.as_pointer()] = self.readers['T1']

        def update(self, scene):
            self.updates.append(scene)

        def restore_support(self):
            self.restored = True
    monkeypatch.setattr(farm_flex, 'FarmFlex', Runtime)
    return bpy, Runtime


def test_multiple_independent_scenes_keep_own_runtime_and_handler(runtimes):
    bpy, _ = runtimes
    a, b = Scene('A'), Scene('B')
    runtime_a = farm_flex.attach(a, 'package')
    runtime_b = farm_flex.attach(b, 'package')
    assert farm_flex.active_for(a) is runtime_a
    assert farm_flex.active_for(b) is runtime_b
    assert farm_flex.is_active(a) and farm_flex._ACTIVE is runtime_a
    assert farm_flex.is_active(b) and farm_flex._ACTIVE is runtime_b
    a.frame_set(121)
    b.frame_set(241)
    assert runtime_a.updates == [a, a]
    assert runtime_b.updates == [b, b]
    assert bpy.app.handlers.frame_change_post.count(farm_flex.update) == 1
    farm_flex.detach(b, restore=False)
    assert not runtime_b.enabled and runtime_a.enabled
    assert farm_flex.is_active(a)
    assert farm_flex.update in bpy.app.handlers.frame_change_post
    farm_flex.detach(restore=False)
    assert farm_flex._ACTIVE is None and not farm_flex._ACTIVES
    assert farm_flex.update not in bpy.app.handlers.frame_change_post
    assert farm_flex.on_load_pre not in bpy.app.handlers.load_pre


def test_failed_copied_scene_cannot_detach_ready_scene(runtimes):
    bpy, _ = runtimes
    a, copied = Scene('A'), Scene('FULL_COPY')
    runtime_a = farm_flex.attach(a, 'package')
    copied.objects = {}
    copied['wfrl_flex_active'] = True
    with pytest.raises(ValueError, match='lacks required'):
        farm_flex.attach(copied, 'package')
    assert farm_flex.active_for(a) is runtime_a and runtime_a.enabled
    assert farm_flex.is_active(a)
    assert not copied['wfrl_flex_active']
    assert clearance_replay.reader_for(copied) is None
    assert farm_flex.update in bpy.app.handlers.frame_change_post
    a.frame_set(181)
    assert runtime_a.updates[-1] is a


def test_linked_scene_cannot_drive_same_objects_with_second_clock(runtimes):
    a, linked = Scene('A'), Scene('LINK_COPY')
    runtime_a = farm_flex.attach(a, 'package')
    linked.objects = a.objects
    with pytest.raises(ValueError, match='already driven by scene: A'):
        farm_flex.attach(linked, 'package')
    assert farm_flex.active_for(a) is runtime_a and farm_flex.is_active(a)
    assert not farm_flex.is_active(linked)


@pytest.fixture
def native_player(monkeypatch):
    a, b = Scene('A'), Scene('B')
    native = SimpleNamespace(playing=False, scene=None)
    class Screen:
        @property
        def is_animation_playing(self):
            # Blender RNA ignores the screen pointer and returns global state.
            return native.playing
    windows = [SimpleNamespace(scene=scene, screen=Screen())
               for scene in (a, b, a)]
    overridden = []

    @contextmanager
    def override(window, screen):
        overridden.append(window)
        yield

    def cancel(**kwargs):
        assert kwargs == {'restore_frame': False}
        native.playing = False
        playback.animation_stopped(native.scene)
        native.scene = None

    handlers = SimpleNamespace(animation_playback_pre=[], animation_playback_post=[], load_pre=[])
    bpy = SimpleNamespace(app=SimpleNamespace(background=False, handlers=handlers),
        context=SimpleNamespace(scene=a, window=windows[0], window_manager=SimpleNamespace(windows=windows),
                                temp_override=override),
        ops=SimpleNamespace(screen=SimpleNamespace(animation_cancel=cancel)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'bpy.app.handlers', SimpleNamespace(persistent=lambda fn: fn))
    monkeypatch.setattr(playback, '_OWNER_SCENE', None)
    monkeypatch.setattr(playback, '_OWNER_WINDOW', None)
    def start(window):
        bpy.context.window = window
        native.scene = window.scene
        playback.animation_started(window.scene)
        native.playing = True
    return bpy, native, windows, overridden, start


def test_pause_cancels_single_native_owner_and_its_same_scene_views(native_player):
    bpy, native, windows, overridden, start = native_player
    start(windows[0])
    assert all(window.screen.is_animation_playing for window in windows)
    assert playback.is_playing(windows[0].scene)
    assert not playback.is_playing(windows[1].scene)
    wfrl_blender._cancel_playback()
    assert not native.playing and playback.owner_scene() is None
    assert overridden == [windows[0]]


def test_global_screen_flag_cannot_authorize_stopping_unrelated_owner(native_player):
    bpy, native, windows, overridden, start = native_player
    start(windows[1])
    bpy.context.scene, bpy.context.window = windows[0].scene, windows[0]
    assert all(window.screen.is_animation_playing for window in windows)
    wfrl_blender._cancel_playback()
    assert native.playing and playback.is_playing(windows[1].scene)
    assert overridden == []
    wfrl_blender._cancel_playback(windows[1].scene)
    assert not native.playing and overridden == [windows[1]]


def test_unknown_owner_is_protected_but_explicit_global_cleanup_cancels_once(native_player):
    bpy, native, windows, overridden, start = native_player
    native.playing = True
    assert playback.owner_scene() is None
    wfrl_blender._cancel_playback(windows[0].scene)
    assert native.playing and overridden == []
    wfrl_blender._cancel_playback(all_scenes=True)
    assert not native.playing and overridden == [windows[0]]


def test_playback_handlers_register_once_and_clear_owner_on_load_and_unload(native_player):
    bpy, native, windows, overridden, start = native_player
    playback.register()
    playback.register()
    callbacks = ((bpy.app.handlers.animation_playback_pre, playback.animation_started),
                 (bpy.app.handlers.animation_playback_post, playback.animation_stopped),
                 (bpy.app.handlers.load_pre, playback.on_load_pre))
    assert all(handlers.count(callback) == 1 for handlers, callback in callbacks)
    start(windows[1])
    playback.on_load_pre()
    assert playback.owner_scene() is None
    assert not playback.is_playing(windows[1].scene)
    start(windows[0])
    playback.animation_stopped(windows[1].scene)
    assert playback.owner_scene() is windows[0].scene
    playback.unregister()
    assert playback.owner_scene() is None
    assert all(callback not in handlers for handlers, callback in callbacks)


def test_queued_end_callback_cannot_stop_another_scene(native_player, monkeypatch):
    bpy, native, windows, overridden, start = native_player
    a, b = windows[0].scene, windows[1].scene
    a.frame_current = a.frame_end = 121
    b.frame_current, b.frame_end = 10, 200
    monkeypatch.setattr(clearance_replay, '_READERS', {a.as_pointer(): object()})
    start(windows[1])
    clearance_replay._stop_finished_playback()
    assert native.playing and playback.is_playing(b) and overridden == []
    monkeypatch.setattr(clearance_replay, '_READERS', {a.as_pointer(): object(), b.as_pointer(): object()})
    b.frame_current = b.frame_end
    clearance_replay._stop_finished_playback()
    assert not native.playing and overridden == [windows[1]]


def test_pause_background_never_calls_window_operator(monkeypatch):
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(app=SimpleNamespace(background=True)))
    wfrl_blender._cancel_playback()
