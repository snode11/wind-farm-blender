"""Offline result playback driven only by Blender's existing timeline."""
import math

_READERS = {}


def _stop_finished_playback():
    """A queued end callback must not stop a newly selected/restarted clip."""
    import bpy
    for window in bpy.context.window_manager.windows:
        scene = window.scene
        if (reader_for(scene) is not None and scene.frame_current >= scene.frame_end
                and window.screen.is_animation_playing):
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.screen.animation_cancel(restore_frame=False)


def reader_for(scene):
    if scene.get('wfrl_scene_kind') != 'clearance_replay':
        return None
    return _READERS.get(scene.as_pointer())


def clear(scene, reason='回放数据未就绪'):
    _READERS.pop(scene.as_pointer(), None)
    from .clearance_visual import reset_beams
    reset_beams(scene)
    try:
        from . import tip_tracking
        tip_tracking.reset(scene, 'replay_clear')
    except ImportError:
        pass
    scene['wfrl_clearance_status'] = reason
    scene.wfrl_clearance_show_config = True
def sample(scene):
    reader = reader_for(scene)
    if reader is None:
        return None
    # The timeline's simulation mapping is fixed when a clip is loaded. Render
    # FPS controls playback speed, not which saved observation a frame means.
    timebase = scene.get('wfrl_clearance_timebase_fps', scene.render.fps / scene.render.fps_base)
    elapsed = (scene.frame_current + scene.frame_subframe - scene.frame_start) / timebase
    return reader.at(min(reader.end_s, max(reader.start_s, reader.start_s + elapsed)))


def load(scene, path, demo):
    from . import runtime, _cancel_playback
    if not runtime.configuration_editable():
        raise ValueError('Stop the active backend before loading results')
    _cancel_playback()
    clear(scene)
    try:
        from ._vendor.lidar.replay import ReplayPackage, ReplayReader
    except ImportError:
        from wfrl.lidar.replay import ReplayPackage, ReplayReader
    package = ReplayPackage.load(path)
    reader = ReplayReader(package)
    tid = package.manifest['turbine_id']
    if scene.objects.get(f'WFRL.Turbine.{tid}.Rotor') is None:
        raise ValueError('Load matching turbine scene first: ' + tid)
    if package.manifest['segment']['id'] != {'normal': 'normal', 'near_tower': 'close'}[demo]:
        raise ValueError('Package Demo does not match selected clip')
    from .turbine_geometry import geometry_data
    geometry = geometry_data()['scalars']
    model = package.manifest['model']
    if (not isinstance(model, dict) or model.get('id') != 'nrel5mw'
            or not math.isclose(model.get('tower_height_m', -1), geometry['TowerHt'], abs_tol=.01)
            or not math.isclose(model.get('rotor_radius_m', -1), geometry['TipRad'], abs_tol=.01)):
        raise ValueError('Result package turbine geometry does not match this scene')
    if any(any(abs(v) > 1e-8 for v in row['nacelle_orientation_deg'][:2]) for row in package.motion):
        raise ValueError('This rigid replay view does not support nacelle roll/pitch motion')
    calibration = package.manifest['calibration']
    origin = calibration['origin_m']
    directions = calibration['beam_directions']
    def vector(value):
        return (isinstance(value, list) and len(value) == 3
                and all(type(v) in (int, float) and math.isfinite(v) for v in value))
    if (not vector(origin) or not isinstance(directions, list) or len(directions) != 3
            or not all(vector(d) and sum(v*v for v in d) > 0 for d in directions)):
        raise ValueError('Invalid radar mounting calibration')
    from .clearance_visual import ensure_radar
    # Offline V1 uses the documented fixed global tower origin and rigid tower.
    yaw_root = scene.objects.get(f'WFRL.Turbine.{tid}.YawRoot')
    from mathutils import Vector
    local_origin = Vector(origin) - yaw_root.parent.location - yaw_root.location
    ensure_radar(scene, tid, local_origin, directions)
    from . import farm_flex, tip_tracking
    farm_flex.detach(scene)
    tip_tracking.disable(scene, 'standalone_replay')
    if 'wfrl_farm_flex_path' in scene:
        del scene['wfrl_farm_flex_path']
    runtime.enter_result_replay()
    timebase = (scene.get('wfrl_clearance_timebase_fps')
                if scene.get('wfrl_scene_kind') == 'clearance_replay' else None)
    if type(timebase) not in (int, float) or not math.isfinite(timebase) or timebase <= 0:
        timebase = scene.render.fps / scene.render.fps_base
    scene['wfrl_clearance_timebase_fps'] = timebase
    scene['wfrl_scene_kind'] = 'clearance_replay'
    scene['wfrl_clearance_turbine'] = tid
    scene['wfrl_clearance_demo'] = demo
    scene['wfrl_clearance_status'] = 'READY'
    scene.wfrl_clearance_show_config = False
    scene.frame_start = 1
    scene.frame_end = max(2, 1 + math.ceil((reader.end_s-reader.start_s) * timebase))
    _READERS[scene.as_pointer()] = reader
    scene.frame_set(1)
    update(scene)


