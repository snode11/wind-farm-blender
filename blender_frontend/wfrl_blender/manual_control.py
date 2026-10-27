"""Independent manual turbine state, rigid poses and main-thread motion."""
import math
import json
import time
from dataclasses import replace

import bpy
from bpy.app.handlers import persistent

from .manual_motion import PitchClock, PitchTrial, RotorClock, TrialClock
from . import manual_pose


DEFAULT_RPM = 9.0
TICK_SECONDS = 1.0 / 60.0
IDLE_SECONDS = 0.25
_POINTER_FIELDS = ('yaw_object', 'rotor_object', 'blade1_object', 'blade2_object', 'blade3_object')
_POSE_FIELDS = manual_pose.POSE_FIELDS
_CLOCKS = {}
_TRIAL_CLOCKS = {}
_PITCH_CLOCKS = {}
_PITCH_FIELDS = frozenset(('pitch_deg', 'individual_pitch', 'pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg'))
POSE_LISTENERS = []
_updating = False
_registered = False


def is_manual(scene):
    try:
        return scene is not None and scene.get('wfrl_scene_kind') == 'manual'
    except ReferenceError:
        return False


def _scene_key(scene):
    return scene.as_pointer()


def _entry_key(scene, entry):
    return (_scene_key(scene), entry.turbine_id)


def _pause_status(scene, reason):
    if not scene.wfrl_manual_error:
        scene.wfrl_manual_status = reason


def _record_error(scene, reason, turbine=''):
    """Latch a scene-local protection reason until explicit validation succeeds."""
    reason = str(reason) or '控制对象无法读取'
    if not turbine and reason.startswith(('T1 ', 'T2 ', 'T3 ')):
        turbine = reason[:2]
    if '约束' in reason or '驱动' in reason or '动作' in reason:
        recovery = '在独立副本中移除冲突约束、动作或驱动'
    elif '旋转' in reason:
        recovery = '撤销原生旋转，或用手动面板重设姿态'
    elif '共享' in reason or '归属' in reason:
        recovery = '解除跨场景共享，或重新创建独立手动场景'
    else:
        recovery = '撤销对象修改；对象缺失时重新创建手动场景'
    values = (reason, turbine or '全部机组', recovery, 'ERROR')
    fields = ('wfrl_manual_error', 'wfrl_manual_error_turbine',
              'wfrl_manual_recovery', 'wfrl_manual_status')
    changed = any(getattr(scene, field) != value for field, value in zip(fields, values))
    for field, value in zip(fields, values):
        setattr(scene, field, value)
    return changed


def _protect_scene(scene, reason, *, turbine='', now=None):
    if not is_manual(scene):
        return
    changed = _record_error(scene, reason, turbine)
    pause_scene(scene, now=now, apply=False)
    if changed:
        _redraw(scene)


def recheck_feedback(scene):
    """Validate every controlled pose without writing or restarting motion."""
    if not is_manual(scene):
        return False, '请先进入手动控制场景'
    pause_scene(scene, apply=False)
    valid, reason = validate_scene(scene)
    turbine = ''
    if valid:
        try:
            for entry in scene.wfrl_manual_turbines:
                turbine = entry.turbine_id
                _validate_controlled_rotation(entry)
        except (ValueError, AttributeError, ReferenceError, RuntimeError) as exc:
            valid, reason = False, str(exc)
    if not valid:
        _protect_scene(scene, reason, turbine=turbine)
        return False, reason
    scene.wfrl_manual_error = ''
    scene.wfrl_manual_error_turbine = ''
    scene.wfrl_manual_recovery = ''
    scene.wfrl_manual_status = 'READY'
    _redraw(scene)
    return True, ''


def trial_active(scene):
    return is_manual(scene) and scene.wfrl_manual_trial.engaged


def _t1(scene):
    return next(entry for entry in scene.wfrl_manual_turbines if entry.turbine_id == 'T1')


def target_pitches(entry):
    return tuple(getattr(entry, f'pitch_b{i}_deg') if entry.individual_pitch else entry.pitch_deg
                 for i in range(1, 4))


def target_pitch(entry, index):
    return target_pitches(entry)[index - 1]


def effective_pitches(entry):
    if entry.pitch_state_ready:
        return tuple(getattr(entry, f'pitch_actual_b{i}_deg') for i in range(1, 4))
    return target_pitches(entry)


