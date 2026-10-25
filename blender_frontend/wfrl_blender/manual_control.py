"""Independent manual turbine state, rigid poses and main-thread motion."""
import math
import json
import os
from pathlib import Path
import tempfile
import time
from dataclasses import replace

import bpy
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ExportHelper, ImportHelper

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
    _validate_native_controls(getattr(entry, field) for field in _POINTER_FIELDS)
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
        pause_scene(scene)
        raise ValueError(reason)
    if action not in {'START', 'PAUSE', 'RESET', 'END'}:
        raise ValueError('试验期间请使用暂停、继续、复位或结束试验')
    now = time.monotonic() if now is None else now
    if undo_checkpoint and action in {'RESET', 'END'}:
        _trial_undo_checkpoint(scene, now)
    _sync_trial(scene, now, running=action == 'START', reset=action == 'RESET')
    if action == 'END':
        scene.wfrl_manual_trial.engaged = False
        _TRIAL_CLOCKS.pop(_scene_key(scene), None)
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
            if not entry.turbine_id or entry.turbine_id in seen_ids:
                return False, '风机标识缺失或重复'
            seen_ids.add(entry.turbine_id)
            if any(not math.isfinite(float(getattr(entry, field))) for field in _POSE_FIELDS):
                return False, '控制参数必须是有限数值'
            if entry.pitch_state_ready and any(not math.isfinite(value) for value in effective_pitches(entry)):
                return False, '实际桨距必须是有限数值'
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
                    return False, '控制对象不属于此手动场景'
                key = obj.as_pointer()
                if key in seen_objects:
                    return False, '风机控制对象不能重复使用'
                seen_objects.add(key)
                if shared is None:
                    shared = other_scene_objects(scene)
                if key in shared:
                    return False, '控制对象与其他场景共享，请使用独立副本'
        if trial_active(scene):
            trial_configuration(scene)
            elapsed = scene.wfrl_manual_trial.elapsed_s
            if 'T1' not in seen_ids or not math.isfinite(elapsed) or elapsed < 0:
                return False, 'T1 变桨试验时间或对象无效'
            try:
                _validate_native_controls(getattr(_t1(scene), field) for field in _POINTER_FIELDS)
            except ValueError as exc:
                return False, str(exc)
        return True, ''
    except (AttributeError, ReferenceError, TypeError, ValueError, KeyError):
        return False, '控制对象已失效，请重新创建场景'


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
        except (AttributeError, ReferenceError, KeyError, ValueError) as exc:
            print('WFRL manual pose listener:', exc)
    for window in bpy.context.window_manager.windows:
        if window.scene == scene:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


def apply_scene(scene):
    """Apply saved manual poses without changing scene, playback or backend state."""
    valid, reason = validate_scene(scene)
    if not valid:
        pause_scene(scene)
        return False
    try:
        for entry in scene.wfrl_manual_turbines:
            _apply_entry(entry)
        _redraw(scene)
        return True
    except (ReferenceError, AttributeError):
        pause_scene(scene)
        return False


def pause_scene(scene, *, now=None, apply=True):
    """Pause motion, retaining each turbine's RPM and final visible phase."""
    global _updating
    if not is_manual(scene):
        return
    valid, _reason = validate_scene(scene)
    now = time.monotonic() if now is None else now
    apply = apply and valid
    if apply:
        try:
            for entry in scene.wfrl_manual_turbines:
                _validate_controlled_rotation(entry)
        except ValueError:
            apply = False
    previous = _updating
    _updating = True
    try:
        if trial_active(scene) and apply:
            _sync_trial(scene, now, running=False)
        for entry in scene.wfrl_manual_turbines:
            if trial_active(scene) and entry.turbine_id == 'T1':
                entry.running = False
                _TRIAL_CLOCKS.pop(_scene_key(scene), None)
                continue
            pitch_clock = _PITCH_CLOCKS.pop(_entry_key(scene, entry), None)
            changed = pitch_clock is not None or entry.running
            if pitch_clock is not None and apply:
                _write_pitches(entry, pitch_clock.configure(now, running=False))
            entry.pitch_paused = True
            clock = _CLOCKS.pop(_entry_key(scene, entry), None)
            changed = changed or clock is not None
            if clock is not None and apply:
                entry.azimuth_deg = clock.configure(now, running=False)
            if entry.running:
                entry.running = False
            if valid and changed and apply:
                _apply_entry(entry)
    except (ReferenceError, AttributeError):
        pass
    finally:
        _updating = previous


