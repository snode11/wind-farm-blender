"""Run native camera lifecycle code against deleted and failing RNA adapters.

The adapter invalidates object access after deletion, including cached proxies.
These checks exercise release/restoration behavior without a Blender window.
"""
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest
from wfrl_blender import playback


class Scene(dict):
    frame_current = 31

    def __init__(self, name='A'):
        super().__init__(name=name)
        self.render = NS(preview_pixel_size='1', resolution_x=1920, resolution_y=1080,
                         pixel_aspect_x=1., pixel_aspect_y=1.)
        self.eevee = NS(taa_samples=64, use_shadows=True, use_fast_gi=True)

    def as_pointer(self):
        return id(self)


class CameraData:
    def __init__(self):
        self.removed = False


class Proxy:
    def __init__(self, data):
        self._data = data
        self.removed = False

    @property
    def data(self):
        if self.removed:
            raise ReferenceError('StructRNA of type Object has been removed')
        return None if self._data.removed else self._data

    @property
    def type(self):
        return 'EMPTY' if self._data.removed else 'CAMERA'


class Collection:
    def __init__(self):
        self.removed = []
        self.fail_once = set()

    def remove(self, resource, **_kwargs):
        if resource in self.fail_once:
            self.fail_once.remove(resource)
            raise RuntimeError('injected release failure')
        if resource.removed:
            raise ReferenceError('StructRNA has been removed')
        resource.removed = True
        self.removed.append(resource)


def area(pointer, shading='SOLID'):
    shader = NS(type=shading, use_scene_lights=True, use_scene_world=True,
                studio_light='forest.exr', studiolight_rotate_z=0.,
                studiolight_intensity=1., studiolight_background_alpha=1.,
                studiolight_background_blur=0., use_studiolight_view_rotation=True)
    return NS(type='VIEW_3D', x=0, y=0, width=1000, height=700,
              spaces=NS(active=NS(shading=shader, camera=None,
                                                overlay=NS(show_overlays=True))),
              as_pointer=lambda: pointer, tag_redraw=lambda: None)


