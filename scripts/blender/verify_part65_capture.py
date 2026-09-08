import bpy, sys, json, pathlib
sys.path.insert(0, '/Users/eason/Desktop/wfcrl/wind farm RL/blender_frontend')
from wfrl_blender.panels import presentation as p
for cls in p.CLASSES: bpy.utils.register_class(cls)
p.register_properties()
out=pathlib.Path('/Users/eason/Desktop/wfcrl/wind farm RL/evidence/part6.5/capture'); out.mkdir(exist_ok=True)
def start():
    s=bpy.context.scene
    s.wfrl_capture_directory=str(out)
    s.wfrl_capture_fps=1
    s.wfrl_capture_duration=.1
    print('SCREENSHOT',bpy.ops.wfrl.capture_screenshot(),flush=True)
    print('RECORD',bpy.ops.wfrl.capture_recording(),flush=True)
    bpy.app.timers.register(check,first_interval=2)
def check():
    for f in out.glob('recording-*/manifest.json'):
        d=json.loads(f.read_text()); print('CAPTURE_RESULT',d['status'],len(d['frames']),str(f),flush=True)
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(start,first_interval=2)