def _write_pitches(entry, values):
    for index, value in enumerate(values, 1):
        setattr(entry, f'pitch_actual_b{index}_deg', value)
    entry.pitch_state_ready = True


def _settle_pitch(entry):
    values = target_pitches(entry)
    _write_pitches(entry, values)
    for index, value in enumerate(values, 1):
        setattr(entry, f'pitch_start_b{index}_deg', value)
    entry.pitch_paused = True
    _PITCH_CLOCKS.pop(_entry_key(entry.id_data, entry), None)


def pitch_ramping(entry):
    return any(abs(a - b) > 1e-6 for a, b in zip(effective_pitches(entry), target_pitches(entry)))


def _pitch_clock(scene, entry, now):
    key = _entry_key(scene, entry)
    if key not in _PITCH_CLOCKS:
        _PITCH_CLOCKS[key] = PitchClock(effective_pitches(entry), target_pitches(entry),
                                      scene.wfrl_manual_pitch_rate_deg_s, not entry.pitch_paused, now)
    return _PITCH_CLOCKS[key]


def _sync_pitch(scene, entry, now, *, running=None, retarget=False):
    clock = _pitch_clock(scene, entry, now)
    values = clock.sample(now)
    if retarget:
        for index, value in enumerate(values, 1):
            setattr(entry, f'pitch_start_b{index}_deg', value)
    values = clock.configure(now, targets=target_pitches(entry),
                             rate=scene.wfrl_manual_pitch_rate_deg_s, running=running)
    _write_pitches(entry, values)
    entry.pitch_paused = not clock.running
    return values


def apply_pitch(entry, pitches):
    for index, pitch in enumerate(pitches, 1):
        blade = getattr(entry, f'blade{index}_object')
        blade.rotation_mode = 'XYZ'
        blade.rotation_euler.z = -math.radians(pitch)


def _validate_native_controls(objects):
    """Reject native evaluation that can override rigid poses or fixed optics."""
    objects = set(objects)
    for obj in tuple(objects):
        ancestor = obj.parent
        while ancestor is not None:
            objects.add(ancestor)
            ancestor = ancestor.parent
    for obj in objects:
        if obj.constraints or obj.animation_data:
            raise ValueError(f'{obj.name} 含约束、动作或驱动，无法保证手动参数与实际姿态一致；'
                             '请在独立副本中处理后再操作')
        if obj.type == 'CAMERA' and obj.data.animation_data:
            raise ValueError(f'{obj.data.name} 镜头含动作或驱动，无法保证固定相机参数')


def _validate_controlled_rotation(entry):
    """Require controlled axes to match the last pose written to the objects."""
    try:
        _validate_native_controls(getattr(entry, field) for field in _POINTER_FIELDS)
    except ValueError as exc:
        raise ValueError(f'{entry.turbine_id} {exc}') from exc
    axes = [(entry.yaw_object, 2, math.radians(entry.yaw_deg), '机舱'),
            (entry.rotor_object, 0, math.radians(entry.azimuth_deg), '叶轮')]
    axes.extend((getattr(entry, f'blade{index}_object'), 2, -math.radians(pitch), f'B{index}')
                for index, pitch in enumerate(effective_pitches(entry), 1))
    for obj, axis, expected, label in axes:
        actual = obj.rotation_euler[axis]
        if (obj.rotation_mode != 'XYZ' or not math.isfinite(actual)
                or abs(math.remainder(actual - expected, math.tau)) > 1e-5):
            raise ValueError(f'{entry.turbine_id} {label} 的原生旋转与手动控制参数不一致；'
                             '请撤销原生变换或通过手动面板重新设置姿态后再确认')


def trial_configuration(scene):
    """Read the saved start pose and frozen trial independently of pending inputs."""
    values = json.loads(scene.wfrl_manual_trial.configuration)
    if values['schema'] != 'wfrl.manual-pitch-trial.v1':
        raise ValueError('无法识别变桨试验配置')
    if values['blade_model'] != scene.get('wfrl_manual_blade_model'):
        raise ValueError('试验叶片模型与保存配置不匹配')
    start = manual_pose.pose_values(values['start_pose'])
    pitches = tuple(start[f'pitch_b{i}_deg'] if start['individual_pitch'] else start['pitch_deg']
                    for i in range(1, 4))
    spec = PitchTrial(start['azimuth_deg'], start['rpm'], pitches,
                      tuple(values['targets']), tuple(values['selected']), values['duration_s'],
                      values.get('interpolation', 'SMOOTH'))
    return start, spec


