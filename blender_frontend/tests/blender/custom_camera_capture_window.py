"""Real GPU pool comparison and capture failure/cancellation restoration.

Use a fresh Blender configuration and WFRL_TEST_OUTPUT directory. Optional
WFRL_ADDON_MODULE / WFRL_EXPECTED_ADDON_ROOT select an isolated installed ZIP.
Never saves a .blend or modifies the daily installation.
"""
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
core = importlib.import_module(MODULE+'.custom_cameras')
preview = importlib.import_module(MODULE+'.custom_camera_preview')
capture = importlib.import_module(MODULE+'.custom_camera_capture')
farm_flex = importlib.import_module(MODULE+'.farm_flex')
clearance_replay = importlib.import_module(MODULE+'.clearance_replay')
charts = importlib.import_module(MODULE+'.charts')
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-capture-window'))
OUT.mkdir(parents=True, exist_ok=True)
STATE = {'phase': 0, 'checks': {}}


def protected(scene, area):
    return {'frame': (scene.frame_current, scene.frame_subframe),
        'time_property': scene.get('wfrl_clearance_time_s'),
        'statistics': json.dumps(clearance_replay.sample(scene)['statistics'], sort_keys=True),
        'charts': repr(charts.history.__dict__),
        'telemetry': farm_flex.active_for(scene).last_telemetry_frame,
        'profile': preview.render_profile(scene),
        'shading': {name: getattr(area.spaces.active.shading, name) for name in
                    ('type', 'studio_light', 'use_scene_lights', 'use_scene_world')},
        'visibility': {obj.name: obj.hide_get() for obj in scene.objects}}


def run_capture(times, fresh=False):
    render_group = preview.render_group
    targets = []
    def per_group(context, cameras, **_kwargs):
        target = preview.RenderTarget()
        targets.append(target)
        try:
            return render_group(context, cameras, target=target)
        finally:
            target.free()
    if fresh:
        preview.render_group = per_group
    started = time.perf_counter()
    try:
        job = capture.Capture(bpy.context, OUT, times)
        while not job.closed:
            job.step(bpy.context)
    finally:
        preview.render_group = render_group
    duration = time.perf_counter()-started
    manifest = json.loads((job.path/'manifest.json').read_text())
    rows = [json.loads(line) for line in (job.path/'frames.jsonl').read_text().splitlines()]
    hashes = [hashlib.sha256((job.path/row['image_relative_path']).read_bytes()).hexdigest() for row in rows]
    usage = ({'allocations': sum(target.allocations for target in targets),
              'releases': sum(target.releases for target in targets), 'active_targets': 0}
             if fresh else manifest['gpu_target_usage'])
    assert manifest['status'] == 'complete'
    assert usage['allocations'] == usage['releases'] and usage['active_targets'] == 0
    return {'path': str(job.path), 'elapsed_s': duration, 'usage': usage,
            'png_hashes': hashes, 'state_hashes': [row['simulation_state_hash'] for row in rows]}