def update(scene, depsgraph=None):
    if scene.get('wfrl_scene_kind') != 'clearance_replay':
        return
    if reader_for(scene) is not None:
        frame = min(scene.frame_end, max(scene.frame_start, scene.frame_current))
        if frame != scene.frame_current:
            # Native timeline edits must obey the same bounds as the replay slider.
            scene.frame_set(frame)
            return
    value = sample(scene)
    if value is None:
        return
    from .clearance_visual import update_beams
    from .radar_feedback import beam_activity
    tid = scene['wfrl_clearance_turbine']; motion = value['motion']
    prefix = f'WFRL.Turbine.{tid}'
    yaw = scene.objects.get(prefix + '.YawRoot')
    rotor = scene.objects.get(prefix + '.Rotor')
    from . import farm_flex
    readers = (farm_flex._ACTIVE.readers if farm_flex.is_active(scene)
               else {tid: reader_for(scene)})
    for radar_tid, radar_reader in readers.items():
        update_beams(scene, radar_tid, beam_activity(radar_reader, value['time_s']))
    if not farm_flex.is_active(scene):
        if 'nacelle_position_m' in motion:
            from mathutils import Vector
            yaw.location = Vector(motion['nacelle_position_m']) - yaw.parent.location
        yaw.rotation_euler.z = math.radians(motion['yaw_deg'])
        rotor.rotation_euler.x = math.radians(motion['azimuth_deg'])
        for index, pitch in enumerate(motion['pitch_deg'], 1):
            blade = scene.objects.get(prefix + f'.Blade{index}')
            if blade:
                blade.rotation_euler.z = math.radians(pitch)
    scene['wfrl_clearance_time_s'] = value['time_s']
    if scene.frame_current >= scene.frame_end:
        import bpy
        if not bpy.app.background and not bpy.app.timers.is_registered(_stop_finished_playback):
            bpy.app.timers.register(_stop_finished_playback, first_interval=0)


def on_load(_):
    _READERS.clear()
    import bpy
    for scene in bpy.data.scenes:
        if scene.get('wfrl_scene_kind') == 'clearance_replay':
            demo = scene.get('wfrl_clearance_demo', 'normal')
            frame = scene.frame_current
            path = getattr(scene, 'wfrl_clearance_' + demo + '_path', '')
            try:
                if scene.get('wfrl_farm_flex_path'):
                    from . import farm_flex, runtime
                    runtime.enter_result_replay()
                    candidate = farm_flex.saved_package(scene)
                    expected = scene.get('wfrl_farm_manifest_sha256')
                    if expected:
                        import hashlib
                        actual = hashlib.sha256((candidate/'manifest.json').read_bytes()).hexdigest()
                        if actual != expected:
                            raise ValueError('保存场景与结果包不匹配，不能替换模型或数据')
                    # attach initializes the reader to T1. Restore the saved
                    # observation turbine so a T2/T3 camera never shows T1's
                    # cards after reopening the file.
                    turbine = scene.get('wfrl_clearance_turbine', 'T1')
                    preview = farm_flex.attach(scene, candidate)
                    if turbine in preview.readers:
                        scene['wfrl_clearance_turbine'] = turbine
                        _READERS[scene.as_pointer()] = preview.readers[turbine]
                    scene.frame_set(frame)
                    continue
                load(scene, bpy.path.abspath(path), demo)
                scene.frame_set(frame)
            except (ValueError, OSError, ImportError, KeyError, TypeError) as exc:
                clear(scene, '回放数据未就绪：' + str(exc))


def register():
    import bpy
    from bpy.app.handlers import persistent
    for handlers, callback in ((bpy.app.handlers.frame_change_post, update), (bpy.app.handlers.load_post, on_load)):
        persistent(callback)
        if callback not in handlers:
            handlers.append(callback)
    from . import tip_tracking
    tip_tracking.register()


def unregister():
    import bpy
    import sys
    from . import radar_feedback
    radar_feedback.unregister()
    farm = sys.modules.get(__package__ + '.farm_flex')
    if farm is not None:
        farm.detach()
    from . import tip_tracking
    tip_tracking.unregister()
    _READERS.clear()
    if bpy.app.timers.is_registered(_stop_finished_playback):
        bpy.app.timers.unregister(_stop_finished_playback)
    for handlers, callback in ((bpy.app.handlers.frame_change_post, update), (bpy.app.handlers.load_post, on_load)):
        if callback in handlers:
            handlers.remove(callback)
