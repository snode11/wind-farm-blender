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


def _embed(scene,key,raw,prefix):
    text=bpy.data.texts.new(prefix+'.'+key)
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


def _material(name,image=None,color=(1.,.55,.10,1.)):
    material=bpy.data.materials.new(name)
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


def _mesh(scene,name,vertices,rotor,texture_material,unknown_material,blade):
    data=bpy.data.meshes.new(name)
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


def _camera(scene,name,position,target):
    data=bpy.data.cameras.new(name)
    data.lens=45
    data.clip_end=3000
    obj=bpy.data.objects.new(name,data)
    scene.collection.objects.link(obj)
    obj.location=position
    obj.rotation_euler=(Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler()
    obj['surface_role']='view_camera'
    return obj


def load_surface(context,surface_path):
    """Validate before scene creation; never auto-load adjacent truth data."""
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
    try:
        scene['wfrl_scene_kind']='blade_recon_surface'
        scene['surface_source_path']=str(path)
        scene['surface_status']='REVIEW_ONLY'
        scene['surface_model_sha256']=MODEL_SHA256
        scene['surface_recon_sha256']=metadata['recon_sha256']
        scene['surface_appearance_scope']=metadata['appearance_scope']
        scene['surface_legend']='captured input RGB; orange unknown; purple conflict; appearance is not geometry accuracy'
        _embed(scene,'recon',json.dumps(package['recon_data'],ensure_ascii=False,allow_nan=False),prefix)
        _embed(scene,'sidecar',json.dumps(metadata,ensure_ascii=False,allow_nan=False),prefix)
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
        scene.world=bpy.data.worlds.new(prefix+'.World')
        scene.world.color=(.025,.035,.055)
        unknown=_material(prefix+'.Unknown')
        vertices,_=sequence.geometry(0)
        blades=[]
        for blade,record in enumerate(metadata['textures']):
            image=bpy.data.images.load(str(path.parent/record['path']),check_existing=False)
            image.colorspace_settings.name='sRGB'
            image.pack()
            image['surface_texture_sha256']=record['sha256']
            material=_material(prefix+f'.B{blade+1}.CapturedRGB',image)
            blades.append(_mesh(scene,prefix+f'.B{blade+1}',vertices[blade],sequence.rotor,material,unknown,blade))
        hub=sequence.rotor.hub
        radius=sequence.rotor.cfg.tip_radius_m
        scene.camera=_camera(scene,prefix+'.Overview',hub+np.array([radius*4,-radius*2,radius*.25]),hub)
        SESSIONS[scene.as_pointer()]=dict(sequence=sequence,blades=blades,pending=True,index=None)
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
    except Exception:
        if context.window and context.window.scene==scene:
            context.window.scene=previous
        SESSIONS.pop(scene.as_pointer(),None)
        bpy.data.scenes.remove(scene)
        raise


def restore(scene):
    metadata=_embedded(scene,'sidecar')
    recon=_embedded(scene,'recon')
    if metadata['model_sha256']!=MODEL_SHA256 or scene.get('surface_model_sha256')!=MODEL_SHA256:
        raise ValueError('saved surface model identity mismatch')
    sequence=ReconSequence.from_data(recon,truth_data=None,cameras_data=None,
        source_label='Saved captured RGB appearance; REVIEW_ONLY',source_sha256=metadata['recon_sha256'])
    blades=sorted((o for o in scene.objects if o.get('surface_role')=='estimate'),key=lambda o:o['surface_blade_id'])
    if len(blades)!=3 or any(len(o.data.vertices)!=sequence.rotor.cfg.n_sections*sequence.rotor.cfg.n_ring for o in blades):
        raise ValueError('saved surface mesh dimensions mismatch')
    for obj in blades:
        if 'template_material_q' not in obj.data.uv_layers:
            raise ValueError('saved fixed UV binding missing')
        texture=obj.data.materials[0].node_tree.nodes.get('Image Texture')
        if texture is None or texture.image is None or texture.image.packed_file is None:
            raise ValueError('saved captured RGB atlas is not packed')
    SESSIONS[scene.as_pointer()]=dict(sequence=sequence,blades=blades,pending=True,index=None)
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
    for window in bpy.context.window_manager.windows:
        if window.scene==scene:
            for area in window.screen.areas:
                area.tag_redraw()


@persistent
def _frame(scene,*_):
    value=SESSIONS.get(scene.as_pointer())
    if value is not None:
        value['pending']=True


@persistent
def _loaded(_):
    SESSIONS.clear()
    for scene in bpy.data.scenes:
        if active(scene):
            scene['surface_error']='restoring captured RGB appearance'


def _tick():
    for scene in tuple(bpy.data.scenes):
        if not active(scene) or scene.get('surface_restore_failed'):
            continue
        try:
            value=SESSIONS.get(scene.as_pointer())
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


def register():
    for handlers,callback in ((bpy.app.handlers.frame_change_post,_frame),(bpy.app.handlers.load_post,_loaded)):
        if callback not in handlers:
            handlers.append(callback)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick,first_interval=.02,persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for handlers,callback in ((bpy.app.handlers.frame_change_post,_frame),(bpy.app.handlers.load_post,_loaded)):
        if callback in handlers:
            handlers.remove(callback)
    SESSIONS.clear()
