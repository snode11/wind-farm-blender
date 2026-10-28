"""Meter-scale camera rig with explicit rotor-envelope framing."""
from __future__ import annotations

import math
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


def clear_camera_mask(camera):
    """Camera observation leaves the surrounding viewport unobscured."""
    camera.data.show_passepartout = False
    camera.data.passepartout_alpha = 0.0


def clear_viewport_masks(scene):
    """Apply the observation display to owned cameras in a saved scene."""
    for camera in scene.objects:
        if getattr(camera, 'type', None) == 'CAMERA' and (camera.name.startswith(
                ('WFRL.', 'WFRLManual.', 'SplitRecon.', 'SplitTexture.', 'SplitMappoTexture.'))
                or camera.get('wfrl_custom_camera') or camera.get('wfrl_native_camera_owner')):
            clear_camera_mask(camera)


def demo_view_areas(scene):
    """Visible main views, excluding independent reconstruction and observation."""
    import bpy
    from . import native_camera_views, split_reconstruction
    seen = set()
    for window in bpy.context.window_manager.windows:
        if window.scene != scene:
            continue
        for area in window.screen.areas:
            if native_camera_views.owns_view(window, area):
                continue
            if area.type != 'VIEW_3D' or area.as_pointer() in seen:
                continue
            seen.add(area.as_pointer())
            if not split_reconstruction.is_reconstruction_view(scene, area.spaces.active):
                yield area


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
        sensor_width = _sensor_width(camera)
        camera.data.lens = sensor_width / (2.0 * math.tan(math.radians(value) / 2.0))
        camera['fov_deg'] = value
    if focus is not None:
        camera['focus'] = str(focus)
    scene.camera = camera
    scene['wfrl_camera'] = camera.name
    from .nacelle_camera import select_output
    select_output(scene, camera)
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
    camera.data.sensor_width = _sensor_width(camera)
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


def select_sidebar_category(area, category):
    """Keep the current tab when Blender's UI region is not editable yet."""
    for region in area.regions:
        if region.type != 'UI':
            continue
        try:
            if region.active_panel_category != category:
                region.active_panel_category = category
            return region.active_panel_category == category
        except (AttributeError, RuntimeError):
            area.tag_redraw()
            return False
    return False


def _layout_finish(job):
    screen, scene, count, layout = job['screen'], job['scene'], job['count'], job['layout']
    names = job.get('camera_names') or ('WFRL.Camera.World', 'WFRL.Camera.T1.Sensor',
                                      'WFRL.Camera.Top', 'WFRL.Camera.Side')
    active = _layout_active_views(screen)
    if len(active) != count:
        return False
    if job.get('camera_names') and not job.get('regions_ready'):
        for index, area in enumerate(active):
            area.spaces.active.show_region_ui = index == count - 1
        job['regions_ready'] = True
        _layout_schedule()
        return False
    for index, area in enumerate(active):
        space = area.spaces.active
        space.use_local_camera = count > 1 and (index > 0 or bool(job.get('camera_names')))
        space.camera = scene.objects.get(names[index % count])
        space.region_3d.view_perspective = 'CAMERA'
        space.region_3d.view_camera_zoom = 0
        space.region_3d.view_camera_offset = (0, 0)
        if job.get('camera_names'):
            space.show_region_ui = index == count - 1
            if space.show_region_ui:
                select_sidebar_category(area, 'MAPPO')
        fill_camera_view(area, scene)
        if job.get('camera_names') and count > 1:
            # Fit in the unobscured region. The front camera has generous
            # horizontal margins for widescreen; crop only those margins.
            from .native_camera_views import frame_gate
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region:
                fx = 1.
                if space.camera.name == 'WFRL.Camera.T1.FrontQuarter':
                    from .turbine_geometry import geometry_data
                    fx = min(1., 2.4 * geometry_data()['scalars']['TipRad'] / space.camera.data.ortho_scale)
                frame_gate(scene, {'area': area, 'proxy': space.camera, 'fx': fx, 'fy': 1.}, region, space)
    scene.camera = scene.objects.get(names[0])
    scene['wfrl_view_layout'] = layout
    return True


