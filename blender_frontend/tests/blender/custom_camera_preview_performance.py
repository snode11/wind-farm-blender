"""Short source-only GPU ownership, geometry invalidation and CPU timing probe.

Run in an isolated factory-startup window while holding the frontend UI lock.
WFRL_TEST_OUTPUT must name a new directory. WFRL_PREVIEW_BASELINE may point to
an independent preview module for a matched three-edit comparison. Timings are
main-thread callback durations, not viewport FPS or physical input latency.
"""
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import custom_cameras as core, custom_camera_preview as preview
from wfrl_blender.panels.custom_cameras import session_identity

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)


def rgba(buffer, size):
    buffer.dimensions = size
    return bytes(buffer)


def run():
    import gpu
    checks = {}
    renderer = None
    baseline_target = None
    try:
        addon.register()
        addon.load_demo_scene(camera_rig=False)
        window = bpy.context.window
        area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
        region = next(region for region in area.regions if region.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            context, scene = bpy.context, bpy.context.scene
            scene.frame_set(755, subframe=.25)
            cameras = []
            for slot in (1, 2):
                name = core.camera_name(slot)
                camera = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
                scene.collection.objects.link(camera)
                camera.parent = scene.objects['WFRL.Turbine.T1.YawRoot']
                camera['wfrl_custom_camera'] = True
                camera['wfrl_custom_slot'] = slot
                core.apply_parameters(camera, replace(core.parameters(camera),
                    location=(0., -10., .3), yaw=180., pitch=-45., output_long_edge_px=640))
                cameras.append(camera)
            context.view_layer.update()
            renderer = preview.Preview()
            renderer.refresh(context, cameras)
            assert not renderer.error and len(renderer.images) == 2, renderer.error
            assert all(image.pixels is None and image._lease is not None for image in renderer.images.values())
            cache = renderer.target.state_cache
            reads = cache.mesh_reads
            digest = cache.hash(scene, context.evaluated_depsgraph_get(), session_identity(scene))
            assert digest == preview.simulation_state_hash(scene, context.evaluated_depsgraph_get())
            assert cache.mesh_reads == reads
            checks['cached_hash_equals_full_evaluated_hash'] = True

            saved = core.parameters(cameras[0])
            core.apply_parameters(cameras[0], replace(saved, yaw=saved.yaw + 1.))
            renderer.last = 0.
            renderer.refresh(context, cameras)
            assert not renderer.error, renderer.error
            assert cache.mesh_reads == reads, (reads, cache.mesh_reads)
            assert len(renderer.target.targets) == 4
            checks['camera_edit_reuses_geometry_and_two_target_groups'] = True

            for image in renderer.images.values():
                offscreen = image._lease[1].offscreen
                with offscreen.bind():
                    expected = rgba(gpu.state.active_framebuffer_get().read_color(
                        0, 0, image.width, image.height, 4, 0, 'UBYTE'), image.width * image.height * 4)
                assert image.rgba() == expected
                assert rgba(image.texture.read(), len(expected)) == expected
                assert len(set(expected)) > 16
            exported = preview.render_group(context, cameras, 640, create_texture=False)
            for image in exported.values():
                assert image.texture is None and len(image.rgba()) == image.width * image.height * 4
                assert image.state_hash == next(iter(renderer.images.values())).state_hash
                image.free()
            checks['gpu_texture_and_lazy_rgba_equal_framebuffer_capture_keeps_pixels'] = True

            mesh_obj = next(obj for obj in scene.objects if obj.type == 'MESH'
                            and obj.name.startswith('WFRL.Turbine.') and len(obj.data.vertices))
            before = cache.hash(scene, context.evaluated_depsgraph_get(), session_identity(scene))
            original_x = mesh_obj.data.vertices[0].co.x
            mesh_obj.data.vertices[0].co.x += .01
            mesh_obj.data.update()
            context.view_layer.update()
            after = cache.hash(scene, context.evaluated_depsgraph_get(), session_identity(scene))
            assert after != before
            assert after == preview.simulation_state_hash(scene, context.evaluated_depsgraph_get())
            mesh_obj.data.vertices[0].co.x = original_x
            mesh_obj.data.update()
            context.view_layer.update()
            checks['same_frame_native_mesh_edit_invalidates_cache'] = True

            old = renderer.images
            old_bytes = {slot: image.rgba() for slot, image in old.items()}
            snapshot = preview.CameraImage.snapshot
            staged = []
            def fail_second(image, offscreen, **kwargs):
                snapshot(image, offscreen, **kwargs)
                staged.append(image)
                if len(staged) == 2:
                    raise RuntimeError('preview ownership check')
            preview.CameraImage.snapshot = fail_second
            try:
                core.apply_parameters(cameras[0], replace(saved, yaw=saved.yaw + 2.))
                renderer.last = 0.
                renderer.refresh(context, cameras)
            finally:
                preview.CameraImage.snapshot = snapshot
            assert renderer.images is old and 'preview ownership check' in renderer.error
            assert all(image.texture is None and image._lease is None for image in staged)
            for slot, image in old.items():
                assert rgba(image.texture.read(), len(old_bytes[slot])) == old_bytes[slot]
            renderer.attempt = renderer.last = 0.
            renderer.refresh(context, cameras)
            assert not renderer.error and renderer.images is not old
            checks['failed_group_preserves_old_gpu_pixels_and_retries'] = True

            timings = {}
            baseline_path = os.environ.get('WFRL_PREVIEW_BASELINE')
            if baseline_path:
                spec = importlib.util.spec_from_file_location('wfrl_blender._preview_performance_baseline', baseline_path)
                baseline = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(baseline)
                baseline_target = baseline.RenderTarget()
                for label, module, target in (('baseline', baseline, baseline_target),
                                               ('leased', preview, renderer.target)):
                    samples = []
                    current = None
                    for yaw in (181., 182., 183., 184.):
                        core.apply_parameters(cameras[0], replace(saved, yaw=yaw))
                        context.view_layer.update()
                        started = time.perf_counter()
                        images = module.render_group(context, cameras[:1], 640, target=target)
                        elapsed = time.perf_counter() - started
                        if current is not None:
                            samples.append(elapsed)
                            for image in current.values():
                                image.free()
                        current = images
                    for image in current.values():
                        image.free()
                    timings[label] = {'samples_s': samples, 'median_s': statistics.median(samples)}
                checks['matched_material_640px_three_edit_cpu_sample'] = True
            handler = cache.updated
            renderer.free()
            assert not renderer.target.targets and handler not in bpy.app.handlers.depsgraph_update_post
            renderer.free()
            if baseline_target:
                baseline_target.free()
            checks['targets_and_geometry_handler_released'] = True
            result = {'status': 'PASS', 'checks': checks, 'cpu_group_timing': timings,
                      'blender': bpy.app.version_string, 'gpu': gpu.platform.renderer_get(),
                      'source': str(Path(preview.__file__).resolve()),
                      'source_sha256': hashlib.sha256(Path(preview.__file__).read_bytes()).hexdigest(),
                      'preview_long_edge': 640, 'profile': preview.render_profile(scene),
                      'boundary': 'Scripted source GPU/CPU check; no OS-input latency, FPS, installation or export sequence benchmark.'}
            (OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print('CUSTOM_CAMERA_PREVIEW_PERFORMANCE_PASS', flush=True)
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        (OUT / 'result.json').write_text(json.dumps({'status': 'FAIL', 'checks': checks, 'error': error}, indent=2))
    finally:
        if renderer:
            renderer.free()
        if baseline_target:
            baseline_target.free()
        addon.unregister()
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1.)
