"""Native radar stop, unload and reload retain ownership until recording drains."""
import json
import os
from pathlib import Path
import sys
import threading

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender.radar_x100 import runtime
from wfrl_blender.radar_x100.core import tracking
from wfrl.radar_x100 import acquisition, recording, schema

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
assert bpy.app.background
addon.register()
scene = bpy.context.scene
checks = []
for action in ('stop', 'load_pre', 'unregister'):
    sink = recording.Recorder(OUT / action)
    entered, release = threading.Event(), threading.Event()
    append = sink.append
    def held_append(frame, stamp):
        entered.set()
        assert release.wait(30)
        append(frame, stamp)
    sink.append = held_append
    fanout = acquisition.Fanout(sink)
    join = fanout.worker.join
    fanout.submit(schema.RadarFrame('X100', action, 0, 'TEST', 'NEAR',
                                  np.zeros(1, dtype=schema.POINT_DTYPE)), 0.)
    s = runtime.Session.__new__(runtime.Session)
    s.scene, s.mode, s.fanout = scene, 'REPLAY', fanout
    s.recording_stopping = s.closing = False
    s.client = s.frame = s.gpu_batch = s.fov_gpu_batch = None
    s.display_valid = False
    # close() resets blade inference state, which Session.__init__ always creates.
    s.blade_tracker = tracking.BladeTracker()
    runtime._SESSIONS[scene.as_pointer()] = s
    scene.wfrl_x100_enabled = True
    try:
        assert entered.wait(3)
        if action == 'stop':
            # A writer still draining keeps ownership: stop() returns False instead of blocking.
            assert runtime.stop(scene) is False
            assert scene.wfrl_x100_enabled and scene['wfrl_x100_status'] != '已关闭'
        elif action == 'load_pre':
            runtime.on_load_pre(None)
        else:
            # Unload returns normally and keeps only the drain poll until the writer exits.
            runtime.unregister()
            assert bpy.app.timers.is_registered(runtime.tick)
            assert all(runtime.on_load_pre not in getattr(bpy.app.handlers, name) for name in runtime._RESET_HANDLERS)
        assert runtime.session_for(scene) is s and s.fanout is fanout
        assert s.recording_stopping and s.closing and fanout.worker.is_alive()
        try: s.start_recording(OUT / 'conflict')
        except ValueError: pass
        else: raise AssertionError('Conflicting restart was accepted')
    finally:
        release.set()
        join(3)
    if action == 'unregister':
        runtime.unregister()
        assert runtime.session_for(scene) is None
        runtime.register()
    runtime.stop(scene)
    assert runtime.session_for(scene) is None and s.fanout is None
    assert sink.state == 'COMPLETE'
    assert recording.Replay(sink.path).frame(0).sequence == 0
    checks.append(dict(action=action, retained_ownership=True, retried=True, complete_after_worker_exit=True))
addon.unregister()
result = dict(status='PASS', module_file=addon.__file__, blender=bpy.app.version_string, checks=checks)
(OUT / 'result.json').write_text(json.dumps(result, indent=2))
print('RADAR_RECORDING_STOP_PASS', json.dumps(result), flush=True)