def _update_entry(entry, context, field):
    global _updating
    if _updating:
        return
    scene = entry.id_data
    valid, _reason = validate_scene(scene)
    if not valid:
        pause_scene(scene)
        return
    now = time.monotonic()
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
        _sync_pitch(scene, entry, now, running=_scene_key(scene) in visible_scenes(), retarget=True)
    running = entry.running and entry.rpm > 0 and _scene_key(scene) in visible_scenes()
    _write_azimuth(entry, clock.configure(now, rpm=entry.rpm, running=running, azimuth_deg=angle))
    if entry.running != running:
        _updating = True
        try:
            entry.running = running
        finally:
            _updating = False
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
                _sync_pitch(scene, entry, now, running=_scene_key(scene) in visible_scenes(), retarget=True)
            elif not preserve_pitch:
                _settle_pitch(entry)
            _apply_entry(entry)
    finally:
        _updating = previous
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


def tick(now=None):
    """Advance visible manual scenes; invalid or hidden scenes remain paused."""
    now = time.monotonic() if now is None else now
    visible = visible_scenes()
    active_keys = set()
    moving = False
    for scene in bpy.data.scenes:
        if not is_manual(scene) or scene.get('wfrl_manual_exporting'):
            continue
        valid, _reason = validate_scene(scene)
        if not valid or _scene_key(scene) not in visible:
            pause_scene(scene, now=now)
            continue
        changed = False
        for entry in scene.wfrl_manual_turbines:
            key = _entry_key(scene, entry)
            active_keys.add(key)
            if entry.turbine_id == 'T1' and trial_active(scene):
                if entry.running:
                    _sync_trial(scene, now)
                    changed = moving = True
                continue
            pitch_moving = pitch_ramping(entry) and not entry.pitch_paused
            if entry.running or pitch_moving:
                try:
                    _validate_controlled_rotation(entry)
                except ValueError:
                    pause_scene(scene, now=now, apply=False)
                    break
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
    except (AttributeError, ReferenceError, RuntimeError, ValueError, KeyError):
        pause_all()
        return IDLE_SECONDS


def _wake_timer():
    if not _registered:
        return
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    bpy.app.timers.register(_timer, first_interval=TICK_SECONDS, persistent=True)


def pause_all():
    for scene in getattr(bpy.data, 'scenes', ()):
        pause_scene(scene)
    _CLOCKS.clear()
    _TRIAL_CLOCKS.clear()
    _PITCH_CLOCKS.clear()


@persistent
def _before_restore(_unused):
    pause_all()


@persistent
def _before_load(_unused):
    from . import manual_scene
    pause_all()
    manual_scene.clear_views()


@persistent
def _after_restore(_unused):
    from .manual_scene_links import restore
    restore()
    pause_all()
    for scene in bpy.data.scenes:
        if is_manual(scene):
            apply_scene(scene)
            sync_batch(scene)
    if _registered and not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=TICK_SECONDS, persistent=True)


