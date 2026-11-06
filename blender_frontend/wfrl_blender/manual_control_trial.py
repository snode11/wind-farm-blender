"""Frozen T1 pitch-trial configuration, timing and actions."""
from . import manual_control as _control


def trial_active(scene):
    return _control.is_manual(scene) and scene.wfrl_manual_trial.engaged


def _t1(scene):
    return next(entry for entry in scene.wfrl_manual_turbines if entry.turbine_id == 'T1')


def trial_configuration(scene):
    """Read the saved start pose and frozen trial independently of pending inputs."""
    values = _control.json.loads(scene.wfrl_manual_trial.configuration)
    if values['schema'] != 'wfrl.manual-pitch-trial.v1':
        raise ValueError('无法识别变桨试验配置')
    if values['blade_model'] != scene.get('wfrl_manual_blade_model'):
        raise ValueError('试验叶片模型与保存配置不匹配')
    start = _control.manual_pose.pose_values(values['start_pose'])
    pitches = tuple(start[f'pitch_b{i}_deg'] if start['individual_pitch'] else start['pitch_deg']
                    for i in range(1, 4))
    spec = _control.PitchTrial(start['azimuth_deg'], start['rpm'], pitches,
                      tuple(values['targets']), tuple(values['selected']), values['duration_s'],
                      values.get('interpolation', 'SMOOTH'))
    return start, spec


def _trial_clock(scene, now):
    key, configuration = _control._scene_key(scene), scene.wfrl_manual_trial.configuration
    cached = _control._TRIAL_CLOCKS.get(key)
    if cached is None or cached[0] != configuration:
        _start, spec = _control.trial_configuration(scene)
        cached = (configuration, _control.TrialClock(spec, scene.wfrl_manual_trial.elapsed_s, False, now))
        _control._TRIAL_CLOCKS[key] = cached
    return cached[1]


def _sync_trial(scene, now, *, running=None, reset=False):
    """Write rotor and all blade pitches from one trial-time sample."""
    trial, entry = scene.wfrl_manual_trial, _control._t1(scene)
    clock = _control._trial_clock(scene, now)
    if running:
        running = _control._scene_key(scene) in _control.visible_scenes()
    pose = clock.configure(now, running=running, reset=reset)
    start, _spec = _control.trial_configuration(scene)
    previous = _control._updating
    _control._updating = True
    try:
        trial.elapsed_s = pose.time_s
        entry.yaw_deg, entry.rpm, entry.pitch_deg = start['yaw_deg'], clock.trial.rpm, start['pitch_deg']
        entry.azimuth_deg = pose.azimuth_deg % 360.0
        at_start = pose.time_s == 0.0
        entry.individual_pitch = start['individual_pitch'] if at_start else True
        for index, pitch in enumerate(pose.pitches_deg, 1):
            setattr(entry, f'pitch_b{index}_deg', start[f'pitch_b{index}_deg'] if at_start else pitch)
        entry.running = clock.running
        _control._settle_pitch(entry)
        _control._apply_entry(entry)
    finally:
        _control._updating = previous
    return pose


