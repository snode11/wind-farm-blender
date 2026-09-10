"""Native visible-window acceptance of real Interactive and Formal workflows."""
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

import bpy
import addon_utils
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get('WFRL_ACCEPT_OUTPUT', ROOT / 'evidence' / 'frontend_completion'))
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.view.show_splash = False
sys.path.insert(0, str(ROOT / 'blender_frontend'))
addon_utils.enable('wfrl_blender', default_set=True)
from wfrl_blender import runtime, charts
from wfrl_blender.preferences import get_preferences
prefs = get_preferences()
assert prefs is not None
prefs.default_scene = str(ROOT / 'scenes/turb3_ctrl3.yaml')
prefs.port = 8881
report = dict(gui=not bpy.app.background, checks={}, errors=[], stages=[], timing={}, samples=[])
started = time.monotonic()
stage = 'connect'
stage_start = started
last_log = started
last_step = None
pause_step = None
run_id = session_id = None
export_attempt = 0
base_build = runtime.build_live_scene

def timed_build(scene):
    begin = time.monotonic()
    base_build(scene)
    report['timing']['scene_build_seconds'] = time.monotonic() - begin
    report['timing']['scene_build_started_seconds'] = begin - started
runtime.build_live_scene = timed_build


def transition(name):
    global stage, stage_start
    stage, stage_start = name, time.monotonic()
    report['stages'].append(dict(stage=name, seconds=stage_start-started))
    print('ACCEPT_STAGE', name, round(stage_start-started, 2), flush=True)
    (OUT / 'progress.json').write_text(json.dumps(report, indent=2, default=str))


def control(action):
    assert bpy.ops.wfrl.backend_run(action=action) == {'FINISHED'}, action


def capture(name):
    path = OUT / (name + '.png')
    assert bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}
    report['checks'][name + '_screenshot'] = path.name


def finish():
    report['final_status'] = runtime.get_state().run_status
    report['elapsed_seconds'] = time.monotonic() - started
    report['passed'] = not report['errors'] and stage == 'complete'
    (OUT / 'report.json').write_text(json.dumps(report, indent=2, default=str))
    print('FRONTEND_ACCEPTANCE', report['passed'], flush=True)
    runtime.shutdown()
    bpy.ops.wm.quit_blender()


assert bpy.ops.wfrl.connection_mode(mode='interactive_training') == {'FINISHED'}
assert bpy.ops.wfrl.bridge_connect() == {'FINISHED'}
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.show_region_ui = True


