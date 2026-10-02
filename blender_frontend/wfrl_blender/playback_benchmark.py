"""Pure evidence evaluation for visible-window playback benchmarks.

No Blender dependency: completion is established independently of FPS and of
later pause/resume checks. POST_PIXEL observations never prove display scanout.
"""
from dataclasses import dataclass
from itertools import combinations
from math import isfinite


@dataclass(frozen=True)
class PlaybackTarget:
    start_frame: int
    end_frame: int
    simulation_fps: float = 60.0
    terminal_tolerance_frames: int = 30
    loop_expected: bool = True
    stall_threshold_s: float = 0.5

    def __post_init__(self):
        if self.end_frame <= self.start_frame or self.simulation_fps <= 0:
            raise ValueError('target must have a positive interval and simulation FPS')
        if not 0 <= self.terminal_tolerance_frames < self.end_frame - self.start_frame:
            raise ValueError('terminal tolerance must be smaller than the target interval')
        if self.stall_threshold_s <= 0:
            raise ValueError('stall threshold must be positive')

    def transition(self, previous, current):
        """A descending frame only proves a loop at the declared boundaries."""
        if current >= previous:
            return 'completed_segment' if current >= self.end_frame else None
        if (self.loop_expected and previous >= self.end_frame-self.terminal_tolerance_frames
                and current <= self.start_frame+self.terminal_tolerance_frames
                and current >= self.start_frame):
            return 'completed_loop'
        return 'unexpected_backward'


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered)-1)*fraction
    low = int(index)
    high = min(low+1, len(ordered)-1)
    return ordered[low]+(ordered[high]-ordered[low])*(index-low)


def _intervals(rows):
    return [(b[0]-a[0])*1000 for a, b in zip(rows, rows[1:])]


def _timing(values):
    return {'p50_ms': _percentile(values, .5), 'p95_ms': _percentile(values, .95),
            'max_ms': max(values) if values else None,
            'intervals_over_100ms': sum(v > 100 for v in values)}


def _rows(rows, label, started, ended, failures):
    result = []
    for row in rows:
        try:
            timestamp, frame = float(row[0]), int(row[1])
            if not isfinite(timestamp) or timestamp < started or timestamp > ended:
                raise ValueError('timestamp outside observation interval')
            if result and timestamp < result[-1][0]:
                raise ValueError('timestamps are not ordered')
            result.append((timestamp, frame))
        except (ValueError, TypeError, IndexError, OverflowError) as exc:
            failures.append(f'{label}: invalid observation ({exc})')
    return result


