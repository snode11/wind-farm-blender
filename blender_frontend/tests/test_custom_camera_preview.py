"""Viewport overlap and the camera-card interaction geometry."""
import sys
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_camera_preview as preview


@pytest.fixture
def gpu_capture(monkeypatch):
    pixels = bytes([0, 17, 91, 143, 255, 0, 128, 255,
                    255, 31, 0, 255, 12, 45, 78, 0])
    state = NS(reads=[], buffers=[], textures=[], draws=[], frees=0,
               pixels=pixels, fail_read=None)

    class ReadBuffer(bytearray):
        pass

    def read_color(*args):
        state.reads.append(args)
        if state.fail_read == len(state.reads):
            raise RuntimeError('injected framebuffer failure')
        return ReadBuffer(pixels)

    def buffer(kind, length, values):
        state.buffers.append((kind, length, values))
        return values

    def texture(size, **kwargs):
        result = NS(size=size, **kwargs)
        state.textures.append(result)
        return result

    class Offscreen:
        def __init__(self, *_args, **_kwargs):
            pass

        def bind(self):
            return nullcontext()

        def draw_view3d(self, *args, **kwargs):
            state.draws.append((args, kwargs))

        def free(self):
            state.frees += 1

    monkeypatch.setitem(sys.modules, 'gpu', NS(
        state=NS(active_framebuffer_get=lambda: NS(read_color=read_color)),
        types=NS(Buffer=buffer, GPUTexture=texture, GPUOffScreen=Offscreen),
        capabilities=NS(max_texture_size_get=lambda: 4096)))
    monkeypatch.setitem(sys.modules, 'bpy', NS())
    monkeypatch.setitem(sys.modules, preview.__package__+'.panels.custom_cameras',
                        NS(session_identity=lambda _scene: 'test-session'))
    monkeypatch.delitem(sys.modules, preview.__package__+'.native_camera_views', raising=False)
    matrix = tuple(tuple(float(i == j) for j in range(4)) for i in range(4))
    params = preview.core.CameraParameters((1., 2., 3.))
    monkeypatch.setattr(preview, 'matrices', lambda *_args: (2, 2, matrix, matrix, matrix))
    monkeypatch.setattr(preview.core, 'parameters', lambda _camera: params)
    monkeypatch.setattr(preview, 'simulation_state_hash', lambda *_args: 'geometry-state')
    monkeypatch.setattr(preview, 'drawing_settings', lambda *_args: nullcontext())
    monkeypatch.setattr(preview, 'without_annotations', lambda *_args: nullcontext())
    context = NS(scene=NS(display_settings=NS(display_device='sRGB'), frame_current=3, frame_subframe=.25),
                 area=NS(type='VIEW_3D', regions=[NS(type='WINDOW')]), space_data=NS(),
                 view_layer=NS(update=lambda: None), evaluated_depsgraph_get=lambda: None,
                 window=NS(as_pointer=lambda: 1))
    return state, context, [{'wfrl_custom_slot': 1}, {'wfrl_custom_slot': 3}]


def test_preview_render_keeps_display_textures_by_default(gpu_capture):
    import numpy as np
    state, context, cameras = gpu_capture
    images = preview.render_group(context, cameras)
    assert set(images) == {1, 3}
    assert len(state.textures) == len(state.buffers) == 2
    for image, texture in zip(images.values(), state.textures):
        assert image.texture is texture and image.rgba() == state.pixels
        assert texture.size == (2, 2) and texture.format == 'RGBA8'
        assert texture.data.dtype == np.float32
        np.testing.assert_array_equal(texture.data, np.frombuffer(state.pixels, dtype=np.uint8).astype(np.float32) / 255.)
        image.free()
    assert state.frees == 1 and not preview._DRAWING


def test_pixel_only_render_skips_float_conversion_and_display_allocations(gpu_capture, monkeypatch):
    import numpy as np
    state, context, cameras = gpu_capture
    def unexpected(*_args, **_kwargs):
        raise AssertionError('display texture allocation in pixel-only capture')
    monkeypatch.setattr(np, 'frombuffer', unexpected)
    monkeypatch.setattr(sys.modules['gpu'].types, 'Buffer', unexpected)
    monkeypatch.setattr(sys.modules['gpu'].types, 'GPUTexture', unexpected)
    images = preview.render_group(context, cameras, create_texture=False)
    assert set(images) == {1, 3}
    for slot, image in images.items():
        assert image.texture is None and image.rgba() == state.pixels
        assert (image.width, image.height, image.camera_id) == (2, 2, f'C{slot}')
        assert image.params.location == (1., 2., 3.) and image.state_hash == 'geometry-state'
        image.free()
        assert image.rgba() is None
    assert state.reads == [(0, 0, 2, 2, 4, 0, 'UBYTE')] * 2
    assert all(kwargs == {'do_color_management': True, 'draw_background': True}
               for _args, kwargs in state.draws)
    assert state.frees == 1 and not preview._DRAWING