def _trial_clock(scene, now):
    key, configuration = _scene_key(scene), scene.wfrl_manual_trial.configuration
    cached = _TRIAL_CLOCKS.get(key)
    if cached is None or cached[0] != configuration:
        _start, spec = trial_configuration(scene)
        cached = (configuration, TrialClock(spec, scene.wfrl_manual_trial.elapsed_s, False, now))
        _TRIAL_CLOCKS[key] = cached
    return cached[1]


def _sync_trial(scene, now, *, running=None, reset=False):
    """Write rotor and all blade pitches from one trial-time sample."""
    global _updating
    trial, entry = scene.wfrl_manual_trial, _t1(scene)
    clock = _trial_clock(scene, now)
    if running:
        running = _scene_key(scene) in visible_scenes()
    pose = clock.configure(now, running=running, reset=reset)
    start, _spec = trial_configuration(scene)
    previous = _updating
    _updating = True
    try:
        trial.elapsed_s = pose.time_s
        entry.yaw_deg, entry.rpm, entry.pitch_deg = start['yaw_deg'], clock.trial.rpm, start['pitch_deg']
        entry.azimuth_deg = pose.azimuth_deg % 360.0
        at_start = pose.time_s == 0.0
        entry.individual_pitch = start['individual_pitch'] if at_start else True
        for index, pitch in enumerate(pose.pitches_deg, 1):
            setattr(entry, f'pitch_b{index}_deg', start[f'pitch_b{index}_deg'] if at_start else pitch)
        entry.running = clock.running
        _settle_pitch(entry)
        _apply_entry(entry)
    finally:
        _updating = previous
    return pose


def confirm_trial(scene, *, now=None):
    """Validate all inputs before freezing one T1 trial and starting its clock."""
    valid, reason = validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    if trial_active(scene):
        raise ValueError('请先结束当前试验，再确认另一组目标')
    if scene.wfrl_manual_error:
        valid, reason = recheck_feedback(scene)
        if not valid:
            raise ValueError(reason)
    _validate_controlled_rotation(_t1(scene))
    now = time.monotonic() if now is None else now
    pending = scene.wfrl_manual_trial
    start = pose_snapshot(scene, now=now)['T1']
    pitches = tuple(start[f'pitch_b{i}_deg'] if start['individual_pitch'] else start['pitch_deg']
                    for i in range(1, 4))
    selected = tuple(getattr(pending, f'select_b{i}') for i in range(1, 4))
    targets = tuple(getattr(pending, f'target_b{i}_deg') for i in range(1, 4))
    spec = PitchTrial(start['azimuth_deg'], start['rpm'], pitches, targets, selected,
                      pending.duration_s, pending.interpolation)
    configuration = json.dumps(dict(schema='wfrl.manual-pitch-trial.v1', start_pose=start,
                                    blade_model=scene.get('wfrl_manual_blade_model'),
                                    selected=selected, targets=targets, duration_s=spec.duration_s,
                                    interpolation=spec.interpolation),
                               allow_nan=False)
    _CLOCKS.pop(_entry_key(scene, _t1(scene)), None)
    pending.configuration, pending.elapsed_s, pending.engaged = configuration, 0.0, True
    _TRIAL_CLOCKS[_scene_key(scene)] = (configuration, TrialClock(spec, 0.0, False, now))
    _sync_trial(scene, now, running=True)
    sync_batch(scene)
    _redraw(scene)
    _wake_timer()


def _trial_undo_checkpoint(scene, now):
    """Materialize the running pose before an undoable reset or completion."""
    if not _t1(scene).running:
        return
    _sync_trial(scene, now, running=False)
    for entry in scene.wfrl_manual_turbines:
        if entry.turbine_id != 'T1' and entry.running:
            clock = _clock(scene, entry, now)
            _write_azimuth(entry, clock.configure(now, rpm=entry.rpm, running=True))
            _apply_rotor(entry)
    sync_batch(scene)
    if bpy.context.preferences.edit.use_global_undo:
        bpy.ops.ed.undo_push(message='T1 变桨试验当前姿态')


