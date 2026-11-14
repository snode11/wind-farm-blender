"""NREL defect evidence outputs; editing and G diagnostics never render."""
import csv
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import bpy
from .. import atomic_json
from ..atomic_json import write_json as _write_json
from .defect_timeline import FrameMapping

_LOG = logging.getLogger(__name__)


@contextmanager
def _staged_run(run, *, require_empty=False):
    """Stage complete files and restore originals if publication raises.

    Failed rollback retains the original backup and names it in the error.
    """
    output = run['output']
    existed = output.exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='.nrel-output-', dir=output.parent))
    retain_backup = False
    try:
        staging, backup = root/'products', root/'previous'
        staging.mkdir()
        backup.mkdir()
        staged = dict(run, output=staging, manifest=deepcopy(run['manifest']))
        yield staged
        if require_empty:
            mode = None
            if output.exists():
                mode = stat.S_IMODE(output.stat().st_mode)
                staging.chmod(mode)
                output.rmdir()
            try:
                os.rename(staging, output)
            except BaseException:
                if mode is not None and not output.exists():
                    output.mkdir(mode=mode)
                raise
            run['manifest'] = staged['manifest']
            return
        files = sorted(staging.iterdir(), key=lambda path: (path.name == 'run_manifest.json', path.name))
        for path in files:
            target = output/path.name
            if target.exists() or target.is_symlink():
                mode = target.lstat().st_mode
                if stat.S_ISREG(mode):
                    path.chmod(stat.S_IMODE(mode))
                shutil.copy2(target, backup/path.name, follow_symlinks=False)
        output.mkdir(exist_ok=True)
        published = []
        try:
            for path in files:
                os.replace(path, output/path.name)
                published.append(path.name)
        except BaseException as error:
            for name in reversed(published):
                previous = backup/name
                try:
                    if previous.exists() or previous.is_symlink():
                        os.replace(previous, output/name)
                    else:
                        (output/name).unlink()
                except OSError as exc:
                    retain_backup = True
                    error.add_note(f'Output rollback failed for {output/name}: {exc}; original backup: {previous}')
                    _LOG.exception('NREL output rollback failed')
            if not existed and not any(output.iterdir()):
                output.rmdir()
            raise
        run['manifest'] = staged['manifest']
    finally:
        if not retain_backup:
            _cleanup(lambda: shutil.rmtree(root))


def _cleanup(*callbacks):
    """Attempt every restoration and preserve the original operation error."""
    primary = failure = sys.exc_info()[1]
    for callback in callbacks:
        try:
            callback()
        except BaseException as exc:
            if failure is None:
                failure = exc
            else:
                failure.add_note(f'NREL capture cleanup failed: {exc}')
                _LOG.exception('NREL capture cleanup failed')
    if failure is not primary:
        raise failure


def write_json(path,value):
    _write_json(path, value, mkdir=True)


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def camera_metadata(editor, camera):
    from dataclasses import asdict
    from .. import custom_cameras
    from .defect_visibility import _camera_descriptor
    descriptor = _camera_descriptor(camera, editor.scene, resolution=editor.camera_resolution(camera))
    return dict(name=camera.name, slot=camera.get('wfrl_custom_slot'),
        parameters=asdict(custom_cameras.parameters(camera)),
        matrix_world=descriptor['matrix_world'], K=descriptor['K'], P=descriptor['P'],
        lens_mm=camera.data.lens, sensor_width_mm=camera.data.sensor_width,
        sensor_height_mm=camera.data.sensor_height,
        resolution=[descriptor['width'], descriptor['height']])


