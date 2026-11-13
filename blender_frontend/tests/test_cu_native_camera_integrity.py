"""Native observation rejects invalid inputs and releases only owned resources."""
from dataclasses import replace
import math
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import native_camera_projection as projection, playback
from wfrl_blender.custom_cameras import CameraParameters
from test_native_camera_lifecycle import area, runtime  # noqa: F401


class Resource(dict):
    removed = False
    users = 0
    use_fake_user = False
    library = None

    def __bool__(self):
        return True


class Resources:
    def __init__(self, factory):
        self.factory, self.items = factory, []

    def new(self, *args):
        value = self.factory(*args)
        self.items.append(value)
        return value

    def remove(self, value, **kwargs):
        self.items.remove(value)
        value.removed = True

    def __iter__(self):
        return iter(self.items)


def optical_source(runtime, params=None):
    class Matrix(tuple):
        def determinant(self):
            return self[0][0] * self[1][1] * self[2][2] * self[3][3]

    camera = NS(as_pointer=lambda: 51, matrix_world=Matrix(
        ((1., 0., 0., 0.), (0., 1., 0., 0.), (0., 0., 1., 0.), (0., 0., 0., 1.))))
    params = params or CameraParameters(location=(0., 0., 0.))
    runtime.monkeypatch.setattr(runtime.native.core, 'get_camera', lambda *_: camera)
    runtime.monkeypatch.setattr(runtime.native.core, 'parameters', lambda _: params)
    runtime.camera_area.regions = [NS(type='WINDOW', width=1000, height=700)]
    key = (51, params.fov, params.vfov, params.clip_near_m, params.clip_far_m, 1920/1080)
    runtime.session.entries[0].update(key=key, view_key=(key, 1000, 700))
    return camera


def test_start_changes_only_the_calling_source_area(runtime):
    r = runtime
    other = area(12, shading='RENDERED')
    r.source.screen.areas.append(other)
    r.bpy.context.area = r.source_area
    r.source_area.spaces.active.shading.type = 'MATERIAL'
    r.windows.remove(r.view)
    r.bpy.ops.wm.window_new = lambda: r.windows.append(r.view) or {'FINISHED'}
    session = r.native.NativeSession(r.bpy.context, [1], 'STRIP')
    session.start()
    assert other.spaces.active.shading.type == 'RENDERED'
    assert session.source_shading == [(10, 11, 'MATERIAL')]
    assert session.restore_source_shading() == []
    assert r.source_area.spaces.active.shading.type == 'MATERIAL'


@pytest.mark.parametrize('field,value', [
    ('resolution_x', 0), ('resolution_y', 0), ('resolution_x', -1),
    ('pixel_aspect_x', math.nan), ('pixel_aspect_y', math.inf),
    ('pixel_aspect_y', 0.), ('pixel_aspect_x', -1.),
])
def test_invalid_render_gate_is_rejected_before_touching_proxy(runtime, field, value):
    optical_source(runtime)
    setattr(runtime.scene.render, field, value)
    with pytest.raises(ValueError):
        runtime.session.sync()
    assert not runtime.proxy.removed and not runtime.data.removed


@pytest.mark.parametrize('near,far', [(0., 1.), (.1, .01), (.005, math.nan), (.005, math.inf)])
def test_invalid_clipping_is_rejected_before_touching_proxy(runtime, near, far):
    params = replace(CameraParameters(location=(0., 0., 0.)), clip_near_m=near, clip_far_m=far)
    optical_source(runtime, params)
    with pytest.raises(ValueError):
        runtime.session.sync()
    assert not runtime.proxy.removed and not runtime.data.removed


@pytest.mark.parametrize('scale', [0., math.nan, math.inf])
def test_invalid_camera_transform_is_rejected_before_touching_proxy(runtime, scale):
    camera = optical_source(runtime)
    camera.matrix_world = type(camera.matrix_world)(
        ((scale, 0., 0., 0.), *camera.matrix_world[1:]))
    with pytest.raises(ValueError):
        runtime.session.sync()
    assert not runtime.proxy.removed and not runtime.data.removed


@pytest.mark.parametrize('failure', ['data_tag', 'object_tag', 'link', 'constraint'])
def test_proxy_allocation_failure_remains_releasable(runtime, failure):
    r = runtime
    optical_source(r)
    r.session.entries[0].update(proxy=None, proxy_data=None, key=None)

    class Data(Resource):
        def __setitem__(self, key, value):
            if failure == 'data_tag':
                raise RuntimeError('data tag failed')
            super().__setitem__(key, value)

    class Object(Resource):
        def __init__(self, name, data):
            self.data, self.type = data, 'CAMERA'
            self.constraints = NS(new=self.constraint)

        def __setitem__(self, key, value):
            if failure == 'object_tag':
                raise RuntimeError('object tag failed')
            super().__setitem__(key, value)

        def constraint(self, _):
            if failure == 'constraint':
                raise RuntimeError('constraint failed')
            return NS()

    def link(_):
        if failure == 'link':
            raise RuntimeError('link failed')

    r.bpy.data.cameras = Resources(lambda _: Data())
    r.bpy.data.objects = Resources(Object)
    r.scene.collection = NS(objects=NS(link=link))
    with pytest.raises(RuntimeError, match='failed'):
        r.session.sync()
    assert r.session.release_proxy(r.session.entries[0]) == []
    assert r.bpy.data.objects.items == []
    assert r.bpy.data.cameras.items == []