def test_pixel_only_render_failure_releases_partial_group_and_target(gpu_capture, monkeypatch):
    state, context, cameras = gpu_capture
    state.fail_read = 2
    images = []
    snapshot = preview.CameraImage.snapshot
    def record_snapshot(image, offscreen, **kwargs):
        images.append(image)
        return snapshot(image, offscreen, **kwargs)
    monkeypatch.setattr(preview.CameraImage, 'snapshot', record_snapshot)
    with pytest.raises(RuntimeError, match='injected framebuffer failure'):
        preview.render_group(context, cameras, create_texture=False)
    assert len(images) == 2 and all(image.texture is None and image.rgba() is None for image in images)
    assert state.frees == 1 and not preview._DRAWING


@pytest.mark.parametrize('overlap', [True, False])
def test_board_excludes_only_overlapping_native_regions(monkeypatch, overlap):
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(preferences=NS(system=NS(use_region_overlap=overlap)))))
    window = NS(type='WINDOW', x=0, y=60, width=1600, height=1000)
    area = NS(regions=[window,
        NS(type='TOOLS', x=0, y=60, width=80, height=960),
        NS(type='UI', x=1200, y=60, width=400, height=960),
        NS(type='HEADER', x=0, y=1020, width=1600, height=40)])
    expected = (80, 0, 1120, 960) if overlap else (0, 0, 1600, 1000)
    assert preview.available_rectangle(area, window) == expected


@pytest.mark.parametrize('scale', [1., 1.5, 2.])
def test_close_button_is_visible_above_cards(scale):
    rectangle = (70, 40, 1200, 900)
    x, y, w, h = preview.close_button(rectangle, scale)
    assert preview.contains(rectangle, x, y)
    assert preview.contains(rectangle, x+w-.01, y+h-.01)
    for card in preview.cells(rectangle, scale):
        assert y > card[1]+card[3]


def test_render_target_reuses_engine_and_releases_on_resize_and_window_change(monkeypatch):
    created = []
    class Offscreen:
        def __init__(self, width, height, **_):
            self.freed = False
            created.append(self)
        def free(self):
            self.freed = True
    monkeypatch.setitem(sys.modules, 'gpu', NS(types=NS(GPUOffScreen=Offscreen)))
    context = NS(window=NS(as_pointer=lambda: 1))
    target = preview.RenderTarget()
    first = target.get(context, 640, 640)
    assert target.get(context, 640, 640) is first
    assert len(created) == 1
    second = target.get(context, 640, 320)
    assert first.freed and not second.freed
    context.window = NS(as_pointer=lambda: 2)
    third = target.get(context, 640, 320)
    assert second.freed and not third.freed
    target.free()
    target.free()
    assert third.freed and target.offscreen is None


def test_capture_pool_keeps_each_size_and_releases_once_on_close(monkeypatch):
    created = []
    class Offscreen:
        def __init__(self, width, height, **_):
            self.size, self.free_calls = (width, height), 0
            created.append(self)
        def free(self):
            self.free_calls += 1
    monkeypatch.setitem(sys.modules, 'gpu', NS(types=NS(GPUOffScreen=Offscreen)))
    context = NS(window=NS(as_pointer=lambda: 1))
    pool = preview.RenderTargetPool()
    sizes = [(640, 640), (640, 320), (320, 640)]
    for _sample in range(4):
        for size in sizes:
            pool.get(context, *size)
    assert len(created) == 3
    assert pool.usage() == {'allocations': 3, 'releases': 0, 'active_targets': 3,
                            'policy': 'transaction / output size'}
    pool.free()
    pool.free()
    assert all(target.free_calls == 1 for target in created)
    assert pool.usage()['releases'] == 3 and pool.usage()['active_targets'] == 0


def test_capture_pool_does_not_reuse_a_different_window_context(monkeypatch):
    created = []
    class Offscreen:
        def __init__(self, *_args, **_kwargs):
            self.freed = False
            created.append(self)
        def free(self):
            self.freed = True
    monkeypatch.setitem(sys.modules, 'gpu', NS(types=NS(GPUOffScreen=Offscreen)))
    context = NS(window=NS(as_pointer=lambda: 1))
    pool = preview.RenderTargetPool()
    pool.get(context, 640, 320)
    pool.get(context, 320, 640)
    context.window = NS(as_pointer=lambda: 2)
    pool.get(context, 640, 320)
    assert [target.freed for target in created] == [True, True, False]
    assert pool.usage()['allocations'] == 3 and pool.usage()['releases'] == 2
    pool.free()


def test_playback_profile_is_explicit_and_does_not_change_export_default():
    scene = NS(view_settings=NS(view_transform='Standard', look='None', exposure=0,
        gamma=1, use_curve_mapping=False), display_settings=NS(display_device='sRGB'))
    assert preview.render_profile(scene)['shading'] == 'MATERIAL'
    assert preview.render_profile(scene, True)['shading'] == 'SOLID'
    assert preview.playback_preview(NS(screen=NS(is_animation_playing=True)))
    assert not preview.playback_preview(NS(screen=NS(is_animation_playing=False)))
    assert not preview.playback_preview(NS())