def trial_action(scene, action, *, now=None, undo_checkpoint=False):
    if not trial_active(scene):
        raise ValueError('请先确认并开始 T1 变桨试验')
    valid, reason = validate_scene(scene)
    if not valid:
        _protect_scene(scene, reason, turbine='T1', now=now)
        raise ValueError(reason)
    if action not in {'START', 'PAUSE', 'RESET', 'END'}:
        raise ValueError('试验期间请使用暂停、继续、复位或结束试验')
    try:
        if action == 'RESET':
            _validate_native_controls(getattr(_t1(scene), field) for field in _POINTER_FIELDS)
        else:
            _validate_controlled_rotation(_t1(scene))
    except (ValueError, AttributeError, ReferenceError) as exc:
        _protect_scene(scene, exc, turbine='T1', now=now)
        raise ValueError(str(exc)) from exc
    if action == 'START' and scene.wfrl_manual_error:
        valid, reason = recheck_feedback(scene)
        if not valid:
            raise ValueError(reason)
    now = time.monotonic() if now is None else now
    if undo_checkpoint and action in {'RESET', 'END'}:
        _trial_undo_checkpoint(scene, now)
    _sync_trial(scene, now, running=action == 'START', reset=action == 'RESET')
    if action == 'END':
        scene.wfrl_manual_trial.engaged = False
        _TRIAL_CLOCKS.pop(_scene_key(scene), None)
    if action != 'START':
        _pause_status(scene, 'USER')
    if action == 'RESET' and scene.wfrl_manual_error:
        recheck_feedback(scene)
    sync_batch(scene)
    _redraw(scene)
    if action == 'START':
        _wake_timer()


def visible_scenes():
    """A scene can move only while a window displays it."""
    try:
        return {window.scene.as_pointer() for window in bpy.context.window_manager.windows}
    except (AttributeError, ReferenceError):
        return set()


def selected_entries(scene):
    if not is_manual(scene):
        return []
    target = scene.wfrl_manual_target
    return [entry for entry in scene.wfrl_manual_turbines
            if target == 'ALL' or entry.turbine_id == target]


def other_scene_objects(scene):
    return {obj.as_pointer() for other in bpy.data.scenes if other != scene for obj in other.objects}


def validate_scene(scene):
    """Reject incomplete controls and objects shared with another scene."""
    if not is_manual(scene):
        return False, '请先进入手动控制场景'
    turbine = ''
    try:
        entries = scene.wfrl_manual_turbines
        if not entries:
            return False, '手动控制场景缺少风机'
        owner = scene.get('wfrl_manual_owner')
        if not owner:
            return False, '手动控制场景缺少对象归属标识'
        seen_objects, seen_ids = set(), set()
        shared = None
        if not math.isfinite(scene.wfrl_manual_pitch_rate_deg_s):
            return False, '变桨速度必须是有限数值'
        for entry in entries:
            turbine = entry.turbine_id
            if not entry.turbine_id or entry.turbine_id in seen_ids:
                return False, '风机标识缺失或重复'
            seen_ids.add(entry.turbine_id)
            if any(not math.isfinite(float(getattr(entry, field))) for field in _POSE_FIELDS):
                return False, f'{turbine} 控制参数必须是有限数值'
            if entry.pitch_state_ready and any(not math.isfinite(value) for value in effective_pitches(entry)):
                return False, f'{turbine} 实际桨距必须是有限数值'
            objects = [getattr(entry, field) for field in _POINTER_FIELDS]
            if any(obj is None for obj in objects):
                return False, f'{entry.turbine_id} 控制对象缺失，请重新创建场景'
            yaw, rotor, *blades = objects
            root = yaw.parent
            if (root is None or root.get('wfrl_turbine_id') != entry.turbine_id
                    or rotor.parent != yaw
                    or any(blade.parent is None or blade.parent.parent != rotor for blade in blades)
                    or any(blade.type != 'MESH' for blade in blades)):
                return False, f'{entry.turbine_id} 控制层级不完整，请重新创建场景'
            objects.extend([root] + [blade.parent for blade in blades])
            for obj in objects:
                if obj is None or scene.objects.get(obj.name) != obj:
                    return False, f'{entry.turbine_id} 控制对象缺失，请重新创建场景'
                if obj.get('wfrl_manual_owner') != owner:
                    return False, f'{turbine} 控制对象不属于此手动场景'
                key = obj.as_pointer()
                if key in seen_objects:
                    return False, f'{turbine} 风机控制对象不能重复使用'
                seen_objects.add(key)
                if shared is None:
                    shared = other_scene_objects(scene)
                if key in shared:
                    return False, f'{turbine} 控制对象与其他场景共享，请使用独立副本'
        if trial_active(scene):
            turbine = 'T1'
            trial_configuration(scene)
            elapsed = scene.wfrl_manual_trial.elapsed_s
            if 'T1' not in seen_ids or not math.isfinite(elapsed) or elapsed < 0:
                return False, 'T1 变桨试验时间或对象无效'
            try:
                _validate_native_controls(getattr(_t1(scene), field) for field in _POINTER_FIELDS)
            except ValueError as exc:
                return False, f'T1 {exc}'
        return True, ''
    except (AttributeError, ReferenceError, TypeError, ValueError, KeyError):
        return False, f'{turbine} 控制对象已失效，请重新创建场景'.strip()


