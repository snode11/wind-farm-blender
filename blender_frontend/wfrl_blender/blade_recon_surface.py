"""Isolated offline appearance viewer; no existing playback entry is changed.

Initial loading checks the explicitly named surface package. Saved files embed
recon JSON and sidecar metadata and pack all three atlases. Timer commits deform
only vertex coordinates; UVs remain attached to template material coordinates.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector
import numpy as np

from .blade_recon_data import ReconSequence,MODEL_SHA256

SESSIONS={}
_PENDING_IMPORTS={}
_RUNNING=False


def active(scene):
    return scene.get('wfrl_scene_kind')=='blade_recon_surface'


def _rgb_reader(path):
    """Read exact stored bytes via Blender, independently of offline OpenCV."""
    image=bpy.data.images.load(str(path),check_existing=False)
    try:
        image.colorspace_settings.name='Non-Color'
        width,height=image.size
        values=np.empty(width*height*4,dtype=np.float32)
        image.pixels.foreach_get(values)
        rgb=np.rint(np.clip(values.reshape(height,width,4)[...,:3],0,1)*255).astype(np.uint8)
        return np.flipud(rgb)
    finally:
        bpy.data.images.remove(image)


def _embed(scene,key,raw,prefix,*,register=None):
    text=bpy.data.texts.new(prefix+'.'+key)
    if register is not None: register('texts',text,owned_fake_user=True)
    text.write(raw)
    text.use_fake_user=True
    scene['surface_'+key+'_text']=text.name
    scene['surface_'+key+'_sha256']=hashlib.sha256(raw.encode()).hexdigest()


def _embedded(scene,key):
    block=bpy.data.texts.get(scene.get('surface_'+key+'_text',''))
    if block is None:
        raise ValueError('missing embedded '+key)
    raw=block.as_string()
    if hashlib.sha256(raw.encode()).hexdigest()!=scene.get('surface_'+key+'_sha256'):
        raise ValueError('embedded '+key+' hash mismatch')
    return json.loads(raw)


def _material(name,image=None,color=(1.,.55,.10,1.),*,register=None):
    material=bpy.data.materials.new(name)
    if register is not None: register('materials',material)
    material.use_nodes=True
    material.diffuse_color=color
    nodes=material.node_tree.nodes
    nodes.clear()
    out=nodes.new('ShaderNodeOutputMaterial')
    emission=nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value=color
    emission.inputs['Strength'].default_value=1
    material.node_tree.links.new(emission.outputs['Emission'],out.inputs['Surface'])
    if image is not None:
        texture=nodes.new('ShaderNodeTexImage')
        texture.image=image
        texture.interpolation='Closest'
        texture.extension='REPEAT'
        material.node_tree.links.new(texture.outputs['Color'],emission.inputs['Color'])
    return material


def _mesh(scene,name,vertices,rotor,texture_material,unknown_material,blade,*,register=None):
    data=bpy.data.meshes.new(name)
    if register is not None: register('meshes',data)
    faces=rotor.tpl.faces()
    data.from_pydata(vertices.reshape(-1,3).tolist(),[],faces.tolist())
    data.materials.append(texture_material)
    data.materials.append(unknown_material)
    uv=data.uv_layers.new(name='template_material_q')
    n=rotor.cfg.n_ring
    vertex_uv=np.stack([np.tile(np.arange(n)/n,rotor.cfg.n_sections),np.repeat(rotor.tpl.xi,n)],axis=-1)
    lateral=(rotor.cfg.n_sections-1)*n*2
    for polygon in data.polygons:
        chart=vertex_uv[np.asarray(polygon.vertices)].copy()
        if np.ptp(chart[:,0])>.5:
            chart[chart[:,0]<.5,0]+=1
        for loop,point in zip(polygon.loop_indices,chart):
            uv.data[loop].uv=point
        if polygon.index>=lateral:
            polygon.material_index=1
    data.update()
    obj=bpy.data.objects.new(name,data)
    if register is not None: register('objects',obj)
    scene.collection.objects.link(obj)
    obj['surface_role']='estimate'
    obj['surface_blade_id']=blade
    obj['surface_applied_frame']=-1
    obj.hide_select=True
    # Seeking hides stale vertices until the main-thread timer really commits.
    driver=obj.driver_add('hide_viewport').driver
    variable=driver.variables.new()
    variable.name='applied'
    variable.targets[0].id=obj
    variable.targets[0].data_path='["surface_applied_frame"]'
    driver.expression='frame != applied'
    return obj


def _camera(scene,name,position,target,*,register=None):
    data=bpy.data.cameras.new(name)
    if register is not None: register('cameras',data)
    data.lens=45
    data.clip_end=3000
    obj=bpy.data.objects.new(name,data)
    if register is not None: register('objects',obj)
    from .cameras import clear_camera_mask
    clear_camera_mask(obj)
    scene.collection.objects.link(obj)
    obj.location=position
    obj.rotation_euler=(Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler()
    obj['surface_role']='view_camera'
    return obj


def _retry_pending():
    for key, pending in list(_PENDING_IMPORTS.items()):
        errors = pending.rollback()
        if errors:
            raise RuntimeError('Surface resource cleanup unconfirmed; retry required') from errors[0]
        try: bpy.data.scenes.remove(pending.scene)
        except ReferenceError: pass
        _PENDING_IMPORTS.pop(key, None)


def load_surface(context,surface_path):
    """Validate before scene creation; never auto-load adjacent truth data."""
    from .split_mappo_texture import _ImportResources
    _retry_pending()
    from blade_recon.surface_io import load_surface_package
    path=Path(surface_path).expanduser().resolve()
    package=load_surface_package(path,image_reader=_rgb_reader)
    metadata=package['metadata']
    if metadata['model_sha256']!=MODEL_SHA256:
        raise ValueError('source and bundled forward-model identities differ')
    sequence=ReconSequence.from_data(package['recon_data'],truth_data=None,cameras_data=None,
        source_label='Captured RGB appearance; REVIEW_ONLY',source_sha256=metadata['recon_sha256'])
    previous=context.scene
    prefix='WFRL.Surface.'+uuid.uuid4().hex[:8]
    scene=bpy.data.scenes.new('叶片表面重建')
    resources=_ImportResources(scene)
    try:
        scene['wfrl_scene_kind']='blade_recon_surface'
        scene['surface_source_path']=str(path)
        scene['surface_status']='REVIEW_ONLY'
        scene['surface_model_sha256']=MODEL_SHA256
        scene['surface_recon_sha256']=metadata['recon_sha256']
        scene['surface_appearance_scope']=metadata['appearance_scope']
        scene['surface_legend']='captured input RGB; orange unknown; purple conflict; appearance is not geometry accuracy'
        _embed(scene,'recon',json.dumps(package['recon_data'],ensure_ascii=False,allow_nan=False),prefix,register=resources.register)
        _embed(scene,'sidecar',json.dumps(metadata,ensure_ascii=False,allow_nan=False),prefix,register=resources.register)
        scene.frame_start=1
        scene.frame_end=len(sequence.states)
        scene.render.fps=max(1,min(32767,round(sequence.fps)))
        scene.render.fps_base=scene.render.fps/sequence.fps
        scene.render.resolution_x,scene.render.resolution_y=1920,1080
        scene.render.resolution_percentage=100
        scene.render.engine='BLENDER_EEVEE'
        scene.view_settings.view_transform='Standard'
        scene.view_settings.look='None'
        scene.view_settings.exposure=0
        scene.view_settings.gamma=1
        scene.unit_settings.system='METRIC'
        world=bpy.data.worlds.new(prefix+'.World')
        resources.register('worlds',world)
        scene.world=world
        scene.world.color=(.025,.035,.055)
        unknown=_material(prefix+'.Unknown',register=resources.register)
        vertices,_=sequence.geometry(0)
        blades=[]
        for blade,record in enumerate(metadata['textures']):
            image=bpy.data.images.load(str(path.parent/record['path']),check_existing=False)
            resources.register('images',image)
            image.colorspace_settings.name='sRGB'
            image.pack()
            image['surface_texture_sha256']=record['sha256']
            material=_material(prefix+f'.B{blade+1}.CapturedRGB',image,register=resources.register)
            blades.append(_mesh(scene,prefix+f'.B{blade+1}',vertices[blade],sequence.rotor,material,unknown,blade,register=resources.register))
        hub=sequence.rotor.hub
        radius=sequence.rotor.cfg.tip_radius_m
        scene.camera=_camera(scene,prefix+'.Overview',hub+np.array([radius*4,-radius*2,radius*.25]),hub,register=resources.register)
        SESSIONS[scene.as_pointer()]=dict(sequence=sequence,blades=blades,pending=True,index=None,
            scene_uid=getattr(scene, 'session_uid', scene.as_pointer()))
        if context.window:
            context.window.scene=scene
        apply(scene)
        if context.screen:
            for area in context.screen.areas:
                if area.type=='VIEW_3D':
                    space=area.spaces.active
                    space.shading.type='MATERIAL'
                    space.region_3d.view_perspective='CAMERA'
                    space.camera=scene.camera
                    space.overlay.show_floor=False
                    space.overlay.show_axis_x=space.overlay.show_axis_y=False
        return scene
    except Exception as error:
        key = scene.as_pointer()
        _PENDING_IMPORTS[key] = resources
        SESSIONS.pop(key, None)
        try:
            if context.window and context.window.scene == scene:
                context.window.scene = previous
        except Exception as exc:
            error.add_note('Surface scene restoration failed: ' + str(exc))
        try:
            scene.camera = None
            scene.world = None
            for name in list(scene.keys()):
                if name.startswith('surface_') or name == 'wfrl_scene_kind':
                    scene.pop(name, None)
            _retry_pending()
        except Exception as exc:
            error.add_note('Surface resource cleanup unconfirmed; next load retries: ' + str(exc))
        raise


def restore(scene):
    metadata=_embedded(scene,'sidecar')
    recon=_embedded(scene,'recon')
    if metadata['model_sha256']!=MODEL_SHA256 or scene.get('surface_model_sha256')!=MODEL_SHA256:
        raise ValueError('saved surface model identity mismatch')
    if metadata['recon_sha256'] != scene.get('surface_recon_sha256'):
        raise ValueError('saved surface reconstruction identity mismatch')
    sequence=ReconSequence.from_data(recon,truth_data=None,cameras_data=None,
        source_label='Saved captured RGB appearance; REVIEW_ONLY',source_sha256=metadata['recon_sha256'])
    blades=sorted((o for o in scene.objects if o.get('surface_role')=='estimate'),key=lambda o:o['surface_blade_id'])
    if ([o.get('surface_blade_id') for o in blades] != [0, 1, 2]
            or any(type(o.get('surface_blade_id')) is not int for o in blades)
            or any(len(o.data.vertices)!=sequence.rotor.cfg.n_sections*sequence.rotor.cfg.n_ring for o in blades)):
        raise ValueError('saved surface mesh dimensions mismatch')
    textures = metadata.get('textures')
    shape = metadata.get('atlas_shape')
    if not isinstance(textures, list) or len(textures) != 3 or not isinstance(shape, list) or len(shape) != 2:
        raise ValueError('saved captured RGB atlas metadata is incomplete')
    for blade, obj in enumerate(blades):
        if 'template_material_q' not in obj.data.uv_layers:
            raise ValueError('saved fixed UV binding missing')
        texture=obj.data.materials[0].node_tree.nodes.get('Image Texture')
        if texture is None or texture.image is None or texture.image.packed_file is None:
            raise ValueError('saved captured RGB atlas is not packed')
        if tuple(texture.image.size) != tuple(reversed(shape)):
            raise ValueError('saved captured RGB atlas dimensions mismatch')
        if hashlib.sha256(texture.image.packed_file.data).hexdigest() != textures[blade].get('sha256'):
            raise ValueError('saved captured RGB atlas hash mismatch')
    SESSIONS[scene.as_pointer()]=dict(sequence=sequence,blades=blades,pending=True,index=None,
        scene_uid=getattr(scene, 'session_uid', scene.as_pointer()))
    scene['surface_error']=''


def apply(scene):
    value=SESSIONS.get(scene.as_pointer())
    if value is None:
        return
    sequence=value['sequence']
    index=min(max(scene.frame_current-1,0),len(sequence.states)-1)
    vertices,_=sequence.geometry(index)
    for blade,obj in enumerate(value['blades']):
        obj.data.vertices.foreach_set('co',vertices[blade].reshape(-1).astype(np.float32))
        obj.data.update()
        obj['surface_applied_frame']=scene.frame_current
    value.update(index=index,pending=False)
    scene['surface_applied_frame']=scene.frame_current
    scene['surface_applied_source_frame']=int(sequence.source_frames[index])
    scene['surface_applied_time']=float(sequence.times[index])
    scene['surface_error']=''
    from . import viewport
    viewport.tag_redraw((scene,), area_types=None)


@persistent
def _frame(scene,*_):
    value=SESSIONS.get(scene.as_pointer())
    if value is not None:
        value['pending']=True


@persistent
def _invalidate(_):
    SESSIONS.clear()


@persistent
def _loaded(_):
    SESSIONS.clear()
    _retry_pending()
    for scene in bpy.data.scenes:
        if active(scene):
            scene['surface_error']='restoring captured RGB appearance'
            scene.pop('surface_restore_failed', None)


def _tick():
    scenes = tuple(bpy.data.scenes)
    live = {scene.as_pointer() for scene in scenes if active(scene)}
    for key in set(SESSIONS) - live:
        SESSIONS.pop(key, None)
    if not _RUNNING:
        return None
    for scene in scenes:
        if not active(scene) or scene.get('surface_restore_failed'):
            continue
        try:
            value=SESSIONS.get(scene.as_pointer())
            uid = getattr(scene, 'session_uid', scene.as_pointer())
            if value is not None and value.get('scene_uid', uid) != uid:
                SESSIONS.pop(scene.as_pointer(), None)
                value = None
            if value is None:
                restore(scene)
                value=SESSIONS[scene.as_pointer()]
            index=min(max(scene.frame_current-1,0),len(value['sequence'].states)-1)
            if value['pending'] or value['index']!=index:
                apply(scene)
        except Exception as exc:
            scene['surface_error']=str(exc)
            scene['surface_restore_failed']=True
            SESSIONS.pop(scene.as_pointer(),None)
            for obj in scene.objects:
                if obj.get('surface_role')=='estimate':
                    obj.hide_set(True,view_layer=scene.view_layers[0])
    return .02


def _callbacks():
    handlers = bpy.app.handlers
    return ((handlers.frame_change_post, _frame), (handlers.load_pre, _invalidate),
            (handlers.undo_pre, _invalidate), (handlers.redo_pre, _invalidate),
            (handlers.load_post, _loaded), (handlers.undo_post, _loaded),
            (handlers.redo_post, _loaded))


def register():
    from .split_reconstruction_lifecycle import _register_callbacks
    global _RUNNING
    with _register_callbacks(_callbacks(), _tick):
        pass
    _RUNNING = True


def unregister():
    from .split_reconstruction_lifecycle import _remove_callbacks
    global _RUNNING
    _RUNNING = False
    errors = _remove_callbacks(_callbacks(), _tick)
    SESSIONS.clear()
    if errors:
        raise errors[0]
