"""Opt-in aggregate instrumentation; never a viewport-FPS measurement.

Disabled by default. No per-frame log or unbounded sample storage is retained.
Timings may nest, so they must not be added to estimate total frame time.
"""
from collections import defaultdict
from functools import wraps
from time import perf_counter

_enabled = False
_counts = defaultdict(int)
_timings = {}
_started = perf_counter()


def configure(enabled=False):
    global _enabled
    _enabled = bool(enabled)
    reset()


def reset():
    global _started
    _counts.clear()
    _timings.clear()
    _started = perf_counter()


def count(name, amount=1):
    if _enabled:
        _counts[name] += amount


def begin():
    return perf_counter() if _enabled else None


def end(name, started):
    if started is None or not _enabled:
        return
    elapsed = perf_counter() - started
    row = _timings.setdefault(name, {'calls': 0, 'total_s': 0.0, 'max_s': 0.0})
    row['calls'] += 1
    row['total_s'] += elapsed
    row['max_s'] = max(row['max_s'], elapsed)


def timed(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not _enabled:
                return function(*args, **kwargs)
            started = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                end(name, started)
        return wrapped
    return decorate


def snapshot():
    return {'enabled': _enabled, 'observation_s': perf_counter() - _started,
            'counts': dict(_counts),
            'timings': {key: {**value, 'mean_ms': value['total_s'] * 1000 / value['calls']}
                        for key, value in _timings.items()},
            'boundary': 'CPU callback timings, possibly nested; not draw FPS or hardware presentation'}