def _layout_timer():
    global _LAYOUT_JOB
    if _LAYOUT_JOB is None:
        return None
    try:
        _layout_step()
    except (ValueError, RuntimeError, ReferenceError, AttributeError) as exc:
        job = _LAYOUT_JOB
        _LAYOUT_JOB = None
        if job is not None:
            job['scene']['wfrl_view_layout_error'] = str(exc)
        print('WFRL view layout failed:', exc)
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
    import time
    if time.monotonic() > job['deadline']:
        raise ValueError('分屏操作超时，请重试')
    screen, count = job['screen'], job['count']
    if job['window'].screen != screen or job['window'].scene != job['scene']:
        _LAYOUT_JOB = None
        return
    active = _layout_active_views(screen)
    if len(active) > count or (job.get('merge_first') and len(active) > 1):
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
    job['merge_first'] = False
    if len(active) < count:
        # A split creates a temporary zero-sized area. Wait for redraw before
        # issuing the next split; otherwise repeated calls can produce a bad
        # screen tree or loop forever.
        if len(_layout_views(screen)) >= count:
            _layout_schedule()
            return
        area = max(active, key=lambda item: item.width * item.height)
        factor = .5
        if job.get('split_factor') is not None and count == 2:
            factor = job['split_factor']
        elif job.get('camera_names') and count == 2:
            sidebar = next((r for r in area.regions if r.type == 'UI'), None)
            if sidebar:
                factor = max(.3, .5 - sidebar.width / (2 * area.width))
        with bpy.context.temp_override(window=job['window'], screen=screen, area=area):
            result = bpy.ops.screen.area_split(
                direction='VERTICAL' if len(_layout_views(screen)) == 1 else 'HORIZONTAL',
                factor=factor)
        if 'FINISHED' not in result:
            _LAYOUT_JOB = None
            raise ValueError('Blender could not split this area')
        _layout_schedule()
        return
    if _layout_finish(job):
        _LAYOUT_JOB = None


def set_view_layout(context, layout, *, camera_names=None, split_factor=None, rebuild=False):
    """Set the number of WFRL 3D views, yielding between screen operations."""
    import bpy
    import time
    global _LAYOUT_JOB
    if layout not in {'SINGLE', 'DUAL', 'QUAD'}:
        raise ValueError('Unknown view layout')
    if split_factor is not None and not .2 <= float(split_factor) <= .8:
        raise ValueError('Split fraction must be between 0.2 and 0.8')
    if bpy.app.background or context.screen is None or context.window is None:
        raise ValueError('View layouts require an interactive Blender window')
    if _LAYOUT_JOB is not None:
        if _LAYOUT_JOB['screen'] is context.screen and _LAYOUT_JOB['layout'] == layout:
            return
        raise ValueError('A view layout change is still in progress')
    if not _layout_active_views(context.screen):
        raise ValueError('Open a 3D View first')
    count = {'SINGLE': 1, 'DUAL': 2, 'QUAD': 4}[layout]
    if camera_names is not None:
        if len(camera_names) != count or any(
                context.scene.objects.get(name) is None or context.scene.objects[name].type != 'CAMERA'
                for name in camera_names):
            raise ValueError('所选分屏相机尚未就绪')
    active = _layout_active_views(context.screen)
    side_by_side = (len(active) == 2 and abs(active[0].y-active[1].y) <= 8
                    and abs(active[0].height-active[1].height) <= 8)
    context.scene['wfrl_view_layout_error'] = ''
    _LAYOUT_JOB = {'screen': context.screen, 'window': context.window,
                   'scene': context.scene, 'count': count,
                   'layout': layout, 'camera_names': camera_names,
                   'split_factor': float(split_factor) if split_factor is not None else None,
                   'merge_first': bool(camera_names and count == 2 and (rebuild or not side_by_side)),
                   'deadline': time.monotonic() + 10}
    try:
        _layout_step()
    except Exception:
        _LAYOUT_JOB = None
        raise


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


