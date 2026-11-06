"""Down viewport rendering and view-state restoration."""
import os
import queue
import subprocess
import tempfile
import threading

import bpy
import numpy as np
from . import manual_down as _down


def _draw_offscreen(scene, job, view, camera, ffmpeg, output):
    """Draw each isolated frame offscreen and pipe raw RGBA to FFmpeg."""
    import gpu
    window, area, region = view
    render, plan = scene.render, job['plan']
    width, height = render.resolution_x, render.resolution_y
    command = [ffmpeg, '-n', '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{width}x{height}', '-r', str(plan['fps']), '-i', '-',
               '-vf', 'vflip', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
               '-g', str(plan['fps']), '-an', '-movflags', '+faststart', output]
    log = tempfile.TemporaryFile()
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log, **options)
    frames, failure = queue.Queue(maxsize=4), []

    def write():
        try:
            while (data := frames.get()) is not None:
                process.stdin.write(data)
        except OSError as exc:
            failure.append(exc)
            while frames.get() is not None:
                pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    offscreen = None
    try:
        offscreen = gpu.types.GPUOffScreen(width, height, format='RGBA8')
        with bpy.context.temp_override(window=window, area=area, region=region, scene=scene):
            space, layer = area.spaces.active, bpy.context.view_layer
            depsgraph = bpy.context.evaluated_depsgraph_get()
            projection = camera.calc_matrix_camera(depsgraph, x=width, y=height)
            for frame in range(plan['frames']):
                if failure:
                    break
                _down.apply_frame(scene, plan, frame / plan['fps'], labels=job['labels'])
                depsgraph.update()
                view_matrix = camera.evaluated_get(depsgraph).matrix_world.inverted()
                offscreen.draw_view3d(scene, layer, space, region, view_matrix, projection,
                                      do_color_management=True)
                _down._check_export_camera(scene, job, frame=frame + 1, depsgraph=depsgraph)
                pixels = offscreen.texture_color.read()
                pixels.dimensions = width * height * 4
                frames.put(np.asarray(pixels, dtype=np.uint8).tobytes())
                bpy.context.window_manager.progress_update(frame)
    finally:
        try:
            frames.put(None, timeout=5)
        except queue.Full:
            process.terminate()
            while True:
                try:
                    frames.get_nowait()
                except queue.Empty:
                    break
            frames.put_nowait(None)
        writer.join(timeout=5)
        if writer.is_alive():
            process.terminate()
            writer.join(timeout=5)
        if offscreen is not None:
            offscreen.free()
        try:
            code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait(timeout=5)
        log.seek(0)
        message = log.read().decode('utf-8', errors='replace')[-600:]
        log.close()
    if failure or code:
        raise RuntimeError(f'FFmpeg 编码失败（{code}）：{message or failure}')


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
    saved = (space, dict(type=space.shading.type, world=space.shading.use_scene_world,
                         lights=space.shading.use_scene_lights, overlays=space.overlay.show_overlays,
                         camera=space.camera, local=space.use_local_camera,
                         perspective=space.region_3d.view_perspective))
    space.use_local_camera = True
    space.camera = camera
    space.region_3d.view_perspective = 'CAMERA'
    space.shading.type = shading
    if shading == 'MATERIAL':
        space.shading.use_scene_world = space.shading.use_scene_lights = True
    space.overlay.show_overlays = False
    return saved


def _restore_view(space, saved):
    try:
        space.shading.type = saved['type']
        space.shading.use_scene_world = saved['world']
        space.shading.use_scene_lights = saved['lights']
        space.overlay.show_overlays = saved['overlays']
        space.camera = saved['camera']
        space.use_local_camera = saved['local']
        space.region_3d.view_perspective = saved['perspective']
    except ReferenceError:
        pass
