"""Loaded geometry, including legacy/custom heights, is the wake anchor."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy,wfrl_blender
from wfrl_blender import cinematic
wfrl_blender.register()
s=bpy.context.scene;c=bpy.data.collections.new('SavedRig');s.collection.children.link(c)
yaw=bpy.data.objects.new('WFRL.Turbine.T1.YawRoot',None);c.objects.link(yaw);yaw.location.z=87.6
rotor=bpy.data.objects.new('WFRL.Turbine.T1.Rotor',None);c.objects.link(rotor);rotor.parent=yaw
s.wfrl_wake_display='CINEMATIC'
for relative_height in (.4374433884,2.4000033884,5.0):
 rotor.location.z=relative_height;bpy.context.view_layer.update()
 cinematic.update(s,0)
 actual=s.objects['WFRL.Cinematic.T1'].location.z
 assert abs(actual-rotor.matrix_world.translation.z)<1e-4,(actual,rotor.matrix_world.translation.z)
print('SAVED_HEIGHT_REGRESSION=PASS')
