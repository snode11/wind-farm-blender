"""Isolated Down export scenes, render settings and fixed-camera validation."""
import math
import uuid

import bpy

from . import manual_control
from . import manual_down as _down


def _camera_optics(camera, scene):
    data, render = camera.data, scene.render
    return dict(lens_mm=data.lens, sensor_width_mm=data.sensor_width,
                vertical_fov_deg=math.degrees(2*math.atan(data.sensor_width/(2*data.lens)*
                    render.resolution_y/render.resolution_x)),
                horizontal_fov_deg=math.degrees(data.angle_x), yaw_deg=camera['gimbal_yaw'],
                pitch_deg=camera['gimbal_pitch'], roll_deg=camera['gimbal_roll'], lens_distortion='none')


def _check_export_camera(scene, job, *, frame=None, depsgraph=None):
    """Validate the fixed camera before accepting a rendered frame or publishing."""
    camera = job['camera']
    if (scene != job['scene'] or scene.camera != camera or camera.data != job['camera_data']
            or scene.objects.get(camera.name) != camera
            or camera.get('wfrl_manual_owner') != scene.get('wfrl_manual_owner')):
        raise ValueError('Down 导出相机身份或场景归属不一致')
    if depsgraph is None:
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            depsgraph = bpy.context.evaluated_depsgraph_get()
    actual = _down._camera_optics(camera.evaluated_get(depsgraph), scene)
    expected = job['camera_optics']
    if (job['sidecar']['optics'] != expected or any(
            actual[key] != value if isinstance(value, str) else
            not math.isclose(actual[key], value, rel_tol=1e-7, abs_tol=1e-7)
            for key, value in expected.items())):
        raise ValueError('Down 导出实际光学参数与 JSON 不一致')
    if frame is not None:
        if type(frame) is not int or not 1 <= frame <= job['plan']['frames']:
            raise ValueError('Down 导出相机检查帧超出输出范围')
        job['camera_frames'].add(frame)


def _clone_scene(source):
    """Copy scene resources before any camera, overlay, material or motion writes."""
    owner = 'WFRL.DownExport.' + uuid.uuid4().hex
    clone = source.copy()
    clone.name = owner
    clone['wfrl_manual_owner'] = owner
    clone['wfrl_manual_exporting'] = True
    objects, data_copies, material_copies = {}, {}, {}
    previous = manual_control._updating
    manual_control._updating = True
    try:
        for marker in clone.timeline_markers:
            marker.camera = None
        for collection in list(clone.collection.children):
            clone.collection.children.unlink(collection)
        for obj in list(clone.collection.objects):
            clone.collection.objects.unlink(obj)
        collection = bpy.data.collections.new(owner + '.Objects')
        collection['wfrl_manual_owner'] = owner
        clone.collection.children.link(collection)
        for original in source.objects:
            obj = original.copy()
            obj.name = owner + '.' + original.name
            obj['wfrl_manual_owner'] = owner
            objects[original] = obj
            collection.objects.link(obj)
            if original.data is not None:
                if original.data not in data_copies:
                    data = original.data.copy()
                    data.name = owner + '.' + original.data.name
                    data['wfrl_manual_owner'] = owner
                    if original.type == 'MESH' and data.get('wfrl_manual_mesh_owner'):
                        data['wfrl_manual_mesh_owner'] = owner
                    data_copies[original.data] = data
                    if hasattr(data, 'materials'):
                        for index, material in enumerate(data.materials):
                            if material is not None:
                                if material not in material_copies:
                                    copy = material.copy()
                                    copy.name = owner + '.' + material.name
                                    copy['wfrl_manual_owner'] = owner
                                    material_copies[material] = copy
                                data.materials[index] = material_copies[material]
                obj.data = data_copies[original.data]
        for original, obj in objects.items():
            obj.parent = objects.get(original.parent)
            for constraint in obj.constraints:
                if hasattr(constraint, 'target') and constraint.target in objects:
                    constraint.target = objects[constraint.target]
        if source.world:
            clone.world = source.world.copy()
            clone.world.name = owner + '.World'
            clone.world['wfrl_manual_owner'] = owner
        clone.camera = objects.get(source.camera)
        for entry in clone.wfrl_manual_turbines:
            for field in manual_control._POINTER_FIELDS:
                setattr(entry, field, objects[getattr(entry, field)])
            entry.running = False
            entry.pitch_paused = True
        clone.wfrl_manual_return_scene = None
        valid, reason = manual_control.validate_scene(clone)
        if not valid:
            raise ValueError(reason)
        return clone
    except BaseException:
        _down._drop_scene(clone)
        raise
    finally:
        manual_control._updating = previous


def _drop_scene(scene):
    """Release only export-owned, unshared resources; preserve users and fake users."""
    owner = scene.get('wfrl_manual_owner')
    shared = manual_control.other_scene_objects(scene)
    for obj in list(scene.objects):
        if obj.get('wfrl_manual_owner') == owner and obj.as_pointer() not in shared:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.scenes.remove(scene)
    for group in (bpy.data.collections, bpy.data.meshes, bpy.data.curves, bpy.data.cameras,
                  bpy.data.lights, bpy.data.worlds, bpy.data.materials):
        for data in list(group):
            if (data.get('wfrl_manual_owner') == owner and data.users == 0
                    and not data.use_fake_user and data.library is None
                    and not getattr(data, 'shape_keys', None)):
                group.remove(data)
    _down._STATIONS.clear()


def _configure_video(scene, camera, fps, frames, engine, filepath, *, size=_down.VIDEO_SIZE, samples=None):
    render = scene.render
    scene.camera = camera
    render.use_compositing = render.use_sequencer = False
    if engine == 'WORKBENCH':
        render.engine = 'BLENDER_WORKBENCH'
    if engine == 'SCENE':
        render.engine = 'BLENDER_EEVEE'
    if engine == 'SCENE' and samples:
        scene.eevee.taa_render_samples = int(samples)
    if engine in _down.VIEWPORT_ENGINES:
        # The viewport path is bounded by per-frame colour processing, so the
        # export uses the plain sRGB transform without dithering or compositing.
        scene.view_settings.view_transform, scene.view_settings.look = 'Standard', 'None'
        render.dither_intensity = 0
        scene.eevee.taa_samples = 1
        scene.display.render_aa = 'FXAA'
    render.resolution_x, render.resolution_y = size
    render.resolution_percentage = 100
    render.pixel_aspect_x = render.pixel_aspect_y = 1
    render.fps, render.fps_base = fps, 1.0
    scene.frame_start, scene.frame_end, scene.frame_step = 1, frames, 1
    render.image_settings.media_type = 'VIDEO'
    render.image_settings.file_format = 'FFMPEG'
    render.image_settings.color_mode = 'RGB'
    render.ffmpeg.format = 'MPEG4'
    render.ffmpeg.codec = 'H264'
    render.ffmpeg.constant_rate_factor = 'HIGH'
    render.ffmpeg.ffmpeg_preset = 'REALTIME' if engine in _down.VIEWPORT_ENGINES else 'GOOD'
    render.ffmpeg.gopsize = fps
    render.ffmpeg.audio_codec = 'NONE'
    render.use_file_extension = True
    render.use_lock_interface = True
    render.filepath = filepath
