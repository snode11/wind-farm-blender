"""Reproducible visible-window benchmark; run only in a dedicated Blender UI.

See docs/blender/前端播放验收.md. Every run requires a fresh output directory.
POST_PIXEL is a viewport draw observation, not display scanout.
"""
import hashlib
import importlib
import importlib.util
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
core = importlib.import_module(MODULE+'.custom_cameras')
native = importlib.import_module(MODULE+'.native_camera_views')
# The evaluator is test infrastructure, independent of the addon under test.
spec = importlib.util.spec_from_file_location('_wfrl_benchmark_evaluator', ROOT/'blender_frontend/wfrl_blender/playback_benchmark.py')
evaluator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evaluator
spec.loader.exec_module(evaluator)
PlaybackTarget, evaluate_playback = evaluator.PlaybackTarget, evaluator.evaluate_playback

PATH = os.environ.get('WFRL_BENCH_PATH', 'triple')
LAYOUT = os.environ.get('WFRL_BENCH_LAYOUT', 'STRIP')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
if OUT.exists() and any(OUT.iterdir()):
    raise RuntimeError('WFRL_TEST_OUTPUT must be a new or empty directory')
OUT.mkdir(parents=True, exist_ok=True)
START = time.perf_counter()
TIMEOUTS = {
    'load': float(os.environ.get('WFRL_BENCH_LOAD_TIMEOUT', '120')),
    'ready': float(os.environ.get('WFRL_BENCH_READY_TIMEOUT', '30')),
    'warm': float(os.environ.get('WFRL_BENCH_WARM_TIMEOUT', '30')),
    'settle': 10.,
    'measure': float(os.environ.get('WFRL_BENCH_SECONDS', '100')),
    'switch_ready': float(os.environ.get('WFRL_BENCH_READY_TIMEOUT', '30')),
    'pause_check': float(os.environ.get('WFRL_BENCH_RECOVERY_TIMEOUT', '10')),
    'resume_check': float(os.environ.get('WFRL_BENCH_RECOVERY_TIMEOUT', '10')),
}
WARM = float(os.environ.get('WFRL_BENCH_WARM_SECONDS', '5'))
SWITCH_AT = float(os.environ.get('WFRL_BENCH_SWITCH_AT', '3'))
FAULT = os.environ.get('WFRL_BENCH_FAULT', '')
FAULT_AT = float(os.environ.get('WFRL_BENCH_FAULT_AT', '2'))
SOURCE_HASHES = {}
for source_name in ('runtime.py', 'deflection.py', 'farm_flex.py', 'native_camera_views.py', 'performance.py'):
    source_path = Path(addon.__file__).resolve().parent/source_name
    SOURCE_HASHES[source_name] = {'path': str(source_path),
        'sha256': hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path.exists() else None}
for source_path in (Path(__file__).resolve(), ROOT/'blender_frontend/wfrl_blender/playback_benchmark.py'):
    SOURCE_HASHES[source_path.name] = {'path': str(source_path), 'sha256': hashlib.sha256(source_path.read_bytes()).hexdigest()}
state = {'stage': 'load', 'stage_started': START, 'events': [], 'results': [],
         'segments': ['WATCH', 'TRIPLE'] if PATH == 'close_reopen' else ['TRIPLE' if PATH == 'triple' else 'WATCH'],
         'recording': False, 'closed': False, 'error': None}


def now():
    return time.perf_counter()-START


def pointer(value):
    try:
        return value.as_pointer() if value else None
    except (ReferenceError, RuntimeError):
        return None


def windows():
    return list(bpy.context.window_manager.windows)


def active_context():
    active = native._ACTIVE
    window = active.window if active and any(w == active.window for w in windows()) else state.get('main')
    if not window or not any(w == window for w in windows()):
        raise RuntimeError('No valid playback window')
    area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
    if not area:
        raise RuntimeError('No valid VIEW_3D playback area')
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if not region:
        raise RuntimeError('No valid WINDOW playback region')
    return window, area, region


