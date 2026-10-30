"""Preview resource ownership, state validation and camera-card geometry."""
import sys
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_camera_preview as preview


def test_preview_keeps_its_render_key_when_only_another_scene_changes(monkeypatch):
    from wfrl_blender import custom_camera_capture

    scene = NS(frame_current=1, frame_subframe=0., as_pointer=lambda: 901)
    other = NS(frame_current=1, frame_subframe=0., as_pointer=lambda: 902)
    context = NS(scene=scene, window=NS(as_pointer=lambda: 10), area=NS(as_pointer=lambda: 20),
                 view_layer=NS(as_pointer=lambda: 30))
    monkeypatch.setattr(preview, '_DRAWING', False)
    monkeypatch.setattr(preview, 'render_profile', lambda *_: {})
    monkeypatch.setattr(preview, 'playback_preview', lambda _: False)
    monkeypatch.setattr(custom_camera_capture, 'current_time', lambda _: 0.)
    monkeypatch.setitem(sys.modules, preview.__package__ + '.panels.custom_cameras',
                        NS(session_identity=lambda owner: owner.as_pointer()))
    before = preview.provenance(context, [], 640)
    preview.changed(other, None)
    assert preview.provenance(context, [], 640) == before
    preview.changed(scene, None)
    updated = preview.provenance(context, [], 640)
    assert updated != before
    preview.changed(None)
    assert preview.provenance(context, [], 640) != updated


def test_preview_quality_tracks_the_scene_owning_playback(monkeypatch):
    from wfrl_blender import playback
    owner = NS(as_pointer=lambda: 1)
    other = NS(as_pointer=lambda: 2)
    monkeypatch.setattr(playback, '_OWNER_SCENE', owner)
    screen = NS(is_animation_playing=True)
    monkeypatch.setitem(sys.modules, 'bpy', NS(app=NS(background=False),
        context=NS(window_manager=NS(windows=[NS(screen=screen)]))))
    assert preview.playback_preview(NS(scene=owner, screen=screen))
    assert not preview.playback_preview(NS(scene=other, screen=screen))


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
            self.texture_color = NS(offscreen=self)
            self.freed = False
            self.draws = 0

        def bind(self):
            return nullcontext()

        def draw_view3d(self, *args, **kwargs):
            assert not self.freed
            self.draws += 1
            state.draws.append((args, kwargs))

        def free(self):
            assert not self.freed
            self.freed = True
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


def test_leased_preview_skips_readback_and_keeps_committed_targets_immutable(gpu_capture, monkeypatch):
    state, context, cameras = gpu_capture
    pool = preview.PreviewTargets()
    monkeypatch.setattr(pool.state_cache, 'hash', lambda *_: 'geometry-state')
    first = preview.render_group(context, cameras, target=pool)
    first_targets = {image._lease[1] for image in first.values()}
    second = preview.render_group(context, cameras, target=pool)
    assert not state.reads and not state.textures and not state.buffers
    assert first_targets.isdisjoint(image._lease[1] for image in second.values())
    assert all(target.offscreen.draws == 1 for target in first_targets)
    assert first[1].rgba() == first[1].rgba() == state.pixels
    assert len(state.reads) == 1
    for image in first.values():
        image.free()
        image.free()
    third = preview.render_group(context, cameras, target=pool)
    assert len(pool.targets) == 4
    assert {image._lease[1] for image in third.values()} == first_targets
    for image in (*second.values(), *third.values()):
        image.free()
    pool.trim(2)
    assert len(pool.targets) == 2 and state.frees == 2
    pool.free()
    pool.free()
    assert state.frees == 4 and not pool.leased


def test_failed_leased_group_releases_staging_without_touching_old_images(gpu_capture, monkeypatch):
    state, context, cameras = gpu_capture
    pool = preview.PreviewTargets()
    monkeypatch.setattr(pool.state_cache, 'hash', lambda *_: 'geometry-state')
    old = preview.render_group(context, cameras, target=pool)
    old_targets = set(pool.leased)
    staged = []
    snapshot = preview.CameraImage.snapshot
    def interrupted(image, offscreen, **kwargs):
        snapshot(image, offscreen, **kwargs)
        staged.append(image)
        if len(staged) == 2:
            raise RuntimeError('injected GPU staging failure')
    monkeypatch.setattr(preview.CameraImage, 'snapshot', interrupted)
    with pytest.raises(RuntimeError, match='GPU staging failure'):
        preview.render_group(context, cameras, target=pool)
    assert pool.leased == old_targets
    assert all(image.texture is None and image._lease is None for image in staged)
    assert all(target.offscreen.draws == 1 for target in old_targets)
    assert old[1].rgba() == state.pixels and not preview._DRAWING
    for image in old.values():
        image.free()
    pool.free()