def evaluate_playback(*, target, expected_routes, scene_rows, draws, started, ended,
                      termination, route_states=None, error=None):
    """Return JSON-ready verdict, complete observation metrics and diagnostics.

    Rows are (monotonic timestamp, scene frame). ``termination`` contains a
    ``reason`` and, for success, ``previous_frame``/``frame`` boundary evidence.
    An operator-driven seek must never be supplied as a playback boundary.
    """
    failures = []
    expected = [str(slot) for slot in expected_routes]
    if not expected or len(set(expected)) != len(expected):
        raise ValueError('expected_routes must be nonempty and unique')
    if ended < started:
        raise ValueError('observation end precedes start')
    total = ended-started
    reason = termination.get('reason', 'runtime_error')
    completed = reason in {'completed_segment', 'completed_loop'}
    rows = _rows(scene_rows, 'scene', started, ended, failures)
    high = max((r[1] for r in rows), default=target.start_frame)
    progress = [r for i, r in enumerate(rows) if i and r[1] != rows[i-1][1]]
    if not rows or rows[0][1] != target.start_frame:
        failures.append('scene: missing declared start frame')
    if not any(b[1] > a[1] for a, b in zip(rows, rows[1:])):
        failures.append('scene: no forward progression')
    for previous, current in zip(rows, rows[1:]):
        if current[1] < previous[1] and target.transition(previous[1], current[1]) != 'completed_loop':
            failures.append('scene: unexpected backward transition')
    if completed:
        if termination.get('playback_running') is not True or termination.get('driver_seek') is not False:
            failures.append('termination: not a verified playback transition (seek/stop state)')
        previous = termination.get('previous_frame')
        frame = termination.get('frame')
        if previous is None or frame is None or target.transition(previous, frame) != reason:
            failures.append('termination: boundary evidence is invalid')
        elif not any(a[1] == previous and b[1] == frame for a, b in zip(rows, rows[1:])):
            failures.append('termination: boundary transition not observed in scene sequence')
        if high < target.end_frame-target.terminal_tolerance_frames:
            failures.append('scene: target terminal interval not covered')
    progress_times = [started]+[r[0] for r in progress]+[ended]
    stalled = sum(max(0., b-a-target.stall_threshold_s) for a, b in zip(progress_times, progress_times[1:]))
    last_progress = progress[-1][0] if progress else None
    states = {str(k): v for k, v in (route_states or {}).items()}
    draw_map = {str(k): v for k, v in draws.items()}
    route_results = {}
    distinct_by_route = {}
    for slot in expected:
        values = _rows(draw_map.get(slot, []), f'C{slot}', started, ended, failures)
        distinct = [row for i, row in enumerate(values) if i == 0 or row[1] != values[i-1][1]]
        distinct_by_route[slot] = distinct
        terminal = any(row[1] >= target.end_frame-target.terminal_tolerance_frames for row in distinct)
        forward = any(b[1] > a[1] for a, b in zip(distinct, distinct[1:]))
        if not values:
            failures.append(f'C{slot}: zero draws')
        if not forward:
            failures.append(f'C{slot}: no drawing progression')
        if not terminal:
            failures.append(f'C{slot}: missing terminal drawing evidence')
        if slot not in states or states[slot].get('valid') is not True:
            failures.append(f'C{slot}: invalid or unverified final route state')
        # Include the trailing silent interval even if the last frame was early.
        heartbeat_age = ended-values[-1][0] if values else total
        if heartbeat_age > target.stall_threshold_s:
            failures.append(f'C{slot}: stale final drawing heartbeat')
        span = distinct[-1][0]-distinct[0][0] if len(distinct) > 1 else 0.
        route_results[slot] = {
            'draw_callbacks': len(values), 'distinct_draws': len(distinct),
            'active_draw_span_s': span,
            'active_draw_fps': (len(distinct)-1)/span if span > 0 else 0.,
            'observation_draw_rate_hz': max(0, len(distinct)-1)/total if total else 0.,
            'full_segment_draw_fps': None,
            **_timing(_intervals(distinct)),
            'has_forward_progress': forward, 'has_terminal_evidence': terminal,
            'last_heartbeat': {'wall_s': values[-1][0], 'frame': values[-1][1]} if values else None,
            'last_heartbeat_age_s': heartbeat_age,
            'state': states.get(slot, {}),
        }
    latency = {}
    for left, right in combinations(expected, 2):
        a = {frame: timestamp for timestamp, frame in distinct_by_route[left]}
        b = {frame: timestamp for timestamp, frame in distinct_by_route[right]}
        shared = sorted(a.keys() & b.keys())
        deltas = [abs(a[frame]-b[frame])*1000 for frame in shared]
        latency[f'C{left}-C{right}'] = {'shared_frames': len(shared), **_timing(deltas),
            'frame_deltas_ms': [[frame, (b[frame]-a[frame])*1000] for frame in shared]}
    if error or reason == 'runtime_error':
        status = 'ERROR'
    elif reason == 'active_stop':
        status = 'ABORTED'
    elif not completed:
        status = 'INCOMPLETE'
    else:
        status = 'FAIL' if failures else 'PASS'
    if status == 'PASS':
        for route in route_results.values():
            route['full_segment_draw_fps'] = route['observation_draw_rate_hz']
    return {
        'status': status, 'termination': dict(termination), 'error': error,
        'failures': list(dict.fromkeys(failures)), 'target': dict(target.__dict__),
        'observation_s': total, 'effective_progress_s': max(0., total-stalled),
        'stopped_progress_s': stalled, 'stall_threshold_s': target.stall_threshold_s,
        'last_scene_progress_wall_s': last_progress,
        'last_scene_progress_age_s': ended-last_progress if last_progress is not None else total,
        'observed_max_frame': high,
        'simulation_progress_s': max(0, min(high, target.end_frame)-target.start_frame)/target.simulation_fps,
        'completed_target_s': ((target.end_frame-target.start_frame)/target.simulation_fps) if status == 'PASS' else None,
        'simulation_speed_ratio': ((target.end_frame-target.start_frame)/target.simulation_fps/total) if status == 'PASS' and total else None,
        'routes': route_results,
        'same_frames_all_routes': all([r[1] for r in distinct_by_route[slot]] == [r[1] for r in distinct_by_route[expected[0]]] for slot in expected),
        'route_draw_time_differences': latency,
        'evidence_scope': 'visible viewport POST_PIXEL callbacks; not monitor scanout or physical synchronization',
    }