def test_proxy_release_does_not_adopt_an_untracked_camera_datablock(runtime):
    runtime.session.entries[0]['proxy_data'] = None
    assert runtime.session.release_proxy(runtime.session.entries[0]) == []
    assert runtime.proxy.removed
    assert not runtime.data.removed


@pytest.mark.parametrize('protected', ['use_fake_user', 'library', 'other_scene'])
def test_proxy_release_preserves_user_retained_or_shared_objects(runtime, protected):
    if protected == 'other_scene':
        runtime.proxy.users_scene = (runtime.scene, object())
    else:
        setattr(runtime.proxy, protected, True)
    assert runtime.session.release_proxy(runtime.session.entries[0]) == []
    assert not runtime.proxy.removed and not runtime.data.removed
    assert runtime.session.entries[0]['proxy'] is None


@pytest.mark.parametrize('protected', ['use_fake_user', 'library'])
def test_proxy_release_preserves_user_retained_camera_data(runtime, protected):
    setattr(runtime.data, protected, True)
    assert runtime.session.release_proxy(runtime.session.entries[0]) == []
    assert runtime.proxy.removed and not runtime.data.removed


@pytest.mark.parametrize('protected', ['use_fake_user', 'library', 'shared_scene'])
def test_loaded_proxy_cleanup_preserves_user_retained_objects(runtime, protected):
    r = runtime
    data, proxy = Resource(), Resource()
    data[r.native._PROXY_OWNER] = proxy[r.native._PROXY_OWNER] = 'saved-owner'
    proxy.data, proxy.type = data, 'CAMERA'
    data.users = 1
    if protected == 'shared_scene':
        proxy.users_scene = (r.scene, object())
    else:
        setattr(proxy, protected, True)
    r.bpy.data.objects, r.bpy.data.cameras = Resources(None), Resources(None)
    r.bpy.data.objects.items.append(proxy)
    r.bpy.data.cameras.items.append(data)
    r.native.on_loaded(None)
    assert not proxy.removed and not data.removed


@pytest.mark.parametrize('protected', ['use_fake_user', 'library'])
def test_loaded_proxy_cleanup_preserves_user_retained_camera_data(runtime, protected):
    r = runtime
    data = Resource()
    data[r.native._PROXY_OWNER] = 'saved-owner'
    setattr(data, protected, True)
    r.bpy.data.objects, r.bpy.data.cameras = Resources(None), Resources(None)
    r.bpy.data.cameras.items.append(data)
    r.native.on_loaded(None)
    assert not data.removed


@pytest.mark.parametrize('reference', ['scene', 'viewport'])
def test_proxy_release_preserves_external_direct_camera_references(runtime, reference):
    r = runtime
    if reference == 'scene':
        r.bpy.data.scenes = [NS(camera=r.proxy)]
    else:
        r.source_area.spaces.active.camera = r.proxy
    assert r.session.release_proxy(r.session.entries[0]) == []
    assert not r.proxy.removed and not r.data.removed


@pytest.mark.parametrize('reference', ['scene', 'viewport'])
def test_loaded_proxy_cleanup_preserves_external_direct_camera_references(runtime, reference):
    r = runtime
    data, proxy = Resource(), Resource()
    data[r.native._PROXY_OWNER] = proxy[r.native._PROXY_OWNER] = 'saved-owner'
    proxy.data, proxy.type = data, 'CAMERA'
    data.users = 1
    r.bpy.data.objects, r.bpy.data.cameras = Resources(None), Resources(None)
    r.bpy.data.objects.items.append(proxy)
    r.bpy.data.cameras.items.append(data)
    if reference == 'scene':
        r.bpy.data.scenes = [NS(camera=proxy)]
    else:
        r.source_area.spaces.active.camera = proxy
    r.native.on_loaded(None)
    assert not proxy.removed and not data.removed


def test_close_cancels_pending_setup_and_first_paint_timers(runtime):
    r = runtime
    r.bpy.app.timers.unregister = r.timers.remove
    r.timers.extend((r.session.prepare, r.session.first_paint))
    r.native.shutdown(preserve_playback=False)
    assert r.timers == []


def test_load_cancels_a_resume_scheduled_by_an_already_closed_session(runtime):
    r = runtime
    r.bpy.app.timers.unregister = r.timers.remove
    r.source.screen.is_animation_playing = True
    playback.animation_started(r.scene)
    r.session.playback_context = lambda: {'window': r.source}

    def cancel(**_):
        r.source.screen.is_animation_playing = False
        playback.animation_stopped(r.scene)
        return {'FINISHED'}

    plays = []
    r.bpy.ops.screen = NS(animation_cancel=cancel, animation_play=lambda: plays.append(True))
    r.native.shutdown()
    assert r.native._ACTIVE is None and r.timers
    pending = r.timers[0]
    r.native.on_load(None)
    assert r.timers == []
    pending()
    assert plays == []