_DOWN_GIMBAL_NAMES = frozenset(f'WFRL.Camera.{turbine}.Gimbal'
                              for turbine in ('T1', 'T2', 'T3'))

# Down is fixed to the nacelle underside, behind the hub on the trailing-edge
# side. Its optical axis is 7.5 degrees from local -Z toward the rotor.
# The 8 mm lens covers 60 degrees vertically at a 16:9 output aspect.
DOWN_MOUNT_XY = (-3.5, -1.15)
DOWN_MOUNT_OFFSET = (0.0, 0.0, -0.2151)
DOWN_YAW_DEG, DOWN_PITCH_DEG, DOWN_ROLL_DEG = 181.2, -82.5, 175.9
DOWN_VFOV_DEG = 60.0
DOWN_ASPECT = 16 / 9
DOWN_HFOV_DEG = math.degrees(2 * math.atan(math.tan(math.radians(DOWN_VFOV_DEG / 2)) * DOWN_ASPECT))
DOWN_FOCAL_LENGTH_MM = 8.0
DOWN_SENSOR_WIDTH_MM = 2 * DOWN_FOCAL_LENGTH_MM * math.tan(math.radians(DOWN_HFOV_DEG / 2))
DOWN_MOUNT_REVISION = 5


def _sensor_width(camera):
    if camera.get('wfrl_nacelle_intrinsics') == nacelle_camera.INTRINSICS_ID:
        return nacelle_camera.SENSOR_WIDTH_MM
    return DOWN_SENSOR_WIDTH_MM if is_down_camera(camera) else 36.0


def _down_mount(camera):
    """Locate the optical centre below the nacelle underside mount point."""
    import bpy
    import re
    from mathutils import Vector
    root = camera.parent
    children = [obj for obj in root.children if obj.type == 'MESH'] if root else []
    shells = [obj for obj in children if obj.get('wfrl_nacelle_shell')]
    if not shells:
        shells = [obj for obj in children if re.search(r'\.Nacelle(?:\.\d+)*$', obj.name)]
    if len(shells) != 1:
        raise ValueError('Down requires the nacelle shell to locate its installation point')
    shell = shells[0]
    bpy.context.view_layer.update()
    local = shell.matrix_local.copy()
    inverse = local.inverted()
    hit, point, _normal, _face = shell.ray_cast(
        inverse @ Vector((*DOWN_MOUNT_XY, -5)),
        (inverse.to_3x3() @ Vector((0, 0, 1))).normalized())
    if not hit:
        raise ValueError('Down installation point does not intersect the nacelle underside')
    anchor = local @ point
    offset = Vector(DOWN_MOUNT_OFFSET)
    camera['wfrl_mount_anchor'] = list(anchor)
    camera['wfrl_mount_offset'] = list(offset)
    return anchor + offset


def is_down_camera(camera):
    return camera is not None and (camera.name in _DOWN_GIMBAL_NAMES or camera.get('wfrl_down_camera', False))


def ensure_gimbal(scene, turbine, *, yaw_object=None, name=None, owner=None):
    """Lazily add a camera to existing saved scenes without rebuilding geometry."""
    import bpy
    if owner is not None:
        from .manual_control import other_scene_objects
    root = yaw_object if yaw_object is not None else scene.objects.get(f'WFRL.Turbine.{turbine}.YawRoot')
    if root is None or scene.objects.get(root.name) != root:
        raise ValueError(f'{turbine} is not present in this scene')
    name = name or f'WFRL.Camera.{turbine}.Gimbal'
    camera = scene.objects.get(name)
    if camera is None and owner is not None:
        candidates = [obj for obj in root.children if obj.get('wfrl_down_camera')
                      and obj.get('wfrl_manual_owner') == owner
                      and scene.objects.get(obj.name) == obj]
        if len(candidates) > 1:
            raise ValueError('Multiple Down cameras belong to this turbine scene')
        camera = next(iter(candidates), None)
    created = camera is None
    if camera is None:
        camera = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
        root.users_collection[0].objects.link(camera)
    elif (camera.type != 'CAMERA' or (owner is not None and
          (camera.get('wfrl_manual_owner') != owner or camera.parent != root
           or camera.data.get('wfrl_manual_owner') != owner or camera.data.users != 1
           or camera.as_pointer() in other_scene_objects(scene)))):
        raise ValueError('Down camera does not belong to this turbine scene')
    camera['wfrl_down_camera'] = True
    if owner is not None:
        camera['wfrl_manual_owner'] = owner
        camera.data['wfrl_manual_owner'] = owner
    revision = DOWN_MOUNT_REVISION
    try:
        if camera.get('wfrl_gimbal_mount_revision', 0) < revision:
            camera.parent = root
            camera.matrix_parent_inverse.identity()
            camera.data.clip_start, camera.data.clip_end = .05, 30000
            camera.data.display_size = .5
            reset_gimbal(camera)
            camera['wfrl_gimbal_mount_revision'] = revision
    except Exception:
        if created:
            data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            if data.users == 0:
                bpy.data.cameras.remove(data)
        raise
    clear_camera_mask(camera)
    return camera