def snapshot():
    identities = []
    for window in windows():
        identities.append({'window': pointer(window), 'screen': pointer(window.screen),
            'scene': pointer(window.scene), 'frame': window.scene.frame_current,
            'simulation_s': (window.scene.frame_current-state.get('first_frame', 1))/60.,
            'playing': bool(window.screen.is_animation_playing),
            'window_px': [window.width, window.height],
            'areas': [{'area': pointer(a), 'type': a.type,
                'regions': [{'region': pointer(r), 'type': r.type} for r in a.regions]} for a in window.screen.areas]})
    return {'wall_s': now(), 'windows': identities,
            'native_window': pointer(native._ACTIVE.window) if native._ACTIVE else None,
            'last_draws': {str(k): rows[-1] if rows else None for k, rows in state.get('draws', {}).items()}}


def event(event_name, **data):
    state['events'].append({'event': event_name, 'context': snapshot(), **data})


def is_playing():
    return any(window.screen.is_animation_playing for window in windows())


def set_playing(desired):
    """Idempotent operation; never assume animation_play means start."""
    before = is_playing()
    result = {'NOOP'}
    window, area, region = active_context()
    if desired != before:
        # Cancel through the actual playing screen if the camera window changed.
        if not desired:
            playing_window = next((w for w in windows() if w.screen.is_animation_playing), window)
            if playing_window != window:
                window = playing_window
                area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
                region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            result = bpy.ops.screen.animation_play() if desired else bpy.ops.screen.animation_cancel(restore_frame=False)
    after = is_playing()
    event('start' if desired else 'pause', before=before, after=after,
          operator_result=sorted(result), operator_context={'window': pointer(window), 'screen': pointer(window.screen),
          'scene': pointer(window.scene), 'area': pointer(area), 'region': pointer(region)})
    if after != desired:
        raise RuntimeError(f'Playback operation failed: requested={desired}, observed={after}')


def stage(name):
    state.update(stage=name, stage_started=time.perf_counter())
    event('stage', name=name)
    write_result()


def seek(frame):
    old = state['scene'].frame_current
    state['seeking'] = True
    try:
        state['scene'].frame_set(frame)
    finally:
        state['seeking'] = False
    event('seek', before_frame=old, after_frame=state['scene'].frame_current)


def open_view(mode, switching=False):
    window, area, region = active_context()
    before = snapshot()
    kwargs = {'mode': mode}
    if 'layout' in bpy.ops.wfrl.native_camera_view.get_rna_type().properties:
        kwargs['layout'] = LAYOUT
    with bpy.context.temp_override(window=window, area=area, region=region):
        result = bpy.ops.wfrl.native_camera_view(**kwargs)
    event('switch_view' if switching else 'open_view', mode=mode, layout=LAYOUT,
          before=before, operator_result=sorted(result))
    if result != {'FINISHED'}:
        raise RuntimeError(f'Camera operator did not finish: {result}')
    state['view_mode'] = mode


def routes_ready():
    session = native._ACTIVE
    count = 1 if state['view_mode'] == 'WATCH' else 3
    return bool(session and len(session.entries) == count
        and all(entry.get('proxy') and entry['area'].type == 'VIEW_3D' for entry in session.entries))


def final_route_states():
    session = native._ACTIVE
    if not session:
        return {}
    result = {}
    for entry in session.entries:
        slot = str(entry['slot'])
        try:
            area, proxy = entry['area'], entry.get('proxy')
            space = area.spaces.active
            result[slot] = {'valid': bool(proxy and area.type == 'VIEW_3D'
                and space.use_local_camera and space.camera == proxy
                and space.region_3d.view_perspective == 'CAMERA'
                and session.window.scene == session.scene),
                'area': pointer(area), 'area_px': [area.width, area.height],
                'camera': pointer(proxy), 'displayed_camera': pointer(space.camera),
                'scene': pointer(session.scene), 'window_scene': pointer(session.window.scene)}
        except (ReferenceError, AttributeError, RuntimeError) as exc:
            result[slot] = {'valid': False, 'error': str(exc)}
    return result


def frame_observed(scene, *_):
    if not state['recording'] or scene != state.get('scene') or state.get('seeking'):
        return
    rows = state['scene_rows']
    frame = scene.frame_current
    if rows and rows[-1][1] == frame:
        return
    previous = rows[-1][1] if rows else state['target'].start_frame
    rows.append([now(), frame])
    reason = state['target'].transition(previous, frame)
    if reason and not state.get('termination'):
        state['termination'] = {'reason': reason, 'previous_frame': previous, 'frame': frame, 'wall_s': now(),
                                'playback_running': is_playing(), 'driver_seek': False}