def test_failed_resume_timer_cancellation_does_not_skip_session_cleanup(runtime):
    r = runtime
    pending = r.session.resume_after_close
    r.session.resume_when_closed = True
    r.native._PENDING_RESUMES.append(pending)
    r.timers.append(pending)

    def fail(_):
        raise RuntimeError('timer removal failed')

    r.bpy.app.timers.unregister = fail
    r.native.shutdown(preserve_playback=False)
    assert r.native._ACTIVE is None
    assert r.proxy.removed and r.data.removed
    assert r.native._PENDING_RESUMES == [pending]
    assert not r.session.resume_when_closed
    r.bpy.app.timers.unregister = r.timers.remove
    r.native.shutdown(preserve_playback=False)
    assert r.native._PENDING_RESUMES == r.timers == []


@pytest.mark.parametrize('missing_at', [0, 1, 2])
def test_frame_gate_restores_view_when_projection_disappears(runtime, monkeypatch, missing_at):
    from wfrl_blender import custom_camera_preview
    r = runtime
    space = r.camera_area.spaces.active
    before = r.native._view_state(space)
    space.region_3d.update = lambda: None
    r.bpy.context.preferences = NS(system=NS(ui_scale=1.))
    monkeypatch.setattr(custom_camera_preview, 'available_rectangle', lambda *_: (0, 0, 1000, 700))
    calls = []

    def bounds(*_):
        index = len(calls)
        calls.append(index)
        return None if index == missing_at else (10., 10., 500., 300.)

    projection.frame_gate(r.scene, r.session.entries[0], NS(), space,
                          bpy=r.bpy, math=math, gate_bounds=bounds)
    assert r.native._view_state(space) == before


@pytest.mark.parametrize('value', [math.nan, math.inf])
def test_frame_gate_rejects_nonfinite_bounds_without_changing_view(runtime, monkeypatch, value):
    from wfrl_blender import custom_camera_preview
    r = runtime
    space = r.camera_area.spaces.active
    before = r.native._view_state(space)
    space.region_3d.update = lambda: None
    r.bpy.context.preferences = NS(system=NS(ui_scale=1.))
    monkeypatch.setattr(custom_camera_preview, 'available_rectangle', lambda *_: (0, 0, 1000, 700))
    with pytest.raises(ValueError):
        projection.frame_gate(r.scene, r.session.entries[0], NS(), space,
            bpy=r.bpy, math=math, gate_bounds=lambda *_: (value, 0., 500., 300.))
    assert r.native._view_state(space) == before


@pytest.mark.parametrize('field', ['clip_start', 'clip_end'])
def test_proxy_rejects_clamped_clipping_and_remains_releasable(runtime, field):
    r = runtime
    optical_source(r)
    r.session.entries[0].update(proxy=None, proxy_data=None, key=None)

    class Data(Resource):
        def __setattr__(self, name, value):
            super().__setattr__(name, value * .5 if name == field else value)

    def proxy_factory(_, data):
        proxy = Resource()
        proxy.data, proxy.type = data, 'CAMERA'
        proxy.constraints = NS(new=lambda _: NS())
        return proxy

    r.bpy.data.cameras = Resources(lambda _: Data())
    r.bpy.data.objects = Resources(proxy_factory)
    r.scene.collection = NS(objects=NS(link=lambda _: None))
    with pytest.raises(ValueError, match='裁剪'):
        r.session.sync()
    assert r.session.release_proxy(r.session.entries[0]) == []
    assert r.bpy.data.cameras.items == r.bpy.data.objects.items == []


def test_native_registration_is_idempotent_and_unregister_removes_all_callbacks(runtime):
    r = runtime
    r.native._ACTIVE = None
    r.bpy.context.window_manager.keyconfigs = NS(addon=None)

    class Header:
        def __init__(self):
            self.callbacks = []

        def prepend(self, callback):
            self.callbacks.append(callback)

        def remove(self, callback):
            self.callbacks.remove(callback)

    r.bpy.types.VIEW3D_HT_header = Header()
    r.bpy.types.IMAGE_HT_header = Header()
    names = ('load_pre', 'load_post', 'save_pre', 'save_post', 'save_post_fail')
    r.bpy.app.handlers = NS(**{name: [] for name in names})
    r.native.register()
    r.native.register()
    assert all(len(getattr(r.bpy.app.handlers, name)) == 1 for name in names)
    assert r.bpy.types.VIEW3D_HT_header.callbacks == [r.native.header]
    assert r.bpy.types.IMAGE_HT_header.callbacks == [r.native.header]
    r.native.unregister()
    r.native.unregister()
    assert all(getattr(r.bpy.app.handlers, name) == [] for name in names)
    assert r.bpy.types.VIEW3D_HT_header.callbacks == []
    assert r.bpy.types.IMAGE_HT_header.callbacks == []
