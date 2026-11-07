"""RNA update callbacks for manual turbine inputs and batch controls."""
from . import manual_control as _control


def _update_entry(entry, context, field):
    if _control._updating:
        return
    scene = entry.id_data
    valid, reason = _control.validate_scene(scene)
    if not valid:
        _control._protect_scene(scene, reason)
        return
    now = _control.time.monotonic()
    protected = bool(scene.wfrl_manual_error)
    if protected:
        _control.pause_scene(scene, now=now, apply=False)
        if field == 'running' or (entry.turbine_id == 'T1' and _control.trial_active(scene)):
            return
        try:
            _control._validate_native_controls(getattr(entry, name) for name in _control._POINTER_FIELDS)
        except (ValueError, AttributeError, ReferenceError) as exc:
            _control._protect_scene(scene, exc, turbine=entry.turbine_id, now=now)
            return
    if entry.turbine_id == 'T1' and _control.trial_active(scene):
        _control._sync_trial(scene, now, running=entry.running if field == 'running' else None)
        _control.sync_batch(scene)
        _control._redraw(scene)
        return
    clock = _control._clock(scene, entry, now)
    angle = entry.azimuth_deg if field == 'azimuth_deg' else None
    if field == 'individual_pitch' and entry.individual_pitch:
        _control._updating = True
        try:
            for index in range(1, 4):
                setattr(entry, f'pitch_b{index}_deg', entry.pitch_deg)
        finally:
            _control._updating = False
    if field == 'pitch_deg':
        _control._updating = True
        try:
            for index in range(1, 4):
                setattr(entry, f'pitch_b{index}_deg', entry.pitch_deg)
        finally:
            _control._updating = False
    if field in _control._PITCH_FIELDS:
        _control._sync_pitch(scene, entry, now, running=not protected and _control._scene_key(scene) in _control.visible_scenes(),
                    retarget=True)
    running = entry.running and entry.rpm > 0 and _control._scene_key(scene) in _control.visible_scenes()
    _control._write_azimuth(entry, clock.configure(now, rpm=entry.rpm, running=running, azimuth_deg=angle))
    if entry.running != running:
        _control._updating = True
        try:
            entry.running = running
        finally:
            _control._updating = False
    if protected:
        if field == 'yaw_deg':
            entry.yaw_object.rotation_mode = 'XYZ'
            entry.yaw_object.rotation_euler.z = _control.math.radians(entry.yaw_deg)
        elif field == 'azimuth_deg':
            _control._apply_rotor(entry)
        elif field in _control._PITCH_FIELDS:
            _control.apply_pitch(entry, _control.effective_pitches(entry))
    else:
        _control._apply_entry(entry)
    _control.sync_batch(scene)
    _control._redraw(scene)
    if (running and entry.rpm > 0) or (_control.pitch_ramping(entry) and not entry.pitch_paused):
        _control._wake_timer()


def _update(field):
    def changed(entry, context):
        _control._update_entry(entry, context, field)
    return changed


def _batch_update(field):
    def changed(batch, context):
        if _control._updating:
            return
        scene = batch.id_data
        if not _control.is_manual(scene) or scene.wfrl_manual_target != 'ALL':
            return
        valid, _reason = _control.validate_scene(scene)
        if not valid:
            _control.pause_scene(scene)
            return
        if _control.trial_active(scene):
            _control.sync_batch(scene)
            return
        entries = scene.wfrl_manual_turbines
        try:
            for entry in entries:
                _control._validate_native_controls(getattr(entry, name) for name in _control._POINTER_FIELDS)
        except (ValueError, AttributeError, ReferenceError) as exc:
            _control._protect_scene(scene, exc, turbine=entry.turbine_id)
            _control.sync_batch(scene)
            return
        value = getattr(batch, field)
        for entry in entries:
            if field == 'individual_pitch' and entry.individual_pitch == value:
                continue
            setattr(entry, field, value)
        _control.sync_batch(scene)
    return changed


def _selection_update(scene, context):
    _control.sync_batch(scene)


def _pitch_rate_update(scene, context):
    if _control._updating or not _control.is_manual(scene):
        return
    now = _control.time.monotonic()
    valid, reason = _control.validate_scene(scene)
    if not valid:
        _control._protect_scene(scene, reason, now=now)
        return
    if scene.wfrl_manual_error:
        _control.pause_scene(scene, now=now, apply=False)
        return
    entries = [entry for entry in scene.wfrl_manual_turbines
               if not (_control.trial_active(scene) and entry.turbine_id == 'T1')]
    try:
        for entry in entries:
            _control._validate_controlled_rotation(entry)
    except (ValueError, AttributeError, ReferenceError) as exc:
        _control._protect_scene(scene, exc, turbine=entry.turbine_id, now=now)
        return
    for entry in entries:
        _control._sync_pitch(scene, entry, now)
        _control.apply_pitch(entry, _control.effective_pitches(entry))
    _control._redraw(scene)
    _control._wake_timer()
