"""Manual pitch targets, actual blade poses and rate-limited clocks."""
from . import manual_control as _control


def target_pitches(entry):
    return tuple(getattr(entry, f'pitch_b{i}_deg') if entry.individual_pitch else entry.pitch_deg
                 for i in range(1, 4))


def target_pitch(entry, index):
    return _control.target_pitches(entry)[index - 1]


def effective_pitches(entry):
    if entry.pitch_state_ready:
        return tuple(getattr(entry, f'pitch_actual_b{i}_deg') for i in range(1, 4))
    return _control.target_pitches(entry)


def _write_pitches(entry, values):
    for index, value in enumerate(values, 1):
        setattr(entry, f'pitch_actual_b{index}_deg', value)
    entry.pitch_state_ready = True


def _settle_pitch(entry):
    values = _control.target_pitches(entry)
    _control._write_pitches(entry, values)
    for index, value in enumerate(values, 1):
        setattr(entry, f'pitch_start_b{index}_deg', value)
    entry.pitch_paused = True
    _control._PITCH_CLOCKS.pop(_control._entry_key(entry.id_data, entry), None)


def pitch_ramping(entry):
    return any(abs(a - b) > 1e-6 for a, b in zip(_control.effective_pitches(entry), _control.target_pitches(entry)))


def _command_pitch(entry, pitch, *, now, settle=False):
    """Set collective pitch, following the scene rate unless immediate placement is requested."""
    scene = entry.id_data
    _control._sync_pitch(scene, entry, now, running=False)
    entry.individual_pitch = False
    entry.pitch_deg = pitch
    for index in range(1, 4):
        setattr(entry, f'pitch_b{index}_deg', pitch)
    if settle:
        _control._settle_pitch(entry)
    else:
        _control._sync_pitch(scene, entry, now, running=True, retarget=True)


def _pitch_clock(scene, entry, now):
    key = _control._entry_key(scene, entry)
    if key not in _control._PITCH_CLOCKS:
        _control._PITCH_CLOCKS[key] = _control.PitchClock(_control.effective_pitches(entry), _control.target_pitches(entry),
                                      scene.wfrl_manual_pitch_rate_deg_s, not entry.pitch_paused, now)
    return _control._PITCH_CLOCKS[key]


def _sync_pitch(scene, entry, now, *, running=None, retarget=False):
    clock = _control._pitch_clock(scene, entry, now)
    values = clock.sample(now)
    if retarget:
        for index, value in enumerate(values, 1):
            setattr(entry, f'pitch_start_b{index}_deg', value)
    values = clock.configure(now, targets=_control.target_pitches(entry),
                             rate=scene.wfrl_manual_pitch_rate_deg_s, running=running)
    _control._write_pitches(entry, values)
    entry.pitch_paused = not clock.running
    return values


def apply_pitch(entry, pitches):
    for index, pitch in enumerate(pitches, 1):
        blade = getattr(entry, f'blade{index}_object')
        blade.rotation_mode = 'XYZ'
        blade.rotation_euler.z = -_control.math.radians(pitch)