def _properties(update_factory):
    return {
        'yaw_deg': bpy.props.FloatProperty(name='机舱朝向 °', default=0.0, min=-180.0, max=180.0,
                                           precision=1, update=update_factory('yaw_deg')),
        'pitch_deg': bpy.props.FloatProperty(name='集体桨距 °', default=0.0, min=-30.0, max=90.0,
                                             description='统一设置三片叶片桨距，覆盖各片独立桨距',
                                             precision=1, update=update_factory('pitch_deg')),
        'rpm': bpy.props.FloatProperty(name='转速 RPM', default=DEFAULT_RPM, min=0.0, max=30.0,
                                       precision=2, update=update_factory('rpm')),
        'azimuth_deg': bpy.props.FloatProperty(name='叶轮角度 °', default=0.0, min=0.0, max=360.0,
                                               precision=1, update=update_factory('azimuth_deg')),
        'individual_pitch': bpy.props.BoolProperty(name='分别调整三片叶片', default=False,
                                                   update=update_factory('individual_pitch')),
        **{f'pitch_b{i}_deg': bpy.props.FloatProperty(name=f'叶片 {i} 桨距 °', default=0.0,
                                                      min=-30.0, max=90.0, precision=1,
                                                      update=update_factory(f'pitch_b{i}_deg'))
           for i in range(1, 4)},
    }


class WFRL_ManualTurbine(bpy.types.PropertyGroup):
    __annotations__ = {
        **_properties(_update),
        'turbine_id': bpy.props.StringProperty(),
        **{f'pitch_actual_b{i}_deg': bpy.props.FloatProperty(default=0.0) for i in range(1, 4)},
        **{f'pitch_start_b{i}_deg': bpy.props.FloatProperty(default=0.0) for i in range(1, 4)},
        'pitch_state_ready': bpy.props.BoolProperty(default=False),
        'pitch_paused': bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'}),
        'running': bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'}, update=_update('running')),
        **{field: bpy.props.PointerProperty(type=bpy.types.Object) for field in _POINTER_FIELDS},
    }


class WFRL_ManualBatch(bpy.types.PropertyGroup):
    __annotations__ = _properties(_batch_update)


