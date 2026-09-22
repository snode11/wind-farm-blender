import bpy
from wfrl_blender import tip_tracking
scene=bpy.context.scene
trail=tip_tracking.active(scene)
trail.lifetime_s=1.5
scene['wfrl_tip_trail_lifetime_s']=1.5
trail.clear('window_tuning')
for f in range(1,181):scene.frame_set(f)
scene.wfrl_deflection_visible=False
