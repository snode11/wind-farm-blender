"""FFmpeg resource ownership and GPU/viewport drawing for Down video."""
from contextlib import ExitStack, contextmanager
import os
import queue
import subprocess
import tempfile
import threading
import time

import bpy
import numpy as np

from . import video_output
from . import manual_down_motion as motion
from .preferences import get_preferences


VIDEO_SIZE = (1920, 1080)
VIDEO_SIZES = {'1080': (1920, 1080), '720': (1280, 720), '540': (960, 540)}

VIEWPORT_ENGINES = {'VIEWPORT_MATERIAL': 'MATERIAL', 'VIEWPORT_SOLID': 'SOLID'}
ENGINES = {'SCENE', 'WORKBENCH', *VIEWPORT_ENGINES}

# Mesh deformation changes the blade geometry every frame. Blender releases the
# superseded GPU buffers only when a window draws, which a blocking offscreen
# loop never does, so the loop redraws the window every _GPU_RELEASE_FRAMES.
_GPU_RELEASE_FRAMES = 15


def _release_gpu(job, frame):
    if job.get('dynamics_rig') and frame % _GPU_RELEASE_FRAMES == _GPU_RELEASE_FRAMES - 1:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def gpu_renderer():
    try:
        import gpu
        return gpu.platform.renderer_get()
    except Exception:  # The gpu module is unavailable in background mode.
        return 'unknown'


def is_integrated_gpu(renderer):
    name = renderer.lower()
    return any(key in name for key in ('intel', 'uhd graphics', 'iris', 'radeon(tm) graphics', 'llvmpipe'))


def ffmpeg_executable(context=None):
    """FFmpeg from the add-on video settings or PATH; None when unavailable."""
    prefs = get_preferences(context)
    configured = bpy.path.abspath(prefs.video_ffmpeg) if prefs and prefs.video_ffmpeg else ''
    try:
        return video_output.executable(configured, 'ffmpeg')
    except ValueError:
        return None


_ENCODER_TIMEOUT_S = 30.0
_ENCODER_EXIT_GRACE_S = 5.0
_ENCODER_LOG_TAIL = 600


def _encoder_error(code, log, failures):
    """Failure carrying FFmpeg's exit code and log tail; the pipe error stands in for an empty log."""
    size = log.seek(0, os.SEEK_END)
    log.seek(max(0, size - _ENCODER_LOG_TAIL))
    message = log.read().decode('utf-8', errors='replace').strip()
    detail = message or '；'.join(map(str, failures))
    return RuntimeError(f'FFmpeg 编码失败（{code}）' + (f'：{detail}' if detail else ''))


def _reap_encoders(job, timeout=.05):
    errors = []
    encoders = job.get('encoders', [])
    for resource in tuple(encoders):
        process, writer, log = resource['process'], resource.get('writer'), resource['log']
        resource['stopping'].set()
        try:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f'FFmpeg 进程未能结束：{exc}')
        try:
            if writer is not None and writer.ident is not None:
                writer.join(timeout=timeout)
                if writer.is_alive():
                    errors.append('FFmpeg 写帧线程未能结束')
                    continue
            if process is not None:
                if process.poll() is None:
                    continue
                process.stdin.close()
            log.close()
        except (OSError, RuntimeError) as exc:
            errors.append(str(exc))
            continue
        encoders.remove(resource)
    job['encoder_error'] = '；'.join(errors) or '等待 FFmpeg 资源释放'
    return not encoders


@contextmanager
def _offscreen_encoder(command, *, job):
    """Register process, writer and log with the export before starting encoding."""
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    pending, failures = queue.Queue(maxsize=4), []
    stopping = threading.Event()
    resource = dict(process=None, writer=None, log=tempfile.TemporaryFile(), stopping=stopping)
    job.setdefault('encoders', []).append(resource)
    log = resource['log']
    try:
        process = resource['process'] = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=log, stderr=log, **options)

        def write():
            try:
                with process.stdin as stream:
                    while not stopping.is_set():
                        try:
                            data = pending.get(timeout=.1)
                        except queue.Empty:
                            continue
                        if data is None:
                            return
                        stream.write(data)
            except OSError as exc:
                failures.append(exc)

        def send(data):
            deadline = time.monotonic() + _ENCODER_TIMEOUT_S
            while not failures:
                if stopping.is_set() or job.get('stop_requested'):
                    raise RuntimeError('Down 视频已请求停止')
                try:
                    pending.put(data, timeout=.1)
                    return
                except queue.Full:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('FFmpeg 写帧超时') from None
            try:
                code = process.wait(timeout=_ENCODER_EXIT_GRACE_S)
            except subprocess.TimeoutExpired:
                process.kill()
                code = process.wait(timeout=_ENCODER_EXIT_GRACE_S)
            raise _encoder_error(code, log, failures) from failures[0]

        writer = resource['writer'] = threading.Thread(target=write, name='wfrl-down-encoder', daemon=True)
        writer.start()
        yield send
        send(None)
        writer.join(timeout=_ENCODER_TIMEOUT_S)
        if writer.is_alive():
            raise RuntimeError('等待 FFmpeg 写帧结束超时')
        code = process.wait(timeout=_ENCODER_TIMEOUT_S)
        if code or failures:
            raise _encoder_error(code, log, failures)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('等待 FFmpeg 编码结束超时') from exc
    finally:
        # ExitStack closes each camera encoder separately; other cameras may still be draining.
        owner = dict(encoders=[resource])
        if _reap_encoders(owner, timeout=_ENCODER_EXIT_GRACE_S):
            job['encoders'].remove(resource)
        else:
            job['state'] = 'STOPPING'
            job['encoder_error'] = owner['encoder_error']
            raise RuntimeError('Down 视频停止中：' + owner['encoder_error'])