def test_geometry_cache_reuses_coordinates_but_tracks_native_edits_and_scope(monkeypatch):
    handlers = []
    monkeypatch.setitem(sys.modules, 'bpy', NS(app=NS(handlers=NS(depsgraph_update_post=handlers))))
    matrix = [[float(i == j) for j in range(4)] for i in range(4)]
    mesh = NS(as_pointer=lambda: 20, bl_rna=NS(identifier='Mesh'))
    obj = NS(name='WFRL.Turbine.T1.Blade', type='MESH', as_pointer=lambda: 10,
             data=mesh, matrix_world=matrix, bl_rna=NS(identifier='Object'))
    obj.evaluated_get = lambda _: obj
    scene = NS(as_pointer=lambda: 1, frame_current=1, frame_subframe=0., objects=[obj])
    graph = NS(as_pointer=lambda: 2, updates=[])
    coordinates = [b'first geometry']
    monkeypatch.setattr(preview, '_mesh_coordinates', lambda _: coordinates[0])
    cache = preview.GeometryCache()
    original = cache.hash(scene, graph, 'source')
    assert original == preview.simulation_state_hash(scene, graph)
    assert cache.hash(scene, graph, 'source') == original and cache.mesh_reads == 1
    camera = NS(type='CAMERA', bl_rna=NS(identifier='Object'))
    graph.updates = [NS(id=camera, is_updated_geometry=True)]
    cache.updated(scene, graph)
    matrix[0][3] = 2.
    moved = cache.hash(scene, graph, 'source')
    assert moved != original and cache.mesh_reads == 1
    coordinates[0] = b'native edited geometry'
    graph.updates = [NS(id=mesh, is_updated_geometry=True)]
    cache.updated(scene, graph)
    edited = cache.hash(scene, graph, 'source')
    assert edited != moved and edited == preview.simulation_state_hash(scene, graph)
    assert cache.mesh_reads == 2
    scene.frame_current += 1
    cache.hash(scene, graph, 'source')
    cache.hash(scene, graph, 'new source')
    handlers.clear()
    cache.hash(scene, graph, 'new source')
    assert cache.mesh_reads == 5 and len(handlers) == 1
    scene.objects.clear()
    cache.hash(scene, graph, 'new source')
    assert not cache.meshes
    cache.free()
    cache.free()
    assert not handlers and cache.scope is None


def test_camera_feedback_coalesces_input_and_reserves_draw_time(monkeypatch):
    from copy import deepcopy
    state = dict(identity='source', frame=1, subframe=0., time_s=0., revision=1,
                 profile='sRGB', long_edge=640, slots=(1,), cameras=((42, False, 'fov=75'),))
    clock = [1.]
    draws = []
    class Image:
        width, height = 2, 2
        def free(self):
            pass
    monkeypatch.setattr(preview, 'provenance', lambda *_: deepcopy(state))
    monkeypatch.setattr(preview.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(preview.time, 'perf_counter', lambda: clock[0])
    def render(*_):
        draws.append(deepcopy(state))
        return {1: Image()}
    monkeypatch.setattr(preview, 'render_group', render)
    context = NS(view_layer=NS(update=lambda: None))
    renderer = preview.Preview()
    renderer.refresh(context, [])
    state['cameras'] = ((42, False, 'fov=80'),)
    clock[0] += .02
    renderer.refresh(context, [])
    assert len(draws) == 1
    state['cameras'] = ((42, False, 'fov=85'),)
    clock[0] += .04
    renderer.refresh(context, [])
    assert len(draws) == 1
    clock[0] += .06
    renderer.refresh(context, [])
    assert len(draws) == 2 and renderer.key['cameras'] == state['cameras']
    renderer.refresh(context, [])
    assert len(draws) == 2
    renderer.elapsed_s = .2
    state['cameras'] = ((42, False, 'fov=90'),)
    clock[0] += .06
    renderer.refresh(context, [])
    assert len(draws) == 2
    clock[0] += .15
    renderer.refresh(context, [])
    assert len(draws) == 3
    renderer.elapsed_s = .01
    for step in range(1, 6):
        state['cameras'] = ((42, False, f'fov={90 + step}'),)
        clock[0] += .04
        renderer.refresh(context, [])
    assert len(draws) == 3
    clock[0] += .06
    renderer.refresh(context, [])
    assert len(draws) == 4
    state['frame'] += 1
    clock[0] += .1
    renderer.refresh(context, [])
    assert len(draws) == 4
    renderer.free()


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
    assert not preview.playback_preview(NS(screen=NS(is_animation_playing=True)))
    assert not preview.playback_preview(NS(screen=NS(is_animation_playing=False)))
    assert not preview.playback_preview(NS())