def _clock(scene, entry, now):
    key = _entry_key(scene, entry)
    if key not in _CLOCKS:
        _CLOCKS[key] = RotorClock(entry.azimuth_deg, entry.rpm, False, now)
    return _CLOCKS[key]


def _write_azimuth(entry, value):
    global _updating
    previous = _updating
    _updating = True
    try:
        entry.azimuth_deg = value
    finally:
        _updating = previous


def _apply_rotor(entry):
    if entry.rotor_object.rotation_mode != 'XYZ':
        entry.rotor_object.rotation_mode = 'XYZ'
    entry.rotor_object.rotation_euler.x = math.radians(entry.azimuth_deg)


def _apply_entry(entry):
    entry.yaw_object.rotation_mode = 'XYZ'
    entry.yaw_object.rotation_euler.z = math.radians(entry.yaw_deg)
    _apply_rotor(entry)
    if not entry.pitch_state_ready:
        _settle_pitch(entry)
    apply_pitch(entry, effective_pitches(entry))


def _redraw(scene):
    for listener in tuple(POSE_LISTENERS):
        try:
            listener(scene)
        except (AttributeError, ReferenceError, RuntimeError, KeyError, ValueError) as exc:
            print('WFRL manual pose listener:', exc)
    for window in getattr(bpy.context.window_manager, 'windows', ()):
        try:
            if window.scene == scene and window.screen is not None:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        except (AttributeError, ReferenceError):
            continue


def apply_scene(scene):
    """Apply saved manual poses without changing scene, playback or backend state."""
    valid, reason = validate_scene(scene)
    if not valid:
        _protect_scene(scene, reason)
        return False
    if scene.wfrl_manual_error:
        pause_scene(scene, apply=False)
        return False
    try:
        for entry in scene.wfrl_manual_turbines:
            _apply_entry(entry)
        _redraw(scene)
        return True
    except (ReferenceError, AttributeError):
        pause_scene(scene)
        return False


def pause_scene(scene, *, now=None, apply=True, reason='USER'):
    """Pause motion, retaining each turbine's RPM and final visible phase."""
    global _updating
    if not is_manual(scene):
        return
    valid, invalid_reason = validate_scene(scene)
    now = time.monotonic() if now is None else now
    apply = apply and valid
    if not valid:
        _record_error(scene, invalid_reason)
    if apply:
        try:
            for entry in scene.wfrl_manual_turbines:
                _validate_controlled_rotation(entry)
        except (ValueError, AttributeError, ReferenceError) as exc:
            _record_error(scene, str(exc), entry.turbine_id)
            apply = False
    _pause_status(scene, reason)
    previous = _updating
    _updating = True
    try:
        if trial_active(scene) and apply:
            _sync_trial(scene, now, running=False)
        for entry in scene.wfrl_manual_turbines:
            try:
                entry.pitch_paused = True
                if trial_active(scene) and entry.turbine_id == 'T1':
                    entry.running = False
                    continue
                pitch_clock = _PITCH_CLOCKS.pop(_entry_key(scene, entry), None)
                changed = pitch_clock is not None or entry.running
                if pitch_clock is not None and apply:
                    _write_pitches(entry, pitch_clock.configure(now, running=False))
                clock = _CLOCKS.pop(_entry_key(scene, entry), None)
                changed = changed or clock is not None
                if clock is not None and apply:
                    entry.azimuth_deg = clock.configure(now, running=False)
                entry.running = False
                if valid and changed and apply:
                    _apply_entry(entry)
            except (ReferenceError, AttributeError):
                continue
    except (ReferenceError, AttributeError):
        pass
    finally:
        key = _scene_key(scene)
        _TRIAL_CLOCKS.pop(key, None)
        for clocks in (_CLOCKS, _PITCH_CLOCKS):
            for entry_key in tuple(clocks):
                if entry_key[0] == key:
                    clocks.pop(entry_key, None)
        _updating = previous