class WFRL_OT_ManualPitchResume(bpy.types.Operator):
    bl_idname = 'wfrl.manual_pitch_resume'
    bl_label = '继续变桨'
    bl_description = '只继续所选风机的桨距过渡，不启动叶轮'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (is_manual(context.scene)
                and not (trial_active(context.scene) and context.scene.wfrl_manual_target in {'T1', 'ALL'})
                and any(pitch_ramping(entry) and entry.pitch_paused for entry in selected_entries(context.scene)))

    def execute(self, context):
        scene = context.scene
        try:
            valid, reason = validate_scene(scene)
            if not valid:
                raise ValueError(reason)
            now = time.monotonic()
            entries = selected_entries(scene)
            for entry in entries:
                _validate_controlled_rotation(entry)
            for entry in entries:
                _sync_pitch(scene, entry, now, running=True)
                _apply_entry(entry)
            _redraw(scene)
            _wake_timer()
        except (ValueError, AttributeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_ManualTrial(bpy.types.PropertyGroup):
    """Pending inputs and the saved configuration of one T1 pitch trial."""

    __annotations__ = {
        **{f'select_b{i}': bpy.props.BoolProperty(name=f'B{i}', default=i == 1)
           for i in range(1, 4)},
        **{f'target_b{i}_deg': bpy.props.FloatProperty(name=f'B{i} 目标 °', default=20.0,
                                                     min=-30.0, max=90.0, precision=1)
           for i in range(1, 4)},
        'duration_s': bpy.props.FloatProperty(name='变桨时长 s', default=8.0, min=.01,
                                             soft_max=60.0, precision=2),
        'interpolation': bpy.props.EnumProperty(name='变桨方式', default='SMOOTH', items=(
            ('SMOOTH', '平滑', '起止变桨速度为零的平滑过渡'),
            ('LINEAR', '匀速', '按桨距差除以时长的固定角速度变桨，到达目标后保持'))),
        'configuration': bpy.props.StringProperty(options={'HIDDEN'}),
        'engaged': bpy.props.BoolProperty(default=False, options={'HIDDEN'}),
        'elapsed_s': bpy.props.FloatProperty(name='试验时间 s', min=0.0, precision=3),
    }


class WFRL_OT_ManualTrial(bpy.types.Operator):
    bl_idname = 'wfrl.manual_trial'
    bl_label = 'T1 恒速变桨试验'
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=(
        ('CONFIRM', '确认并开始', '整体校验，保存当前实际姿态并以恒定正 RPM 开始'),
        ('START', '继续', '从暂停的试验时间继续叶轮旋转和变桨'),
        ('PAUSE', '暂停', '同时冻结叶轮与三片桨距'),
        ('RESET', '复位试验', '暂停并恢复本次试验的起始姿态'),
        ('END', '结束试验', '暂停并保留当前姿态，允许调整下一组输入')))

    @classmethod
    def poll(cls, context):
        return is_manual(context.scene)

    def execute(self, context):
        try:
            if self.action == 'CONFIRM':
                confirm_trial(context.scene)
            else:
                trial_action(context.scene, self.action, undo_checkpoint=True)
        except (ValueError, KeyError, AttributeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualDown(bpy.types.Operator):
    bl_idname = 'wfrl.manual_down'
    bl_label = 'T1 Down'
    bl_description = '从手动 T1 机舱固定下视同一叶轮；保留试验时间与运行状态'

    @classmethod
    def poll(cls, context):
        return is_manual(context.scene)

    def execute(self, context):
        from . import manual_scene
        try:
            manual_scene.down_view(context)
        except (ValueError, RuntimeError, AttributeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualEnter(bpy.types.Operator):
    bl_idname = 'wfrl.manual_enter'
    bl_label = '进入手动控制场景'
    bl_description = '创建或进入独立的风机手动控制场景'

    @classmethod
    def poll(cls, context):
        from . import manual_scene
        if not manual_scene.can_enter(context):
            cls.poll_message_set(manual_scene.entry_error(context))
            return False
        return True

    def execute(self, context):
        from . import manual_scene
        existing_scenes = {scene.as_pointer() for scene in bpy.data.scenes}
        try:
            scene = manual_scene.enter(context)
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if (scene.as_pointer() not in existing_scenes
                and context.preferences.edit.use_global_undo):
            bpy.ops.ed.undo_push(message='创建风机手动控制场景')
        return {'FINISHED'}


class WFRL_OT_ManualLeave(bpy.types.Operator):
    bl_idname = 'wfrl.manual_leave'
    bl_label = '返回原场景'
    bl_description = '暂停手动风机并返回进入前的场景'

    @classmethod
    def poll(cls, context):
        return is_manual(context.scene)

    def execute(self, context):
        from . import manual_scene
        try:
            manual_scene.leave(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualAction(bpy.types.Operator):
    bl_idname = 'wfrl.manual_action'
    bl_label = '风机手动控制'
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=(('START', '运行', '按当前 RPM 运行'),
                                         ('PAUSE', '暂停', '暂停运动并保留 RPM'),
                                         ('STOP', '停转', '将 RPM 设为 0 并暂停'),
                                         ('RESET', '复位', '恢复默认姿态与 9 RPM，保持暂停')))

    @classmethod
    def poll(cls, context):
        return is_manual(context.scene) and bool(selected_entries(context.scene))

    def execute(self, context):
        global _updating
        scene = context.scene
        valid, reason = validate_scene(scene)
        if not valid:
            pause_scene(scene)
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}
        entries = selected_entries(scene)
        t1_trial = trial_active(scene) and any(entry.turbine_id == 'T1' for entry in entries)
        if t1_trial and self.action == 'STOP':
            self.report({'WARNING'}, '请先结束 T1 变桨试验，再修改 RPM')
            return {'CANCELLED'}
        if self.action == 'START' and not any(entry.rpm > 0 for entry in entries):
            self.report({'WARNING'}, '请先设置大于 0 的转速')
            return {'CANCELLED'}
        if self.action != 'RESET':
            try:
                for entry in entries:
                    _validate_controlled_rotation(entry)
            except ValueError as exc:
                pause_scene(scene, apply=False)
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
        now = time.monotonic()
        if t1_trial and self.action == 'RESET':
            _trial_undo_checkpoint(scene, now)
        for entry in entries:
            if t1_trial and entry.turbine_id == 'T1':
                trial_action(scene, self.action, now=now)
                continue
            clock = _clock(scene, entry, now)
            _write_azimuth(entry, clock.configure(now, running=False))
            _updating = True
            try:
                if self.action == 'STOP':
                    entry.rpm = 0.0
                elif self.action == 'RESET':
                    entry.yaw_deg = entry.pitch_deg = entry.azimuth_deg = 0.0
                    entry.rpm = DEFAULT_RPM
                    entry.individual_pitch = False
                    for index in range(1, 4):
                        setattr(entry, f'pitch_b{index}_deg', 0.0)
                    _settle_pitch(entry)
                _sync_pitch(scene, entry, now, running=self.action == 'START')
                entry.running = (self.action == 'START' and entry.rpm > 0
                                 and _scene_key(scene) in visible_scenes())
            finally:
                _updating = False
            clock.configure(now, rpm=entry.rpm, running=entry.running, azimuth_deg=entry.azimuth_deg)
            _apply_entry(entry)
        sync_batch(scene)
        _redraw(scene)
        if self.action == 'START':
            _wake_timer()
        return {'FINISHED'}


class WFRL_OT_ManualFocus(bpy.types.Operator):
    bl_idname = 'wfrl.manual_focus'
    bl_label = '观察选中风机'
    bl_description = '调整当前视口，观察所选风机或全部风机'

    @classmethod
    def poll(cls, context):
        return is_manual(context.scene) and bool(selected_entries(context.scene))

    def execute(self, context):
        from . import manual_scene
        try:
            manual_scene.focus(context, context.scene.wfrl_manual_target)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class _ManualPoseTarget:
    @classmethod
    def poll(cls, context):
        return is_manual(context.scene) and bool(selected_entries(context.scene))


class WFRL_OT_ManualPosePreset(_ManualPoseTarget, bpy.types.Operator):
    bl_idname = 'wfrl.manual_pose_preset'
    bl_label = '应用常用姿态'
    bl_options = {'REGISTER', 'UNDO'}

    preset: bpy.props.EnumProperty(items=(
        ('BLADE_DOWN', '叶片1朝下', '暂停所选风机，将叶片1转到叶轮盘下方，保留桨距、朝向与转速'),
        ('BLADE_SIDE', '叶片1侧向', '暂停所选风机，将叶片1转到叶轮盘侧方，保留桨距、朝向与转速'),
        ('FEATHER', '顺桨 90°', '暂停所选风机，关闭分别变桨并将三片桨距设为90°，保留朝向、叶轮角度与转速')))

    def execute(self, context):
        scene = context.scene
        try:
            valid, reason = validate_scene(scene)
            if not valid:
                raise ValueError(reason)
            now = time.monotonic()
            poses = _selected_pose_changes(scene, manual_pose.preset_changes(self.preset), now=now)
            _apply_poses(scene, poses, now=now)
        except (ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualPoseNudge(_ManualPoseTarget, bpy.types.Operator):
    bl_idname = 'wfrl.manual_pose_nudge'
    bl_label = '微调角度'
    bl_description = '按各台风机当前值增减角度；角度限于预览范围，叶轮角度循环'
    bl_options = {'REGISTER', 'UNDO'}

    field: bpy.props.EnumProperty(items=tuple((name, label, '') for name, label in (
        ('yaw_deg', '机舱朝向'), ('pitch_deg', '统一桨距'), ('azimuth_deg', '叶轮角度'),
        ('pitch_b1_deg', '叶片1桨距'), ('pitch_b2_deg', '叶片2桨距'), ('pitch_b3_deg', '叶片3桨距'))))
    delta: bpy.props.FloatProperty(name='增量 °', default=1.0)

    def execute(self, context):
        scene = context.scene
        try:
            valid, reason = validate_scene(scene)
            if not valid:
                raise ValueError(reason)
            entries = selected_entries(scene)
            if self.field == 'azimuth_deg' and any(entry.running and entry.rpm > 0 for entry in entries):
                raise ValueError('请先暂停所选风机，再微调叶轮角度')
            if self.field.startswith('pitch_b') and any(not entry.individual_pitch for entry in entries):
                raise ValueError('请先为所选风机启用分别变桨')
            now = time.monotonic()
            snapshot = pose_snapshot(scene, now=now, actual_pitch=False)
            poses = {}
            for entry in entries:
                values = snapshot[entry.turbine_id]
                values[self.field] = manual_pose.adjusted_angle(self.field, values[self.field], self.delta)
                if self.field == 'pitch_deg':
                    for index in range(1, 4):
                        values[f'pitch_b{index}_deg'] = values['pitch_deg']
                poses[entry.turbine_id] = values
            _apply_poses(scene, poses, pause=False, now=now, ramp=self.field in _PITCH_FIELDS,
                         preserve_pitch=self.field not in _PITCH_FIELDS)
        except (ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualPoseExport(_ManualPoseTarget, bpy.types.Operator, ExportHelper):
    bl_idname = 'wfrl.manual_pose_export'
    bl_label = '保存三机姿态'
    bl_description = '将三台风机的姿态、桨距模式和转速保存为JSON，不保存运行状态'
    filename_ext = '.json'

    filter_glob: bpy.props.StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        temporary = None
        try:
            valid, reason = validate_scene(context.scene)
            if not valid:
                raise ValueError(reason)
            payload = manual_pose.encode_poses(pose_snapshot(context.scene))
            path = Path(bpy.path.abspath(self.filepath))
            if not path.name or path.is_dir():
                raise ValueError('请选择JSON文件位置')
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.' + path.name, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            os.replace(temporary, path)
        except (OSError, ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, f'无法保存姿态：{exc}')
            return {'CANCELLED'}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.report({'INFO'}, '三机姿态已保存')
        return {'FINISHED'}


class WFRL_OT_ManualPoseImport(_ManualPoseTarget, bpy.types.Operator, ImportHelper):
    bl_idname = 'wfrl.manual_pose_import'
    bl_label = '载入所选姿态'
    bl_description = '按T1/T2/T3编号恢复所选风机，保持暂停；未选风机保持原状态'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'

    filter_glob: bpy.props.StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            valid, reason = validate_scene(context.scene)
            if not valid:
                raise ValueError(reason)
            path = Path(bpy.path.abspath(self.filepath))
            with path.open('r', encoding='utf-8') as stream:
                contents = stream.read(1024 * 1024 + 1)
            if len(contents) > 1024 * 1024:
                raise ValueError('姿态文件过大')
            poses = manual_pose.decode_poses(contents)
            ids = [entry.turbine_id for entry in selected_entries(context.scene)]
            missing = set(ids) - set(poses)
            if missing:
                raise ValueError('姿态文件缺少所选风机：' + '、'.join(sorted(missing)))
            _apply_poses(context.scene, {tid: poses[tid] for tid in ids})
        except (OSError, ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, f'无法载入姿态：{exc}')
            return {'CANCELLED'}
        self.report({'INFO'}, '所选风机姿态已载入，保持暂停')
        return {'FINISHED'}


CLASSES = (WFRL_ManualTurbine, WFRL_ManualBatch, WFRL_ManualTrial, WFRL_OT_ManualPitchResume,
           WFRL_OT_ManualTrial, WFRL_OT_ManualDown, WFRL_OT_ManualEnter,
           WFRL_OT_ManualLeave, WFRL_OT_ManualAction, WFRL_OT_ManualFocus,
           WFRL_OT_ManualPosePreset, WFRL_OT_ManualPoseNudge,
           WFRL_OT_ManualPoseExport, WFRL_OT_ManualPoseImport)
_HANDLERS = (('load_pre', _before_load), ('load_post', _after_restore),
             ('undo_pre', _before_restore), ('undo_post', _after_restore),
             ('redo_pre', _before_restore), ('redo_post', _after_restore))
_SCENE_PROPERTIES = ('wfrl_manual_turbines', 'wfrl_manual_batch', 'wfrl_manual_target',
                     'wfrl_manual_return_scene', 'wfrl_manual_scene', 'wfrl_manual_step_deg',
                     'wfrl_manual_trial', 'wfrl_manual_pitch_rate_deg_s', 'wfrl_manual_display_detail')


def _pitch_rate_update(scene, context):
    if _updating or not is_manual(scene):
        return
    now = time.monotonic()
    valid, _reason = validate_scene(scene)
    if not valid:
        pause_scene(scene, now=now, apply=False)
        return
    entries = [entry for entry in scene.wfrl_manual_turbines
               if not (trial_active(scene) and entry.turbine_id == 'T1')]
    try:
        for entry in entries:
            _validate_controlled_rotation(entry)
    except ValueError:
        pause_scene(scene, now=now, apply=False)
        return
    for entry in entries:
        _sync_pitch(scene, entry, now)
        apply_pitch(entry, effective_pitches(entry))
    _redraw(scene)
    _wake_timer()


def register():
    global _registered
    if _registered:
        return
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)
    bpy.types.Scene.wfrl_manual_turbines = bpy.props.CollectionProperty(type=WFRL_ManualTurbine)
    bpy.types.Scene.wfrl_manual_batch = bpy.props.PointerProperty(type=WFRL_ManualBatch)
    bpy.types.Scene.wfrl_manual_trial = bpy.props.PointerProperty(type=WFRL_ManualTrial)
    from .manual_geometry import DETAIL_ITEMS, detail_update
    bpy.types.Scene.wfrl_manual_display_detail = bpy.props.EnumProperty(
        name='显示精度', items=DETAIL_ITEMS, default='OVERVIEW', update=detail_update)
    bpy.types.Scene.wfrl_manual_pitch_rate_deg_s = bpy.props.FloatProperty(
        name='变桨速度 °/s', description='普通手动桨距按此速度到位；0 为立即到位，冻结试验使用自己的时间曲线',
        default=0.0, min=0.0, max=30.0, precision=1, update=_pitch_rate_update)
    bpy.types.Scene.wfrl_manual_return_scene = bpy.props.PointerProperty(type=bpy.types.Scene)
    from .manual_scene_links import bind, resolve, restore
    bpy.types.Scene.wfrl_manual_scene = property(resolve, bind)
    bpy.types.Scene.wfrl_manual_step_deg = bpy.props.FloatProperty(
        name='微调步长 °', description='加减按钮按每台风机的当前值调整，不统一已有差异',
        default=1.0, min=.1, max=15.0, precision=1)
    bpy.types.Scene.wfrl_manual_target = bpy.props.EnumProperty(
        name='控制对象', items=(('T1', 'T1', '控制风机 T1'), ('T2', 'T2', '控制风机 T2'),
                              ('T3', 'T3', '控制风机 T3'), ('ALL', '全部', '只将编辑的参数应用到全部风机')),
        default='T1', update=_selection_update)
    _registered = True
    from . import manual_video
    manual_video.register()
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)
    if hasattr(bpy.data, 'scenes'):
        restore()
        pause_all()
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=TICK_SECONDS, persistent=True)


def unregister():
    global _registered
    from . import manual_scene
    from . import manual_video
    manual_video.unregister()
    _registered = False
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    pause_all()
    manual_scene.clear_views()
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    for name in _SCENE_PROPERTIES:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