def draw():
    if state['stage'] == 'settle' and state.get('profile'):
        session = native._ACTIVE
        if session and bpy.context.window == session.window:
            prefix = 'paused'
        elif bpy.context.window == state.get('main'):
            prefix = 'paused.main'
        else:
            return
        state['profile'].count(prefix+'.visible_draw_callbacks')
        key = (pointer(bpy.context.window), pointer(bpy.context.area))
        previous = state.setdefault('paused_draw_last', {}).get(key)
        frame = state['scene'].frame_current
        if previous != frame:
            state['profile'].count(prefix+'.distinct_draws')
            state['paused_draw_last'][key] = frame
        return
    if not state['recording']:
        return
    try:
        session = native._ACTIVE
        if not session or bpy.context.window != session.window:
            return
        entry = next((e for e in session.entries if e['area'] == bpy.context.area), None)
        if not entry or FAULT == 'zero_draws' or (FAULT == 'missing_route' and entry['slot'] == 3):
            return
        rows = state['draws'].setdefault(str(entry['slot']), [])
        if state.get('profile'):
            state['profile'].count('visible.draw_callbacks')
            if not rows or rows[-1][1] != session.scene.frame_current:
                state['profile'].count('visible.distinct_draws')
        rows.append([now(), session.scene.frame_current])
    except Exception:
        state['callback_error'] = traceback.format_exc()


def begin_measurement():
    scene = state['scene']
    start = scene.frame_current
    tolerance = int(os.environ.get('WFRL_BENCH_TERMINAL_TOLERANCE', '30'))
    state.update(target=PlaybackTarget(start, scene.frame_end, terminal_tolerance_frames=tolerance),
        draws={str(slot): [] for slot in (core.SLOTS if PATH in {'playing_switch', 'paused_switch'} or state['mode'] == 'TRIPLE' else [state['slot']])},
        scene_rows=[[now(), start]], measure_started=now(), termination=None,
        layout_before=core.layout_dict(scene), recording=True, switched=False, fault_done=False)
    # Align exact bounds for the pure evaluator.
    state['scene_rows'][0][0] = state['measure_started']
    if state.get('profile'):
        state['paused_profile'] = state['profile'].snapshot()
        state['profile'].reset()
    stage('measure')
    set_playing(True)
    print('BENCHMARK_START', PATH, state['mode'], flush=True)


def finish_measurement(reason=None, error=None):
    if not state['recording']:
        return
    frame_observed(state['scene'])
    ended = now()
    termination = {'reason': reason, 'wall_s': ended} if reason else state.get('termination')
    if not termination:
        termination = {'reason': 'runtime_error', 'wall_s': ended}
    state['recording'] = False
    result = evaluate_playback(target=state['target'], expected_routes=state['draws'].keys(),
        scene_rows=state['scene_rows'], draws=state['draws'], started=state['measure_started'], ended=ended,
        termination=termination, route_states=final_route_states(), error=error)
    result.update(mode=state['mode'], final_view_mode=state['view_mode'], path=PATH, layout=LAYOUT,
        layout_unchanged=core.layout_dict(state['scene']) == state['layout_before'],
        sync_mode=state['scene'].sync_mode,
        render_fps=state['scene'].render.fps, render_fps_base=state['scene'].render.fps_base,
        preview_pixel_size=state['scene'].render.preview_pixel_size,
        preview_samples=state['scene'].eevee.taa_samples,
        window_px=[native._ACTIVE.window.width, native._ACTIVE.window.height] if native._ACTIVE else None,
        paused_profile=state.get('paused_profile', {'enabled': False}),
        profile=state['profile'].snapshot() if state.get('profile') else {'enabled': False},
        pause_resume={'status': 'NOT_RUN'})
    if not result['layout_unchanged']:
        result['failures'].append('Physical camera layout changed during benchmark')
        result['status'] = 'FAIL'
    prefix = f"{len(state['results'])+1:02d}-{state['mode']}"
    (OUT/f'{prefix}-draws.json').write_text(json.dumps(state['draws']), encoding='utf-8')
    (OUT/f'{prefix}-scene.json').write_text(json.dumps(state['scene_rows']), encoding='utf-8')
    result['raw_files'] = [f'{prefix}-draws.json', f'{prefix}-scene.json']
    state['results'].append(result)
    event('measurement_ended', termination=termination, status=result['status'])
    write_result()


