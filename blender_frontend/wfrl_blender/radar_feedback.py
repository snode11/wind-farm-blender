"""Presentation of recorded radar observations, driven only by simulation time."""
from bisect import bisect_right
import math
from pathlib import Path

PULSE_SECONDS = 0.20  # Visibility aid, not physical laser emission duration.
LABELS = {'above_threshold': '正常', 'near_threshold': '低净空报警', 'waiting': '等待测量'}
_icons = None


def alarm_state(value):
    """Use the reader's aligned, expiring, hysteretic B2 state; never truth."""
    if (value or {}).get('measurement_mode') == 'dual_beam':
        return value['status']
    measurement = (value or {}).get('measurement')
    if not measurement:
        return 'waiting'
    estimate = measurement.get('estimate_m')
    if (not isinstance(estimate, (int, float)) or not math.isfinite(estimate)
            or value['time_s'] > measurement['expires_at_s']):
        return 'waiting'
    state = value.get('status')
    return state if state in LABELS else 'waiting'


def beam_activity(reader, time_s):
    """Recent per-beam valid observations; seeks cannot leak future events."""
    if hasattr(reader, 'beam_activity'):
        return reader.beam_activity(time_s, PULSE_SECONDS)
    times = getattr(reader, '_radar_feedback_times', None)
    if times is None:
        times = {name: sorted(r['time_s'] for r in reader.package.measurements
                             if r['beams'][name]['valid'])
                 for name in ('B1', 'B2', 'B3')}
        reader._radar_feedback_times = times
    active = []
    for name in ('B1', 'B2', 'B3'):
        events = times[name]
        index = bisect_right(events, time_s) - 1
        active.append(index >= 0 and time_s - events[index] <= PULSE_SECONDS + 1e-9)
    return tuple(active)


def icon_id(state):
    global _icons
    if _icons is None:
        import bpy.utils.previews
        _icons = bpy.utils.previews.new()
        for name in LABELS:
            _icons.load(name, str(Path(__file__).parent / 'assets' / 'radar' / (name + '.png')), 'IMAGE')
    return _icons[state].icon_id


def unregister():
    global _icons
    if _icons is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_icons)
        _icons = None
