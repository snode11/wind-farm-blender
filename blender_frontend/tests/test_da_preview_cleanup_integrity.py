"""Preview cleanup failures remain observable and retryable without blocking peers."""
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_camera_preview as preview
from blender_frontend.tests.test_custom_camera_preview import gpu_capture


class Offscreen:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def free(self):
        self.calls += 1
        if self.error:
            raise self.error


def target(error=None):
    value = preview.RenderTarget()
    value.offscreen = Offscreen(error)
    value.key = (1, 640, 480)
    value.allocations = 1
    return value


def test_gpu_release_failure_preserves_handle_and_success_count():
    value = target(RuntimeError('GPU context unavailable'))
    resource, key = value.offscreen, value.key
    with pytest.raises(RuntimeError, match='GPU context unavailable'):
        value.free()
    assert value.offscreen is resource and value.key == key
    assert value.releases == 0
    resource.error = None
    value.free()
    value.free()
    assert value.offscreen is None and value.key is None
    assert value.releases == 1 and resource.calls == 2


def test_resize_does_not_replace_a_target_that_failed_to_release(monkeypatch):
    allocated = []
    monkeypatch.setitem(sys.modules, 'gpu', NS(types=NS(
        GPUOffScreen=lambda *args, **kwargs: allocated.append(args))))
    value = target(RuntimeError('GPU context unavailable'))
    original = value.offscreen
    with pytest.raises(RuntimeError, match='GPU context unavailable'):
        value.get(NS(window=NS(as_pointer=lambda: 2)), 320, 240)
    assert value.offscreen is original and value.key == (1, 640, 480)
    assert not allocated and value.allocations == 1


def test_already_invalid_gpu_reference_is_retired_once():
    value = target(ReferenceError('GPUOffScreen has been freed'))
    resource = value.offscreen
    value.free()
    value.free()
    assert value.offscreen is None and value.key is None
    assert value.releases == 1 and resource.calls == 1


def test_capture_pool_releases_peers_and_retains_failed_target():
    pool = preview.RenderTargetPool()
    failed, good = target(RuntimeError('GPU context unavailable')), target()
    failed_resource, good_resource = failed.offscreen, good.offscreen
    pool.targets = {(640, 480): failed, (320, 240): good}
    pool.window, pool.allocations = 1, 2
    with pytest.raises(RuntimeError, match='GPU context unavailable'):
        pool.free()
    assert pool.targets == {(640, 480): failed} and pool.window == 1
    assert good_resource.calls == 1 and pool.releases == 1
    failed_resource.error = None
    pool.free()
    pool.free()
    assert not pool.targets and pool.window is None and pool.releases == 2
    assert good_resource.calls == 1 and failed_resource.calls == 2


def test_preview_pool_releases_peers_and_geometry_handlers_on_failure():
    pool = preview.PreviewTargets()
    failed, good = target(RuntimeError('GPU context unavailable')), target()
    failed_resource, good_resource = failed.offscreen, good.offscreen
    pool.targets = [failed, good]
    pool.leased = {failed, good}
    callbacks = [pool.state_cache.updated]
    pool.state_cache.handler_lists = (callbacks,)
    pool.state_cache.meshes[1] = b'geometry'
    with pytest.raises(RuntimeError, match='GPU context unavailable'):
        pool.free()
    assert pool.targets == [failed] and pool.leased == {failed}
    assert not callbacks and not pool.state_cache.meshes
    assert good_resource.calls == 1
    failed_resource.error = None
    pool.free()
    assert not pool.targets and not pool.leased


def test_trim_releases_all_unneeded_targets_after_one_failure():
    pool = preview.PreviewTargets()
    leased, failed, good = target(), target(RuntimeError('GPU context unavailable')), target()
    leased_resource, failed_resource, good_resource = leased.offscreen, failed.offscreen, good.offscreen
    pool.targets, pool.leased = [leased, failed, good], {leased}
    with pytest.raises(RuntimeError, match='GPU context unavailable'):
        pool.trim(0)
    assert pool.targets == [leased, failed] and pool.leased == {leased}
    assert leased_resource.calls == 0 and good_resource.calls == 1
    failed_resource.error = None
    pool.free()


@pytest.fixture
def renderers(monkeypatch):
    monkeypatch.setattr(preview, '_RENDERERS', set())
    monkeypatch.setattr(preview, '_CONTROL_DRAW_CACHE', preview.DrawCache())
    yield
    for renderer in tuple(preview._RENDERERS):
        for value in renderer.target.targets:
            value.offscreen.error = None
        renderer.free()