def write_result(status=None, termination=None):
    results = state['results']
    # A completed first segment must never make a still-running multi-segment
    # path look finished. Only end() supplies both a verdict and termination.
    overall = status if status is not None and termination is not None else 'INCOMPLETE'
    data = {'schema_version': 2, 'status': overall, 'path': PATH, 'layout': LAYOUT,
        'blender': bpy.app.version_string, 'platform': platform.platform(),
        'addon_path': str(Path(addon.__file__).resolve()), 'background': bpy.app.background,
        'source_hashes': SOURCE_HASHES,
        'method': 'Visible-window POST_PIXEL draws; fixed 60 Hz simulation mapping; not display scanout',
        'configuration': {'warm_s': WARM, 'timeouts_s': TIMEOUTS, 'switch_at_s': SWITCH_AT,
            'fault': FAULT, 'profile_requested': os.environ.get('WFRL_BENCH_PROFILE', '0'),
            'terminal_tolerance_frames': int(os.environ.get('WFRL_BENCH_TERMINAL_TOLERANCE', '30')),
            'screen_scale': bpy.context.preferences.system.ui_scale},
        'observation_s': now(), 'termination': termination, 'error': state['error'],
        'stage': state['stage'], 'stage_started_s': state['stage_started']-START,
        'results': results, 'events': state['events']}
    temporary = OUT/'result.json.tmp'
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(OUT/'result.json')


def end(status=None, reason='finished', error=None):
    if state['closed']:
        return
    state['closed'] = True
    state['error'] = error
    if state['recording']:
        finish_measurement(reason, error)
    try:
        if is_playing():
            set_playing(False)
        event('close_view_before')
        native.shutdown()
        event('close_view_after')
    except Exception:
        error = error or traceback.format_exc()
        state['error'] = error
        status = 'ERROR'
    if state.get('handler'):
        bpy.types.SpaceView3D.draw_handler_remove(state['handler'], 'WINDOW')
    if frame_observed in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(frame_observed)
    if error:
        (OUT/'error.txt').write_text(error, encoding='utf-8')
    if status is None:
        statuses = [r['status'] for r in state['results']]
        complete_path = (len(statuses) == (2 if PATH == 'close_reopen' else 1)
                         and not state['segments'] and state['stage'] == 'resume_check')
        status = 'PASS' if complete_path and all(s == 'PASS' for s in statuses) and all(r['pause_resume']['status'] == 'PASS' for r in state['results']) else next((s for s in ('ERROR', 'FAIL', 'ABORTED', 'INCOMPLETE') if s in statuses), 'INCOMPLETE')
    write_result(status, {'reason': reason, 'wall_s': now(), 'stage': state['stage']})
    print('BENCHMARK_RESULT', status, str(OUT/'result.json'), flush=True)
    bpy.ops.wm.quit_blender()


def recovery():
    set_playing(False)
    state['paused_frame'] = state['scene'].frame_current
    stage('pause_check')