def _update_entry(entry, context, field):
    global _updating
    if _updating:
        return
    scene = entry.id_data
    valid, reason = validate_scene(scene)
    if not valid:
        _protect_scene(scene, reason)
        return
    now = time.monotonic()
    protected = bool(scene.wfrl_manual_error)
    if protected:
        pause_scene(scene, now=now, apply=False)
        if field == 'running' or (entry.turbine_id == 'T1' and trial_active(scene)):
            return
        try:
            _validate_native_controls(getattr(entry, name) for name in _POINTER_FIELDS)
        except (ValueError, AttributeError, ReferenceError) as exc:
            _protect_scene(scene, exc, turbine=entry.turbine_id, now=now)
            return
    if entry.turbine_id == 'T1' and trial_active(scene):
        _sync_trial(scene, now, running=entry.running if field == 'running' else None)
        sync_batch(scene)
        _redraw(scene)
        return
    clock = _clock(scene, entry, now)
    angle = entry.azimuth_deg if field == 'azimuth_deg' else None
    if field == 'individual_pitch' and entry.individual_pitch:
        _updating = True
        try:
            for index in range(1, 4):
                setattr(entry, f'pitch_b{index}_deg', entry.pitch_deg)
        finally:
            _updating = False
    if field == 'pitch_deg':
        _updating = True
        try:
            for index in range(1, 4):
                setattr(entry, f'pitch_b{index}_deg', entry.pitch_deg)
        finally:
            _updating = False
    if field in _PITCH_FIELDS:
        _sync_pitch(scene, entry, now, running=not protected and _scene_key(scene) in visible_scenes(),
                    retarget=True)
    running = entry.running and entry.rpm > 0 and _scene_key(scene) in visible_scenes()
    _write_azimuth(entry, clock.configure(now, rpm=entry.rpm, running=running, azimuth_deg=angle))
    if entry.running != running:
        _updating = True
        try:
            entry.running = running
        finally:
            _updating = False
    if protected:
        if field == 'yaw_deg':
            entry.yaw_object.rotation_mode = 'XYZ'
            entry.yaw_object.rotation_euler.z = math.radians(entry.yaw_deg)
        elif field == 'azimuth_deg':
            _apply_rotor(entry)
        elif field in _PITCH_FIELDS:
            apply_pitch(entry, effective_pitches(entry))
    else:
        _apply_entry(entry)
    sync_batch(scene)
    _redraw(scene)
    if (running and entry.rpm > 0) or (pitch_ramping(entry) and not entry.pitch_paused):
        _wake_timer()


def _update(field):
    def changed(entry, context):
        _update_entry(entry, context, field)
    return changed


def _batch_update(field):
    def changed(batch, context):
        if _updating:
            return
        scene = batch.id_data
        if not is_manual(scene) or scene.wfrl_manual_target != 'ALL':
            return
        valid, _reason = validate_scene(scene)
        if not valid:
            pause_scene(scene)
            return
        if trial_active(scene):
            sync_batch(scene)
            return
        value = getattr(batch, field)
        for entry in scene.wfrl_manual_turbines:
            if field == 'individual_pitch' and entry.individual_pitch == value:
                continue
            setattr(entry, field, value)
        sync_batch(scene)
    return changed


def sync_batch(scene, *, fields=_POSE_FIELDS):
    """Display the first turbine's inputs without modifying any turbine."""
    global _updating
    if not is_manual(scene) or not scene.wfrl_manual_turbines:
        return
    previous = _updating
    _updating = True
    try:
        first = scene.wfrl_manual_turbines[0]
        for field in fields:
            setattr(scene.wfrl_manual_batch, field, getattr(first, field))
    finally:
        _updating = previous


def _selection_update(scene, context):
    sync_batch(scene)


def mixed_fields(scene):
    entries = list(scene.wfrl_manual_turbines)
    if not entries:
        return []
    return [field for field in _POSE_FIELDS
            if any(abs(float(getattr(entry, field)) - float(getattr(entries[0], field))) > 1e-4
                   for entry in entries[1:])]