def test_renderer_failure_still_drops_images_cache_and_stale_metadata(renderers):
    renderer = preview.Preview()
    failed, good = target(RuntimeError('GPU context unavailable')), target()
    resource = failed.offscreen
    renderer.target.targets = [failed, good]
    renderer.draw_cache.fills['old layout'] = object()
    renderer.key = renderer.metadata = renderer.request = {'identity': 'old scene'}
    released = []
    renderer.images = {1: NS(free=lambda: released.append(1))}
    with pytest.raises(RuntimeError, match='GPU context unavailable'):
        renderer.free()
    assert released == [1] and not renderer.images and not renderer.draw_cache.fills
    assert renderer.key is renderer.metadata is renderer.request is None
    assert renderer in preview._RENDERERS and 'GPU context unavailable' in renderer.error
    resource.error = None
    renderer.free()
    assert renderer not in preview._RENDERERS


def test_shutdown_finishes_other_renderers_and_control_cache_on_failure(renderers):
    first, second = preview.Preview(), preview.Preview()
    failed, good = target(RuntimeError('GPU context unavailable')), target()
    resource, good_resource = failed.offscreen, good.offscreen
    first.target.targets, second.target.targets = [failed], [good]
    preview._CONTROL_DRAW_CACHE.fills['old layout'] = object()
    with pytest.raises(RuntimeError, match='GPU context unavailable'):
        preview.shutdown()
    assert good_resource.calls == 1 and preview._RENDERERS == {first}
    assert not preview._CONTROL_DRAW_CACHE.fills
    resource.error = None
    preview.shutdown()
    assert not preview._RENDERERS and resource.calls == 2


def test_owned_render_target_failure_resets_drawing_guard_and_can_retry(gpu_capture, monkeypatch):
    state, context, cameras = gpu_capture
    offscreen_type = sys.modules['gpu'].types.GPUOffScreen
    real_free = offscreen_type.free
    pending = []

    def fail_free(resource):
        pending.append(resource)
        raise RuntimeError('GPU context unavailable')

    monkeypatch.setattr(offscreen_type, 'free', fail_free)
    images = []
    snapshot = preview.CameraImage.snapshot

    def track(image, *args, **kwargs):
        images.append(image)
        return snapshot(image, *args, **kwargs)

    monkeypatch.setattr(preview.CameraImage, 'snapshot', track)
    with pytest.raises(RuntimeError, match='GPU context unavailable'):
        preview.render_group(context, cameras, create_texture=False)
    assert not preview._DRAWING
    assert all(image.pixels is image.texture is None for image in images)
    monkeypatch.setattr(offscreen_type, 'free', real_free)
    preview.shutdown()
    assert len(pending) == 1 and pending[0].freed and state.frees == 1


def test_trim_failure_is_visible_and_does_not_leave_preview_updating(renderers, monkeypatch):
    request = dict(identity='scene', frame=1, subframe=0., time_s=None, revision=1,
                   profile='sRGB', long_edge=640, slots=(1,), cameras=())
    monkeypatch.setattr(preview, 'provenance', lambda *_args: request.copy())
    image = NS(width=640, height=480, free=lambda: None)
    monkeypatch.setattr(preview, 'render_group', lambda *_args: {1: image})
    renderer = preview.Preview()

    def fail_trim(_count):
        raise RuntimeError('GPU trim unavailable')

    monkeypatch.setattr(renderer.target, 'trim', fail_trim)
    renderer.refresh(NS(view_layer=NS(update=lambda: None)), [])
    assert not renderer.updating and 'GPU trim unavailable' in renderer.status()
    assert renderer.images == {1: image}


def test_annotation_restore_failure_does_not_skip_remaining_objects():
    restored, updates = [], []

    class Annotation:
        def __init__(self, name):
            self.name = name

        def hide_get(self, **_kwargs):
            return False

        def hide_set(self, value, **_kwargs):
            if not value:
                restored.append(self.name)
                if self.name.endswith('first'):
                    raise RuntimeError('annotation restore unavailable')

    objects = {name: Annotation(name) for name in ('WFRL.Label.first', 'WFRL.Label.second')}
    layer = NS(objects=objects, update=lambda: updates.append(1))
    with pytest.raises(RuntimeError, match='annotation restore unavailable'):
        with preview.without_annotations(NS(objects=objects.values()), layer):
            pass
    assert restored == list(objects) and updates == [1, 1]


def test_shading_restore_failure_does_not_skip_remaining_viewport_settings():
    values = dict(type='SOLID', use_scene_lights=True, use_scene_world=True,
                  studio_light='studio.sl', studiolight_rotate_z=1., studiolight_intensity=.5,
                  studiolight_background_alpha=.2, studiolight_background_blur=.1,
                  use_studiolight_view_rotation=True)

    class Shading(NS):
        fail = False

        def __setattr__(self, key, value):
            if self.fail and key == 'type':
                raise RuntimeError('shading restore unavailable')
            super().__setattr__(key, value)

    shading = Shading(**values)
    space = NS(shading=shading, overlay=NS(show_overlays=True), show_gizmo=True)
    with pytest.raises(RuntimeError, match='shading restore unavailable'):
        with preview.drawing_settings(space):
            shading.fail = True
    assert all(getattr(shading, key) == value for key, value in values.items() if key != 'type')
    assert space.overlay.show_overlays and space.show_gizmo