def confirm_trial(scene, *, now=None):
    """Validate all inputs before freezing one T1 trial and starting its clock."""
    valid, reason = _control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    if _control.trial_active(scene):
        raise ValueError('请先结束当前试验，再确认另一组目标')
    if scene.wfrl_manual_error:
        valid, reason = _control.recheck_feedback(scene)
        if not valid:
            raise ValueError(reason)
    _control._validate_controlled_rotation(_control._t1(scene))
    now = _control.time.monotonic() if now is None else now
    pending = scene.wfrl_manual_trial
    start = _control.pose_snapshot(scene, now=now)['T1']
    pitches = tuple(start[f'pitch_b{i}_deg'] if start['individual_pitch'] else start['pitch_deg']
                    for i in range(1, 4))
    selected = tuple(getattr(pending, f'select_b{i}') for i in range(1, 4))
    targets = tuple(getattr(pending, f'target_b{i}_deg') for i in range(1, 4))
    spec = _control.PitchTrial(start['azimuth_deg'], start['rpm'], pitches, targets, selected,
                      pending.duration_s, pending.interpolation)
    configuration = _control.json.dumps(dict(schema='wfrl.manual-pitch-trial.v1', start_pose=start,
                                    blade_model=scene.get('wfrl_manual_blade_model'),
                                    selected=selected, targets=targets, duration_s=spec.duration_s,
                                    interpolation=spec.interpolation),
                               allow_nan=False)
    _control._CLOCKS.pop(_control._entry_key(scene, _control._t1(scene)), None)
    pending.configuration, pending.elapsed_s, pending.engaged = configuration, 0.0, True
    _control._TRIAL_CLOCKS[_control._scene_key(scene)] = (configuration, _control.TrialClock(spec, 0.0, False, now))
    _control._sync_trial(scene, now, running=True)
    _control.sync_batch(scene)
    _control._redraw(scene)
    _control._wake_timer()


def _trial_undo_checkpoint(scene, now):
    """Materialize the running pose before an undoable reset or completion."""
    if not any(entry.running or (_control.pitch_ramping(entry) and not entry.pitch_paused)
               for entry in scene.wfrl_manual_turbines):
        return
    _control._sync_trial(scene, now, running=False)
    for entry in scene.wfrl_manual_turbines:
        if entry.turbine_id == 'T1':
            continue
        if _control.pitch_ramping(entry) and not entry.pitch_paused:
            _control._sync_pitch(scene, entry, now)
            _control.apply_pitch(entry, _control.effective_pitches(entry))
        if entry.running:
            clock = _control._clock(scene, entry, now)
            _control._write_azimuth(entry, clock.configure(now, rpm=entry.rpm, running=True))
            _control._apply_rotor(entry)
    _control.sync_batch(scene)
    if _control.bpy.context.preferences.edit.use_global_undo:
        _control.bpy.ops.ed.undo_push(message='T1 变桨试验当前姿态')


def trial_action(scene, action, *, now=None, undo_checkpoint=False):
    if not _control.trial_active(scene):
        raise ValueError('请先确认并开始 T1 变桨试验')
    valid, reason = _control.validate_scene(scene)
    if not valid:
        _control._protect_scene(scene, reason, turbine='T1', now=now)
        raise ValueError(reason)
    if action not in {'START', 'PAUSE', 'RESET', 'END'}:
        raise ValueError('试验期间请使用暂停、继续、复位或结束试验')
    try:
        if action == 'RESET':
            _control._validate_native_controls(getattr(_control._t1(scene), field) for field in _control._POINTER_FIELDS)
        else:
            _control._validate_controlled_rotation(_control._t1(scene))
    except (ValueError, AttributeError, ReferenceError) as exc:
        _control._protect_scene(scene, exc, turbine='T1', now=now)
        raise ValueError(str(exc)) from exc
    if action == 'START' and scene.wfrl_manual_error:
        valid, reason = _control.recheck_feedback(scene)
        if not valid:
            raise ValueError(reason)
    now = _control.time.monotonic() if now is None else now
    if undo_checkpoint and action in {'RESET', 'END'}:
        _control._trial_undo_checkpoint(scene, now)
    _control._sync_trial(scene, now, running=action == 'START', reset=action == 'RESET')
    if action == 'END':
        scene.wfrl_manual_trial.engaged = False
        _control._TRIAL_CLOCKS.pop(_control._scene_key(scene), None)
    if action != 'START':
        _control._pause_status(scene, 'USER')
    if action == 'RESET' and scene.wfrl_manual_error:
        _control.recheck_feedback(scene)
    _control.sync_batch(scene)
    _control._redraw(scene)
    if action == 'START':
        _control._wake_timer()