def pose_snapshot(scene, *, now=None, actual_pitch=True):
    """Capture all turbine parameters at one wall-clock instant."""
    now = time.monotonic() if now is None else now
    poses = {}
    for entry in scene.wfrl_manual_turbines:
        values = {field: getattr(entry, field) for field in _POSE_FIELDS}
        if entry.turbine_id == 'T1' and trial_active(scene):
            clock = _trial_clock(scene, now)
            pose = replace(clock).sample(now)
            values['azimuth_deg'] = pose.azimuth_deg % 360.0
            if pose.time_s > 0:
                values['individual_pitch'] = True
            if values['individual_pitch']:
                for index, pitch in enumerate(pose.pitches_deg, 1):
                    values[f'pitch_b{index}_deg'] = pitch
        else:
            clock = _CLOCKS.get(_entry_key(scene, entry))
            if entry.running and clock is not None:
                values['azimuth_deg'] = replace(clock).sample(now)
            if actual_pitch:
                pitch_clock = _PITCH_CLOCKS.get(_entry_key(scene, entry))
                pitches = replace(pitch_clock).sample(now) if pitch_clock else effective_pitches(entry)
                if max(pitches) - min(pitches) > 1e-5:
                    values['individual_pitch'] = True
                if not values['individual_pitch']:
                    values['pitch_deg'] = pitches[0]
                for index, pitch in enumerate(pitches, 1):
                    values[f'pitch_b{index}_deg'] = pitch
        poses[entry.turbine_id] = manual_pose.pose_values(values)
    return poses


def _apply_poses(scene, poses, *, pause=True, now=None, ramp=False, preserve_pitch=False):
    """Validate every target before applying poses and synchronizing its clock."""
    global _updating
    valid, reason = validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    entries = {entry.turbine_id: entry for entry in scene.wfrl_manual_turbines}
    if not poses or not set(poses) <= set(entries):
        raise ValueError('姿态文件中的风机与当前场景不匹配')
    if 'T1' in poses and trial_active(scene):
        raise ValueError('请先结束 T1 变桨试验，再调整或载入 T1 姿态')
    checked = {tid: manual_pose.pose_values(values) for tid, values in poses.items()}
    now = time.monotonic() if now is None else now
    protected = bool(scene.wfrl_manual_error)
    if protected:
        pause_scene(scene, now=now, apply=False)
        pause = True
        for tid in checked:
            _validate_native_controls(getattr(entries[tid], field) for field in _POINTER_FIELDS)
    previous = _updating
    _updating = True
    try:
        for tid, values in checked.items():
            entry = entries[tid]
            running = not pause and entry.running and values['rpm'] > 0
            for field, value in values.items():
                setattr(entry, field, value)
            entry.running = running
            key = _entry_key(scene, entry)
            _CLOCKS.pop(key, None)
            if running:
                _CLOCKS[key] = RotorClock(entry.azimuth_deg, entry.rpm, True, now)
            if ramp:
                _sync_pitch(scene, entry, now,
                            running=not protected and _scene_key(scene) in visible_scenes(), retarget=True)
            elif not preserve_pitch:
                _settle_pitch(entry)
            _apply_entry(entry)
    finally:
        _updating = previous
    if protected:
        recheck_feedback(scene)
    elif pause:
        _pause_status(scene, 'USER')
    sync_batch(scene)
    _redraw(scene)
    if ramp:
        _wake_timer()


def _selected_pose_changes(scene, changes, *, now):
    poses = pose_snapshot(scene, now=now)
    result = {}
    for entry in selected_entries(scene):
        result[entry.turbine_id] = {**poses[entry.turbine_id], **changes}
    return result


def _tick_scene(scene, now, visible, active_keys):
    """Keep protection failures local to the scene that owns the controls."""
    turbine = ''
    try:
        valid, reason = validate_scene(scene)
        if not valid:
            _protect_scene(scene, reason, now=now)
            return False
        if scene.wfrl_manual_error:
            pause_scene(scene, now=now, apply=False)
            return False
        if _scene_key(scene) not in visible:
            if any(entry.running or not entry.pitch_paused for entry in scene.wfrl_manual_turbines):
                pause_scene(scene, now=now, reason='HIDDEN')
                _redraw(scene)
            return False
        changed = moving = False
        for entry in scene.wfrl_manual_turbines:
            turbine = entry.turbine_id
            active_keys.add(_entry_key(scene, entry))
            if entry.running or (pitch_ramping(entry) and not entry.pitch_paused):
                _validate_controlled_rotation(entry)
        for entry in scene.wfrl_manual_turbines:
            turbine = entry.turbine_id
            key = _entry_key(scene, entry)
            if entry.turbine_id == 'T1' and trial_active(scene):
                if entry.running:
                    _sync_trial(scene, now)
                    changed = moving = True
                continue
            pitch_moving = pitch_ramping(entry) and not entry.pitch_paused
            if pitch_moving:
                _sync_pitch(scene, entry, now)
                apply_pitch(entry, effective_pitches(entry))
                changed = True
                moving = moving or pitch_ramping(entry)
            if not entry.running:
                _CLOCKS.pop(key, None)
                continue
            clock = _clock(scene, entry, now)
            _write_azimuth(entry, clock.configure(now, rpm=entry.rpm, running=True))
            if entry.rpm > 0:
                _apply_rotor(entry)
                changed = True
                moving = True
        if changed:
            sync_batch(scene)
            _redraw(scene)
        return moving
    except (AttributeError, ReferenceError, RuntimeError, ValueError, KeyError) as exc:
        _protect_scene(scene, exc, turbine=turbine, now=now)
        return False


