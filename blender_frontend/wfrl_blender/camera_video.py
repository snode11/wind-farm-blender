"""Isolated, explicit global-shutter export. Run only in background Blender.

Every shutter sample is rendered to scene-linear float EXR. Samples are averaged
before one view transform to the 16-bit PNG delivery master. No UI clock, native
motion blur, or frame blending participates in exposure integration.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def frame_times(start_s, end_s, fps, exposure_s):
    if fps not in (15, 20, 25):
        raise ValueError('Supported frame rates: 15, 20, 25')
    count = (end_s - start_s) * fps
    if not all(math.isfinite(v) for v in (start_s, end_s, exposure_s)) or not 0 < exposure_s <= 1 / fps:
        raise ValueError('Exposure must be finite and in (0, 1/fps]')
    if count <= 0 or abs(count - round(count)) > 1e-7:
        raise ValueError('Duration must be a positive integer number of frames')
    return start_s + (np.arange(round(count), dtype=np.float64) + .5) / fps


def shutter_times(center_s, exposure_s, samples):
    if samples < 1:
        raise ValueError('Exposure samples must be positive')
    return center_s + ((np.arange(samples) + .5) / samples - .5) * exposure_s


def is_auxiliary(obj):
    name = obj.name
    return (obj.type == 'FONT' or name.startswith(('WFRL.Grid.', 'WFRL.Inflow.',
            'WFRL.Fixture.', 'WFRL.Label.', 'WFRL.Wake', 'WFRL.Deflection.'))
            or any(token in name for token in ('.TipTrail.', '.ClearanceRadar.Beam',
                                               '.SensorFrustum', '.ReferenceLine')))


def clean_scene(scene):
    """hide_render excludes complete objects from camera, shadow and reflection rays."""
    hidden = []
    for obj in scene.objects:
        if is_auxiliary(obj):
            obj.hide_render = True
            hidden.append(obj.name)
    return sorted(hidden)


def _nodes(tree):
    if tree is None:
        return None
    result = []
    for node in tree.nodes:
        sockets = {}
        for socket in node.inputs:
            value = getattr(socket, 'default_value', None)
            if isinstance(value, (str, bool, float, int)) or value is None:
                sockets[socket.name] = value
            else:
                try: sockets[socket.name] = list(value)
                except TypeError: sockets[socket.name] = str(value)
        properties = {}
        for key in ('sky_type', 'sun_elevation', 'sun_rotation', 'sun_size',
                    'sun_intensity', 'altitude', 'air_density', 'dust_density',
                    'ozone_density', 'turbidity', 'ground_albedo', 'projection'):
            if hasattr(node, key): properties[key] = getattr(node, key)
        if getattr(node, 'image', None) is not None:
            import bpy
            path = Path(bpy.path.abspath(node.image.filepath))
            properties['image'] = {'name': node.image.name, 'filepath': str(path),
                                   'sha256': sha256(path) if path.is_file() else None,
                                   'packed': bool(node.image.packed_file)}
        result.append({'name': node.name, 'type': node.bl_idname, 'inputs': sockets,
                       'properties': properties})
    return {'nodes': result, 'links': [[l.from_node.name, l.from_socket.name,
            l.to_node.name, l.to_socket.name] for l in tree.links]}


def camera_configuration(scene, camera, args):
    from mathutils import Matrix
    from .turbine_geometry import geometry_data
    data = camera.data
    local_mount = camera.matrix_parent_inverse @ camera.matrix_basis
    mount = Matrix.Translation((0, 0, geometry_data()['scalars']['TowerHt'])) @ local_mount
    aspect = args.width / args.height
    horizontal = data.sensor_fit == 'HORIZONTAL' or (data.sensor_fit == 'AUTO' and aspect >= 1)
    effective_width = data.sensor_width if horizontal else data.sensor_height * aspect
    effective_height = data.sensor_width / aspect if horizontal else data.sensor_height
    return dict(schema='wfrl.camera-video-camera.v1', camera_name=camera.name,
        turbine_id=args.turbine, camera_mount=[list(row) for row in mount],
        camera_mount_definition='world = turbine_layout @ nacelle_source_transform @ camera_mount',
        parent=camera.parent.name, parent_mount_matrix_local=[list(row) for row in local_mount],
        lens_mm=data.lens, sensor_width_mm=data.sensor_width, sensor_height_mm=data.sensor_height,
        effective_sensor_width_mm=effective_width, effective_sensor_height_mm=effective_height,
        sensor_fit=data.sensor_fit, shift_x=data.shift_x, shift_y=data.shift_y,
        pixel_aspect=[scene.render.pixel_aspect_x, scene.render.pixel_aspect_y],
        focus_distance_m=data.dof.focus_distance, aperture_fstop=data.dof.aperture_fstop,
        dof_enabled=data.dof.use_dof, clip_start_m=data.clip_start, clip_end_m=data.clip_end,
        exposure_s=args.exposure_s, shutter='ideal_global_uniform',
        camera_model='ideal perspective; no distortion, sensor noise, autofocus or stabilization',
        resolution=[args.width, args.height], world={'name': scene.world.name,
            'color': list(scene.world.color), 'nodes': _nodes(scene.world.node_tree)},
        lights=[dict(name=o.name, type=o.data.type, energy=o.data.energy,
            color=list(o.data.color), matrix_world=[list(row) for row in o.matrix_world],
            angle=getattr(o.data, 'angle', None), nodes=_nodes(o.data.node_tree))
            for o in scene.objects if o.type == 'LIGHT'],
        color_management=dict(display_device=scene.display_settings.display_device,
            view_transform=scene.view_settings.view_transform, look=scene.view_settings.look,
            exposure=scene.view_settings.exposure, gamma=scene.view_settings.gamma))


def prepare_scene(args):
    import bpy
    import wfrl_blender
    from . import cameras, farm_flex
    if not bpy.app.background:
        raise RuntimeError('Export requires separate Blender --background process; live scene is protected')
    wfrl_blender.register()
    if args.scene:
        bpy.ops.wm.open_mainfile(filepath=str(Path(args.scene).resolve()))
        scene = bpy.context.scene
        farm_flex.attach(scene, args.package)
    else:
        wfrl_blender.load_demo_scene(args.package)
        scene = bpy.context.scene
    active = farm_flex._ACTIVE
    if args.turbine not in active.readers:
        raise ValueError('Turbine absent from source package')
    camera = scene.objects.get(args.camera) if args.camera else cameras.ensure_nacelle_gimbal(scene, args.turbine)
    if camera is None or camera.type != 'CAMERA' or camera.parent != scene.objects.get(f'WFRL.Turbine.{args.turbine}.YawRoot'):
        raise ValueError('Select a camera mounted on the target nacelle YawRoot')
    if camera.data.type != 'PERSP':
        raise ValueError('First camera video implementation requires a perspective camera')
    if not camera.get('wfrl_nacelle_camera'):
        raise ValueError('Camera must be a nacelle-mounted camera, not the low Down demonstration camera')
    if camera.constraints or camera.animation_data or camera.data.animation_data:
        raise ValueError('Camera constraints/animation are incompatible with a frozen installation')
    scene.camera = camera
    # Only this isolated background process is affected; callbacks must not reset
    # geometry to the review clock when Cycles evaluates an exposure sample.
    bpy.app.handlers.frame_change_pre.clear()
    bpy.app.handlers.frame_change_post.clear()
    scene.wfrl_show_wake = False
    scene.wfrl_flex_show_tip_trails = False
    scene.wfrl_deflection_visible = False
    active.visible_turbines = set(range(len(active.readers)))
    removed = []
    if args.scene_scope == 'single':
        from . import tip_tracking
        tip_tracking.discard(scene, 'single_turbine_export')
        index = list(active.readers).index(args.turbine)
        active.export_turbines = {index}
        active.visible_turbines = {index}
        active.blades = [record for record in active.blades if record[5] == index]
        active.towers = [record for record in active.towers if record[4] == index]
        active.fittings = [record for record in active.fittings if record[1] == index]
        other_ids = set(active.readers) - {args.turbine}
        for obj in list(scene.objects):
            if is_auxiliary(obj) or any(tid in obj.name.split('.') for tid in other_ids):
                removed.append(obj.name)
                bpy.data.objects.remove(obj, do_unlink=True)
    active.export_removed_objects = sorted(removed)
    active.cache.clear()
    # View selection may hide physical turbines. Restore real geometry, retain
    # existing material opacity and keep the camera position exactly as selected.
    for obj in scene.objects:
        if obj.name.startswith('WFRL.Turbine.') and not is_auxiliary(obj):
            obj.hide_render = False
    scene.render.engine = 'CYCLES'
    if args.engine == 'eevee':
        scene.render.engine = 'BLENDER_EEVEE'
        scene.eevee.taa_render_samples = args.samples
    elif args.device == 'metal':
        preferences = bpy.context.preferences.addons['cycles'].preferences
        preferences.compute_device_type = 'METAL'
        preferences.get_devices()
        available = [device for device in preferences.devices if device.type == 'METAL']
        if not available:
            raise ValueError('Cycles Metal device is unavailable; explicit CPU selection required')
        for device in preferences.devices:
            device.use = device.type == 'METAL'
        scene.cycles.device = 'GPU'
    else:
        scene.cycles.device = 'CPU'
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = False
    scene.cycles.seed = 0
    scene.cycles.use_animated_seed = False
    scene.render.use_motion_blur = False
    scene.render.resolution_x, scene.render.resolution_y = args.width, args.height
    scene.render.resolution_percentage = 100
    scene.render.use_border = False
    scene.render.use_crop_to_border = False
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.fps, scene.render.fps_base = args.fps, 1
    scene.render.film_transparent = False
    scene.use_nodes = False
    scene.render.use_sequencer = False
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.image_settings.exr_codec = args.exr_codec
    scene.render.image_settings.color_management = 'FOLLOW_SCENE'
    if camera.data.dof.focus_object is not None and args.focus_distance is None:
        camera.data.dof.focus_distance = (camera.matrix_world.translation
            - camera.data.dof.focus_object.matrix_world.translation).length
    camera.data.dof.focus_object = None
    if args.focus_distance is not None: camera.data.dof.focus_distance = args.focus_distance
    if args.aperture is not None: camera.data.dof.aperture_fstop = args.aperture
    # Existing saved optical settings are preserved unless explicitly overridden.
    if not args.scene:
        camera.data.dof.focus_distance = args.focus_distance or 60.
        camera.data.dof.aperture_fstop = args.aperture or 8.
        camera.data.dof.use_dof = False
    bpy.context.view_layer.update()
    return scene, camera, active, clean_scene(scene)


@contextmanager
def output_lock(output, package):
    """Non-blocking process lock; OS releases it after failure/termination."""
    output, package = Path(output).resolve(), Path(package).resolve()
    if output == package or package in output.parents:
        raise ValueError('Export must not write into the source result package')
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.render.lock').open('a+b') as handle:
        try:
            import fcntl
        except ImportError:  # Windows: lock one persistent byte.
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError('Another render process owns this output directory') from error
        else:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError('Another render process owns this output directory') from error
        yield output


def render_package(args):
    with output_lock(args.output, args.package):
        return _render_package(args)


def _render_package(args):
    import bpy
    for name in ('focus_distance', 'aperture'):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(name + ' must be finite and positive')
    if args.samples < 1 or args.exposure_samples < 1:
        raise ValueError('Spatial and exposure samples must be positive')
    if (args.width, args.height) not in ((2560, 1440), (3840, 2160)) and not args.test_resolution:
        raise ValueError('Delivery resolution must be 2560x1440 or 3840x2160; use --test-resolution for diagnostics')
    if args.width < 1 or args.height < 1:
        raise ValueError('Resolution must be positive')
    args.exposure_s = args.exposure_s if args.exposure_s is not None else .001
    preparation_begun = time.monotonic()
    scene, camera, active, hidden = prepare_scene(args)
    preparation_seconds = time.monotonic() - preparation_begun
    start = float(active.times[0]) if args.start_s is None else args.start_s
    end = float(active.times[-1]) if args.end_s is None else args.end_s
    if start < active.times[0] or end > active.times[-1]:
        raise ValueError('Requested segment lies outside source results')
    centers = frame_times(start, end, args.fps, args.exposure_s)
    camera_config = camera_configuration(scene, camera, args)
    package = Path(args.package)
    source_hashes = {name: sha256(package / name)
                     for name in sorted(set(active.manifest['files']) | {'manifest.json'})}
    config = dict(schema='wfrl.camera-video-render.v1', source_sha256=source_hashes,
        source_status=active.manifest['status'], blender_version=bpy.app.version_string,
        camera=camera_config, scene_sha256=sha256(args.scene) if args.scene else None,
        code_sha256={str(p.relative_to(Path(__file__).parent)): sha256(p)
                     for p in sorted(Path(__file__).parent.rglob('*.py'))},
        render_assets_sha256={str(p.relative_to(Path(__file__).parent / 'assets')): sha256(p)
                     for p in sorted((Path(__file__).parent / 'assets').rglob('*'))
                     if p.is_file() and 'mappo' not in p.parts},
        external_image_sha256={image.name: sha256(bpy.path.abspath(image.filepath))
            for image in bpy.data.images if image.filepath and not image.packed_file
            and Path(bpy.path.abspath(image.filepath)).is_file()},
        fps=args.fps, start_s=start, end_s=end, frame_count=len(centers),
        width=args.width, height=args.height, exposure_s=args.exposure_s,
        exposure_samples=args.exposure_samples, spatial_samples=args.samples,
        integration='uniform midpoint shutter quadrature; scene-linear float EXR then one view transform',
        intermediate_exr_codec=scene.render.image_settings.exr_codec,
        output_color_management=scene.render.image_settings.color_management,
        master='16-bit PNG after fixed color transform', engine=scene.render.engine,
        device=args.device if args.engine == 'cycles' else 'gpu', denoising=False,
        scene_scope=args.scene_scope, rendered_turbines=[args.turbine] if args.scene_scope == 'single' else list(active.readers),
        removed_objects=active.export_removed_objects, source_turbines=list(active.readers),
        new_physics_solve=False, source_wake_influence_retained=True,
        seed='exposure_sample_index; fixed across output frames', auxiliary_hidden=hidden, diagnostic_resolution=bool(args.test_resolution),
        sampling_status='finite quadrature; convergence must be checked for chosen production quality')
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode()).hexdigest()
    output = Path(args.output).resolve()
    if output == package.resolve() or package.resolve() in output.parents:
        raise ValueError('Export must not write into the source result package')
    output.mkdir(parents=True, exist_ok=True)
    frames = output / 'master_frames'; frames.mkdir(exist_ok=True)
    checks = output / 'checks'; checks.mkdir(exist_ok=True)
    render_manifest = output / 'render_manifest.json'
    if render_manifest.exists():
        if json.loads(render_manifest.read_text())['config_sha256'] != fingerprint:
            raise ValueError('Resume configuration mismatch; use a new output directory')
    elif any(frames.iterdir()):
        raise ValueError('Existing frames without matching render manifest; refusing mixed frames')
    write_json(render_manifest, {**config, 'config_sha256': fingerprint})
    write_json(output / 'camera.json', camera_config)
    record_path = checks / 'render_frames.json'
    records = json.loads(record_path.read_text()) if record_path.exists() else {}
    for key in list(records):
        if not key.isdigit() or not 0 <= int(key) < len(centers):
            raise ValueError('Frame record outside configured range')
        target = frames / f'{int(key):06d}.png'
        if not target.exists():
            del records[key]
        elif sha256(target) != records[key]['sha256']:
            raise ValueError(f'Changed master frame: {target}')
    for target in frames.glob('*.png'):
        if target.name.endswith('.partial.png'):
            continue
        if not target.stem.isdigit() or str(int(target.stem)) not in records:
            raise ValueError(f'Unverified master frame: {target}')
    limit = len(centers) if args.max_frames is None else min(len(centers), args.max_frames)
    if limit < 1: raise ValueError('--max-frames must be positive')
    begun = time.monotonic()
    temporary_exr = output / '.exposure_sample.exr'
    matrices = np.empty((len(centers), 4, 4), dtype=np.float64)
    for index, center in enumerate(centers):
        active.update(scene, sim_time_s=float(center), record_telemetry=False)
        bpy.context.view_layer.update()
        matrices[index] = np.asarray(camera.matrix_world)
    np.save(output / 'camera_world.npy', matrices, allow_pickle=False)
    rendered = 0
    for index in range(limit):
        target = frames / f'{index:06d}.png'
        previous = records.get(str(index))
        if target.exists():
            if previous is None or sha256(target) != previous['sha256']:
                raise ValueError(f'Unverified or changed master frame: {target}')
            continue
        accumulation = np.zeros(args.width * args.height * 4, dtype=np.float64)
        times = shutter_times(centers[index], args.exposure_s, args.exposure_samples)
        frame_begun = time.monotonic()
        timings = dict(geometry=0., render_write=0., read_accumulate=0., master_write=0.)
        for sample_index, sample_time in enumerate(times):
            stage = time.monotonic()
            scene.cycles.seed = sample_index
            active.update(scene, sim_time_s=float(sample_time), record_telemetry=False)
            clean_scene(scene)
            bpy.context.view_layer.update()
            timings['geometry'] += time.monotonic() - stage
            scene.render.image_settings.file_format = 'OPEN_EXR'
            scene.render.image_settings.color_depth = '32'
            scene.render.filepath = str(temporary_exr)
            stage = time.monotonic()
            bpy.ops.render.render(write_still=True)
            timings['render_write'] += time.monotonic() - stage
            stage = time.monotonic()
            linear = bpy.data.images.load(str(temporary_exr), check_existing=False)
            try:
                pixels = np.empty(len(accumulation), dtype=np.float32)
                linear.pixels.foreach_get(pixels)
                if not np.isfinite(pixels).all(): raise ValueError('Non-finite linear radiance')
                accumulation += pixels / args.exposure_samples
            finally:
                bpy.data.images.remove(linear)
            timings['read_accumulate'] += time.monotonic() - stage
        stage = time.monotonic()
        integrated = bpy.data.images.new('WFRL.IntegratedLinearExposure', width=args.width,
                                         height=args.height, alpha=True, float_buffer=True)
        try:
            integrated.colorspace_settings.name = 'Linear Rec.709'
            integrated.pixels.foreach_set(accumulation.astype(np.float32))
            scene.render.image_settings.file_format = 'PNG'
            scene.render.image_settings.color_depth = '16'
            temporary_png = target.with_suffix('.partial.png')
            integrated.save_render(str(temporary_png), scene=scene)
            timings['master_write'] = time.monotonic() - stage
            # Commit the verified record before publishing the PNG. If interrupted
            # here, resume discards a record with no final PNG and renders again;
            # a published PNG can never lack its verification record.
            records[str(index)] = dict(sha256=sha256(temporary_png), sim_time_s=float(centers[index]),
                exposure_samples_s=times.tolist(), seconds=time.monotonic()-frame_begun,
                timings=timings,
                linear_rgb_mean=float(accumulation.reshape(-1, 4)[:, :3].mean()))
            write_json(record_path, records)
            temporary_png.replace(target)
        finally:
            bpy.data.images.remove(integrated)
        rendered += 1
        print(f'CAMERA_VIDEO_FRAME {index + 1}/{len(centers)} {target}', flush=True)
    temporary_exr.unlink(missing_ok=True)
    result = dict(status='complete' if len(records) == len(centers) else 'partial',
        expected_frames=len(centers), verified_frames=len(records), rendered_this_run=rendered,
        wall_seconds=time.monotonic()-begun, preparation_seconds=preparation_seconds, config_sha256=fingerprint,
        source_status=active.manifest['status'], output=str(output))
    write_json(checks / 'render.json', result)
    write_json(render_manifest, {**config, 'config_sha256': fingerprint,
                                'status': result['status'], 'complete': result['status'] == 'complete'})
    print('CAMERA_VIDEO_RENDER', json.dumps(result), flush=True)
    return result