@pytest.fixture
def runtime(monkeypatch):
    scene = Scene()
    source_area, camera_area = area(11), area(21)
    source = NS(as_pointer=lambda: 10, scene=scene,
                screen=NS(areas=[source_area], is_animation_playing=False))
    view = NS(as_pointer=lambda: 20, scene=scene,
              screen=NS(areas=[camera_area], is_animation_playing=False), workspace=NS())
    windows, closed_windows, removed_handlers, timers = [source, view], [], [], []
    context = NS(window=source, scene=scene, workspace=NS(), space_data=source_area.spaces.active,
                 window_manager=NS(windows=windows), screen=source.screen)

    @contextmanager
    def override(**values):
        previous = {key: getattr(context, key, None) for key in values}
        for key, value in values.items():
            setattr(context, key, value)
        try:
            yield
        finally:
            for key, value in previous.items():
                setattr(context, key, value)

    context.temp_override = override

    def window_close():
        closed_windows.append(context.window)
        windows.remove(context.window)
        return {'FINISHED'}

    def draw_remove(handle, _region):
        removed_handlers.append(handle)

    bpy = NS(context=context, data=NS(objects=Collection(), cameras=Collection()),
             app=NS(background=False, timers=NS(register=lambda fn, **_kw: timers.append(fn),
                              is_registered=lambda fn: fn in timers)),
             types=NS(Operator=object,
                      SpaceView3D=NS(draw_handler_remove=draw_remove, draw_handler_add=lambda *_args: 103),
                      SpaceImageEditor=NS(draw_handler_remove=draw_remove)),
             props=NS(EnumProperty=lambda **_kwargs: None, IntProperty=lambda **_kwargs: None),
             ops=NS(wm=NS(window_close=window_close)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(playback, '_OWNER_SCENE', None)
    monkeypatch.setattr(playback, '_OWNER_WINDOW', None)
    monkeypatch.setitem(sys.modules, 'bpy.app.handlers', NS(persistent=lambda fn: fn))
    monkeypatch.setitem(sys.modules, 'wfrl_blender.custom_camera_capture',
                        NS(_ACTIVE=None, active=lambda: False))
    # Load a private module name so its bpy adapter cannot leak into other tests.
    path = Path(__file__).parents[1] / 'wfrl_blender/native_camera_views.py'
    spec = spec_from_file_location('wfrl_blender._host_native_camera_lifecycle', path)
    native = module_from_spec(spec)
    spec.loader.exec_module(native)
    session = native.NativeSession(context, [1], 'STRIP')
    session.window, session.window_id, session.workspace = view, 20, view.workspace
    session.owns_window = True
    session.preview_quality = session.quality_settings()
    session.set_quality(('2', 8, False, False))
    session.source_shading = [(10, 11, 'MATERIAL')]
    session.handle, session.diagram_handle = 101, 102
    data = CameraData()
    proxy = Proxy(data)
    session.entries = [{'area': camera_area, 'slot': 1, 'proxy': proxy, 'proxy_data': data}]
    native._ACTIVE = session
    return NS(native=native, session=session, bpy=bpy, scene=scene, proxy=proxy, data=data,
              source=source, view=view, source_area=source_area, camera_area=camera_area,
              windows=windows, closed_windows=closed_windows, removed_handlers=removed_handlers,
              timers=timers, monkeypatch=monkeypatch)


def assert_restored(runtime):
    assert runtime.session.quality_settings() == ('1', 64, True, True)
    assert runtime.source_area.spaces.active.shading.type == 'MATERIAL'
    assert runtime.view not in runtime.windows


@pytest.mark.parametrize('deleted_proxy', [False, True])
@pytest.mark.parametrize('os_closed_window', [False, True])
def test_repeated_close_restores_and_releases_deleted_resources(runtime, deleted_proxy, os_closed_window):
    if deleted_proxy:
        runtime.bpy.data.objects.remove(runtime.proxy)
    if os_closed_window:
        runtime.windows.remove(runtime.view)
        # Accessing the cached pointer would now reproduce Blender's dead RNA.
        def invalid_pointer():
            raise ReferenceError('Window has been removed')
        runtime.view.as_pointer = invalid_pointer
    assert runtime.session.close()
    assert runtime.session.close()
    assert_restored(runtime)
    assert runtime.bpy.data.objects.removed == [runtime.proxy]
    assert runtime.bpy.data.cameras.removed == [runtime.data]
    assert runtime.removed_handlers == [101, 102]
    assert runtime.closed_windows == ([] if os_closed_window else [runtime.view])
    assert not runtime.session.entries
    assert not runtime.native.last_error(runtime.scene)


@pytest.mark.parametrize('failing_resource', ['objects', 'cameras'])
def test_release_failure_keeps_only_pending_resource_for_shutdown_retry(runtime, failing_resource):
    other_data = CameraData()
    other_proxy = Proxy(other_data)
    runtime.session.entries.append({'area': runtime.camera_area, 'slot': 2,
                                    'proxy': other_proxy, 'proxy_data': other_data})
    collection = getattr(runtime.bpy.data, failing_resource)
    collection.fail_once.add(runtime.proxy if failing_resource == 'objects' else runtime.data)
    runtime.native.shutdown()
    assert_restored(runtime)
    assert runtime.native._ACTIVE is runtime.session
    assert runtime.session.closing
    assert other_proxy.removed and other_data.removed
    assert len(runtime.session.entries) == 1
    error = runtime.native.last_error(runtime.scene)
    assert 'injected release failure' in error
    runtime.native.shutdown()
    runtime.native.shutdown()
    assert runtime.native._ACTIVE is None
    assert runtime.proxy.removed and runtime.data.removed
    assert runtime.removed_handlers == [101, 102]
    assert runtime.closed_windows == [runtime.view]
    assert runtime.native.last_error(runtime.scene) == error


def test_unexpected_clear_exception_cannot_skip_shading_or_window_and_can_retry(runtime):
    original_clear = runtime.session.clear_views
    attempts = []

    def fail_once():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError('injected clear failure')
        return original_clear()

    runtime.monkeypatch.setattr(runtime.session, 'clear_views', fail_once)
    runtime.native.shutdown()
    assert_restored(runtime)
    assert runtime.native._ACTIVE is runtime.session
    assert 'injected clear failure' in runtime.native.last_error(runtime.scene)
    runtime.native.shutdown()
    assert runtime.native._ACTIVE is None
    assert runtime.proxy.removed and runtime.data.removed


def test_quality_failure_does_not_skip_source_shading_or_window_and_retains_snapshot(runtime):
    original_set = runtime.session.set_quality
    attempts = []

    def fail_once(settings):
        attempts.append(settings)
        if len(attempts) == 1:
            raise RuntimeError('injected quality failure')
        original_set(settings)

    runtime.monkeypatch.setattr(runtime.session, 'set_quality', fail_once)
    runtime.native.shutdown()
    assert runtime.source_area.spaces.active.shading.type == 'MATERIAL'
    assert runtime.view not in runtime.windows
    assert runtime.session.preview_quality == ('1', 64, True, True)
    assert runtime.native._ACTIVE is runtime.session
    runtime.native.shutdown()
    assert_restored(runtime)
    assert runtime.native._ACTIVE is None
    assert 'injected quality failure' in runtime.native.last_error(runtime.scene)


def test_source_shading_failure_does_not_skip_window_and_can_retry(runtime):
    class Shading:
        current = 'SOLID'
        fail = True

        @property
        def type(self):
            return self.current

        @type.setter
        def type(self, value):
            if self.fail:
                self.fail = False
                raise RuntimeError('injected source shading failure')
            self.current = value

    runtime.source_area.spaces.active.shading = Shading()
    runtime.native.shutdown()
    assert runtime.session.quality_settings() == ('1', 64, True, True)
    assert runtime.view not in runtime.windows
    assert runtime.session.source_shading == [(10, 11, 'MATERIAL')]
    assert runtime.native._ACTIVE is runtime.session
    runtime.native.shutdown()
    assert_restored(runtime)
    assert runtime.native._ACTIVE is None
    assert 'injected source shading failure' in runtime.native.last_error(runtime.scene)


@pytest.mark.parametrize('failure', ['draw_handler', 'window'])
def test_callback_and_window_release_failures_retain_ownership_until_retried(runtime, failure):
    attempts = []
    if failure == 'draw_handler':
        original = runtime.bpy.types.SpaceView3D.draw_handler_remove

        def fail_once(*args):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError('injected callback failure')
            return original(*args)
        runtime.monkeypatch.setattr(runtime.bpy.types.SpaceView3D, 'draw_handler_remove', fail_once)
    else:
        original = runtime.bpy.ops.wm.window_close

        def fail_once():
            attempts.append(True)
            return {'CANCELLED'} if len(attempts) == 1 else original()
        runtime.monkeypatch.setattr(runtime.bpy.ops.wm, 'window_close', fail_once)
    runtime.native.shutdown()
    assert runtime.session.quality_settings() == ('1', 64, True, True)
    assert runtime.source_area.spaces.active.shading.type == 'MATERIAL'
    assert runtime.native._ACTIVE is runtime.session
    runtime.native.shutdown()
    assert_restored(runtime)
    assert runtime.native._ACTIVE is None
    assert sorted(runtime.removed_handlers) == [101, 102]
    assert runtime.closed_windows == [runtime.view]


def test_repeated_close_transfers_live_playback_only_once(runtime):
    playback_calls = []
    runtime.view.screen.is_animation_playing = True
    runtime.source.screen.is_animation_playing = True
    playback.animation_started(runtime.scene)

    def cancel(**_kwargs):
        playback_calls.append('cancel')
        runtime.source.screen.is_animation_playing = False
        runtime.view.screen.is_animation_playing = False
        playback.animation_stopped(runtime.scene)
        return {'FINISHED'}

    def play():
        playback_calls.append('play')
        runtime.source.screen.is_animation_playing = True
        playback.animation_started(runtime.scene)
        return {'FINISHED'}
    runtime.bpy.ops.screen = NS(animation_cancel=cancel, animation_play=play)
    runtime.monkeypatch.setattr(runtime.session, 'playback_context', lambda: {'window': runtime.source})
    runtime.native.shutdown()
    runtime.native.shutdown()
    assert runtime.session.close()
    assert runtime.timers == [runtime.session.resume_after_close]
    runtime.timers[0]()
    assert playback_calls == ['cancel', 'play']
    assert not runtime.native.last_error(runtime.scene)


def test_closing_scene_a_camera_cannot_cancel_scene_b_global_player(runtime):
    scene_b = Scene('B')
    window_b = NS(as_pointer=lambda: 30, scene=scene_b,
                  screen=NS(areas=[area(31)], is_animation_playing=True))
    runtime.windows.append(window_b)
    runtime.bpy.context.window = window_b
    playback.animation_started(scene_b)
    runtime.bpy.context.window = runtime.source
    runtime.source.screen.is_animation_playing = True
    runtime.view.screen.is_animation_playing = True
    calls = []
    runtime.bpy.ops.screen = NS(animation_cancel=lambda **_kwargs: calls.append('cancel'),
                                animation_play=lambda: calls.append('play'))
    assert playback.is_playing(scene_b) and not playback.is_playing(runtime.scene)
    runtime.native.shutdown()
    assert_restored(runtime)
    assert runtime.native._ACTIVE is None
    assert calls == []
    assert runtime.timers == []
    assert playback.is_playing(scene_b)
    assert not runtime.native.last_error(runtime.scene)


def test_delayed_scene_a_resume_cannot_take_over_new_scene_b_player(runtime):
    scene_b = Scene('B')
    window_b = NS(as_pointer=lambda: 30, scene=scene_b,
                  screen=NS(areas=[area(31)], is_animation_playing=False))
    runtime.windows.append(window_b)
    runtime.source.screen.is_animation_playing = True
    runtime.view.screen.is_animation_playing = True
    playback.animation_started(runtime.scene)
    calls = []

    def cancel(**_kwargs):
        calls.append(('cancel', playback.owner_scene()))
        playback.animation_stopped(runtime.scene)
        for window in runtime.windows:
            window.screen.is_animation_playing = False
        return {'FINISHED'}

    def play():
        calls.append(('play', runtime.bpy.context.window.scene))
        return {'FINISHED'}
    runtime.bpy.ops.screen = NS(animation_cancel=cancel, animation_play=play)
    runtime.monkeypatch.setattr(runtime.session, 'playback_context', lambda: {'window': runtime.source})
    runtime.native.shutdown()
    assert calls == [('cancel', runtime.scene)]
    assert runtime.timers == [runtime.session.resume_after_close]
    runtime.bpy.context.window = window_b
    playback.animation_started(scene_b)
    for window in runtime.windows:
        window.screen.is_animation_playing = True
    runtime.bpy.context.window = runtime.source
    runtime.timers[0]()
    assert calls == [('cancel', runtime.scene)]
    assert playback.is_playing(scene_b)
    assert runtime.session.lifecycle[-1]['event'] == 'resume_skipped'
    assert not runtime.native.last_error(runtime.scene)


def test_watch_deleted_proxy_preserves_error_until_actual_prepare_succeeds(runtime):
    runtime.bpy.data.objects.remove(runtime.proxy)
    assert runtime.native.watch() == .25
    assert_restored(runtime)
    assert runtime.native._ACTIVE is None
    assert runtime.data.removed
    error = runtime.native.last_error(runtime.scene)
    assert 'Object has been removed' in error
    runtime.native.shutdown()
    assert runtime.native.last_error(runtime.scene) == error
    runtime.windows.append(runtime.view)
    reopened = runtime.native.NativeSession(runtime.bpy.context, [1], 'STRIP')
    reopened.window, reopened.window_id, reopened.workspace = runtime.view, 20, runtime.view.workspace
    runtime.native._ACTIVE = reopened
    runtime.monkeypatch.setattr(reopened, 'apply_quality', lambda: None)
    runtime.monkeypatch.setattr(reopened, 'sync', lambda: None)
    assert reopened._prepare() is None
    assert not reopened.preparing
    assert len(reopened.entries) == 1
    assert reopened.handle == 103
    assert not runtime.native.last_error(runtime.scene)


def test_prepare_failure_records_reason_and_restores(runtime):
    def failure():
        raise ValueError('injected setup failure')
    runtime.monkeypatch.setattr(runtime.session, '_prepare', failure)
    assert runtime.session.prepare() is None
    assert_restored(runtime)
    assert runtime.native._ACTIVE is None
    assert '相机观察准备失败：injected setup failure' == runtime.native.last_error(runtime.scene)


def test_watch_deleted_camera_datablock_closes_empty_proxy_and_restores(runtime):
    runtime.bpy.data.cameras.remove(runtime.data)
    assert runtime.proxy.data is None and runtime.proxy.type == 'EMPTY'
    assert runtime.native.watch() == .25
    assert_restored(runtime)
    assert runtime.native._ACTIVE is None
    assert runtime.proxy.removed
    assert runtime.bpy.data.cameras.removed == [runtime.data]
    assert '观察相机或镜头已被删除' in runtime.native.last_error(runtime.scene)