def tick(now=None):
    """Advance visible manual scenes; invalid or hidden scenes remain paused."""
    now = time.monotonic() if now is None else now
    visible = visible_scenes()
    active_keys = set()
    moving = False
    for scene in bpy.data.scenes:
        if not is_manual(scene) or scene.get('wfrl_manual_exporting'):
            continue
        moving = _tick_scene(scene, now, visible, active_keys) or moving
    for key in tuple(_CLOCKS):
        if key not in active_keys:
            _CLOCKS.pop(key, None)
    for key in tuple(_TRIAL_CLOCKS):
        if (key, 'T1') not in active_keys:
            _TRIAL_CLOCKS.pop(key, None)
    for key in tuple(_PITCH_CLOCKS):
        if key not in active_keys:
            _PITCH_CLOCKS.pop(key, None)
    return TICK_SECONDS if moving else IDLE_SECONDS


def _timer():
    if not _registered:
        return None
    try:
        return tick()
    except (AttributeError, ReferenceError, RuntimeError, ValueError, KeyError) as exc:
        for scene in getattr(bpy.data, 'scenes', ()):
            if is_manual(scene) and not scene.get('wfrl_manual_exporting'):
                _protect_scene(scene, f'手动控制计时异常：{exc}')
        return IDLE_SECONDS


def _wake_timer():
    if not _registered:
        return
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    bpy.app.timers.register(_timer, first_interval=TICK_SECONDS, persistent=True)


def pause_all(*, reason='USER', apply=True):
    for scene in getattr(bpy.data, 'scenes', ()):
        pause_scene(scene, reason=reason, apply=apply)
    _CLOCKS.clear()
    _TRIAL_CLOCKS.clear()
    _PITCH_CLOCKS.clear()


@persistent
def _before_restore(_unused):
    pause_all(reason='RESTORED')


@persistent
def _before_load(_unused):
    from . import manual_scene
    pause_all(reason='RESTORED')
    manual_scene.clear_views()


@persistent
def _after_restore(_unused):
    from .manual_scene_links import restore
    restore()
    pause_all(reason='RESTORED', apply=False)
    for scene in bpy.data.scenes:
        if is_manual(scene):
            valid, _reason = recheck_feedback(scene)
            if valid:
                scene.wfrl_manual_status = 'RESTORED'
                apply_scene(scene)
                sync_batch(scene)
    if _registered and not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=TICK_SECONDS, persistent=True)


_HANDLERS = (('load_pre', _before_load), ('load_post', _after_restore),
             ('undo_pre', _before_restore), ('undo_post', _after_restore),
             ('redo_pre', _before_restore), ('redo_post', _after_restore))


def _pitch_rate_update(scene, context):
    if _updating or not is_manual(scene):
        return
    now = time.monotonic()
    valid, reason = validate_scene(scene)
    if not valid:
        _protect_scene(scene, reason, now=now)
        return
    if scene.wfrl_manual_error:
        pause_scene(scene, now=now, apply=False)
        return
    entries = [entry for entry in scene.wfrl_manual_turbines
               if not (trial_active(scene) and entry.turbine_id == 'T1')]
    try:
        for entry in entries:
            _validate_controlled_rotation(entry)
    except (ValueError, AttributeError, ReferenceError) as exc:
        _protect_scene(scene, exc, turbine=entry.turbine_id, now=now)
        return
    for entry in entries:
        _sync_pitch(scene, entry, now)
        apply_pitch(entry, effective_pitches(entry))
    _redraw(scene)
    _wake_timer()


def register():
    from . import manual_rna
    manual_rna.register()


def unregister():
    from . import manual_rna
    manual_rna.unregister()
