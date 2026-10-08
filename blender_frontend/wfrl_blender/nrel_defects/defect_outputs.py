"""NREL defect evidence outputs; editing and G diagnostics never render."""
import csv
import hashlib
import json
from pathlib import Path
import bpy
from .defect_timeline import FrameMapping


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    temp.replace(path)


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create_run(editor,directory,*,mode='G',mapping=None):
    if editor.draft is not None or editor.healthy_payload is not None: raise ValueError('请先结束编辑或健康对照')
    editor.verify_cameras()
    output=Path(directory).expanduser().resolve()
    if output.exists() and any(output.iterdir()): raise ValueError('旧运行不覆盖；请选择新的空目录')
    output.mkdir(parents=True,exist_ok=True)
    start,end=map(float,(editor.flex.times[0],editor.flex.times[-1]))
    mapping=mapping or FrameMapping(start,end-start,60.,60.,start)
    write_json(output/'defects.json',editor.store.document)
    package=Path(__file__).parents[1]
    sources=list(Path(__file__).parent.glob('*.py'))+[package/'farm_flex.py',package/'turbine_geometry.py',package/'assets/nrel5mw_geometry.json']
    manifest=dict(schema='wfrl.nrel_blade_defects.run.v1',mode=mode,state='PREPARED',run_id=output.name,
        state_version=editor.store.version,blender_version=bpy.app.version_string,
        source_hashes={str(p.relative_to(package)):digest(p) for p in sources},
        defects_sha256=digest(output/'defects.json'),reference_surface=editor.surface.metadata(),
        replay_manifest_sha256=editor.scene.get('wfrl_farm_manifest_sha256'),
        source_motion=dict(start_s=start,end_s=end,source_sampling_hz=1/float(editor.flex.times[1]-editor.flex.times[0]),timeline_hz=60.),
        frame_mapping=mapping.to_dict(),cameras=[dict(name=c.name,matrix_world=[list(r) for r in c.matrix_world],
        lens_mm=c.data.lens,sensor_width_mm=c.data.sensor_width,sensor_height_mm=c.data.sensor_height,
        resolution=list(editor.camera_resolution(c))) for c in editor.cameras()],
        measurements=editor.payload['measurements'],effects=dict(physics_coupled=False,sensor_noise=False,distortion=False,motion_blur=False),
        products={name:dict(state='NOT_GENERATED') for name in ('visibility','visible_intervals','camera_raw','healthy')},images=[],
        limitations=['Synthetic visual morphology and user dimensions, no physical damage coupling.',
            'Existing saved radar measurements describe their original simulation geometry.',
            'G visibility does not establish image discernibility or measured damage accuracy.'])
    run=dict(output=output,manifest=manifest,mapping=mapping)
    update_run(run);save_review(editor,run)
    return run


def update_run(run): write_json(run['output']/'run_manifest.json',run['manifest'])


def save_records(run,rows):
    write_json(run['output']/'visibility.json',rows)
    fields=['turbine_id','blade_id','defect_id','revision','support_id','support_kind','primary','camera_id','time_s','frame_index','status','in_frame_fraction','visible_fraction_in_frame','visible_area_px2']
    with (run['output']/'projection_stats.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    run['manifest']['products']['visibility']=dict(state='GENERATED',records=len(rows),sha256=digest(run['output']/'visibility.json'))


def save_analysis(editor,run):
    rows=editor.analyze(run_id=run['manifest']['run_id'])
    save_records(run,rows);run['manifest']['state']='G_CURRENT_COMPLETE'
    update_run(run);save_review(editor,run)
    return rows


def save_review(editor,run):
    (run['output']/'review.md').write_text('\n'.join(['# NREL 5MW 合成缺陷检查','',
        '状态：'+run['manifest']['state'],'','六类合成视觉形态；使用当前 NREL 健康参考面与已有柔性回放。',
        '尺寸与深度检查为静态健康参考下的生成器校验，不代表现场损伤测量。',
        'G 是理想几何投影/遮挡诊断，不能据此认定可辨识。A 图外观尚未审核。',
        '缺陷未进入 OpenFAST 刚度、载荷或既有雷达数据；查看时保留原数据来源。','']))


def capture_images(editor,directory):
    """Explicit UI/CLI action: paired instantaneous lossless PNGs at current time."""
    run=create_run(editor,directory,mode='A')
    scene=editor.scene;render=scene.render
    saved=(scene.camera,render.filepath,render.image_settings.file_format,render.image_settings.color_mode,
        render.image_settings.color_depth,render.use_file_extension,scene.frame_current,scene.frame_subframe,
        render.resolution_x,render.resolution_y,render.resolution_percentage)
    blur=hasattr(render,'use_motion_blur')
    old_blur=render.use_motion_blur if blur else None
    frame_time=float(scene['defect_time_s'])
    try:
        render.image_settings.file_format='PNG';render.image_settings.color_mode='RGBA';render.image_settings.color_depth='8';render.use_file_extension=True
        if blur: render.use_motion_blur=False
        empty=editor.store.document
        for defect in empty['defects']: defect['enabled']=False
        healthy=editor.geometry.prepare(empty)
        try:
            for role,payload in (('damaged',editor.payload),('healthy',healthy)):
                editor.geometry.activate(payload)
                editor.set_time(frame_time)
                for camera in editor.cameras():
                    editor.verify_cameras();scene.camera=camera
                    render.resolution_x,render.resolution_y = editor.camera_resolution(camera)
                    render.resolution_percentage = 100
                    path=run['output']/(role+'-'+camera.name.replace('.','_')+'.png')
                    render.filepath=str(path)
                    bpy.ops.render.render(write_still=True)
                    run['manifest']['images'].append(dict(role=role,camera_id=camera.name,time_s=frame_time,
                        path=path.name,sha256=digest(path),resolution=[render.resolution_x,render.resolution_y]))
        finally:
            editor.geometry.activate(editor.payload);editor.geometry.dispose(healthy)
        run['manifest']['state']='A_IMAGES_GENERATED; NOT_REVIEWED'
        for key in ('camera_raw','healthy'): run['manifest']['products'][key]=dict(state='GENERATED')
        update_run(run);save_review(editor,run)
    except BaseException as exc:
        run['manifest']['state']='A_FAILED'
        run['manifest']['error']=str(exc)
        update_run(run);save_review(editor,run)
        raise
    finally:
        scene.camera,render.filepath,render.image_settings.file_format,render.image_settings.color_mode,render.image_settings.color_depth,render.use_file_extension,frame,subframe,render.resolution_x,render.resolution_y,render.resolution_percentage=saved
        if blur: render.use_motion_blur=old_blur
        scene.frame_set(frame,subframe=subframe)
        editor.set_time(frame_time)
    return run