def tick():
    if state['closed']:
        return None
    try:
        current = state['stage']
        age = time.perf_counter()-state['stage_started']
        if age > TIMEOUTS.get(current, 30.):
            end('INCOMPLETE', f'{current}_timeout')
            return None
        if state.get('callback_error'):
            raise RuntimeError(state['callback_error'])
        if current == 'load':
            if bpy.app.background:
                raise RuntimeError('Visible benchmark cannot run with --background')
            if PATH not in {'single', 'triple', 'close_reopen', 'playing_switch', 'paused_switch'}:
                raise ValueError('Unknown WFRL_BENCH_PATH')
            addon.register()
            addon.load_demo_scene()
            state.update(scene=bpy.context.scene, main=bpy.context.window,
                slot=bpy.context.window_manager.wfrl_custom_slot, first_frame=bpy.context.scene.frame_start)
            try:
                performance = importlib.import_module(MODULE+'.performance')
                performance.configure(enabled=os.environ.get('WFRL_BENCH_PROFILE', '0') == '1')
                state['profile'] = performance
            except ImportError:
                if os.environ.get('WFRL_BENCH_PROFILE') == '1':
                    raise
            state['handler'] = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_PIXEL')
            bpy.app.handlers.frame_change_post.append(frame_observed)
            stage('open')
        elif current == 'open':
            state['mode'] = state['segments'].pop(0)
            open_view(state['mode'])
            stage('ready')
        elif current == 'ready':
            if not routes_ready():
                return .1
            if os.environ.get('WFRL_BENCH_SHADING') == 'SOLID':
                for entry in native._ACTIVE.entries:
                    entry['area'].spaces.active.shading.type = 'SOLID'
            set_playing(False)
            seek(state['first_frame'])
            set_playing(True)
            stage('warm')
        elif current == 'warm':
            if age < WARM:
                return .1
            set_playing(False)
            seek(state['first_frame'])
            if state.get('profile'):
                state['profile'].reset()
            state['paused_draw_last'] = {}
            stage('settle')
        elif current == 'settle':
            if age < 2.:
                return .1
            begin_measurement()
        elif current in {'measure', 'switch_ready'}:
            elapsed = now()-state['measure_started']
            if elapsed > TIMEOUTS['measure']:
                end('INCOMPLETE', 'measure_timeout')
                return None
            frame_observed(state['scene'])
            if (current == 'measure' and not is_playing() and not state.get('termination')
                    and FAULT != 'timeout'):
                end('INCOMPLETE', 'playback_stopped')
                return None
            if FAULT and elapsed >= FAULT_AT and not state['fault_done']:
                state['fault_done'] = True
                if FAULT == 'active_stop':
                    end('ABORTED', 'active_stop')
                    return None
                if FAULT == 'runtime_error':
                    raise RuntimeError('Deliberately injected runtime error')
                if FAULT == 'timeout':
                    set_playing(False)
                if FAULT == 'unexpected_backward':
                    state['scene'].frame_set(state['first_frame'])
            if state.get('termination'):
                finish_measurement()
                recovery()
                return .1
            if current == 'switch_ready':
                if not routes_ready():
                    return .1
                if is_playing() != state['switch_playing']:
                    raise AssertionError('View switch changed playback state')
                frame = state['scene'].frame_current
                if frame < state['switch_frame'] or (not state['switch_playing'] and frame != state['switch_frame']):
                    raise AssertionError('View switch changed progress or paused frame')
                event('switch_verified', preserved_frame=state['switch_frame'], playing=state['switch_playing'])
                state['switched'] = True
                # Resume is explicit only for the intentionally paused path.
                if PATH == 'paused_switch':
                    set_playing(True)
                stage('measure')
            elif PATH in {'playing_switch', 'paused_switch'} and not state['switched'] and elapsed >= SWITCH_AT:
                if PATH == 'paused_switch':
                    set_playing(False)
                state.update(switch_frame=state['scene'].frame_current, switch_playing=is_playing())
                open_view('TRIPLE', switching=True)
                stage('switch_ready')
        elif current == 'pause_check':
            if age < .8:
                return .1
            if state['scene'].frame_current != state['paused_frame'] or is_playing():
                raise AssertionError('Paused frame or state drifted')
            # Preserve the old benchmark sequence exactly: pause -> frame 755 ->
            # resume 0.8 s -> pause -> close -> next open -> warm/reset/play.
            seek(755)
            set_playing(True)
            state['resume_start_frame'] = 755
            stage('resume_check')
        elif current == 'resume_check':
            if age < .8:
                return .1
            set_playing(False)
            if state['scene'].frame_current <= state['resume_start_frame']:
                raise AssertionError('Recovery playback did not advance')
            state['results'][-1]['pause_resume'] = {'status': 'PASS', 'resume_frame': state['scene'].frame_current}
            event('close_view_before')
            native.shutdown()
            event('close_view_after')
            if state['segments']:
                stage('between_segments')
            else:
                end()
                return None
        elif current == 'between_segments':
            if age >= 1.:
                stage('open')
    except Exception:
        end('ERROR', 'runtime_error', traceback.format_exc())
        return None
    return .1

write_result()
bpy.app.timers.register(tick, first_interval=1.)