def _draw_offscreen(scene, job, view, camera, ffmpeg, output):
    _draw_offscreen_multi(scene, job, view, (camera,), ffmpeg, (output,))


def _draw_offscreen_multi(scene, job, view, cameras_list, ffmpeg, outputs):
    """Draw every camera at each frame and encode in parallel using a shared GPU buffer."""
    import gpu
    from . import manual_down_jobs as jobs
    window, area, region = view
    render, plan = scene.render, job['plan']
    width, height = render.resolution_x, render.resolution_y
    fps = str(plan['fps'])
    command = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{width}x{height}', '-r', fps, '-i', '-',
               '-vf', 'vflip', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
               '-g', fps, '-an', '-movflags', '+faststart']
    with ExitStack() as resources:
        offscreen = gpu.types.GPUOffScreen(width, height, format='RGBA8')
        resources.callback(offscreen.free)
        writers = [resources.enter_context(_offscreen_encoder([*command, str(path)], job=job)) for path in outputs]
        with bpy.context.temp_override(window=window, area=area, region=region, scene=scene):
            scene.frame_set(scene.frame_start)
            space, layer = area.spaces.active, bpy.context.view_layer
            depsgraph = bpy.context.evaluated_depsgraph_get()
            projections = [cam.calc_matrix_camera(depsgraph, x=width, y=height) for cam in cameras_list]
            for frame in range(plan['frames']):
                jobs._check_owner(job)
                if job.get('stop_requested'):
                    raise RuntimeError('Down 视频已请求停止')
                sample = motion.apply_motion(scene, job, frame)
                depsgraph.update()
                for camera, projection, write in zip(cameras_list, projections, writers):
                    view_matrix = camera.evaluated_get(depsgraph).matrix_world.inverted()
                    offscreen.draw_view3d(scene, layer, space, region, view_matrix, projection,
                                          do_color_management=True)
                    pixels = offscreen.texture_color.read()
                    pixels.dimensions = width * height * 4
                    write(np.asarray(pixels, dtype=np.uint8).tobytes())
                jobs._append_frame(job, frame, sample)
                _release_gpu(job, frame)
                bpy.context.window_manager.progress_update(frame)


def _view_area(context):
    """The 3D view that renders a viewport export: the calling area, else the largest one."""
    window = getattr(context, 'window', None) or bpy.context.window_manager.windows[0]
    area = getattr(context, 'area', None)
    if area is None or area.type != 'VIEW_3D':
        areas = [item for item in window.screen.areas if item.type == 'VIEW_3D']
        if not areas:
            raise ValueError('视口渲染需要一个三维视图')
        area = max(areas, key=lambda item: item.width * item.height)
    region = next(item for item in area.regions if item.type == 'WINDOW')
    return window, area, region


def _prepare_view(area, camera, shading):
    space = area.spaces.active
    region = space.region_3d
    saved = (space, dict(type=space.shading.type, world=space.shading.use_scene_world,
                         lights=space.shading.use_scene_lights, overlays=space.overlay.show_overlays,
                         camera=space.camera, local=space.use_local_camera,
                         perspective=region.view_perspective, zoom=region.view_camera_zoom,
                         offset=tuple(region.view_camera_offset)))
    try:
        space.use_local_camera = True
        space.camera = camera
        region.view_perspective = 'CAMERA'
        space.shading.type = shading
        if shading == 'MATERIAL':
            space.shading.use_scene_world = space.shading.use_scene_lights = True
        space.overlay.show_overlays = False
    except Exception:
        _restore_view(*saved)
        raise
    return saved


def _restore_view(space, saved, job=None):
    from . import manual_down_jobs as jobs
    try:
        jobs._check_owner(job)
        space.shading.type = saved['type']
        jobs._check_owner(job)
        space.shading.use_scene_world = saved['world']
        jobs._check_owner(job)
        space.shading.use_scene_lights = saved['lights']
        jobs._check_owner(job)
        space.overlay.show_overlays = saved['overlays']
        jobs._check_owner(job)
        space.use_local_camera = saved['local']
        jobs._check_owner(job)
        space.camera = saved['camera']
        region = space.region_3d
        jobs._check_owner(job)
        region.view_perspective = saved['perspective']
        jobs._check_owner(job)
        region.view_camera_zoom, region.view_camera_offset = saved['zoom'], saved['offset']
    except ReferenceError:
        pass
