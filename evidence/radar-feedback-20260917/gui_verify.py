import bpy,json,traceback
from pathlib import Path
from bl_ext.user_default.wfrl_blender import radar_feedback,clearance_replay
OUT=Path('/Users/eason/Desktop/wfcrl/wind farm RL/evidence/radar-feedback-20260917')
scene=bpy.context.scene
area=bpy.context.area
area.type='VIEW_3D'
area.spaces.active.show_region_ui=True
for region in area.regions:
    if region.type=='UI': region.active_panel_category='MAPPO'
checks=[('gray',1,'waiting'),('green',1507,'above_threshold'),('red',2992,'near_threshold'),('held-red-no-pulse',3019,'near_threshold')]
results=[]
def stage():
    try:
        if not checks:
            scene.frame_set(2992)
            (OUT/'gui-checks.json').write_text(json.dumps(results,indent=2))
            return None
        name,frame,expected=checks[0]
        scene.frame_set(frame)
        value=clearance_replay.sample(scene)
        state=radar_feedback.alarm_state(value)
        assert state==expected,(name,state)
        assert radar_feedback.icon_id(state)>0
        if name=='held-red-no-pulse':
            assert not scene.objects['WFRL.Turbine.T1.ClearanceRadar.Beam2']['wfrl_beam_active']
        results.append(dict(name=name,frame=frame,time_s=value['time_s'],state=state,
            activity=[scene.objects[f'WFRL.Turbine.T1.ClearanceRadar.Beam{i}']['wfrl_beam_active'] for i in (1,2,3)]))
        for a in bpy.context.screen.areas:a.tag_redraw()
        bpy.app.timers.register(capture,first_interval=2.)
    except Exception:
        (OUT/'gui-error.txt').write_text(traceback.format_exc())
    return None
def capture():
    name=checks.pop(0)[0]
    bpy.ops.screen.screenshot(filepath=str(OUT/(name+'.png')))
    bpy.app.timers.register(stage,first_interval=.2)
    return None
bpy.app.timers.register(stage,first_interval=1.)
