"""Bounded, opt-in timing probes; never writes files on user interactions.

t1: handler received input; t2: group draw starts; t3: atomic image commit;
t4: viewport callback finished (not physical screen illumination). External test
tools must supply their own t0; missing endpoints are never inferred.
"""
from collections import deque
import time

ENABLED = False
EVENTS = deque(maxlen=10000)


def record(kind, **values):
    if ENABLED:
        EVENTS.append({'kind': kind, 'at': time.perf_counter(), 'unix_s': time.time(), **values})


def start():
    global ENABLED
    EVENTS.clear()
    ENABLED = True


def stop():
    global ENABLED
    ENABLED = False
    return list(EVENTS)
