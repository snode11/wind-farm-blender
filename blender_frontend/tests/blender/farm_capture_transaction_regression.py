"""Native RNA capture ownership with replay, image and playback adapters.

The check uses real Scenes, Windows and ViewLayers. It does not render GPU
pixels, run the formal FarmFlex deformation, or exercise native playback.
"""
import importlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path.insert(0, str(ROOT / 'blender_frontend'))
addon = importlib.import_module(MODULE)
capture = importlib.import_module(MODULE + '.custom_camera_capture')
core = importlib.import_module(MODULE + '.custom_cameras')
preview = importlib.import_module(MODULE + '.custom_camera_preview')
farm_flex = importlib.import_module(MODULE + '.farm_flex')
clearance = importlib.import_module(MODULE + '.clearance_replay')
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(addon.__file__).resolve().parent == Path(expected_root).resolve(), addon.__file__
output = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
output.mkdir(parents=True, exist_ok=False)

addon.register()
scene, window = bpy.context.scene, bpy.context.window
area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
region = next(region for region in area.regions if region.type == 'WINDOW')
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
root = bpy.data.objects.new(core.ROOT_NAME, None)
scene.collection.objects.link(root)
bpy.ops.mesh.primitive_cube_add(size=2)
shell = bpy.context.object
shell.name, shell.parent = core.SURFACE_NAMES[0], root
bpy.context.view_layer.update()
camera = core.begin_draft(scene, 1)
params = core.CameraParameters((0, 0, 1.1), 123, -45, 37, 90, 60, None, 640, .006, 2000)
core.apply_parameters(camera, params, core.validate_position(scene, params.location)[0])
core.commit_draft(scene, 1, camera)
capture_layer = window.view_layer
other_layer = scene.view_layers.new('OtherCaptureLayer')
shell.hide_set(True, view_layer=other_layer)
assert shell.hide_get(view_layer=other_layer)
assert not shell.hide_get(view_layer=capture_layer)
other_scene = bpy.data.scenes.new('OtherCaptureScene')

reader = SimpleNamespace(start_s=117., end_s=127.)
reader.at = lambda at: {'time_s': at, 'motion': {'yaw_deg': 0., 'azimuth_deg': 0., 'pitch_deg': 0.}}
evaluations, player_requests = [], []


def update(scene, *, sim_time_s, record_telemetry, update_comparison):
    assert not record_telemetry and not update_comparison
    evaluations.append(float(sim_time_s))
    scene['wfrl_clearance_time_s'] = float(sim_time_s)
    root.location.z = (sim_time_s - reader.start_s) * .1


farm = SimpleNamespace(times=(117., 127.), readers={'T1': reader}, path=output / 'replay-adapter', update=update)
saved = (farm_flex.active_for, farm_flex.is_active, clearance.reader_for,
         preview.render_group, capture.Capture._play, capture.playback.is_playing)
original_playing = False
render_mutation = None


def render(context, cameras, *, target, create_texture):
    global render_mutation
    assert context.scene == scene and context.view_layer == window.view_layer
    assert not create_texture and target is not None
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    images = {}
    for camera in cameras:
        image = preview.CameraImage.__new__(preview.CameraImage)
        image.params = core.parameters(camera)
        image.width, image.height, image.view, image.projection, image.world = preview.matrices(
            camera, depsgraph, image.params.output_long_edge_px)
        image.state_hash = 'explicit-image-adapter'
        image.texture = None
        image.pixels = bytes((17, 91, 143, 255)) * (image.width * image.height)
        images[int(camera['wfrl_custom_slot'])] = image
    mutation, render_mutation = render_mutation, None
    if mutation:
        mutation()
    return images


def player(job, playing):
    player_requests.append((job.capture_id, bool(playing)))


def prepared():
    global capture_layer
    window.scene = scene
    if not any(layer.name == 'ViewLayer' for layer in scene.view_layers):
        capture_layer = scene.view_layers.new('ViewLayer')
    else:
        capture_layer = scene.view_layers['ViewLayer']
    window.view_layer = capture_layer
    scene.frame_start = 301
    scene['wfrl_clearance_timebase_fps'] = 60.
    with capture.readonly_frame_handlers():
        scene.frame_set(451, subframe=0.)
        update(scene, sim_time_s=119.5, record_telemetry=False, update_comparison=False)
    capture_layer.update()


def first_sample(job, context):
    assert not job.step(context)
    deadline = time.monotonic() + 5
    while job.index == 0:
        assert time.monotonic() < deadline, 'PNG writer timed out'
        time.sleep(.001)
        assert not job.step(context)
    assert job.index == 1
    records = (job.path / 'frames.jsonl').read_bytes()
    assert len(records.splitlines()) == 1
    assert json.loads(records)['scene_frame'] == 451
    assert (job.path / 'C1/frame_000000.png').is_file()
    return records


