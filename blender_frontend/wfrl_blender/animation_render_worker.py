"""Render a frozen scene in an independent Blender process."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import struct
import sys
import traceback
import uuid
import zlib


def _sync_png(path):
    """Check complete PNG chunks and sync the staged file before publication."""
    identity = path.lstat()
    if not stat.S_ISREG(identity.st_mode):
        raise RuntimeError(f'渲染帧不是普通 PNG 文件：{path.name}')
    with path.open('r+b') as stream:
        current = os.fstat(stream.fileno())
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise RuntimeError(f'渲染帧文件已被替换：{path.name}')
        if stream.read(8) != b'\x89PNG\r\n\x1a\n':
            raise RuntimeError(f'渲染帧不是完整 PNG：{path.name}')
        first, has_data = True, False
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise RuntimeError(f'PNG 缺少完整结束块：{path.name}')
            length, kind = struct.unpack('!I4s', header)
            if first and (kind != b'IHDR' or length != 13):
                raise RuntimeError(f'PNG 缺少图像头：{path.name}')
            if length > current.st_size - stream.tell() - 4:
                raise RuntimeError(f'PNG 数据块不完整：{path.name}')
            crc, remaining = zlib.crc32(kind), length
            while remaining:
                data = stream.read(min(remaining, 1024 * 1024))
                if not data:
                    raise RuntimeError(f'PNG 数据块不完整：{path.name}')
                crc = zlib.crc32(data, crc)
                remaining -= len(data)
            if stream.read(4) != struct.pack('!I', crc & 0xffffffff):
                raise RuntimeError(f'PNG 数据块校验失败：{path.name}')
            first = False
            has_data |= kind == b'IDAT' and length > 0
            if kind == b'IEND':
                if length or not has_data or stream.read(1):
                    raise RuntimeError(f'PNG 结束块或图像数据无效：{path.name}')
                os.fsync(stream.fileno())
                return identity


def _publish_frame(paths, publish):
    """Publish all validated views; roll back only this frame's owned files."""
    checked = []
    for source, destination in paths:
        if os.path.lexists(destination):
            raise FileExistsError(f'渲染帧已存在：{destination.name}')
        checked.append((source, destination, _sync_png(source)))
    attempted = []
    try:
        for source, destination, identity in checked:
            attempted.append((destination, identity))
            publish(source, destination)
    except BaseException:
        for destination, identity in attempted:
            try:
                current = destination.lstat()
            except FileNotFoundError:
                continue
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                destination.unlink()
        raise


def _cleanup_temporary(output, prefix):
    errors = []
    for path in output.glob(prefix + '*'):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f'{path.name}: {exc}')
    if errors:
        raise OSError('无法清理临时渲染文件：' + '; '.join(errors))