def down_gimbal(camera):
    """Restore the underside view of the passing blade, with the tower toward image top."""
    if camera.get('wfrl_nacelle_camera'):
        return reset_nacelle_gimbal(camera)
    camera.location = _down_mount(camera)
    camera['mount'] = 'nacelle underside, behind hub, trailing-edge side / fixed Down'
    camera['gimbal_roll'] = DOWN_ROLL_DEG
    camera.data.shift_x = camera.data.shift_y = 0
    return aim_gimbal(camera, DOWN_YAW_DEG, DOWN_PITCH_DEG, DOWN_HFOV_DEG)


def reset_gimbal(camera):
    """Restore a mounted Down view or another camera's oblique overview."""
    if camera.get('wfrl_nacelle_camera'):
        return reset_nacelle_gimbal(camera)
    if is_down_camera(camera):
        return down_gimbal(camera)
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


from . import nacelle_camera
from .turbine_geometry import geometry_data, hub_position

# Nominal static location for callers that inspect the packaged NREL datum.
# Camera construction uses the scene Rotor origin rather than the visible Hub.
_NACELLE_HUB = hub_position()
NACELLE_LOCATION = (_NACELLE_HUB[0] + nacelle_camera.HUB_OFFSET_M[0], 0.0,
                    _NACELLE_HUB[2] - geometry_data()['scalars']['TowerHt']
                    + nacelle_camera.HUB_OFFSET_M[2])
NACELLE_FOV = nacelle_camera.HFOV_DEG


def nacelle_side_view(camera):
    """Turn the existing camera toward the lower outboard rotor sector."""
    import math
    from mathutils import Vector
    key = 'wfrl_nacelle_before_side'
    if key not in camera:
        camera[key] = [float(camera['gimbal_yaw']), float(camera['gimbal_pitch']),
                       float(camera['gimbal_fov']), float(camera.get('gimbal_roll', 0)),
                       float(camera.data.shift_x), float(camera.data.shift_y)]
    # In the rolled nacelle image, -Y is to the right. Aim into the sector
    # between the downward blade and the lower-right rotor edge in the sketch.
    direction = Vector((-13.2, -26.0, -55.0)) - camera.location
    yaw = math.degrees(math.atan2(direction.y, direction.x))
    pitch = math.degrees(math.atan2(direction.z, math.hypot(direction.x, direction.y)))
    camera['gimbal_roll'] = 180.0
    return aim_gimbal(camera, yaw, pitch, camera['gimbal_fov'])


def restore_nacelle_view(camera):
    """Return to the pre-shortcut view; ordinary selection preserves manual PTZ."""
    key = 'wfrl_nacelle_before_side'
    if key in camera:
        yaw, pitch, fov, roll, shift_x, shift_y = camera[key]
        del camera[key]
        camera['gimbal_roll'] = roll
        camera.data.shift_x, camera.data.shift_y = shift_x, shift_y
        aim_gimbal(camera, yaw, pitch, fov)
    return camera


