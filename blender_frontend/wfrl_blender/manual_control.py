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
FEATHER_PITCH_DEG = 90.0
RUN_PITCH_DEG = 0.0
DEFAULT_PITCH_RATE_DEG_S = 5.0
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


from .manual_control_validation import (
    _pause_status as _pause_status,
    _record_error as _record_error,
    _protect_scene as _protect_scene,
    recheck_feedback as recheck_feedback,
    _validate_native_controls as _validate_native_controls,
    _validate_controlled_rotation as _validate_controlled_rotation,
    other_scene_objects as other_scene_objects,
    validate_scene as validate_scene,
    guard_pose_value as guard_pose_value,
    validate_pose_export as validate_pose_export,
)
from .manual_control_pitch import (
    target_pitches as target_pitches,
    target_pitch as target_pitch,
    effective_pitches as effective_pitches,
    _write_pitches as _write_pitches,
    _settle_pitch as _settle_pitch,
    pitch_ramping as pitch_ramping,
    _command_pitch as _command_pitch,
    _pitch_clock as _pitch_clock,
    _sync_pitch as _sync_pitch,
    apply_pitch as apply_pitch,
)
from .manual_control_trial import (
    trial_active as trial_active,
    _t1 as _t1,
    trial_configuration as trial_configuration,
    _trial_clock as _trial_clock,
    _sync_trial as _sync_trial,
    confirm_trial as confirm_trial,
    _trial_undo_checkpoint as _trial_undo_checkpoint,
    trial_action as trial_action,
)
from .manual_control_poses import (
    selected_entries as selected_entries,
    _write_azimuth as _write_azimuth,
    _apply_rotor as _apply_rotor,
    _apply_entry as _apply_entry,
    apply_scene as apply_scene,
    sync_batch as sync_batch,
    mixed_fields as mixed_fields,
    pose_snapshot as pose_snapshot,
    _apply_poses as _apply_poses,
    _selected_pose_changes as _selected_pose_changes,
)
from .manual_control_updates import (
    _update_entry as _update_entry,
    _update as _update,
    _batch_update as _batch_update,
    _selection_update as _selection_update,
    _pitch_rate_update as _pitch_rate_update,
)


def is_manual(scene):
    try:
        return scene is not None and scene.get('wfrl_scene_kind') == 'manual'
    except ReferenceError:
        return False


def _scene_key(scene):
    return scene.as_pointer()


def _entry_key(scene, entry):
    return (_scene_key(scene), entry.turbine_id)


def visible_scenes():
    """A scene can move only while a window displays it."""
    try:
        return {window.scene.as_pointer() for window in bpy.context.window_manager.windows}
    except (AttributeError, ReferenceError):
        return set()


def _clock(scene, entry, now):
    key = _entry_key(scene, entry)
    if key not in _CLOCKS:
        _CLOCKS[key] = RotorClock(entry.azimuth_deg, entry.rpm, False, now)
    return _CLOCKS[key]


def _redraw(scene):
    for listener in tuple(POSE_LISTENERS):
        try:
            listener(scene)
        except (AttributeError, ReferenceError, RuntimeError, KeyError, ValueError) as exc:
            print('WFRL manual pose listener:', exc)
    from . import viewport
    try:
        viewport.tag_redraw((scene,))
    except (AttributeError, ReferenceError):
        pass


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


def register():
    from . import manual_rna
    manual_rna.register()


def unregister():
    from . import manual_rna
    manual_rna.unregister()