def run(job_path):
    import bpy
    job_path = Path(job_path).resolve()
    job = json.loads(job_path.read_text(encoding='utf-8'))
    root = job_path.parent
    addon_root = Path(job['addon_root'])
    project = addon_root.parent.parent
    if (project / 'wfrl/lidar/replay.py').is_file():
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(project))
    spec = importlib.util.spec_from_file_location('_wfrl_animation_worker',
        addon_root / '__init__.py', submodule_search_locations=[str(addon_root)])
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    from importlib import import_module
    service = import_module(spec.name + '.animation_render')
    record = dict(schema='wfrl.animation-render.v1', job_id=job['job_id'], state='RUNNING',
                  completed_frames=0, total_frames=job['total_frames'], worker_pid=os.getpid(),
                  worker_identity=service._pid_identity(os.getpid()))
    manifest = root / 'manifest.json'
    written = {}
    try:
        requested = (job['frame_start'], job['frame_end'], job['frame_step'])
        if (any(type(value) is not int for value in requested)
                or requested[2] < 1 or requested[1] < requested[0]):
            raise ValueError('无效的动画帧范围或步长')
        frames = range(requested[0], requested[1] + 1, requested[2])
        if type(job['total_frames']) is not int or len(frames) != job['total_frames']:
            raise ValueError('动画范围与作业帧数不一致')
        addon.register()
        bpy.ops.wm.open_mainfile(filepath=job['snapshot'])
        scene = bpy.data.scenes.get(job['scene_name'])
        if scene is None or scene.camera is None:
            raise ValueError('独立渲染场景或相机不可用')
        service.validate_output_nodes(scene)
        if scene.get('wfrl_scene_kind') == 'clearance_replay':
            replay = import_module(spec.name + '.clearance_replay')
            if replay.reader_for(scene) is None:
                raise ValueError(scene.get('wfrl_clearance_status', '回放数据恢复失败'))
            if scene.get('wfrl_farm_flex_path'):
                farm = import_module(spec.name + '.farm_flex')
                if not farm.is_active(scene):
                    raise ValueError(scene.get('wfrl_clearance_status', '回放数据恢复失败'))
        control = import_module(spec.name + '.manual_control')
        control.pause_all()
        packing, views = service.png_output_views(scene)
        scene.render.image_settings.file_format = 'PNG'
        resolution_keys = {'resolution_x', 'resolution_y', 'resolution_percentage'}
        for name, settings in job.get('render_settings', {}).items():
            target = scene.render.image_settings if name == 'image_settings' else getattr(scene, name)
            for key, value in settings.items():
                if name == 'render' and key in resolution_keys:
                    if type(value) is not int or value <= 0:
                        raise ValueError(f'无效的渲染分辨率：{key}')
                if name == 'image_settings' and key == 'color_depth':
                    value = service.png_color_depth(value)
                setattr(target, key, value)
                if name == 'render' and key in resolution_keys and getattr(target, key) != value:
                    raise ValueError(f'渲染分辨率超出 Blender 支持范围：{key}')
        for key in resolution_keys:
            value = getattr(scene.render, key)
            if type(value) is not int or value <= 0:
                raise ValueError(f'无效的渲染分辨率：{key}')
        scene.frame_start, scene.frame_end, scene.frame_step = requested
        if (scene.frame_start, scene.frame_end, scene.frame_step) != requested:
            raise ValueError('动画帧范围或步长超出 Blender 支持范围')
        output = root / 'frames'
        output.mkdir()
        temporary_prefix = f'.wfrl-{uuid.uuid4().hex}-'
        scene.render.filepath = str(output / (temporary_prefix + 'frame_'))
        scene.render.image_settings.file_format = 'PNG'
        scene.render.image_settings.views_format = packing
        scene.render.use_file_extension = True
        scene.render.use_overwrite = False
        scene.render.use_placeholder = False
        info = import_module(spec.name + '.frontend_diagnostics').build_information()
        record.update(source=info, scene_name=scene.name, camera=scene.camera.name,
                      frame_range=[scene.frame_start, scene.frame_end, scene.frame_step],
                      fps=scene.render.fps, fps_base=scene.render.fps_base,
                      exposure=scene.view_settings.exposure)
        if views:
            record['views'] = list(views)
        service.write_json(manifest, record)
        publish = import_module(spec.name + '.video_output')._commit_export
        written_paths = set()
        callback_error = None
        cancel_signal = None

        def cancel(signum, _frame):
            nonlocal cancel_signal
            cancel_signal = signum
            if callback_error is not None:
                raise callback_error
            raise SystemExit(128 + signum)

        def frame_written(render_scene, *_args):
            nonlocal callback_error
            if render_scene != scene:
                return
            if callback_error is not None:
                return
            try:
                if cancel_signal is not None:
                    raise SystemExit(128 + cancel_signal)
                frame = scene.frame_current
                if frame not in frames or frame in written:
                    raise RuntimeError(f'收到非预期的渲染帧：{frame}')
                files, paths = {}, []
                for view in views or ('',):
                    path = Path(scene.render.frame_path(frame=frame, view=view))
                    if (path.parent != output or not path.name.startswith(temporary_prefix)
                            or not path.is_file()):
                        raise RuntimeError(f'渲染帧未写入预期文件：{path.name}')
                    destination = output / path.name[len(temporary_prefix):]
                    if destination in written_paths or destination in (item[1] for item in paths):
                        raise RuntimeError(f'收到重复的渲染文件：{destination.name}')
                    paths.append((path, destination))
                    files[view] = destination.relative_to(root).as_posix()
                _publish_frame(paths, publish)
                written[frame] = dict(frame=frame, views=files) if views else dict(frame=frame, file=files[''])
                written_paths.update(destination for _, destination in paths)
                record.update(completed_frames=len(written), last_frame=frame)
                service.write_json(manifest, record)
                for path, _ in paths:
                    path.unlink(missing_ok=True)
            except BaseException as exc:
                # Blender may report handler errors without propagating them to the operator.
                callback_error = exc
                if isinstance(exc, Exception):
                    record.update(state='FAILING', error=f'{type(exc).__name__}: {exc}')
                    service.write_json(manifest, record)
                raise

        previous_signals = {}
        try:
            for signum in (signal.SIGTERM, signal.SIGINT):
                # Python cannot restore an unknown native signal handler.
                if signal.getsignal(signum) is not None:
                    previous_signals[signum] = signal.signal(signum, cancel)
            bpy.app.handlers.render_write.append(frame_written)
            result = bpy.ops.render.render(animation=True, scene=scene.name)
            if callback_error is not None:
                raise callback_error
            if cancel_signal is not None:
                raise SystemExit(128 + cancel_signal)
        finally:
            try:
                if frame_written in bpy.app.handlers.render_write:
                    bpy.app.handlers.render_write.remove(frame_written)
                _cleanup_temporary(output, temporary_prefix)
            finally:
                for signum, handler in previous_signals.items():
                    signal.signal(signum, handler)
        if result != {'FINISHED'} or len(written) != len(frames):
            raise RuntimeError('动画渲染未完成全部声明帧')
        record.update(state='RENDERED', frames=[written[frame] for frame in frames])
        service.write_json(manifest, record)
    except Exception as exc:
        record.update(state='FAILING', error=f'{type(exc).__name__}: {exc}')
        service.write_json(manifest, record)
        raise


if __name__ == '__main__':
    try:
        run(sys.argv[sys.argv.index('--') + 1])
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
