"""Rigid turbine poses, batch display and synchronized snapshots."""
from . import manual_control as _control


def selected_entries(scene):
    if not _control.is_manual(scene):
        return []
    target = scene.wfrl_manual_target
    return [entry for entry in scene.wfrl_manual_turbines
            if target == 'ALL' or entry.turbine_id == target]


def _write_azimuth(entry, value):
    previous = _control._updating
    _control._updating = True
    try:
        entry.azimuth_deg = value
    finally:
        _control._updating = previous


def _apply_rotor(entry):
    if entry.rotor_object.rotation_mode != 'XYZ':
        entry.rotor_object.rotation_mode = 'XYZ'
    entry.rotor_object.rotation_euler.x = _control.math.radians(entry.azimuth_deg)


def _apply_entry(entry):
    entry.yaw_object.rotation_mode = 'XYZ'
    entry.yaw_object.rotation_euler.z = _control.math.radians(entry.yaw_deg)
    _control._apply_rotor(entry)
    if not entry.pitch_state_ready:
        _control._settle_pitch(entry)
    _control.apply_pitch(entry, _control.effective_pitches(entry))


def apply_scene(scene):
    """Apply saved manual poses without changing scene, playback or backend state."""
    valid, reason = _control.validate_scene(scene)
    if not valid:
        _control._protect_scene(scene, reason)
        return False
    if scene.wfrl_manual_error:
        _control.pause_scene(scene, apply=False)
        return False
    try:
        for entry in scene.wfrl_manual_turbines:
            _control._apply_entry(entry)
        _control._redraw(scene)
        return True
    except (ReferenceError, AttributeError):
        _control.pause_scene(scene)
        return False


def sync_batch(scene, *, fields=_control._POSE_FIELDS):
    """Display the first turbine's inputs without modifying any turbine."""
    if not _control.is_manual(scene) or not scene.wfrl_manual_turbines:
        return
    previous = _control._updating
    _control._updating = True
    try:
        first = scene.wfrl_manual_turbines[0]
        for field in fields:
            setattr(scene.wfrl_manual_batch, field, getattr(first, field))
    finally:
        _control._updating = previous


def mixed_fields(scene):
    entries = list(scene.wfrl_manual_turbines)
    if not entries:
        return []
    return [field for field in _control._POSE_FIELDS
            if any(abs(float(getattr(entry, field)) - float(getattr(entries[0], field))) > 1e-4
                   for entry in entries[1:])]


def pose_snapshot(scene, *, now=None, actual_pitch=True):
    """Capture all turbine parameters at one wall-clock instant."""
    now = _control.time.monotonic() if now is None else now
    poses = {}
    for entry in scene.wfrl_manual_turbines:
        values = {field: getattr(entry, field) for field in _control._POSE_FIELDS}
        if entry.turbine_id == 'T1' and _control.trial_active(scene):
            clock = _control._trial_clock(scene, now)
            pose = _control.replace(clock).sample(now)
            values['azimuth_deg'] = pose.azimuth_deg % 360.0
            if pose.time_s > 0:
                values['individual_pitch'] = True
            if values['individual_pitch']:
                for index, pitch in enumerate(pose.pitches_deg, 1):
                    values[f'pitch_b{index}_deg'] = pitch
        else:
            clock = _control._CLOCKS.get(_control._entry_key(scene, entry))
            if entry.running and clock is not None:
                values['azimuth_deg'] = _control.replace(clock).sample(now)
            if actual_pitch:
                pitch_clock = _control._PITCH_CLOCKS.get(_control._entry_key(scene, entry))
                pitches = _control.replace(pitch_clock).sample(now) if pitch_clock else _control.effective_pitches(entry)
                if max(pitches) - min(pitches) > 1e-5:
                    values['individual_pitch'] = True
                if not values['individual_pitch']:
                    values['pitch_deg'] = pitches[0]
                for index, pitch in enumerate(pitches, 1):
                    values[f'pitch_b{index}_deg'] = pitch
        poses[entry.turbine_id] = _control.manual_pose.pose_values(values)
    return poses


def _apply_poses(scene, poses, *, pause=True, now=None, ramp=False, preserve_pitch=False):
    """Validate every target before applying poses and synchronizing its clock."""
    valid, reason = _control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    entries = {entry.turbine_id: entry for entry in scene.wfrl_manual_turbines}
    if not poses or not set(poses) <= set(entries):
        raise ValueError('姿态文件中的风机与当前场景不匹配')
    if 'T1' in poses and _control.trial_active(scene):
        raise ValueError('请先结束 T1 变桨试验，再调整或载入 T1 姿态')
    checked = {tid: _control.manual_pose.pose_values(values) for tid, values in poses.items()}
    now = _control.time.monotonic() if now is None else now
    for tid in checked:
        _control._validate_native_controls(getattr(entries[tid], field) for field in _control._POINTER_FIELDS)
    protected = bool(scene.wfrl_manual_error)
    if protected:
        _control.pause_scene(scene, now=now, apply=False)
        pause = True
    previous = _control._updating
    _control._updating = True
    try:
        for tid, values in checked.items():
            entry = entries[tid]
            running = not pause and entry.running and values['rpm'] > 0
            for field, value in values.items():
                setattr(entry, field, value)
            entry.running = running
            key = _control._entry_key(scene, entry)
            _control._CLOCKS.pop(key, None)
            if running:
                _control._CLOCKS[key] = _control.RotorClock(entry.azimuth_deg, entry.rpm, True, now)
            if ramp:
                _control._sync_pitch(scene, entry, now,
                            running=not protected and _control._scene_key(scene) in _control.visible_scenes(), retarget=True)
            elif not preserve_pitch:
                _control._settle_pitch(entry)
            _control._apply_entry(entry)
    finally:
        _control._updating = previous
    if protected:
        _control.recheck_feedback(scene)
    elif pause:
        _control._pause_status(scene, 'USER')
    _control.sync_batch(scene)
    _control._redraw(scene)
    if ramp:
        _control._wake_timer()


def _selected_pose_changes(scene, changes, *, now):
    poses = _control.pose_snapshot(scene, now=now)
    result = {}
    for entry in _control.selected_entries(scene):
        result[entry.turbine_id] = {**poses[entry.turbine_id], **changes}
    return result
