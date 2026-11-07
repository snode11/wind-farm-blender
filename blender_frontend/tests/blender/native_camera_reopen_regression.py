"""Exercise full recorded playback, close/reopen and repeated camera sessions.

Run source with --factory-startup, or an installed module in its isolated profile,
in a dedicated visible process with a fresh
WFRL_TEST_OUTPUT directory. Native operators drive the unchanged saved clip;
POST_PIXEL observations record viewport draws, not display scanout or FPS.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
OUT.mkdir(parents=True, exist_ok=False)
(OUT / 'temporary').mkdir()
bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'temporary')
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
    expected = ROOT / 'blender_frontend/wfrl_blender'
else:
    expected = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT'])
addon = importlib.import_module(MODULE)
native = importlib.import_module(MODULE + '.native_camera_views')
playback = importlib.import_module(MODULE + '.playback')
core = importlib.import_module(MODULE + '.custom_cameras')
clearance = importlib.import_module(MODULE + '.clearance_replay')
assert Path(addon.__file__).resolve().parent == expected.resolve()
RESULT = {
    'status': 'RUNNING', 'addon_file': addon.__file__, 'blender': bpy.app.version_string,
    'method': 'Independent visible process; native operators; unchanged saved MAPPO clip; POST_PIXEL draws.',
    'source_sha256': {Path(m.__file__).name: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                      for m in (native, playback, clearance)},
    'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'checks': {}, 'observations': [], 'draws': {}, 'frames': [], 'heartbeats': [],
}
STATE = {'step': 'setup', 'deadline': time.monotonic() + 480, 'segment': 'setup'}


def pointer(value):
    try:
        return value.as_pointer() if value is not None else None
    except ReferenceError:
        return None


def save():
    temporary = OUT / 'result.json.tmp'
    temporary.write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(OUT / 'result.json')


def observe(event, **details):
    scene = STATE.get('scene')
    session = native._ACTIVE
    row = {
        'event': event, 'segment': STATE['segment'], 'wall_s': time.monotonic(),
        'frame': scene.frame_current if scene else None,
        'loop_mode': scene.playback_loop_mode if scene else None,
        'owner_scene': playback.owner_scene().name if playback.owner_scene() else None,
        'owner_window': pointer(playback.owner_window()), 'playing': playback.any_playing(),
        'end_callback_pending': bpy.app.timers.is_registered(clearance._stop_finished_playback),
        'prepare_pending': bool(session and bpy.app.timers.is_registered(session.prepare)),
        'first_paint_pending': bool(session and bpy.app.timers.is_registered(session.first_paint)),
        'watch_pending': bpy.app.timers.is_registered(native.watch),
        'windows': [{'id': pointer(w), 'scene': pointer(w.scene), 'screen': pointer(w.screen),
                     'frame': w.scene.frame_current} for w in bpy.context.window_manager.windows],
        **details,
    }
    RESULT['observations'].append(row)
    save()


def check(name, condition, **details):
    RESULT['checks'][name] = {'passed': bool(condition), **details}
    save()
    assert condition, (name, details)


def context_for(window, area=None):
    area = area or next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return {'window': window, 'area': area, 'region': region}


def operator(identifier, *, window=None, **kwargs):
    window = window or (native._ACTIVE.window if native._ACTIVE else STATE['main'])
    context = context_for(window)
    observe(identifier + '_before', context={k: pointer(v) for k, v in context.items()}, arguments=kwargs)
    with bpy.context.temp_override(**context):
        response = getattr(bpy.ops.wfrl, identifier)(**kwargs)
    observe(identifier + '_after', response=sorted(response))
    assert response == {'FINISHED'}, (identifier, response)


def screenshot(name):
    window = native._ACTIVE.window if native._ACTIVE else STATE['main']
    with bpy.context.temp_override(window=window):
        assert bpy.ops.screen.screenshot(filepath=str(OUT / (name + '.png'))) == {'FINISHED'}


def draw():
    session = native._ACTIVE
    if not session or bpy.context.window != session.window:
        return
    entry = next((e for e in session.entries if e['area'] == bpy.context.area), None)
    if entry:
        key = STATE['segment'] + ':C' + str(entry['slot'])
        RESULT['draws'].setdefault(key, []).append([time.monotonic(), session.scene.frame_current])


def frame(scene, *_):
    if scene == STATE.get('scene'):
        RESULT['frames'].append([STATE['segment'], time.monotonic(), scene.frame_current])


def wait_ready(slots):
    deadline = time.monotonic() + 30
    while native._ACTIVE and native._ACTIVE.preparing:
        assert time.monotonic() < deadline, 'Camera layout timeout'
        yield .2
    session = native._ACTIVE
    check(STATE['segment'] + '_routes', bool(session) and [e['slot'] for e in session.entries] == slots)
    yield .7


def open_view(mode, layout='STRIP'):
    operator('native_camera_view', window=STATE['main'], mode=mode, layout=layout)
    yield from wait_ready([1] if mode == 'WATCH' else [1, 2, 3])


def close_view():
    session = native._ACTIVE
    operator('native_camera_exit')
    yield .5
    check(STATE['segment'] + '_closed', native._ACTIVE is None and len(bpy.context.window_manager.windows) == 1,
          lifecycle=session.lifecycle)
    check(STATE['segment'] + '_quality_restored', session.quality_settings() == STATE['quality'])
    check(STATE['segment'] + '_source_view_preserved', native._view_state(STATE['area'].spaces.active) == STATE['view'])


def advancing(name, seconds=1.2):
    scene = STATE['scene']
    start = scene.frame_current
    yield seconds
    observe(name)
    check(name, playback.is_playing(scene) and scene.frame_current > start,
          before=start, after=scene.frame_current)


def complete_clip(name, slots):
    scene = STATE['scene']
    STATE['segment'] = name
    start = scene.frame_current
    started = time.monotonic()
    operator('native_camera_play')
    yield .6
    check(name + '_starts_from_beginning', scene.frame_current > scene.frame_start
          and scene.frame_current < scene.frame_end and playback.is_playing(scene),
          before=start, after=scene.frame_current)
    screenshot(name + '-advancing')
    deadline = started + 120
    termination = None
    while termination is None:
        frames = [row[2] for row in RESULT['frames'] if row[0] == name and row[1] >= started]
        if scene.frame_current >= scene.frame_end and not playback.any_playing():
            termination = 'terminal_stop'
        elif any(a >= scene.frame_end - 30 and b <= scene.frame_start + 30
                 for a, b in zip(frames, frames[1:])):
            raise AssertionError('Unexpected loop before terminal stop')
        if termination:
            break
        assert time.monotonic() < deadline, 'Full-clip playback timeout'
        assert playback.is_playing(scene), 'Playback stopped before terminal frame'
        yield .25
    yield .4
    observe(name + '_terminal', termination=termination, maximum_frame=max(frames))
    check(name + '_terminal', scene.frame_current == scene.frame_end and not playback.any_playing(),
          termination=termination, maximum_frame=max(frames))
    for slot in slots:
        rows = [row for row in RESULT['draws'].get(name + ':C' + str(slot), []) if row[0] >= started]
        check(name + '_C' + str(slot) + '_draws', len({row[1] for row in rows}) > 3
              and rows[-1][1] == scene.frame_end,
              count=len(rows), first=rows[0] if rows else None, last=rows[-1] if rows else None)
    screenshot(name + '-terminal')


def run():
    check('isolated_visible_factory_startup', not bpy.app.background and not bpy.data.filepath
          and (MODULE.startswith('bl_ext.') or '--factory-startup' in sys.argv)
          and len(bpy.context.window_manager.windows) == 1)
    if hasattr(bpy.context.preferences.filepaths, 'use_save_on_exit'):
        bpy.context.preferences.filepaths.use_save_on_exit = False
    addon.register()
    addon.load_demo_scene()
    scene, main = bpy.context.scene, bpy.context.window
    area = next(a for a in main.screen.areas if a.type == 'VIEW_3D')
    area.spaces.active.region_3d.view_perspective = 'PERSP'
    area.spaces.active.region_3d.view_location = (4, 5, 6)
    area.spaces.active.region_3d.view_distance = 200
    bpy.context.window_manager.wfrl_custom_slot = 1
    STATE.update(scene=scene, main=main, area=area,
                 quality=(scene.render.preview_pixel_size, scene.eevee.taa_samples,
                          scene.eevee.use_shadows, scene.eevee.use_fast_gi),
                 view=native._view_state(area.spaces.active))
    RESULT['source'] = {'path': scene.get('wfrl_farm_flex_path'),
                        'frame_start': scene.frame_start, 'frame_end': scene.frame_end}
    STATE['handle'] = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_PIXEL')
    bpy.app.handlers.frame_change_post.append(frame)
    STATE['segment'] = 'single'
    yield from open_view('WATCH')
    yield from complete_clip('single', [1])
    finished_frame = scene.frame_current
    yield from close_view()
    STATE['segment'] = 'triple'
    yield from open_view('TRIPLE')
    check('reopen_preserves_completed_cursor', scene.frame_current == finished_frame)
    yield from complete_clip('triple', [1, 2, 3])
    yield from close_view()

    for index in range(2):
        STATE['segment'] = 'repeat_' + str(index)
        yield from open_view('WATCH')
        operator('native_camera_play')
        yield from advancing(STATE['segment'] + '_single_advances')
        operator('native_camera_view', mode='TRIPLE', layout='GRID' if index == 0 else 'STRIP')
        yield from wait_ready([1, 2, 3])
        yield from advancing(STATE['segment'] + '_triple_advances')
        operator('native_camera_play')
        held = scene.frame_current
        yield .5
        check(STATE['segment'] + '_pause_holds', not playback.any_playing() and scene.frame_current == held)
        yield from close_view()
        yield from open_view('TRIPLE')
        check(STATE['segment'] + '_reopen_stays_paused', not playback.any_playing() and scene.frame_current == held)
        operator('native_camera_play')
        yield from advancing(STATE['segment'] + '_resumes')
        yield from close_view()
        yield from advancing(STATE['segment'] + '_closed_source_advances')
        with bpy.context.temp_override(**context_for(main)):
            bpy.ops.screen.animation_cancel(restore_frame=False)
        check(STATE['segment'] + '_source_pauses', not playback.any_playing())

    STATE['segment'] = 'delayed_close_resume'
    yield from open_view('WATCH')
    operator('native_camera_play')
    yield from advancing('delayed_close_resume_initial_playback')
    session = native._ACTIVE
    operator('native_camera_exit')
    check('close_resume_is_pending', session.resume_scheduled and not playback.any_playing())
    with bpy.context.temp_override(**context_for(main)):
        bpy.ops.screen.animation_play()
        bpy.ops.screen.animation_cancel(restore_frame=False)
    held = scene.frame_current
    yield .7
    check('new_pause_invalidates_old_close_resume', not playback.any_playing() and scene.frame_current == held,
          lifecycle=session.lifecycle)
    check('camera_layout_preserved', core.layout_dict(scene) == STATE['camera_layout'])
    RESULT['status'] = 'PASS'


RUN = run()


def tick():
    try:
        assert time.monotonic() < STATE['deadline'], 'Regression deadline exceeded'
        if STATE.get('scene'):
            if 'camera_layout' not in STATE:
                STATE['camera_layout'] = core.layout_dict(STATE['scene'])
            RESULT['heartbeats'].append([STATE['segment'], time.monotonic(), STATE['scene'].frame_current,
                                         playback.any_playing(), pointer(playback.owner_window())])
        delay = next(RUN)
        save()
        return delay
    except StopIteration:
        pass
    except Exception:
        RESULT.update(status='FAIL', failed_segment=STATE['segment'], error=traceback.format_exc())
        observe('failure')
        screenshot('failure')
        print(RESULT['error'], flush=True)
    save()
    if STATE.get('handle'):
        bpy.types.SpaceView3D.draw_handler_remove(STATE['handle'], 'WINDOW')
    if frame in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(frame)
    addon._cancel_playback(all_scenes=True)
    native.shutdown(preserve_playback=False)
    print('NATIVE_CAMERA_REOPEN_' + RESULT['status'], flush=True)
    bpy.ops.wm.quit_blender()
    return None


save()
bpy.app.timers.register(tick, first_interval=1)
