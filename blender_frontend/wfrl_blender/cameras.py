"""Meter-scale camera rig with explicit rotor-envelope framing."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CameraView:
    name: str
    label: str
    fov_deg: float
    focus: str


WORLD_LOCATION = (-330, -780, 245)
WORLD_TARGET = (540, 0, 105)
WORLD_LENS_MM = 28.5


CAMERA_VIEWS = (
    CameraView('WFRL.Camera.World', 'World', 41.0, 'farm'),
    CameraView('WFRL.Camera.Top', 'Top', 41.0, 'farm'),
    CameraView('WFRL.Camera.Side', 'Side', 41.0, 'farm'),
    CameraView('WFRL.Camera.T1.Closeup', 'T1 Closeup', 48.0, 'T1'),
    CameraView('WFRL.Camera.T1.Sensor', 'T1 Sensor', 75.0, 'T1'),
)


def camera_view_names():
    return tuple(view.name for view in CAMERA_VIEWS)


def select_camera(scene, name: str, *, fov_deg: float | None = None, focus: str | None = None):
    """Select a named camera and update optional FOV/focus metadata."""
    camera = scene.objects.get(name)
    if camera is None or getattr(camera, 'type', None) != 'CAMERA':
        raise ValueError(f'Unknown WFRL camera: {name}')
    if fov_deg is not None:
        value = float(fov_deg)
        if not 1.0 <= value <= 179.0:
            raise ValueError('fov_deg must be between 1 and 179')
        import math
        camera.data.lens = 36.0 / (2.0 * math.tan(math.radians(value) / 2.0))
        camera['fov_deg'] = value
    if focus is not None:
        camera['focus'] = str(focus)
    scene.camera = camera
    scene['wfrl_camera'] = camera.name
    return camera


def build_cameras(scene):
    import bpy
    from mathutils import Vector

    collection = bpy.data.collections['WFRL_Scene']
    center = Vector((sum(t.x_m for t in scene.turbines) / len(scene.turbines),
                     sum(t.y_m for t in scene.turbines) / len(scene.turbines), 68))
    points = [Vector((t.x_m + x, t.y_m + y, z)) for t in scene.turbines
              for x in (-70, 70) for y in (-70, 70) for z in (0, 155)]
    cameras = {}
    def create(name, location, target, bounds=None):
        data = bpy.data.cameras.new(name + '.Data')
        data.show_passepartout = False
        data.passepartout_alpha = 0
        camera = bpy.data.objects.new(name, data)
        collection.objects.link(camera)
        camera.location = location
        camera.rotation_euler = (Vector(target) - camera.location).to_track_quat('-Z', 'Y').to_euler()
        data.clip_start, data.clip_end = .1, 30000
        data.lens = 48
        data['fov_deg'] = 41.0
        camera['fidelity'] = 'SYNTH'
        if bounds:
            data.type = 'ORTHO'
            inverse = camera.rotation_euler.to_matrix().transposed()
            local = [inverse @ (p - Vector(target)) for p in bounds]
            aspect = bpy.context.scene.render.resolution_x / bpy.context.scene.render.resolution_y
            data.ortho_scale = 2 * max(max(abs(p.x) for p in local), max(abs(p.y) for p in local) * aspect) * 1.15
        cameras[name] = camera
        return camera
    # A slightly more lateral overview balances the three turbines while
    # retaining terrain depth and the full downstream wake corridor.
    world = create('WFRL.Camera.World', Vector(WORLD_LOCATION), Vector(WORLD_TARGET))
    world.data.lens = WORLD_LENS_MM
    create('WFRL.Camera.Top', center + Vector((0, 0, 1500)), center, points)
    create('WFRL.Camera.Side', center + Vector((0, -1500, 160)), center, points)
    first = scene.turbines[0]
    t1 = Vector((first.x_m, first.y_m, 0))
    create('WFRL.Camera.T1.Closeup', t1 + Vector((-150, -205, 126)), t1 + Vector((-3, 0, 96)))
    sensor = create('WFRL.Camera.T1.Sensor', (-4, -2.4, -2.4), (-9, 0, -76))
    sensor.parent = bpy.data.objects[f'WFRL.Turbine.{first.turbine_id}.YawRoot']
    from .turbine_geometry import geometry_data
    sensor.location.z += geometry_data()['scalars']['Twr2Shft']
    sensor.data.lens = 27
    sensor['mount'] = 'nacelle_down / geometric clearance view'
    sensor.data.display_size = 1.2
    bpy.context.scene.camera = cameras['WFRL.Camera.World']
    bpy.context.scene['wfrl_camera'] = 'WFRL.Camera.World'
    bpy.context.scene['wfrl_camera_views'] = ','.join(cameras)
    bpy.context.view_layer.update()
    return cameras


def camera_angles(fov_deg, pitch_deg):
    """Validate optical FOV and elevation, in degrees (positive looks up)."""
    import math
    fov, pitch = float(fov_deg), float(pitch_deg)
    if not math.isfinite(fov) or not 1 <= fov <= 179:
        raise ValueError('FOV must be between 1 and 179 degrees')
    if not math.isfinite(pitch) or not -89.9 <= pitch <= 89.9:
        raise ValueError('Pitch must be between -89.9 and 89.9 degrees')
    return fov, pitch


def configure_camera(scene, name, fov_deg, pitch_deg, focus='farm'):
    """Aim at a farm/turbine target, retaining distance and horizontal bearing."""
    import math
    from mathutils import Vector
    fov, pitch = camera_angles(fov_deg, pitch_deg)
    camera = scene.objects.get(name)
    if camera is None or camera.type != 'CAMERA':
        raise ValueError('Select an existing WFRL camera')
    roots = [obj for obj in scene.objects if obj.name.startswith('WFRL.Turbine.')
             and obj.name.endswith('.YawRoot')]
    if focus != 'farm':
        roots = [obj for obj in roots if obj.name == f'WFRL.Turbine.{focus}.YawRoot']
    if not roots:
        raise ValueError(f'Focus target is unavailable: {focus}')
    target = sum((obj.matrix_world.translation for obj in roots), Vector()) / len(roots)
    # Mounted sensor cameras stay on their nacelle; pitch changes their viewing
    # direction in mount coordinates. Other cameras orbit the selected focus.
    if camera.parent:
        direction = Vector((math.cos(math.radians(pitch)), 0, math.sin(math.radians(pitch))))
        camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    else:
        offset = camera.location - target
        radius = max(offset.length, 1)
        bearing = math.atan2(offset.y, offset.x)
        elevation = -math.radians(pitch)
        camera.location = target + Vector((radius * math.cos(elevation) * math.cos(bearing),
                                            radius * math.cos(elevation) * math.sin(bearing),
                                            radius * math.sin(elevation)))
        camera.rotation_euler = (target-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type = 'PERSP'
    camera.data.sensor_fit = 'HORIZONTAL'
    camera.data.sensor_width = 36.0
    select_camera(scene, name, fov_deg=fov, focus=focus)
    camera['pitch_deg'] = pitch
    return camera


_LAYOUT_JOB = None


def _layout_views(screen):
    return sorted((a for a in screen.areas if a.type == 'VIEW_3D'),
                  key=lambda a: (a.x, -a.y))


def _layout_active_views(screen):
    return [area for area in _layout_views(screen)
            if area.width > 0 and area.height > 0]


def _layout_finish(job):
    screen, scene, count, layout = job['screen'], job['scene'], job['count'], job['layout']
    names = ('WFRL.Camera.World', 'WFRL.Camera.T1.Sensor',
             'WFRL.Camera.Top', 'WFRL.Camera.Side')
    active = _layout_active_views(screen)
    if len(active) != count:
        return False
    for index, area in enumerate(active):
        space = area.spaces.active
        space.use_local_camera = count > 1 and index > 0
        space.camera = scene.objects.get(names[index % count])
        space.region_3d.view_perspective = 'CAMERA'
        space.region_3d.view_camera_zoom = 0
        space.region_3d.view_camera_offset = (0, 0)
        fill_camera_view(area, scene)
    scene.camera = scene.objects.get(names[0])
    scene['wfrl_view_layout'] = layout
    return True


def _layout_timer():
    global _LAYOUT_JOB
    if _LAYOUT_JOB is None:
        return None
    _layout_step()
    # Keep one timer alive across the redraws required by Blender's screen
    # operators. Returning an interval is important: a timer is considered
    # registered while its callback is running, so trying to register a new
    # callback from inside `_layout_step` can otherwise stall after one split.
    return 0.05 if _LAYOUT_JOB is not None else None


def _layout_schedule():
    import bpy
    if not bpy.app.timers.is_registered(_layout_timer):
        bpy.app.timers.register(_layout_timer, first_interval=0.05)


def _layout_step():
    """Perform one area operation, yielding between joins/splits for redraw."""
    global _LAYOUT_JOB
    import bpy
    job = _LAYOUT_JOB
    if job is None:
        return
    screen, count = job['screen'], job['count']
    active = _layout_active_views(screen)
    if len(active) > count:
        # Keep the largest existing view as source so shrinking does not leave
        # the user with a tiny strip after Blender applies the join.
        source = max(active, key=lambda area: (area.width * area.height, -area.x, -area.y))
        others = [area for area in active if area is not source]
        def distance(area):
            dx = max(area.x - (source.x + source.width),
                     source.x - (area.x + area.width), 0)
            dy = max(area.y - (source.y + source.height),
                     source.y - (area.y + area.height), 0)
            return dx + dy
        target = min(others, key=lambda area: (distance(area), -area.width * area.height))
        with bpy.context.temp_override(window=job['window'], screen=screen, area=source):
            result = bpy.ops.screen.area_join(
                source_xy=(source.x + source.width // 2, source.y + source.height // 2),
                target_xy=(target.x + target.width // 2, target.y + target.height // 2))
        if 'FINISHED' not in result:
            _LAYOUT_JOB = None
            raise ValueError('Blender could not join this view')
        _layout_schedule()
        return
    if len(active) < count:
        # A split creates a temporary zero-sized area. Wait for redraw before
        # issuing the next split; otherwise repeated calls can produce a bad
        # screen tree or loop forever.
        if len(_layout_views(screen)) >= count:
            _layout_schedule()
            return
        area = max(active, key=lambda item: item.width * item.height)
        with bpy.context.temp_override(window=job['window'], screen=screen, area=area):
            result = bpy.ops.screen.area_split(
                direction='VERTICAL' if len(_layout_views(screen)) == 1 else 'HORIZONTAL',
                factor=.5)
        if 'FINISHED' not in result:
            _LAYOUT_JOB = None
            raise ValueError('Blender could not split this area')
        _layout_schedule()
        return
    if _layout_finish(job):
        _LAYOUT_JOB = None


def set_view_layout(context, layout):
    """Set the number of WFRL 3D views, yielding between screen operations."""
    import bpy
    global _LAYOUT_JOB
    if layout not in {'SINGLE', 'DUAL', 'QUAD'}:
        raise ValueError('Unknown view layout')
    if bpy.app.background or context.screen is None or context.window is None:
        raise ValueError('View layouts require an interactive Blender window')
    if _LAYOUT_JOB is not None:
        if _LAYOUT_JOB['screen'] is context.screen and _LAYOUT_JOB['layout'] == layout:
            return
        raise ValueError('A view layout change is still in progress')
    if not _layout_active_views(context.screen):
        raise ValueError('Open a 3D View first')
    _LAYOUT_JOB = {'screen': context.screen, 'window': context.window,
                   'scene': context.scene, 'count': {'SINGLE': 1, 'DUAL': 2, 'QUAD': 4}[layout],
                   'layout': layout}
    _layout_step()


def cancel_view_layout():
    """Cancel a pending asynchronous screen-tree change during extension cleanup."""
    global _LAYOUT_JOB
    _LAYOUT_JOB = None
    try:
        import bpy
        if bpy.app.timers.is_registered(_layout_timer):
            bpy.app.timers.unregister(_layout_timer)
    except (ImportError, RuntimeError):
        pass


def ensure_gimbal(scene, turbine):
    """Lazily add a camera to existing saved scenes without rebuilding geometry."""
    import bpy
    root = scene.objects.get(f'WFRL.Turbine.{turbine}.YawRoot')
    if root is None:
        raise ValueError(f'{turbine} is not present in this scene')
    name = f'WFRL.Camera.{turbine}.Gimbal'
    camera = scene.objects.get(name)
    if camera is None:
        camera = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
        root.users_collection[0].objects.link(camera)
    if camera.get('wfrl_gimbal_mount_revision', 0) < 1:
        from .turbine_geometry import geometry_data
        radius = geometry_data()['scalars']['TipRad']
        # Tower-side inspection station, lowered toward the passing blade tip.
        # Looking from the hub along the entire span foreshortens the last
        # metres into a few pixels and lets the near blade fill the image.
        # A 1.2 m chordwise offset is only a slight deviation from front-on.
        camera.parent = root
        camera.matrix_parent_inverse.identity()
        camera.location = (-3.5, 1.2, -radius * .60)
        camera.data.clip_start, camera.data.clip_end = .05, 30000
        camera.data.display_size = .5
        camera['mount'] = 'tower-side inner blade-tip observation / illustrative SYNTH position'
        camera['wfrl_gimbal_mount_revision'] = 1
        reset_gimbal(camera)
    camera.data.show_passepartout = False
    camera.data.passepartout_alpha = 0
    return camera



def down_gimbal(camera):
    """Tower-side inner-tip composition for the selected turbine."""
    if camera.get('wfrl_nacelle_camera'):
        return reset_nacelle_gimbal(camera)
    from .turbine_geometry import geometry_data
    camera.location = (-3.5, 1.2, -geometry_data()['scalars']['TipRad'] * .60)
    # Orient the downward image like the reference: tower above, passing
    # blade below. This is an optical roll, not a change to rotor pose.
    camera['gimbal_roll'] = 180.0
    camera.data.shift_x = camera.data.shift_y = 0
    return aim_gimbal(camera, 180, -67, 85)


def reset_gimbal(camera):
    """Return to an oblique overview of the selected turbine."""
    if camera.get('wfrl_nacelle_camera'):
        return reset_nacelle_gimbal(camera)
    from mathutils import Vector
    from .turbine_geometry import geometry_data
    import math
    radius = geometry_data()['scalars']['TipRad']
    camera.location = (-radius * 2.8, -radius * 2.8, radius * .30)
    target = Vector((-5, 0, -radius * .30))
    direction = target - camera.location
    yaw = math.degrees(math.atan2(direction.y, direction.x))
    pitch = math.degrees(math.atan2(direction.z, math.hypot(direction.x, direction.y)))
    camera['gimbal_roll'] = 0.0
    camera.data.shift_x = camera.data.shift_y = 0
    return aim_gimbal(camera, yaw, pitch, 62)


# Metres relative to YawRoot, +X downwind and +Z up. These are optical
# coordinates for the project's service shell, not NREL mounting-hole data.
NACELLE_LOCATION = (-1.5, -2.3, 0.0)
NACELLE_TARGET = (-13.2, -2.3, -60.6)
# User-selected widest PTZ view, measured in the actual Blender window.
NACELLE_FOV = 120.0


def reset_nacelle_gimbal(camera):
    """Restore the installed optical centre and fixed clearance composition."""
    import math
    from mathutils import Vector
    camera.location = NACELLE_LOCATION
    direction = Vector(NACELLE_TARGET) - camera.location
    yaw = math.degrees(math.atan2(direction.y, direction.x))
    pitch = math.degrees(math.atan2(direction.z, math.hypot(direction.x, direction.y)))
    camera['gimbal_roll'] = 180.0
    camera['wfrl_nacelle_view_revision'] = 2
    camera.data.shift_x = camera.data.shift_y = 0
    return aim_gimbal(camera, yaw, pitch, NACELLE_FOV)


def ensure_nacelle_gimbal(scene, turbine):
    """Independent, persistent PTZ camera, inheriting nacelle motion once.

    The short support joins the actual shell surface to the gimbal pivot.
    It documents a geometric mounting envelope, not a certified bracket.
    """
    import bpy
    from mathutils import Vector
    root = scene.objects.get(f'WFRL.Turbine.{turbine}.YawRoot')
    shell = scene.objects.get(f'WFRL.Turbine.{turbine}.Nacelle')
    if root is None or shell is None:
        raise ValueError(f'{turbine} nacelle shell is not present in this scene')
    name = f'WFRL.Camera.{turbine}.NacelleGimbal'
    camera = scene.objects.get(name)
    if camera is not None:
        # Upgrade the former 35-degree default in existing scenes once.
        # Other saved zoom values, manual aim and the mount remain intact.
        if camera.get('wfrl_nacelle_view_revision', 1) < 2:
            if abs(float(camera.get('gimbal_fov', 0)) - 35.0) < 1e-6:
                aim_gimbal(camera, camera['gimbal_yaw'], camera['gimbal_pitch'], NACELLE_FOV)
            camera['wfrl_nacelle_view_revision'] = 2
        return camera  # Selecting/reloading must not overwrite manual PTZ.
    camera = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
    collection = root.users_collection[0]
    collection.objects.link(camera)
    camera.parent = root
    camera.matrix_parent_inverse.identity()
    camera['wfrl_nacelle_camera'] = True
    camera['mount'] = 'nacelle front lower side / short bracket / geometric demonstration'
    camera['mount_coordinate_frame'] = 'YawRoot; +X downwind; +Z up; metres'
    camera['mount_location_m'] = NACELLE_LOCATION
    camera.data.clip_start, camera.data.clip_end = .02, 30000
    camera.data.display_size = .18
    camera.data.show_passepartout = False
    reset_nacelle_gimbal(camera)
    # A vertical swivel stem ends above the optical centre. Find its shell
    # attachment from the real mesh instead of an assumed bounding box.
    pivot = Vector(NACELLE_LOCATION) + Vector((0, 0, .25))
    found, point, normal, _ = shell.closest_point_on_mesh(shell.matrix_basis.inverted() @ pivot)
    if not found:
        bpy.data.objects.remove(camera, do_unlink=True)
        raise ValueError('Cannot locate the nacelle bracket attachment')
    anchor = shell.matrix_basis @ point
    camera['bracket_anchor_m'] = anchor
    camera['bracket_pivot_m'] = pivot
    camera['bracket_length_m'] = (pivot - anchor).length
    camera['mount_scope'] = 'model clearance only; no structural certification or visual ranging'
    curve = bpy.data.curves.new(name + '.Bracket.Data', 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = .025
    curve.bevel_resolution = 2
    spline = curve.splines.new('POLY')
    spline.points.add(2)
    for item, xyz in zip(spline.points, (anchor, pivot, Vector(NACELLE_LOCATION) + Vector((0, 0, .12)))):
        item.co = (*xyz, 1)
    bracket = bpy.data.objects.new(f'WFRL.Turbine.{turbine}.NacelleCameraBracket', curve)
    collection.objects.link(bracket)
    bracket.parent = root
    from .materials import get_material
    curve.materials.append(get_material('hub'))
    bracket['provenance'] = 'Camera support envelope; not radar hardware or certified mount'
    return camera


def aim_gimbal(camera, yaw, pitch, fov):
    """Yaw about mount Z, then local pitch; exact poles retain stable roll."""
    import math
    from mathutils import Euler, Quaternion
    yaw, pitch, fov = float(yaw), float(pitch), float(fov)
    if not all(math.isfinite(v) for v in (yaw, pitch, fov)):
        raise ValueError('Camera angles must be finite')
    yaw, pitch, fov = yaw % 360, max(-90, min(90, pitch)), max(10, min(120, fov))
    orientation = Euler((math.radians(90 + pitch), 0, math.radians(yaw - 90)), 'XYZ').to_quaternion()
    roll = math.radians(float(camera.get('gimbal_roll', 0)))
    camera.rotation_euler = (orientation @ Quaternion((0, 0, 1), roll)).to_euler('XYZ')
    camera.data.type = 'PERSP'
    camera.data.sensor_fit = 'HORIZONTAL'
    camera.data.sensor_width = 36
    camera.data.lens = 36 / (2 * math.tan(math.radians(fov) / 2))
    camera['gimbal_yaw'], camera['gimbal_pitch'], camera['gimbal_fov'] = yaw, pitch, fov
    return camera


def fill_camera_view(area, scene):
    """Fill the viewport with the camera image, hiding frame and passepartout.

    This is viewport overscan only; exported camera framing stays unchanged.
    """
    import math
    space = area.spaces.active
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None or not region.width or not region.height:
        return
    camera = space.camera if space.use_local_camera else scene.camera
    if camera:
        camera.data.show_passepartout = False
    aspect = (scene.render.resolution_x * scene.render.pixel_aspect_x /
              (scene.render.resolution_y * scene.render.pixel_aspect_y))
    base = max(region.width, region.height)
    width, height = (base, base / aspect) if aspect >= 1 else (base * aspect, base)
    scale = max(region.width / width, region.height / height) * 1.02
    space.region_3d.view_camera_zoom = min(600, (math.sqrt(4 * scale) - math.sqrt(2)) * 50)
    space.region_3d.view_camera_offset = (0, 0)


def ensure_clearance_camera(scene, turbine_id):
    """Front-left 45-degree perspective of the lower-blade measurement zone."""
    import math
    import bpy
    from mathutils import Vector
    from .turbine_geometry import geometry_data
    yaw = scene.objects.get(f'WFRL.Turbine.{turbine_id}.YawRoot')
    if yaw is None:
        raise ValueError('请先加载包含所选风机的场景')
    name = f'WFRL.Camera.{turbine_id}.Clearance'
    camera = scene.objects.get(name)
    if camera is None:
        camera = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
        yaw.users_collection[0].objects.link(camera)
    radius = geometry_data()['scalars']['TipRad']
    # Parent only the explanatory camera, never the radar, to the yaw frame.
    camera.parent = yaw
    target = Vector((-5, 0, -radius * .42))
    # Rotor front is local -X. Looking toward +X with +Z up, screen-left
    # is +Y; keep this azimuth relative to the turbine as its yaw changes.
    direction = Vector((-1, 1, 0)).normalized()
    aspect = (scene.render.resolution_x * scene.render.pixel_aspect_x
              / (scene.render.resolution_y * scene.render.pixel_aspect_y))
    camera.data.type = 'PERSP'
    camera.data.sensor_fit = 'HORIZONTAL'
    camera.data.sensor_width = 36
    camera.data.lens = 50
    frame_width = radius * 1.65 * max(1., aspect)
    distance = frame_width / (2 * math.tan(camera.data.angle_x / 2))
    camera.location = target + direction * distance
    camera.rotation_mode = 'XYZ'
    camera.rotation_euler = (target - camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.clip_start, camera.data.clip_end = .1, 30000
    camera.data.show_passepartout = False
    camera['provenance'] = '测量区左前方45°透视；刚性模型不显示弹性变形或精确测距'
    return camera


def ensure_radar_closeup_camera(scene, turbine_id):
    """Inspect the housing, mount and optical face at the actual radar location."""
    import math
    import bpy
    from mathutils import Vector
    radar = scene.objects.get(f'WFRL.Turbine.{turbine_id}.ClearanceRadar')
    if radar is None or radar.parent is None:
        raise ValueError('请先加载包含雷达的风机场景')
    name = f'WFRL.Camera.{turbine_id}.RadarCloseup'
    camera = scene.objects.get(name)
    if camera is None:
        camera = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
        radar.users_collection[0].objects.link(camera)
    camera.parent = radar.parent
    camera.matrix_parent_inverse.identity()
    # Stay below and beside the mount so the nacelle does not hide the sensor.
    # Follow its installed position; do not move or scale the radar itself.
    target = radar.location + Vector((0, 0, .095))
    direction = Vector((-1, 1, -.45)).normalized()
    aspect = (scene.render.resolution_x * scene.render.pixel_aspect_x
              / (scene.render.resolution_y * scene.render.pixel_aspect_y))
    camera.data.type = 'PERSP'
    camera.data.sensor_fit = 'HORIZONTAL'
    camera.data.sensor_width = 36
    camera.data.lens = 55
    frame_width = max(1.25, .82 * aspect)
    distance = frame_width / (2 * math.tan(camera.data.angle_x / 2))
    camera.location = target + direction * distance
    camera.rotation_mode = 'XYZ'
    camera.rotation_euler = (target - camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.clip_start, camera.data.clip_end = .01, 30000
    camera.data.show_passepartout = False
    camera['provenance'] = '雷达安装特写；外壳为示意，光束不代表命中点或测距结果'
    return camera