checks = []
try:
    farm_flex.active_for = lambda target: farm if target == scene else None
    farm_flex.is_active = lambda target: target == scene
    clearance.reader_for = lambda target: reader if target == scene else None
    preview.render_group = render
    capture.Capture._play = player
    capture.playback.is_playing = lambda target: target == scene and original_playing
    for original_playing in (False, True):
        for action in ('success', 'cancel', 'frame_start', 'timebase', 'removed_timebase', 'layer', 'delete_layer',
                       'scene', 'foreign_context', 'render_layer', 'render_timebase'):
            prepared()
            start = len(evaluations)
            optical_state = (core.parameters(camera), camera.data.lens, scene.render.resolution_x,
                             scene.render.resolution_y, scene.render.resolution_percentage)
            with bpy.context.temp_override(window=window, area=area, region=region):
                job = capture.Capture(bpy.context, output, [119.5, 120.])
                assert job.farm_sequence and job.view_layer == capture_layer
                assert job.time_mapping == (301, 60., 117.)
                assert job.playing is original_playing
                assert job.same_session() and job._clock_unchanged()
                committed = first_sample(job, bpy.context)
                if action == 'success':
                    assert not job.step(bpy.context)
                    deadline = time.monotonic() + 5
                    while not job.closed:
                        assert time.monotonic() < deadline, 'capture completion timed out'
                        time.sleep(.001)
                        job.step(bpy.context)
                elif action == 'cancel':
                    job.request_cancel()
                else:
                    context = bpy.context
                    if action == 'frame_start':
                        assert clearance.sample(scene)['time_s'] == 119.5
                        scene.frame_start = 1
                        assert clearance.sample(scene)['time_s'] == 124.5
                    elif action == 'timebase':
                        scene['wfrl_clearance_timebase_fps'] = 30.
                    elif action == 'removed_timebase':
                        del scene['wfrl_clearance_timebase_fps']
                        assert clearance.sample(scene)['time_s'] != 119.5
                    elif action == 'layer':
                        window.view_layer = other_layer
                    elif action == 'delete_layer':
                        scene.view_layers.remove(capture_layer)
                    elif action == 'scene':
                        window.scene = other_scene
                    elif action == 'foreign_context':
                        context = SimpleNamespace(window=None, scene=scene, area=area, view_layer=capture_layer)
                    elif action == 'render_layer':
                        render_mutation = lambda: setattr(window, 'view_layer', other_layer)
                    else:
                        render_mutation = lambda: scene.__setitem__('wfrl_clearance_timebase_fps', 30.)
                    selection = (window.scene, window.view_layer, scene.frame_start,
                                 scene.get('wfrl_clearance_timebase_fps'))
                    try:
                        job.step(context)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError(f'{action} did not invalidate capture')
                    if action not in {'render_layer', 'render_timebase'}:
                        assert selection == (window.scene, window.view_layer, scene.frame_start,
                                             scene.get('wfrl_clearance_timebase_fps'))
                    assert (job.path / 'frames.jsonl').read_bytes() == committed
                    assert not (job.path / 'C1/frame_000001.png').exists()
                    assert job.index == 1
                assert job.closed and not capture.active()
                manifest = json.loads((job.path / 'manifest.json').read_text())
                assert manifest['status'] == ('complete' if action == 'success' else 'incomplete')
                assert manifest['completed_samples'] == (2 if action == 'success' else 1)
                requests = [playing for identity, playing in player_requests if identity == job.capture_id]
                assert requests == ([False, False, original_playing] if action in {'success', 'cancel'} else [False])
                if action in {'success', 'cancel'}:
                    assert (scene.frame_current, scene.frame_subframe) == (451, 0.)
                    assert scene.get('wfrl_clearance_time_s') == 119.5
                assert optical_state == (core.parameters(camera), camera.data.lens, scene.render.resolution_x,
                                         scene.render.resolution_y, scene.render.resolution_percentage)
                checks.append({'action': action, 'original_playing': original_playing,
                               'status': manifest['status'], 'completed_samples': manifest['completed_samples'],
                               'playback_requests': requests, 'evaluations': evaluations[start:],
                               'capture_directory': str(job.path)})
    for during_restore in (False, True):
        for jump_subframe in (False, True):
            prepared()
            original_playing = True
            with bpy.context.temp_override(window=window, area=area, region=region):
                job = capture.Capture(bpy.context, output, [119.5, 120.])
                first_sample(job, bpy.context)
                fired = [False]
                target_frame = 451 if during_restore else 481
                external_frame = target_frame if jump_subframe else 501
                external_subframe = .75 if jump_subframe else 0.
                def external_seek(changed_scene, *_args):
                    if changed_scene == scene and not fired[0] and scene.frame_current == target_frame:
                        fired[0] = True
                        scene.frame_set(external_frame, subframe=external_subframe)
                bpy.app.handlers.frame_change_post.append(external_seek)
                try:
                    deadline = time.monotonic() + 5
                    while not job.closed:
                        assert time.monotonic() < deadline
                        try:
                            job.step(bpy.context)
                        except RuntimeError:
                            assert job.closed
                        time.sleep(.001)
                finally:
                    bpy.app.handlers.frame_change_post.remove(external_seek)
                assert fired[0]
                assert (scene.frame_current, scene.frame_subframe) == (external_frame, external_subframe)
                manifest = json.loads((job.path / 'manifest.json').read_text())
                assert manifest['status'] == 'incomplete'
                assert manifest['completed_samples'] == (2 if during_restore else 1)
                requests = [playing for identity, playing in player_requests if identity == job.capture_id]
                assert requests == ([False, False] if during_restore else [False])
                assert len((job.path / 'frames.jsonl').read_text().splitlines()) == (2 if during_restore else 1)
                checks.append(dict(action='native_frame_callback', during_restore=during_restore,
                                   subframe_only=jump_subframe, status=manifest['status'],
                                   completed_samples=manifest['completed_samples']))
finally:
    if capture.active():
        capture.shutdown(restore=False)
    (farm_flex.active_for, farm_flex.is_active, clearance.reader_for,
     preview.render_group, capture.Capture._play, capture.playback.is_playing) = saved
    window.scene = scene
    addon.unregister()

(output / 'result.json').write_text(json.dumps({
    'status': 'PASS', 'blender': bpy.app.version_string, 'module': addon.__file__,
    'scope': 'real RNA ownership and capture transaction; replay, image and playback adapters; no GPU proof',
    'checks': checks}, ensure_ascii=False, indent=2) + '\n')
print('FARM_CAPTURE_TRANSACTION_REGRESSION_PASS', len(checks), flush=True)
