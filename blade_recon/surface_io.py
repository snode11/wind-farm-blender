"""Versioned, checked appearance sidecars; recon.json v1 is copied unchanged.

Only explicitly named package assets are read. This module never searches for,
opens, or passes a neighboring truth.json to a legacy display/solver loader.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

import numpy as np

try:
    from . import model
    from .surface_texture import CHART_VERSION, UNKNOWN, CONFLICT, array_sha256, atlas_binding
except ImportError:
    import model
    from surface_texture import CHART_VERSION, UNKNOWN, CONFLICT, array_sha256, atlas_binding

SCHEMA = 'blade-recon-surface.v1'
MAX_ASSET_BYTES = 256*1024*1024


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')


def _atomic_bytes(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name = tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _asset(root, relative):
    if not isinstance(relative,str) or not relative:
        raise ValueError('package asset path must be a nonempty relative string')
    p = PurePosixPath(relative)
    if p.is_absolute() or any(part in ('..','.') for part in p.parts) or '\\' in relative:
        raise ValueError('package asset path escapes controlled directory')
    resolved = (root/relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('package asset symlink escapes controlled directory')
    return resolved


def _record(path, root):
    return dict(path=Path(path).relative_to(root).as_posix(),sha256=sha256(path),bytes=Path(path).stat().st_size)


def _recon_config(recon):
    """Bound dimensions and validate saved states before allocating a Rotor."""
    if not isinstance(recon,dict) or not isinstance(recon.get('turbine'),dict):
        raise ValueError('invalid explicit reconstruction object')
    raw=recon['turbine']
    for name,minimum,maximum in [('n_sections',2,4096),('n_ring',4,1024)]:
        value=raw.get(name,getattr(model.TurbineConfig(),name))
        if type(value) is not int or not minimum<=value<=maximum:
            raise ValueError('unsupported template dimension '+name)
    cfg=model.TurbineConfig.from_dict(raw)
    if cfg.n_sections*cfg.n_ring>1_048_576 or cfg.n_ring%2:
        raise ValueError('unsupported template sampling size')
    values=cfg.to_dict()
    if not all(np.isfinite(value) for value in values.values()) or not 0<cfg.hub_radius_m<cfg.tip_radius_m or cfg.hub_height_m<=0:
        raise ValueError('invalid template physical dimensions')
    if cfg.spin not in (-1,1) or abs(cfg.tilt_deg)>=90 or abs(cfg.cone_deg)>=90:
        raise ValueError('invalid template orientation configuration')
    frames=recon.get('frames')
    if not isinstance(frames,list) or not 1<=len(frames)<=10000:
        raise ValueError('invalid saved reconstruction frame count')
    last_id,last_t=-1,-1.
    for frame in frames:
        state=np.asarray(frame.get('state'),dtype=float)
        fid,t=frame.get('frame'),frame.get('t')
        if state.shape!=(13,) or not np.isfinite(state).all():
            raise ValueError('invalid saved 13-dimensional reconstruction state')
        if type(fid) is not int or fid<=last_id or not isinstance(t,(int,float)) or isinstance(t,bool) or not np.isfinite(t) or t<0 or t<=last_t:
            raise ValueError('invalid saved reconstruction frame/time ordering')
        last_id,last_t=fid,t
    fps=recon.get('fps')
    if not isinstance(fps,(int,float)) or isinstance(fps,bool) or not np.isfinite(fps) or fps<=0:
        raise ValueError('invalid reconstruction sample rate')
    return cfg


def write_surface_package(output_dir, recon_path, arrays, sources, summary, *,
                          experiment_id, appearance_scope='single_frame', target_identity='T1 rotor'):
    """Write new controlled assets and commit surface.json last.

    The directory must not already contain surface.json; a failed first write
    leaves run_state.json FAILED, preserving evidence rather than replacing it.
    """
    import cv2
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True,exist_ok=True)
    if (root/'surface.json').exists():
        raise FileExistsError('refusing to overwrite an existing surface package')
    _atomic_bytes(root/'run_state.json',_json_bytes(dict(status='RUNNING')))
    try:
        raw = Path(recon_path).read_bytes()
        recon = json.loads(raw)
        if appearance_scope not in ('single_frame','short_window'):
            raise ValueError('appearance_scope must be single_frame or short_window')
        if appearance_scope=='single_frame' and len(sources)!=1:
            raise ValueError('single_frame appearance may read exactly one RGB')
        _atomic_bytes(root/'recon.json',raw)
        fd,npz_temp = tempfile.mkstemp(prefix='.bindings.',suffix='.npz',dir=root)
        os.close(fd)
        try:
            np.savez_compressed(npz_temp,**arrays)
            os.replace(npz_temp,root/'bindings.npz')
        finally:
            if os.path.exists(npz_temp):
                os.unlink(npz_temp)
        textures=[]
        support_maps=[]
        for blade,rgb in enumerate(arrays['texture_rgb']):
            path=root/f'texture_B{blade+1}.png'
            # OpenCV arrays have top-left origin, while Blender UV v=0 is bottom.
            # Root q0=0 therefore goes on the bottom row of the stored PNG.
            ok=cv2.imwrite(str(path),cv2.cvtColor(np.flipud(rgb),cv2.COLOR_RGB2BGR))
            if not ok:
                raise OSError('failed to save RGB texture')
            textures.append(_record(path,root))
            support_path=root/f'support_B{blade+1}.png'
            support=np.array([0,127,255],dtype=np.uint8)[arrays['support_status'][blade]]
            if not cv2.imwrite(str(support_path),np.flipud(support)):
                raise OSError('failed to save explicit support map')
            support_maps.append(_record(support_path,root))
        records=[]
        for i,source in enumerate(sources):
            source=dict(source)
            original=Path(source.pop('rgb_path')).expanduser().resolve()
            if sha256(original)!=source['rgb_sha256']:
                raise ValueError('selected input RGB changed during extraction')
            asset=root/'evidence'/f'source_{i:03d}{original.suffix.lower()}'
            asset.parent.mkdir(exist_ok=True)
            shutil.copyfile(original,asset)
            source.update(rgb_file=_record(asset,root),original_rgb_path=str(original))
            records.append(source)
        cfg=_recon_config(recon)
        rotor=model.Rotor(cfg)
        payload=dict(schema_version=SCHEMA,status='REVIEW_ONLY',experiment_id=str(experiment_id),
            target_mode='rotor',target_identity=str(target_identity),appearance_kind='captured_rgb',
            appearance_scope=appearance_scope,geometry_relationship='unchanged bytes of explicitly supplied recon.json v1',
            canonical_binding='q; triangle_id/vertex_indices/barycentric_weights are checked derived caches',
            chart_version=CHART_VERSION,coordinates=dict(model='M: tower-base, x upwind, y right-handed, z up, metres',
                camera='C: OpenCV x right,y down,z forward',pixel='original integer centers; no crop/resize',
                q=['(r-hub_radius)/(tip_radius-hub_radius)','perimeter turns'],uv=['q1 modulo 1','q0']),
            recon=_record(root/'recon.json',root),recon_sha256=hashlib.sha256(raw).hexdigest(),
            model_sha256=sha256(model.__file__),model_identity='unchanged blade_recon/model.py v0.1',
            topology_sha256=array_sha256(rotor.tpl.faces()),template_config=recon['turbine'],
            template_xi_sha256=array_sha256(rotor.tpl.xi),uv_sha256=array_sha256(arrays['uv']),
            atlas_shape=list(arrays['support_status'].shape[1:]),
            bindings=_record(root/'bindings.npz',root),textures=textures,support_maps=support_maps,
            support_png_codes={'0':'unknown','127':'captured RGB','255':'conflict'},
            texture_png_orientation='top row tip; bottom row root; Blender UV directly uses q',
            support_codes={'0':'unknown orange','1':'captured input RGB','2':'conflict purple'},
            allowed_input_sources=['selected input RGB','manual original-pixel regions','declared healthy template',
                                   'saved estimated states','timestamps','explicit model-to-camera calibration'],
            forbidden_sources_read=False,sources=records,summary=summary)
        # Full validation before marking the package complete. It intentionally
        # uses the same explicit data path that later display verification uses.
        _validate_payload(root,payload)
        _atomic_bytes(root/'surface.json',_json_bytes(payload))
        _atomic_bytes(root/'run_state.json',_json_bytes(dict(status='COMPLETED_SOFTWARE_CHECKS',acceptance='REVIEW_ONLY')))
        return root/'surface.json'
    except Exception as exc:
        _atomic_bytes(root/'run_state.json',_json_bytes(dict(status='FAILED',error=str(exc))))
        raise


def _checked_record(root, record):
    if not isinstance(record,dict) or not isinstance(record.get('bytes'),int) or not 0 <= record['bytes'] <= MAX_ASSET_BYTES:
        raise ValueError('invalid or oversized asset record')
    path=_asset(root,record.get('path'))
    if not path.is_file() or path.stat().st_size != record['bytes'] or sha256(path)!=record.get('sha256'):
        raise ValueError('asset missing, changed, or hash mismatch: '+str(path))
    return path


def _read_rgb(path,image_reader=None):
    if image_reader is not None:
        return np.asarray(image_reader(path),dtype=np.uint8)
    import cv2
    bgr=cv2.imread(str(path),cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError('cannot decode RGB image '+str(path))
    return cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)


def _validate_payload(root,payload,image_reader=None):
    if payload.get('schema_version')!=SCHEMA or payload.get('chart_version')!=CHART_VERSION:
        raise ValueError('unsupported surface/chart schema')
    if payload.get('appearance_kind')!='captured_rgb' or payload.get('target_mode')!='rotor':
        raise ValueError('unsupported appearance kind or target mode')
    if payload.get('model_sha256')!=sha256(model.__file__):
        raise ValueError('surface forward-model identity mismatch')
    shape=payload.get('atlas_shape')
    if not isinstance(shape,list) or len(shape)!=2 or any(type(x) is not int for x in shape):
        raise ValueError('invalid atlas dimensions')
    h,w=shape
    if not 8<=h<=4096 or not 8<=w<=2048 or h*w>4_194_304:
        raise ValueError('unsupported atlas dimensions')
    recon_path=_checked_record(root,payload['recon'])
    if sha256(recon_path)!=payload.get('recon_sha256'):
        raise ValueError('recon identity mismatch')
    recon=json.loads(recon_path.read_text(encoding='utf-8'))
    cfg=_recon_config(recon)
    if cfg.to_dict()!=model.TurbineConfig.from_dict(payload['template_config']).to_dict():
        raise ValueError('surface template differs from reconstruction')
    rotor=model.Rotor(cfg)
    if array_sha256(rotor.tpl.faces())!=payload['topology_sha256'] or array_sha256(rotor.tpl.xi)!=payload['template_xi_sha256']:
        raise ValueError('template topology/hash mismatch')
    bindings_path=_checked_record(root,payload['bindings'])
    with zipfile.ZipFile(bindings_path) as z:
        if sum(i.file_size for i in z.infolist())>MAX_ASSET_BYTES:
            raise ValueError('binding NPZ expands beyond supported memory limit')
    with np.load(bindings_path,allow_pickle=False) as z:
        arrays={name:z[name] for name in z.files}
    expected=dict(q=(h,w,2),uv=(h,w,2),triangle_id=(h,w),vertex_indices=(h,w,3),
        barycentric_weights=(h,w,3),texture_rgb=(3,h,w,3),captured_rgb=(3,h,w,3),
        support_status=(3,h,w),source_index=(3,h,w),source_xy=(3,h,w,2),
        sample_count=(3,h,w),template_area_weights_m2=(h,w))
    if set(arrays)!=set(expected):
        raise ValueError('binding arrays missing or unexpected')
    for name,dimensions in expected.items():
        if arrays[name].shape!=dimensions or arrays[name].dtype.hasobject or not np.isfinite(arrays[name]).all():
            raise ValueError('invalid binding array '+name)
    for name in ('triangle_id','vertex_indices','source_index','source_xy'):
        if arrays[name].dtype!=np.int32:
            raise ValueError(name+' must use exact int32 identifiers/pixels')
    if arrays['sample_count'].dtype!=np.uint16:
        raise ValueError('sample_count must be uint16')
    derived=atlas_binding(rotor,(h,w))
    for name in ('q','uv','triangle_id','vertex_indices','barycentric_weights'):
        if not np.array_equal(arrays[name],derived[name]):
            raise ValueError('canonical q and derived cache disagree: '+name)
    if array_sha256(arrays['uv'])!=payload['uv_sha256']:
        raise ValueError('UV hash mismatch')
    if arrays['texture_rgb'].dtype!=np.uint8 or arrays['captured_rgb'].dtype!=np.uint8 or arrays['support_status'].dtype!=np.uint8:
        raise ValueError('texture/support dtypes must be uint8')
    if not np.isin(arrays['support_status'],[0,1,2]).all():
        raise ValueError('invalid support status')
    status=arrays['support_status']
    if not np.array_equal(arrays['texture_rgb'][status==1],arrays['captured_rgb'][status==1]):
        raise ValueError('displayed captured RGB differs from original pixel evidence')
    if not np.all(arrays['texture_rgb'][status==0]==UNKNOWN) or not np.all(arrays['texture_rgb'][status==2]==CONFLICT):
        raise ValueError('unknown/conflict colors do not match explicit support states')
    sources=payload.get('sources')
    if not isinstance(sources,list) or not 1<=len(sources)<=128:
        raise ValueError('invalid selected input sources')
    if payload.get('appearance_scope')=='single_frame' and len(sources)!=1:
        raise ValueError('single_frame appearance has multiple input RGB sources')
    has=arrays['support_status']>0
    if np.any(arrays['sample_count'][has]==0) or np.any(arrays['sample_count']>len(sources)):
        raise ValueError('support count differs from accepted source evidence')
    if np.any(arrays['source_index'][~has]!=-1) or np.any(arrays['source_xy'][~has]!=-1):
        raise ValueError('unknown texels may not retain invented source evidence')
    if not np.all((arrays['source_index'][has]>=0)&(arrays['source_index'][has]<len(sources))):
        raise ValueError('supported texels have invalid source identity')
    for i,source in enumerate(sources):
        p=_checked_record(root,source['rgb_file'])
        if sha256(p)!=source['rgb_sha256']:
            raise ValueError('source RGB hash identity mismatch')
        dimensions=source.get('original_size')
        if not isinstance(dimensions,list) or len(dimensions)!=2 or any(type(x) is not int or not 1<=x<=8192 for x in dimensions) or dimensions[0]*dimensions[1]>33_554_432:
            raise ValueError('source original dimensions exceed supported limits')
        rgb=_read_rgb(p,image_reader)
        if list(rgb.shape[1::-1])!=source['original_size']:
            raise ValueError('source RGB dimensions changed')
        selected=has & (arrays['source_index']==i)
        xy=arrays['source_xy'][selected]
        if len(xy):
            if np.any(xy<0) or np.any(xy[:,0]>=rgb.shape[1]) or np.any(xy[:,1]>=rgb.shape[0]):
                raise ValueError('source pixel evidence out of original image')
            if not np.array_equal(arrays['captured_rgb'][selected],rgb[xy[:,1],xy[:,0]]):
                raise ValueError('captured RGB does not match recorded original source pixels')
    textures=payload.get('textures')
    if not isinstance(textures,list) or len(textures)!=3:
        raise ValueError('three template blade textures are required')
    for blade,record in enumerate(textures):
        p=_checked_record(root,record)
        rgb=_read_rgb(p,image_reader)
        if not np.array_equal(rgb,np.flipud(arrays['texture_rgb'][blade])):
            raise ValueError('stored PNG orientation/color does not match atlas')
    support_maps=payload.get('support_maps')
    if not isinstance(support_maps,list) or len(support_maps)!=3:
        raise ValueError('three explicit support maps are required')
    for blade,record in enumerate(support_maps):
        p=_checked_record(root,record)
        rgb=_read_rgb(p,image_reader)
        expected=np.flipud(np.array([0,127,255],dtype=np.uint8)[arrays['support_status'][blade]])
        if not np.array_equal(rgb,np.repeat(expected[...,None],3,axis=-1)):
            raise ValueError('stored support PNG differs from support state array')
    return dict(metadata=payload,recon_data=recon,rotor=rotor,arrays=arrays,root=root)


def load_surface_package(surface_path, *, explicit_recon_path=None,image_reader=None):
    path=Path(surface_path).expanduser().resolve()
    if path.stat().st_size>8*1024*1024:
        raise ValueError('surface JSON is oversized')
    payload=json.loads(path.read_text(encoding='utf-8'))
    package=_validate_payload(path.parent,payload,image_reader)
    if explicit_recon_path is not None and sha256(explicit_recon_path)!=payload['recon_sha256']:
        raise ValueError('explicit reconstruction differs from surface geometry identity')
    return package