def create_run(editor,directory,*,mode='G',mapping=None):
    if editor.draft is not None or editor.healthy_payload is not None: raise ValueError('请先结束编辑或健康对照')
    editor.verify_cameras()
    output=Path(directory).expanduser().resolve()
    if output.exists() and any(output.iterdir()): raise ValueError('旧运行不覆盖；请选择新的空目录')
    start,end=map(float,(editor.flex.times[0],editor.flex.times[-1]))
    mapping=mapping or FrameMapping(start,end-start,60.,60.,start)
    package=Path(__file__).parents[1]
    sources=list(Path(__file__).parent.glob('*.py'))+[package/'farm_flex.py',package/'turbine_geometry.py',package/'assets/nrel5mw_geometry.json']
    run=dict(output=output,manifest={},mapping=mapping)
    with _staged_run(run, require_empty=True) as staged:
        write_json(staged['output']/'defects.json',editor.store.document)
        staged['manifest'] = _run_manifest(editor, output.name, mode, mapping, start, end, package, sources,
            digest(staged['output']/'defects.json'))
        update_run(staged)
        save_review(editor,staged)
    return run


def _run_manifest(editor, run_id, mode, mapping, start, end, package, sources, defects_sha256):
    return dict(schema='wfrl.nrel_blade_defects.run.v1',mode=mode,state='PREPARED',run_id=run_id,
        state_version=editor.store.version,blender_version=bpy.app.version_string,
        source_hashes={str(p.relative_to(package)):digest(p) for p in sources},
        defects_sha256=defects_sha256,reference_surface=editor.surface.metadata(),
        replay_manifest_sha256=editor.scene.get('wfrl_farm_manifest_sha256'),
        source_motion=dict(start_s=start,end_s=end,source_sampling_hz=1/float(editor.flex.times[1]-editor.flex.times[0]),timeline_hz=60.),
        frame_mapping=mapping.to_dict(),cameras=[camera_metadata(editor, c) for c in editor.cameras()],
        measurements=editor.payload['measurements'],effects=dict(physics_coupled=False,sensor_noise=False,distortion=False,motion_blur=False),
        products={name:dict(state='NOT_GENERATED') for name in ('visibility','visible_intervals','camera_raw','healthy')},images=[],
        limitations=['Synthetic visual morphology and user dimensions, no physical damage coupling.',
            'Existing saved radar measurements describe their original simulation geometry.',
            'G visibility does not establish image discernibility or measured damage accuracy.'])


def update_run(run): write_json(run['output']/'run_manifest.json',run['manifest'])


def save_records(run,rows):
    fields=['turbine_id','blade_id','defect_id','revision','support_id','support_kind','primary','camera_id','time_s','frame_index','status','in_frame_fraction','visible_fraction_in_frame','visible_area_px2']
    with _staged_run(run) as staged:
        write_json(staged['output']/'visibility.json',rows)
        with (staged['output']/'projection_stats.csv').open('w',newline='',encoding='utf-8') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        staged['manifest']['products']['visibility']=dict(state='GENERATED',records=len(rows),sha256=digest(staged['output']/'visibility.json'))


def save_analysis(editor,run):
    rows=editor.analyze(run_id=run['manifest']['run_id'])
    with _staged_run(run) as staged:
        save_records(staged,rows)
        staged['manifest']['state']='G_CURRENT_COMPLETE'
        update_run(staged)
        save_review(editor,staged)
    return rows


def save_review(editor,run):
    atomic_json.write_text(run['output']/'review.md', '\n'.join(['# NREL 5MW 合成缺陷检查','',
        '状态：'+run['manifest']['state'],'','六类合成视觉形态；使用当前 NREL 健康参考面与已有柔性回放。',
        '尺寸与深度检查为静态健康参考下的生成器校验，不代表现场损伤测量。',
        'G 是理想几何投影/遮挡诊断，不能据此认定可辨识。A 图外观尚未审核。',
        '缺陷未进入 OpenFAST 刚度、载荷或既有雷达数据；查看时保留原数据来源。','']))


def capture_images(editor,directory):
    """Explicit UI/CLI action: paired instantaneous lossless PNGs at current time."""
    run=create_run(editor,directory,mode='A')
    try:
        with _staged_run(run) as staged:
            _capture_images(editor, staged)
    except BaseException as exc:
        run['manifest']['state']='A_FAILED'
        run['manifest']['error']=str(exc)
        _cleanup(lambda: update_run(run), lambda: save_review(editor,run))
        raise
    return run