def reset_nacelle_gimbal(camera):
    """Restore the nominal hub-axis mount and the 8 mm, 59° by 40° view."""
    import bpy
    if 'wfrl_nacelle_before_side' in camera:
        del camera['wfrl_nacelle_before_side']
    root = camera.parent
    rotor_name = root.name.removesuffix('.YawRoot') + '.Rotor' if root else ''
    rotor = next((child for child in root.children if child.name == rotor_name), None) if root else None
    if rotor is None:
        raise ValueError('NREL nacelle camera requires the mechanical Rotor datum')
    camera.location = nacelle_camera.mount_location(rotor)
    camera['gimbal_roll'] = nacelle_camera.ROLL_DEG
    camera['wfrl_nacelle_view_revision'] = 3
    camera['wfrl_nacelle_intrinsics'] = nacelle_camera.INTRINSICS_ID
    camera['mount'] = 'nominal nacelle underside / hub-axis installation'
    camera['mount_reference'] = rotor.name + ' mechanical origin; no visible Hub offset'
    camera['mount_coordinate_frame'] = 'YawRoot; +X downwind; +Z up; metres'
    camera['mount_offset_from_hub_m'] = nacelle_camera.HUB_OFFSET_M
    camera['mount_location_m'] = tuple(camera.location)
    camera['mount_nominal_focal_length_mm'] = nacelle_camera.FOCAL_LENGTH_MM
    camera['mount_nominal_hfov_deg'] = nacelle_camera.HFOV_DEG
    camera['mount_nominal_vfov_deg'] = nacelle_camera.VFOV_DEG
    camera['mount_scope'] = 'nominal optical installation; exterior shell fit unverified; no certified bracket'
    camera['optical_roll_scope'] = '180 deg image orientation; optical axis unchanged'
    for key in ('bracket_anchor_m', 'bracket_pivot_m', 'bracket_length_m'):
        if key in camera:
            del camera[key]
    bracket = bpy.data.objects.get(root.name.removesuffix('.YawRoot') + '.NacelleCameraBracket')
    if bracket is not None:
        bpy.data.objects.remove(bracket, do_unlink=True)
    camera.data.shift_x = camera.data.shift_y = 0
    result = aim_gimbal(camera, nacelle_camera.YAW_DEG, nacelle_camera.PITCH_DEG, NACELLE_FOV)
    for scene in camera.users_scene:
        if scene.camera == camera:
            nacelle_camera.select_output(scene, camera)
    return result


def ensure_nacelle_gimbal(scene, turbine):
    """Persistent NREL camera, inheriting nacelle motion without rotor spin.

    Reset establishes the nominal fixed optical baseline. Manual PTZ persists
    until reset, and no support geometry implies an externally validated mount.
    """
    import bpy
    root = scene.objects.get(f'WFRL.Turbine.{turbine}.YawRoot')
    shell = scene.objects.get(f'WFRL.Turbine.{turbine}.Nacelle')
    if root is None or shell is None:
        raise ValueError(f'{turbine} nacelle shell is not present in this scene')
    name = f'WFRL.Camera.{turbine}.NacelleGimbal'
    camera = scene.objects.get(name)
    if camera is not None:
        clear_camera_mask(camera)
        return camera  # Selecting/reloading must not overwrite manual PTZ.
    camera = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
    collection = root.users_collection[0]
    collection.objects.link(camera)
    camera.parent = root
    camera.matrix_parent_inverse.identity()
    camera['wfrl_nacelle_camera'] = True
    camera.data.clip_start, camera.data.clip_end = .005, 30000
    camera.data.display_size = .18
    camera.data.show_passepartout = False
    reset_nacelle_gimbal(camera)
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
    sensor_width = _sensor_width(camera)
    camera.data.sensor_width = sensor_width
    if camera.get('wfrl_nacelle_intrinsics') == nacelle_camera.INTRINSICS_ID:
        camera.data.sensor_height = nacelle_camera.SENSOR_HEIGHT_MM
    camera.data.lens = sensor_width / (2 * math.tan(math.radians(fov) / 2))
    camera['gimbal_yaw'], camera['gimbal_pitch'], camera['gimbal_fov'] = yaw, pitch, fov
    return camera


