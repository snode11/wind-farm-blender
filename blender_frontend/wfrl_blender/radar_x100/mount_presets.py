"""Installation guards and transactional single-sensor mount presets."""
import uuid
import numpy as np
from . import configuration, display, view


def _validate_mount_drivers(mount):
    node = mount
    while node is not None:
        if node.constraints or node.animation_data or node.parent_type != 'OBJECT':
            raise ValueError('X100 安装或父级存在约束或动画驱动；请检查后重新应用标定配置')
        node = node.parent


def _guard_installation():
    from .. import custom_camera_capture
    if custom_camera_capture.active():
        raise ValueError('请先结束或取消相机采集，再应用或关闭 X100 安装')


def apply_mount_preset(scene, preset, *, search_report=None):
    """Switch one sensor's calibration and resample at the existing scene time."""
    import bpy
    from mathutils import Matrix
    from .. import farm_flex
    from .scene import (SceneSampler, ORIGINAL_LOCATION, ORIGINAL_ROTATION,
                        nacelle_side_installation, down_camera_installation, rear_hatch_installation,
                        installation_matrix)
    from .runtime import (_guard_installation, _validate_mount_drivers, session_for,
                          start, _simulation_time, _simulation_frame)
    _guard_installation()
    if scene.wfrl_x100_source != 'SIMULATION':
        raise ValueError('安装位置切换仅用于仿真')
    if preset not in {'ORIGINAL', 'NACELLE_SIDE', 'SEARCH', 'DOWN_CAMERA', 'REAR_HATCH'}:
        raise ValueError('未知雷达安装位置：' + str(preset))
    current = session_for(scene)
    if current is not None and (current.fanout or current.closing):
        raise ValueError('请先结束记录或完成关闭，再切换雷达安装位置')
    if current is not None and current.mode != 'SIMULATION':
        raise ValueError('请先应用仿真配置，再切换雷达安装位置')
    flex = farm_flex.active_for(scene)
    if flex is None or not farm_flex.is_active(scene):
        raise ValueError('请先加载本场景的风场柔性回放')
    sampler = None
    if current is not None:
        _validate_mount_drivers(current.mount)
        mount, fov = view.existing_installation(scene, current.owner)
        if mount != current.mount or fov != current.fov:
            raise ValueError('雷达安装对象已改变，请重新应用配置')
        if any(len(obj.users_scene) > 1 for obj in scene.objects
               if obj.get('wfrl_x100_owner') == current.owner):
            raise ValueError('雷达安装与其他场景共享，请先应用配置以建立独立安装')
        if current.sampler.flex is flex:
            try:
                current.sampler.validate()
                sampler = current.sampler
            except (ValueError, ReferenceError):
                pass
    if sampler is None:
        sampler = SceneSampler(flex, mount_from_sensor=np.eye(4))
    field_of_view, shape = view.field_of_view(), 'ANGULAR'
    if preset == 'SEARCH':
        from .core import installation
        location, rotation = installation.selected_installation(search_report, sampler.source_id, sampler.geometry_id)
    elif preset == 'ORIGINAL':
        location, rotation = ORIGINAL_LOCATION, ORIGINAL_ROTATION
    elif preset == 'DOWN_CAMERA':
        location, rotation, field_of_view = down_camera_installation(scene)
        shape = 'PROJECTIVE'
    elif preset == 'REAR_HATCH':
        location, rotation = rear_hatch_installation(sampler)
    else:
        location, rotation = nacelle_side_installation(sampler)
    location = tuple(float(np.float32(value)) for value in location)
    rotation = tuple(float(np.float32(value)) for value in rotation)
    field_of_view = tuple(float(np.float32(value)) for value in field_of_view)
    sampler = sampler.with_installation('NACELLE', installation_matrix(location, rotation))
    sampler.field_of_view, sampler.field_of_view_shape = field_of_view, shape
    yaw = scene.objects['WFRL.Turbine.T1.YawRoot']
    settings = dict(mount='NACELLE', location=location, rotation=rotation, field_of_view=field_of_view,
                    field_of_view_shape=shape)
    previous = {key: getattr(scene, 'wfrl_x100_' + key) for key in settings}
    for key in ('location', 'rotation', 'field_of_view'):
        previous[key] = tuple(previous[key])

    def write_settings(values):
        for key, value in values.items():
            setattr(scene, 'wfrl_x100_' + key, value)

    if current is None:
        try:
            write_settings(settings)
            return start(scene)
        except Exception:
            write_settings(previous)
            raise

    def publish_settings():
        model = view.housing(current.mount)
        hidden = model.hide_viewport, model.hide_render
        camera_hide = model.get(view._CAMERA_HIDE)
        camera_hide = tuple(camera_hide) if camera_hide is not None else None
        try:
            view.sync_housing_visibility(scene, current.mount)
            write_settings(settings)
            current.capture_configuration()
        except Exception:
            write_settings(previous)
            model.hide_viewport, model.hide_render = hidden
            if camera_hide is None:
                model.pop(view._CAMERA_HIDE, None)
            else:
                model[view._CAMERA_HIDE] = camera_hide
            raise

    if (current.display_valid and current.frame is not None
            and current.sampler.calibration_id == sampler.calibration_id
            and current.sampler.geometry_id == sampler.geometry_id
            and current.mount.parent == yaw
            and view.saved_field_of_view(current.fov) == (field_of_view, shape)
            and current.sampler.field_of_view == field_of_view
            and current.sampler.field_of_view_shape == shape
            and np.allclose(np.array(current.mount.matrix_parent_inverse) @ np.array(current.mount.matrix_basis),
                            sampler.mount_from_sensor, atol=1e-5, rtol=0)):
        current.update()
        publish_settings()
        return current

    source_session = str(uuid.uuid4())
    sequence, source_t = _simulation_time(scene, sampler)
    frame = _simulation_frame(scene, sampler, source_session, sequence, source_t,
        observation_mode=current.observation_mode)
    if frame is None or frame.completeness != 'COMPLETE':
        raise ValueError('当前时刻没有完整的 X100 观测')
    style = display.style_key(scene, frame)
    batch = view.batch(frame, style[0], color_scale=style[1], velocity_limit=style[2]) if not bpy.app.background else None
    previous_mount = (current.mount.parent, current.mount.matrix_parent_inverse.copy(),
                      current.mount.matrix_basis.copy())
    saved_session = dict(current.__dict__)
    previous_fov_mesh = None
    try:
        if view.saved_field_of_view(current.fov) != (field_of_view, shape):
            previous_fov_mesh = view.set_field_of_view(current.fov, field_of_view, shape)
            current.fov_gpu_batch = (view.fov_batch(current.fov)
                                     if current.fov_shown and not bpy.app.background else None)
        current.mount.parent = yaw
        current.mount.matrix_parent_inverse = Matrix.Identity(4)
        current.mount.matrix_basis = Matrix(sampler.mount_from_sensor.tolist())
        current.sampler, current.source_session, current.key = sampler, source_session, sequence
        current.frame, current.gpu_batch, current.display_valid = frame, batch, True
        current.gpu_retry_at = None
        current.render_error = ''
        current.render_color = style
        from .core import tracking
        current.blade_tracker = tracking.BladeTracker()
        current.clearance_tracker = current.clearance_source = None
        current.pair_context(frame)
        current.derive_blades()
        if style[0] == 'ECHO_CLASS':
            current._display(frame, required_gpu=True, record=False)
        publish_settings()
    except Exception:
        if previous_fov_mesh is not None:
            discarded, current.fov.data = current.fov.data, previous_fov_mesh
            bpy.data.meshes.remove(discarded)
        current.mount.parent, current.mount.matrix_parent_inverse, current.mount.matrix_basis = previous_mount
        current.__dict__.clear()
        current.__dict__.update(saved_session)
        raise
    if previous_fov_mesh is not None:
        view.remove_mesh(previous_fov_mesh)
    scene['wfrl_x100_error'] = scene.get('wfrl_x100_record_error', '')
    scene['wfrl_x100_status'] = configuration.OBSERVATION_LABELS[current.observation_mode] + ' · ' + configuration.RATE_LABEL
    return current