def tick():
    try:
        if STATE['phase'] == 0:
            expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
            if expected:
                assert Path(addon.__file__).resolve().parent == Path(expected).resolve(), addon.__file__
            addon.register()
            addon.load_demo_scene(camera_rig=False)
            window = bpy.context.window
            area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
            region = next(region for region in area.regions if region.type == 'WINDOW')
            STATE.update(window=window, area=area, region=region, phase=1)
            return .5
        with bpy.context.temp_override(window=STATE['window'], area=STATE['area'], region=STATE['region']):
            scene, area = bpy.context.scene, STATE['area']
            if STATE['phase'] == 1:
                scene.frame_set(755, subframe=.25)
                geometry = core._geometry(scene)[0]
                for slot, target, optics in zip(core.SLOTS,
                        ((0, -10, .2), (0, 10, .2), (0, 0, 10)), ((90, 60), (40, 100), (75, 75))):
                    point, normal, *_ = geometry[-1].find_nearest(Vector(target))
                    cam = core.begin_draft(scene, slot)
                    core.place_on_surface(cam, core.SurfaceAnchor(tuple(point), tuple(normal), .1, geometry[0]))
                    core.apply_parameters(cam, replace(core.parameters(cam), yaw=180, pitch=-50,
                        fov=optics[0], vfov=optics[1], output_long_edge_px=320))
                    core.commit_draft(scene, slot, cam)
                bpy.context.view_layer.update()
                assert not capture.preflight(bpy.context)['errors']
                STATE['original'] = protected(scene, area)
                at = capture.current_time(scene)
                STATE['times'] = [at, at+.05, at+.1]
                # Warm shader/texture loading before timing the two strategies.
                images = preview.render_group(bpy.context, core.enabled_cameras(scene))
                for image in images.values():
                    image.free()
                STATE['phase'] = 2
                return .2
            if STATE['phase'] == 2:
                baseline = run_capture(STATE['times'], fresh=True)
                pooled = run_capture(STATE['times'])
                assert baseline['usage']['allocations'] == 9, baseline
                assert pooled['usage']['allocations'] == 3, pooled
                assert baseline['state_hashes'] == pooled['state_hashes']
                assert baseline['png_hashes'] == pooled['png_hashes'], 'GPU pool changed image pixels'
                assert protected(scene, area) == STATE['original']
                STATE.update(baseline=baseline, pooled=pooled)
                STATE['checks']['three_sizes_three_samples_9_to_3_allocations_same_png_bytes'] = True
                STATE['phase'] = 3
                return .2
            if STATE['phase'] == 3:
                job = capture.Capture(bpy.context, OUT, STATE['times'])
                job.step(bpy.context)
                job.request_cancel()
                manifest = json.loads((job.path/'manifest.json').read_text())
                assert job.closed and job.index == 1 and manifest['status'] == 'incomplete'
                assert manifest['gpu_target_usage']['allocations'] == manifest['gpu_target_usage']['releases'] == 3
                assert protected(scene, area) == STATE['original']
                STATE['checks']['cancel_releases_and_restores_frame_statistics_quality'] = True
                job = capture.Capture(bpy.context, OUT, STATE['times'])
                job.step(bpy.context)
                original_snapshot = preview.CameraImage.snapshot
                def failure(_image, _offscreen):
                    raise RuntimeError('injected GPU readback failure')
                preview.CameraImage.snapshot = failure
                try:
                    job.step(bpy.context)
                except RuntimeError as exc:
                    assert 'injected GPU readback failure' in str(exc)
                else:
                    raise AssertionError('readback failure was not propagated')
                finally:
                    preview.CameraImage.snapshot = original_snapshot
                manifest = json.loads((job.path/'manifest.json').read_text())
                assert job.closed and manifest['status'] == 'incomplete' and job.index == 1
                assert manifest['gpu_target_usage']['allocations'] == manifest['gpu_target_usage']['releases'] == 3
                assert protected(scene, area) == STATE['original']
                STATE['checks']['gpu_failure_releases_and_restores_frame_statistics_quality'] = True
                STATE['phase'] = 4
                return .2
            if STATE['phase'] == 4:
                # A native edit after the first group invalidates the transaction
                # without replacing the user's edited position with saved XYZ.
                job = capture.Capture(bpy.context, OUT, STATE['times'])
                job.step(bpy.context)
                cam = core.get_camera(scene, 1)
                params = core.parameters(cam)
                cam.lock_location = (False, False, False)
                cam.location.z += .1
                edited_z = cam.location.z
                bpy.context.view_layer.update()
                try:
                    job.step(bpy.context)
                except RuntimeError as exc:
                    assert '原生相机' in str(exc)
                else:
                    raise AssertionError('native edit was accepted')
                assert cam.location.z == edited_z and job.closed and job.index == 1
                assert job.manifest['gpu_target_usage']['active_targets'] == 0
                core.apply_parameters(cam, params)
                cam.lock_location = (True, True, True)
                bpy.context.view_layer.update()
                assert protected(scene, area) == STATE['original']
                STATE['checks']['native_edit_aborts_without_overwriting_edit'] = True
                import gpu
                result = {'status': 'PASS', 'module': addon.__file__, 'blender': bpy.app.version_string,
                    'gpu': gpu.platform.renderer_get(), 'checks': STATE['checks'],
                    'fresh': STATE['baseline'], 'pooled': STATE['pooled'],
                    'measurement_boundary': 'isolated scripted GPU window at 320 px; elapsed times are observations'}
                (OUT/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
                print('CUSTOM_CAMERA_CAPTURE_WINDOW_PASS', flush=True)
                addon.unregister()
                bpy.ops.wm.quit_blender()
                return None
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        (OUT/'result.json').write_text(json.dumps({'status': 'FAIL', 'phase': STATE['phase'], 'error': error}, indent=2))
        try:
            capture.shutdown()
            addon.unregister()
        except Exception:
            pass
        bpy.ops.wm.quit_blender()
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.)