def apply_nacelle_output(scene, camera):
    """Select the nominal nacelle gate or restore the previous scene output."""
    return nacelle_camera.select_output(scene, camera)


def fill_camera_view(area, scene):
    """Frame observation views without a camera exterior mask."""
    import math
    import bpy
    space = area.spaces.active
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None or not region.width or not region.height:
        return
    camera = space.camera if space.use_local_camera else scene.camera
    calibrated = camera is not None and camera.get('wfrl_nacelle_intrinsics') == nacelle_camera.INTRINSICS_ID
    if camera:
        clear_camera_mask(camera)
        if is_down_camera(camera):
            fit_camera_view(area, scene)
            return
        if calibrated:
            if not bpy.app.background:
                from .native_camera_views import frame_gate
                frame_gate(scene, {'area': area, 'proxy': camera, 'fx': 1.0, 'fy': 1.0}, region, space)
            return
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


def fit_camera_view(area, scene, margin=16):
    """Show the complete frame inside the unobscured part of one viewport.

    Unlike fill_camera_view this is a contain fit. Side panels can overlap the
    WINDOW region, so reserve their actual intersection before computing zoom.
    Neither the optical camera nor the scene output dimensions are modified.
    Returns False if Blender's hard minimum zoom prevents a full contain fit
    (for example an extreme portrait frame or an almost entirely covered view).
    """
    import math
    region = next((item for item in area.regions if item.type == 'WINDOW'), None)
    if region is None or region.width <= 0 or region.height <= 0:
        return
    space = area.spaces.active
    left, right = region.x, region.x + region.width
    bottom, top = region.y, region.y + region.height
    for panel in area.regions:
        if panel.type not in {'UI', 'TOOLS'} or panel.width <= 1:
            continue
        if panel.type == 'UI' and not space.show_region_ui:
            continue
        if panel.type == 'TOOLS' and not space.show_region_toolbar:
            continue
        x0, x1 = max(left, panel.x), min(right, panel.x + panel.width)
        if x1 <= x0:
            continue
        if panel.x + panel.width / 2 > region.x + region.width / 2:
            right = min(right, panel.x)
        else:
            left = max(left, panel.x + panel.width)
    width, height = max(1, right - left - 2 * margin), max(1, top - bottom - 2 * margin)
    aspect = (scene.render.resolution_x * scene.render.pixel_aspect_x /
              (scene.render.resolution_y * scene.render.pixel_aspect_y))
    camera = space.camera if space.use_local_camera else scene.camera
    sensor_fit = camera.data.sensor_fit if camera is not None else 'AUTO'
    # Blender resolves explicit sensor fit independently of output aspect:
    # horizontal uses WINDOW width even for portrait output. AUTO uses the
    # larger viewport dimension, then the output's own aspect fit.
    if sensor_fit == 'HORIZONTAL':
        frame_width, frame_height = region.width, region.width / aspect
    elif sensor_fit == 'VERTICAL':
        frame_width, frame_height = region.height * aspect, region.height
    else:
        base = max(region.width, region.height)
        frame_width, frame_height = (base, base / aspect) if aspect >= 1 else (base * aspect, base)
    requested_scale = min(width / frame_width, height / frame_height)
    zoom = max(-30, min(600, (math.sqrt(4 * requested_scale) - math.sqrt(2)) * 50))
    space.region_3d.view_camera_zoom = zoom
    # Use the actual (possibly hard-clamped) zoom when centering the frame.
    scale = (math.sqrt(2) + zoom / 50) ** 2 / 4
    # Blender's camera offset translates the frame by -offset * viewport-size
    # * 2 * zoom-factor. Its sign is the inverse of image centre displacement.
    center_dx = (left + right) / 2 - (region.x + region.width / 2)
    center_dy = (bottom + top) / 2 - (region.y + region.height / 2)
    space.region_3d.view_camera_offset = (-center_dx / (2 * scale * region.width),
                                         -center_dy / (2 * scale * region.height))
    return scale <= requested_scale + 1e-7
