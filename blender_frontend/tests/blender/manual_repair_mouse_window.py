"""Read-only mouse/keyboard observations and short viewport draw sampling."""
import importlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists()
OUT.mkdir(parents=True)
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
addon.register()
if hasattr(bpy.context.preferences.filepaths, 'use_save_on_exit'):
    bpy.context.preferences.filepaths.use_save_on_exit = False
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        area.spaces.active.show_region_ui = True
EVENTS, DRAWS, SAMPLES = [], [], []
PERF = dict(stage=None)
LAST = None


def drawn():
    if PERF['stage']:
        DRAWS.append(time.monotonic())


HANDLE = bpy.types.SpaceView3D.draw_handler_add(drawn, (), 'WINDOW', 'POST_PIXEL')


def observe():
    global LAST
    try:
        now = time.monotonic()
        scene = bpy.context.window.scene
        state = dict(time=now, scene=scene.name, scenes=list(bpy.data.scenes.keys()),
                     manual=control.is_manual(scene), addon_file=addon.__file__,
                     blender=bpy.app.version_string)
        if state['manual']:
            state.update(valid=control.validate_scene(scene), target=scene.wfrl_manual_target,
                         turbines=[dict(id=e.turbine_id, yaw=e.yaw_deg, pitch=e.pitch_deg,
                                        rpm=e.rpm, angle=e.azimuth_deg, running=e.running)
                                   for e in scene.wfrl_manual_turbines])
        space = next(a.spaces.active for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        state['shading'] = dict(type=space.shading.type, world=space.shading.use_scene_world,
                                lights=space.shading.use_scene_lights)
        key = {k: v for k, v in state.items() if k != 'time'}
        if LAST != key:
            EVENTS.append(state)
            LAST = key
            (OUT / 'observations.json').write_text(json.dumps(EVENTS, ensure_ascii=False, indent=2))
        (OUT / 'current.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
        if (state['manual'] and PERF['stage'] is None and not SAMPLES
                and scene.wfrl_manual_target == 'ALL'
                and all(e.running and e.rpm == 8 for e in scene.wfrl_manual_turbines)):
            PERF.update(stage='MATERIAL warmup', start=now)
            DRAWS.clear()
        if PERF['stage'] and now - PERF['start'] >= 10:
            stage = PERF['stage']
            if stage != 'MATERIAL warmup':
                duration = now - PERF['start']
                gaps = [b - a for a, b in zip(DRAWS, DRAWS[1:])]
                SAMPLES.append(dict(shading=stage, seconds=duration, draws=len(DRAWS),
                                    draw_fps=len(DRAWS) / duration,
                                    median_gap_ms=statistics.median(gaps) * 1000 if gaps else None,
                                    max_gap_ms=max(gaps) * 1000 if gaps else None))
            if stage == 'MATERIAL warmup':
                PERF.update(stage='MATERIAL', start=now)
            elif stage == 'MATERIAL':
                PERF.update(stage='SOLID', start=now)
                space.shading.type = 'SOLID'
            else:
                PERF['stage'] = None
                space.shading.type = 'MATERIAL'
                report = dict(status='PASS', addon_file=addon.__file__, samples=SAMPLES,
                              scope='Ten-second POST_PIXEL draw samples in one fixed viewport; not full-clip or long-duration validation')
                (OUT / 'viewport-sampling.json').write_text(json.dumps(report, indent=2))
                PERF['capture_after'] = now + 3
            DRAWS.clear()
        if PERF.get('capture_after') and now >= PERF['capture_after']:
            bpy.ops.screen.screenshot(filepath=str(OUT / 'manual-daylight.png'))
            PERF['capture_after'] = None
    except Exception:
        (OUT / 'observer-error.txt').write_text(traceback.format_exc())
    return .2


bpy.app.timers.register(observe, first_interval=1, persistent=True)
print('MANUAL_REPAIR_MOUSE_READY ' + addon.__file__, flush=True)