def _capture_images(editor, run):
    scene=editor.scene
    render=scene.render
    saved=(scene.camera,render.filepath,render.image_settings.file_format,render.image_settings.color_mode,
        render.image_settings.color_depth,render.use_file_extension,scene.frame_current,scene.frame_subframe,
        render.resolution_x,render.resolution_y,render.resolution_percentage)
    saved_pixels = (render.pixel_aspect_x, render.pixel_aspect_y)
    saved_border = (render.use_border, render.use_crop_to_border)
    saved_postprocessing = (render.use_compositing, render.use_sequencer)
    blur=hasattr(render,'use_motion_blur')
    old_blur=render.use_motion_blur if blur else None
    frame_time=float(scene['defect_time_s'])

    def restore_render():
        scene.camera,render.filepath,render.image_settings.file_format,render.image_settings.color_mode,render.image_settings.color_depth,render.use_file_extension,_frame,_subframe,render.resolution_x,render.resolution_y,render.resolution_percentage=saved
        render.pixel_aspect_x, render.pixel_aspect_y = saved_pixels
        render.use_border, render.use_crop_to_border = saved_border
        render.use_compositing, render.use_sequencer = saved_postprocessing
        if blur: render.use_motion_blur=old_blur

    try:
        render.image_settings.file_format='PNG'
        render.image_settings.color_mode='RGBA'
        render.image_settings.color_depth='8'
        render.use_file_extension=True
        render.use_border = False
        render.use_crop_to_border = False
        render.use_compositing = render.use_sequencer = False
        if blur: render.use_motion_blur=False
        empty=editor.store.document
        for defect in empty['defects']: defect['enabled']=False
        healthy=editor.geometry.prepare(empty)
        try:
            for role,payload in (('damaged',editor.payload),('healthy',healthy)):
                editor.geometry.activate(payload)
                editor.set_time(frame_time)
                for camera in editor.cameras():
                    editor.verify_cameras()
                    scene.camera=camera
                    render.resolution_x,render.resolution_y = editor.camera_resolution(camera)
                    render.resolution_percentage = 100
                    from .. import custom_cameras, camera_projection
                    from .defect_visibility import _camera_descriptor
                    params = custom_cameras.parameters(camera)
                    render.pixel_aspect_x, render.pixel_aspect_y = camera_projection.pixel_scales(
                        params.fov, params.vfov, render.resolution_x, render.resolution_y)
                    descriptor = _camera_descriptor(camera, scene, resolution=(render.resolution_x, render.resolution_y))
                    path=run['output']/(role+'-'+camera.name.replace('.','_')+'.png')
                    render.filepath=str(path)
                    result = bpy.ops.render.render(write_still=True, scene=scene.name)
                    if result != {'FINISHED'}:
                        raise RuntimeError('Camera raw render did not finish: ' + str(result))
                    run['manifest']['images'].append(dict(role=role,camera_id=camera.name,time_s=frame_time,
                        path=path.name,sha256=digest(path),resolution=[render.resolution_x,render.resolution_y],
                        K=descriptor['K'], P=descriptor['P'], matrix_world=descriptor['matrix_world'],
                        pixel_aspect=[render.pixel_aspect_x, render.pixel_aspect_y]))
        finally:
            _cleanup(lambda: editor.geometry.activate(editor.payload),
                     lambda: editor.geometry.dispose(healthy))
        run['manifest']['state']='A_IMAGES_GENERATED; NOT_REVIEWED'
        for key in ('camera_raw','healthy'): run['manifest']['products'][key]=dict(state='GENERATED')
        update_run(run)
        save_review(editor,run)
    finally:
        _cleanup(restore_render, lambda: scene.frame_set(saved[6],subframe=saved[7]),
                 lambda: editor.set_time(frame_time))
    return run
