"""Down export renders its own camera despite camera-bound markers; annotation exclusion restores."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import manual_down as down, custom_camera_preview as preview
from wfrl_blender import manual_down_jobs as jobs, manual_down_motion as motion, manual_down_render as rendering

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
assert bpy.app.background


def check_encoder_startup_cleanup(scene, ffmpeg):
    """Exercise encoder ownership on GPU allocation failure and partial FFmpeg launch."""
    popen = subprocess.Popen
    for allocation_fails in (True, False):
        started, freed = [], []

        def allocate(*_args, **_kwargs):
            if allocation_fails:
                raise RuntimeError('GPU allocation failed')
            return SimpleNamespace(free=lambda: freed.append(True))

        def launch(*args, **kwargs):
            if started:
                raise OSError('second encoder failed')
            process = popen(*args, **kwargs)
            started.append(process)
            return process

        gpu = SimpleNamespace(types=SimpleNamespace(GPUOffScreen=allocate))
        job = {'plan': {'fps': 25}}
        view = (None, None, None)
        try:
            with patch.dict(sys.modules, gpu=gpu), patch.object(rendering.subprocess, 'Popen', launch):
                try:
                    if allocation_fails:
                        down._draw_offscreen(scene, job, view, None, ffmpeg, str(OUT / 'gpu.mp4'))
                    else:
                        down._draw_offscreen_multi(scene, job, view, (None, None), ffmpeg,
                                                  [str(OUT / 'first.mp4'), str(OUT / 'second.mp4')])
                except (RuntimeError, OSError) as exc:
                    assert ('GPU allocation failed' if allocation_fails else 'second encoder failed') in str(exc)
                else:
                    raise AssertionError('Encoder startup should have failed')
            assert all(process.poll() is not None for process in started), 'FFmpeg survived failed startup'
            if not allocation_fails:
                assert len(started) == 1 and freed == [True]
        finally:
            for process in started:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


addon.register()
try:
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    scene = bpy.context.scene
    # Export camera shake scales with RPM and takes a random phase per export; at 0 RPM
    # without structural motion two exports of the same pose are pixel-identical.
    for entry in scene.wfrl_manual_turbines:
        entry.rpm = 0.0
    intended = down.ensure_down_camera(scene, 'T1', reset=True)
    extra = bpy.data.objects.new('Contract alternate camera', bpy.data.cameras.new('Contract alternate optics'))
    scene.collection.objects.link(extra)
    extra.matrix_world = intended.matrix_world.copy()
    extra.data.lens = 50
    markers = [scene.timeline_markers.new('Camera '+str(frame), frame=frame) for frame in (1, 2)]
    for marker in markers:
        marker.camera = extra
    scene.frame_set(2, subframe=.375)
    scene.camera = intended
    scene.frame_step = 2

    def export(name, engine='WORKBENCH'):
        target = OUT/(name+'.mp4')
        down.export_down_video(scene, 'T1', str(target), fps=25, frames=2, engine=engine,
                               blocking=True, size=(64, 64), samples=1, labels=False, dynamics=False)
        data = json.loads(target.with_suffix('.json').read_text(encoding='utf-8'))
        assert data['optics']['lens_mm'] == 8, data['optics']
        return target

    marked = export('markers')
    assert all(marker.camera == extra for marker in markers)
    assert scene.camera == intended and extra.data.lens == 50
    assert (scene.frame_current, scene.frame_subframe) == (2, .375)
    assert scene.frame_step == 2
    for marker in markers:
        marker.camera = None
    plain = export('plain')
    ffmpeg = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
    pixels = [subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-f', 'rawvideo',
                             '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
              for path in (marked, plain)]
    assert len(pixels[0]) == 2 * 64 * 64 * 3 and pixels[0] == pixels[1]

    # Markers edited while an interactive render runs: bindings follow name and frame, a moved
    # marker keeps its camera by name, and a marker added meanwhile stays unbound.
    for marker in markers:
        marker.camera = extra
    third = scene.timeline_markers.new('Camera 3', frame=3)
    third.camera = intended
    saved = down._snapshot(scene)
    down._configure_video(scene, intended, 25, 2, 'WORKBENCH', str(OUT / 'edit_'), size=(64, 64), samples=1)
    assert all(marker.camera is None for marker in scene.timeline_markers)
    scene.timeline_markers.remove(markers[0])
    markers[1].frame = 5
    added = scene.timeline_markers.new('Added during render', frame=4)
    down._restore(scene, saved)
    bound = {marker.name: (marker.frame, marker.camera) for marker in scene.timeline_markers}
    assert bound == {'Camera 2': (5, extra), 'Camera 3': (3, intended), 'Added during render': (4, None)}, bound
    for marker in tuple(scene.timeline_markers):
        scene.timeline_markers.remove(marker)

    rotor = scene.wfrl_manual_turbines[0].rotor_object
    rotor.driver_add('rotation_euler', 0).driver.expression = '1.234'
    try:
        export('conflicting-driver')
    except ValueError as exc:
        assert '驱动' in str(exc) and rotor.name in str(exc)
    else:
        raise AssertionError('A native driver can override the exported rotor trajectory')
    finally:
        rotor.driver_remove('rotation_euler', 0)
    assert not (OUT / 'conflicting-driver.mp4').exists()

    scene.render.engine = 'BLENDER_WORKBENCH'
    export('eevee', engine='SCENE')
    eevee = json.loads((OUT / 'eevee.json').read_text(encoding='utf-8'))
    assert eevee['render_engine'] == 'BLENDER_EEVEE', eevee['render_engine']
    assert scene.render.engine == 'BLENDER_WORKBENCH'

    scene.render.use_multiview = True
    scene.render.views_format = 'MULTIVIEW'
    scene.render.image_settings.views_format = 'INDIVIDUAL'
    assert export('from-multiview').is_file()
    assert scene.render.use_multiview and scene.render.views_format == 'MULTIVIEW'
    assert scene.render.image_settings.views_format == 'INDIVIDUAL'
    scene.render.use_multiview = False

    render = scene.render
    render.use_border = render.use_crop_to_border = True
    render.border_min_x = render.border_min_y = .25
    render.border_max_x = render.border_max_y = .75
    render.image_settings.color_depth = '16'
    for engine in ('WORKBENCH', 'SCENE'):
        path = export('full-frame-' + engine, engine=engine)
        frame_bytes = subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-f', 'rawvideo',
                                      '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
        metadata = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        assert len(frame_bytes) == 2 * 64 * 64 * 3, (engine, len(frame_bytes))
        assert metadata['resolution'] == [64, 64], metadata['resolution']
        assert render.use_border and render.use_crop_to_border
        assert (render.border_min_x, render.border_min_y, render.border_max_x, render.border_max_y) == (.25, .25, .75, .75)
        assert render.image_settings.color_depth == '16', render.image_settings.color_depth

    saved = down._snapshot(scene)
    scratch = Path(tempfile.gettempdir())
    old_temps = set(scratch.glob('wfrl_down_*'))
    destination = OUT / 'failed-pose.mp4'
    sidecar = destination.with_suffix('.json')
    destination.write_bytes(b'previous video')
    sidecar.write_bytes(b'previous samples')
    apply_motion = down.apply_motion

    def fail_pose(render_scene, job, frame):
        if frame == 1:
            raise RuntimeError('controlled pose failure')
        return apply_motion(render_scene, job, frame)

    with patch.object(motion, 'apply_motion', fail_pose):
        try:
            export('failed-pose')
        except RuntimeError as exc:
            assert 'controlled pose failure' in str(exc), exc
        else:
            raise AssertionError('A failed frame was published as a successful Down video')
    assert destination.read_bytes() == b'previous video' and sidecar.read_bytes() == b'previous samples'
    assert down._snapshot(scene) == saved and not jobs._EXPORT
    assert set(scratch.glob('wfrl_down_*')) == old_temps
    retry = export('failed-pose').with_suffix('.json')
    retry_data = json.loads(retry.read_text(encoding='utf-8'))
    assert [row['frame'] for row in retry_data['samples']] == [0, 1]

    for existing in (False, True):
        name = 'competing-existing' if existing else 'competing-absent'
        target = OUT / (name + '.mp4')
        sidecar = target.with_suffix('.json')
        if existing:
            target.write_bytes(b'authorized previous video')
            sidecar.write_bytes(b'authorized previous samples')
        external_movie = b'external video written while rendering'
        external_json = b'{"external":true}\n'
        observed = []

        def write_during_render(render_scene, *_unused):
            if render_scene != scene or observed:
                return
            for path, data in ((target, external_movie), (sidecar, external_json)):
                incoming = path.with_name(path.name + '.external')
                incoming.write_bytes(data)
                os.replace(incoming, path)
            observed.append(render_scene.frame_current)

        bpy.app.handlers.render_write.append(write_during_render)
        try:
            try:
                export(name)
            except RuntimeError as exc:
                assert '视频输出目标' in str(exc), exc
            else:
                raise AssertionError('Down video replaced a file written during render')
        finally:
            bpy.app.handlers.render_write.remove(write_during_render)
        assert observed == [1]
        assert target.read_bytes() == external_movie and sidecar.read_bytes() == external_json
        assert down._snapshot(scene) == saved and not jobs._EXPORT
        assert not scene.get('wfrl_manual_last_video')
        assert set(scratch.glob('wfrl_down_*')) == old_temps and not list(OUT.glob('.wfrl-down-*'))

    initial_cwd, later_cwd = OUT / 'initial-cwd', OUT / 'later-cwd'
    initial_cwd.mkdir()
    later_cwd.mkdir()
    cwd_before_export = Path.cwd()

    def change_cwd(render_scene, *_unused):
        if render_scene == scene:
            os.chdir(later_cwd)

    bpy.app.handlers.render_write.append(change_cwd)
    try:
        os.chdir(initial_cwd)
        written = down.export_down_video(scene, 'T1', 'relative.mp4', fps=25, frames=2, engine='WORKBENCH',
                                         blocking=True, size=(64, 64), samples=1, labels=False, dynamics=False)
        assert Path.cwd() == later_cwd.resolve()
        assert Path(written) == (initial_cwd / 'relative.mp4').resolve()
    finally:
        os.chdir(cwd_before_export)
        bpy.app.handlers.render_write.remove(change_cwd)
    assert sorted(path.name for path in initial_cwd.iterdir()) == ['relative.json', 'relative.mp4']
    assert not list(later_cwd.iterdir())

    frame_written = down._write_frame_handler

    def miss_last_write(render_scene, depsgraph=None):
        if render_scene.frame_current != 2:
            frame_written(render_scene, depsgraph)

    with patch.object(jobs, '_write_frame_handler', miss_last_write):
        try:
            export('missing-write')
        except RuntimeError as exc:
            assert '1/2' in str(exc), exc
        else:
            raise AssertionError('An incomplete sample stream was published')
    assert not (OUT / 'missing-write.mp4').exists()
    assert miss_last_write not in bpy.app.handlers.render_write

    rejected = SimpleNamespace(render=SimpleNamespace(render=lambda *_args, **_kwargs: {'CANCELLED'}))
    native = SimpleNamespace(ops=rejected, context=bpy.context, data=bpy.data, app=bpy.app, path=bpy.path)
    with patch.object(jobs, 'bpy', native):
        try:
            down.export_down_video(scene, 'T1', str(OUT / 'cancelled.mp4'), engine='SCENE',
                                   blocking=False, frames=2, size=(96, 54), labels=False, dynamics=False)
        except RuntimeError:
            pass
        else:
            raise AssertionError('A rejected render invocation was treated as an active job')
    assert not scene.get('wfrl_manual_exporting') and scene.as_pointer() not in jobs._EXPORT
    assert down._snapshot(scene) == saved
    assert set(scratch.glob('wfrl_down_*')) == old_temps
    assert down._frame_handler not in bpy.app.handlers.frame_change_pre
    assert down._render_complete not in bpy.app.handlers.render_complete
    assert not (OUT / 'cancelled.mp4').exists()

    center = OUT / 'take_left.mp4'
    native = SimpleNamespace(app=SimpleNamespace(background=False), path=bpy.path)
    with patch.object(jobs, 'bpy', native), patch.object(rendering, '_view_area', side_effect=AssertionError):
        try:
            down.export_triple_down_video(scene, 'T1', [center, center, OUT / 'take_right.mp4'])
        except ValueError:
            pass
        else:
            raise AssertionError('Three-camera export should reject repeated targets before touching the view')
    assert down._snapshot(scene) == saved
    check_encoder_startup_cleanup(scene, ffmpeg)

    layer = scene.view_layers[0]
    excluded = bpy.data.collections.new('Excluded contract annotations')
    scene.collection.children.link(excluded)
    hidden = bpy.data.objects.new('WFRL.Grid.ExcludedContract', None)
    excluded.objects.link(hidden)
    layer.layer_collection.children[excluded.name].exclude = True
    visible = bpy.data.objects.new('WFRL.Grid.VisibleContract', None)
    existing = bpy.data.objects.new('WFRL.Grid.HiddenContract', None)
    scene.collection.objects.link(visible)
    scene.collection.objects.link(existing)
    layer.update()
    existing.hide_set(True, view_layer=layer)
    for body_raises in (False, True):
        try:
            with preview.without_annotations(scene, layer):
                assert visible.hide_get(view_layer=layer) and existing.hide_get(view_layer=layer)
                assert hidden.name not in layer.objects
                if body_raises:
                    raise ValueError('controlled body failure')
        except ValueError as exc:
            assert body_raises and str(exc) == 'controlled body failure'
        assert not visible.hide_get(view_layer=layer) and existing.hide_get(view_layer=layer)
        assert layer.layer_collection.children[excluded.name].exclude
    result = dict(status='PASS', blender=bpy.app.version_string, module=down.__file__,
        marker_pixels_equal=True, decoded_sha256=hashlib.sha256(pixels[0]).hexdigest(),
        annotations_restored=True, camera_and_subframe_restored=True, native_driver_rejected=True,
        eevee_selected=True, multiview_restored=True, full_frame_and_image_depth_restored=True,
        failed_frame_kept_previous=True, concurrent_targets_preserved=True, relative_output_kept_original_cwd=True,
        rejected_render_released=True, duplicate_targets_rejected=True,
        encoder_startup_released=True,
        source_sha256={name:hashlib.sha256((ROOT/'blender_frontend/wfrl_blender'/name).read_bytes()).hexdigest()
                       for name in ('manual_down.py','video_output.py','custom_camera_preview.py')})
    (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    print('CAMERA_EXPORT_CONTRACT_PASS', flush=True)
finally:
    addon.unregister()
