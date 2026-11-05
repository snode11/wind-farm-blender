"""Render a frozen scene in an independent Blender process."""
import importlib.util
import json
from pathlib import Path
import sys
import traceback


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
                  completed_frames=0, total_frames=job['total_frames'])
    manifest = root / 'manifest.json'
    written = {}
    try:
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
        for name, settings in job.get('render_settings', {}).items():
            target = scene.render.image_settings if name == 'image_settings' else getattr(scene, name)
            for key, value in settings.items():
                if name == 'image_settings' and key == 'color_depth':
                    value = service.png_color_depth(value)
                setattr(target, key, value)
        scene.frame_start, scene.frame_end, scene.frame_step = (
            job['frame_start'], job['frame_end'], job['frame_step'])
        frames = range(scene.frame_start, scene.frame_end + 1, scene.frame_step)
        if len(frames) != job['total_frames']:
            raise ValueError('动画范围与作业帧数不一致')
        output = root / 'frames'
        output.mkdir()
        scene.render.filepath = str(output / 'frame_')
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
        written_paths = set()

        def frame_written(render_scene, *_args):
            if render_scene != scene:
                return
            frame = scene.frame_current
            if frame not in frames or frame in written:
                raise RuntimeError(f'收到非预期的渲染帧：{frame}')
            files = {}
            for view in views or ('',):
                path = Path(scene.render.frame_path(frame=frame, view=view))
                if (path.parent != output or path in written_paths
                        or not path.is_file() or path.stat().st_size == 0):
                    raise RuntimeError(f'渲染帧未写入预期文件：{path.name}')
                written_paths.add(path)
                files[view] = path.relative_to(root).as_posix()
            written[frame] = dict(frame=frame, views=files) if views else dict(frame=frame, file=files[''])
            record.update(completed_frames=len(written), last_frame=frame)
            service.write_json(manifest, record)

        bpy.app.handlers.render_write.append(frame_written)
        try:
            result = bpy.ops.render.render(animation=True, scene=scene.name)
        finally:
            if frame_written in bpy.app.handlers.render_write:
                bpy.app.handlers.render_write.remove(frame_written)
        if result != {'FINISHED'} or len(written) != len(frames):
            raise RuntimeError('动画渲染未完成全部声明帧')
        record.update(state='COMPLETE', frames=[written[frame] for frame in frames])
        service.write_json(manifest, record)
    except Exception as exc:
        record.update(state='FAILED', error=f'{type(exc).__name__}: {exc}')
        service.write_json(manifest, record)
        raise


if __name__ == '__main__':
    try:
        run(sys.argv[sys.argv.index('--') + 1])
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
