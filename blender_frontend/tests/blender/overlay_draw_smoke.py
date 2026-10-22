"""Lightweight GPU smoke: no wind farm, physics process or render job."""
import sys, math, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
assert not bpy.app.background, "Run in a native window to exercise GPU drawing"
from wfrl_blender import charts, presentation, runtime
from wfrl_blender.state import FrontendState
from wfrl_blender.animation import KinematicState
from wfrl_blender.overlays import scene_overlay
s=bpy.context.scene
s['wfrl_power_mw']=[1.,2.,3.]
assert 'POWER   6.00 MW' in scene_overlay(s)
out=Path('/tmp/wfrl-overlay-review');out.mkdir(exist_ok=True)
bpy.context.preferences.view.show_splash=False
bpy.context.workspace.name='WFRL Workspace'
bpy.types.Scene.wfrl_selected_turbine=bpy.props.StringProperty(default='ALL')
charts.register()
bpy.context.scene.wfrl_chart_visible=True
bpy.context.scene.wfrl_chart_channel='power'
for step in range(60):
    def record(value):
        return dict(value=value,unit='MW',fidelity='DIRECT',validity='valid',source_age_seconds=0.,stale_after_seconds=120.)
    data=dict(mode='replay',step=step,timestamp=dict(value=step*.1,timebase='simulation_seconds'),
       scene={'backend':'fastfarm','inflow':{'speed':8}},
       turbines=[dict(turbine_id=f'T{i+1}',channels={'power':record(1+i*.3+.2*math.sin(step*.12+i))}) for i in range(3)],farm={})
    charts.record_snapshot(dict(session_id='test',sequence=step+1,payload=data))
runtime._state=FrontendState(mode='replay',connection='CONNECTED',run_status='PAUSED',confirmed=True)
runtime.kinematics=KinematicState();runtime.kinematics.apply_snapshot(data)
presentation.register_overlay()
errors=[]; draws=[]
# Replace only registered drawing callbacks to collect actual GPU errors.
for module, key, callback in [(charts,'_handle',charts.draw_plot),(presentation,'_HANDLE',presentation._draw)]:
    handle=getattr(module,key)
    if handle: bpy.types.SpaceView3D.draw_handler_remove(handle,'WINDOW')
    def wrapped(fn=callback):
        try: fn(); draws.append(fn.__name__)
        except Exception: errors.append(traceback.format_exc())
    setattr(module,key,bpy.types.SpaceView3D.draw_handler_add(wrapped,(),'WINDOW','POST_PIXEL'))
for a in bpy.context.screen.areas:
    if a.type=='VIEW_3D': a.spaces.active.show_region_ui=False;a.spaces.active.overlay.show_text=False;a.tag_redraw()
phase=0
def finish():
    global phase
    if phase==0:
        bpy.ops.screen.screenshot(filepath=str(out/'wide.png'))
        area=next(a for a in bpy.context.screen.areas if a.type=='PROPERTIES')
        area.type='VIEW_3D'
        area.spaces.active.show_region_ui=False
        area.spaces.active.overlay.show_text=False
        area.tag_redraw()
        phase=1
        return 1.
    bpy.ops.screen.screenshot(filepath=str(out/'narrow.png'))
    (out/'result.txt').write_text('draws='+str(set(draws))+'\n'+('\n'.join(errors) if errors else 'GPU_DRAW_PASS'))
    assert not errors, errors
    assert {'_draw', 'draw_plot'} <= set(draws), draws
    bpy.ops.wm.quit_blender()
    return None
bpy.app.timers.register(finish,first_interval=2.)
