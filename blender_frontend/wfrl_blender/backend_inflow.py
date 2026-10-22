"""Bounded backend inflow history for illustrative, simulation-clock tracers."""
import json
import math
from bisect import bisect_right

KEY = 'wfrl_cinematic_inflow_history'


def record(scene, payload):
    inflow = (payload.get('scene') or {}).get('inflow') or {}
    stamp = payload.get('timestamp') or {}
    try:
        sample = [float(stamp['value']), float(inflow['speed']),
                  math.radians(270 - float(inflow['direction']))]
    except (KeyError, TypeError, ValueError):
        scene['wfrl_cinematic_inflow_valid'] = False
        return False
    if not all(math.isfinite(v) for v in sample) or sample[1] < 0:
        scene['wfrl_cinematic_inflow_valid'] = False
        return False
    history = json.loads(scene.get(KEY, '[]'))
    if history and sample[0] < history[-1][0]:
        history = []  # backend reset/rewind starts a new timeline
    previous = history[-1] if history else None
    distance = (previous[3] + (sample[0] - previous[0]) * previous[1]) if previous and len(previous) > 3 else 0.0
    sample.append(distance)
    if history and sample[0] == history[-1][0]:
        history[-1] = sample
    else:
        history.append(sample)
    scene[KEY] = json.dumps(history[-2048:])
    scene['wfrl_cinematic_inflow_valid'] = True
    scene['wfrl_cinematic_backend_time'] = sample[0]
    scene['wfrl_wind_speed_mps'] = sample[1]
    return True


def history(scene):
    return json.loads(scene.get(KEY, '[]'))


def sample(rows, at):
    """Hold measured snapshots; never invent a future wind transition."""
    i = max(0, bisect_right([r[0] for r in rows], at) - 1)
    return rows[i][1], rows[i][2]


def active(scene):
    return scene.get('wfrl_scene_kind') == 'live' and bool(scene.get('wfrl_cinematic_inflow_valid', False))