def tick():
    global last_log, last_step, pause_step, run_id, session_id, export_attempt
    try:
        now = time.monotonic()
        ui = runtime.get_state()
        snap = runtime.kinematics.snapshot
        step = snap['step'] if snap else -1
        if now-last_log > 15:
            print('ACCEPT_PROGRESS', stage, round(now-started), ui.connection, ui.run_status, step, flush=True)
            last_log = now
        if ui.error and not (stage in {'disconnected', 'reconnecting'} and 'unconfirmed' in ui.error):
            raise AssertionError(ui.error)
        if snap and snap['turbines'][0]['channels']['power']['validity'] == 'valid':
            report['timing'].setdefault('first_valid_measurement_seconds', now-started)
        if snap and step != last_step:
            last_step = step
            if 'first_visible_sample_seconds' not in report['timing']:
                report['timing']['first_visible_sample_seconds'] = now-started
                bpy.ops.wfrl.select_camera(camera_name='WFRL.Camera.World')
            if len(report['samples']) < 100:
                blade = bpy.data.objects.get('WFRL.Turbine.T1.Blade1')
                report['samples'].append(dict(seconds=now-started, step=step, phase=snap['phase'],
                    channels=snap['turbines'][0]['channels'],
                    blade_pitch_degrees=math.degrees(blade.rotation_euler.z) if blade else None))
        if stage == 'connect' and runtime.allows_command('start'):
            settings = bpy.context.scene.wfrl_workflow
            settings.iters, settings.n_steps, settings.warmup_steps = 100, 8, 0
            control('start'); transition('running')
        elif stage == 'running' and step >= 11 and step % 8 not in (0, 7):
            assert runtime.training_dashboard.metric('mean_power')['validity'] == 'valid'
            report['checks']['interactive_statistics'] = dict(phase=runtime.training_dashboard.phase(),
                metrics={k:runtime.training_dashboard.metric(k) for k in ('mean_power','mean_raw_reward','mean_reward','value_loss')})
            run_id, session_id = ui.run_id, ui.session_id
            control('pause'); transition('pausing')
        elif stage == 'pausing' and ui.run_status == 'PAUSED' and runtime.allows_command('step'):
            pause_step = step
            capture('interactive-paused')
            transition('paused_stale')
        elif stage == 'paused_stale':
            assert step == pause_step, (step,pause_step)
            if now-stage_start >= 32:
                assert runtime.training_dashboard.metric('mean_power')['validity'] == 'stale'
                report['checks']['pause_stable_seconds'] = now-stage_start
                control('step'); transition('single_step')
        elif stage == 'single_step' and step == pause_step+1 and runtime.allows_command('step'):
            assert ui.run_status == 'PAUSED'
            assert runtime.training_dashboard.phase()['validity'] == 'valid'
            assert runtime.training_dashboard.metric('mean_power')['validity'] == 'stale'
            report['checks']['independent_freshness'] = dict(phase=runtime.training_dashboard.phase(), metric=runtime.training_dashboard.metric('mean_power'))
            capture('single-step-fresh-phase-stale-metric')
            transition('step_stable')
        elif stage == 'step_stable' and now-stage_start > 1.5:
            assert step == pause_step+1
            report['checks']['single_step_exactly_one'] = True
            control('resume'); transition('resumed')
        elif stage == 'resumed' and step > pause_step+2:
            assert bpy.ops.wfrl.bridge_disconnect() == {'FINISHED'}
            report['checks']['disconnect_unconfirmed'] = not runtime.get_state().confirmed
            transition('disconnected')
        elif stage == 'disconnected' and now-stage_start > 2:
            assert bpy.ops.wfrl.bridge_connect() == {'FINISHED'}
            transition('reconnecting')
        elif stage == 'reconnecting' and ui.connection == 'CONNECTED' and runtime.allows_command('stop'):
            assert ui.session_id == session_id and ui.run_id == run_id
            assert ui.run_status == 'RUNNING'
            report['checks']['active_reconnect_same_run'] = True
            capture('reconnected')
            control('stop'); transition('stopping')
        elif stage == 'stopping' and ui.run_status == 'STOPPED':
            report['checks']['stop_seconds'] = now-stage_start
            assert now-stage_start < 20
            assert ui.connection == 'CONNECTED'
            capture('stopped-connected')
            assert bpy.ops.wfrl.export_history(filepath=str(OUT/'interactive-history.json')) == {'FINISHED'}
            transition('export')
        elif stage == 'export' and charts.export_job.poll() == 'COMPLETE':
            data = json.loads((OUT/'interactive-history.json').read_text())
            report['checks']['history_export'] = dict(schema=data['schema_version'], bytes=(OUT/'interactive-history.json').stat().st_size)
            assert bpy.ops.wfrl.export_history(filepath='/dev/null/history.json') == {'FINISHED'}
            transition('export_failure')
        elif stage == 'export_failure' and charts.export_job.poll() == 'FAILED':
            assert charts.export_job.error
            report['checks']['export_failure_visible'] = charts.export_job.error
            capture('export-failure')
            if os.environ.get('WFRL_ACCEPT_INTERACTIVE_ONLY') == '1':
                transition('complete'); finish(); return None
            assert bpy.ops.wfrl.connection_mode(mode='formal_training') == {'FINISHED'}
            assert bpy.ops.wfrl.bridge_connect() == {'FINISHED'}
            transition('formal_connect')
        elif stage == 'formal_connect' and runtime.allows_command('start'):
            settings = bpy.context.scene.wfrl_workflow
            settings.iters, settings.n_steps, settings.warmup_steps = 2, 4, 0
            control('start'); transition('formal_running')
        elif stage == 'formal_running':
            assert not runtime.allows_command('pause') and not runtime.allows_command('step')
            metric = runtime.training_dashboard.metric('mean_power')
            if metric['validity'] == 'valid' and 'formal_statistics' not in report['checks']:
                assert ui.run_id and ui.run_id != run_id
                report['checks']['formal_statistics'] = dict(run_id=ui.run_id,phase=runtime.training_dashboard.phase(),
                    metrics={k:runtime.training_dashboard.metric(k) for k in ('mean_power','mean_reward','value_loss','explained_variance','episode_return')})
                capture('formal-dashboard')
            if ui.run_status == 'STOPPED':
                assert 'formal_statistics' in report['checks']
                report['checks']['formal_natural_stop'] = True
                transition('formal_stale')
        elif stage == 'formal_stale' and now-stage_start > 32:
            assert runtime.training_dashboard.metric('mean_power')['validity'] == 'stale'
            report['checks']['formal_stale'] = True
            capture('formal-stale')
            assert bpy.ops.wfrl.export_history(filepath=str(OUT/'formal-history.json')) == {'FINISHED'}
            transition('formal_export')
        elif stage == 'formal_export' and charts.export_job.poll() == 'COMPLETE':
            report['checks']['formal_export'] = True
            transition('complete'); finish(); return None
        if now-started > 540:
            raise TimeoutError('Acceptance deadline: ' + stage)
    except Exception:
        report['errors'].append(traceback.format_exc())
        print(report['errors'][-1], flush=True)
        finish(); return None
    return .1

bpy.app.timers.register(tick, first_interval=.5)
