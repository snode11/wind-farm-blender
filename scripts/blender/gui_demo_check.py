"""Actual GUI playback check; loaded from the development refresh timer."""
import bpy, time, json
from pathlib import Path
output = Path('/Users/eason/Desktop/wfcrl/wind farm RL/evidence/part2')
bpy.ops.wfrl.demo_reset()
bpy.context.scene.sync_mode = 'FRAME_DROP'
bpy.ops.wfrl.demo_start()
started = time.monotonic()
checks = {'started': True}

def check():
    elapsed = time.monotonic() - started
    scene = bpy.context.scene
    if elapsed > 3 and 'resume_preserved_frame' not in checks:
        bpy.ops.wfrl.demo_pause()
        frame = scene.frame_current
        bpy.ops.wfrl.demo_resume()
        checks['resume_preserved_frame'] = scene.frame_current == frame
    if elapsed > 18 and 'running_capture' not in checks:
        bpy.ops.screen.screenshot(filepath=str(output/'workspace_running.png'))
        checks['running_capture'] = scene.frame_current
    if elapsed > 70:
        checks.update(elapsed_s=round(elapsed,2), frame=scene.frame_current,
                      status=scene.get('wfrl_run_status'), playing=bpy.context.screen.is_animation_playing)
        checks['pass'] = checks['resume_preserved_frame'] and checks['frame']==1651 and checks['status']=='STOPPED' and not checks['playing']
        bpy.ops.screen.screenshot(filepath=str(output/'workspace_stopped.png'))
        (output/'gui_playback_check.json').write_text(json.dumps(checks,indent=2))
        bpy.ops.wfrl.demo_reset()
        bpy.ops.wm.save_as_mainfile(filepath=str(output/'wfrl_part2_static.blend'))
        return None
    return .2
bpy.app.timers.register(check,first_interval=.2)
