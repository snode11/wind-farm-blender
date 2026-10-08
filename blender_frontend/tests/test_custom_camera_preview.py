"""Viewport overlap and the camera-card interaction geometry."""
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_camera_preview as preview


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
