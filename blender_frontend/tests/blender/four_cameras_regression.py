"""Native four-slot, migration, mount and atomic layout import regression."""
import sys
from pathlib import Path
from dataclasses import replace
import math
import copy
import bpy
from mathutils import Vector
sys.path[:0]=[str(Path(__file__).resolve().parents[3]/'blender_frontend')]
import wfrl_blender as addon
from wfrl_blender import custom_cameras as c, camera_projection as p
addon.register()
scene=bpy.context.scene
for obj in list(scene.objects):bpy.data.objects.remove(obj,do_unlink=True)
root=bpy.data.objects.new(c.ROOT_NAME,None);scene.collection.objects.link(root)
root.location=(2,4,87.6);root.rotation_euler=(.1,.2,.3)
bpy.ops.mesh.primitive_cube_add(size=2)
shell=bpy.context.object;shell.name=c.SURFACE_NAMES[0];shell.parent=root
bpy.context.view_layer.update()
settings=(scene.camera,scene.render.resolution_x,scene.render.resolution_y,scene.render.pixel_aspect_x,scene.render.pixel_aspect_y)
for slot,(h,v) in zip(c.SLOTS,[(90,60),(40,110),(75,75),(100,50)]):
    draft=c.begin_draft(scene,slot)
    params=c.CameraParameters((0,0,1.1),123, -45,37,h,v)
    c.apply_parameters(draft,params,c.validate_position(scene,params.location)[0])
    cam=c.commit_draft(scene,slot,draft)
    assert cam.parent==root
    original=c.parameters(cam)
    c.apply_parameters(cam,replace(original,focal_length_mm_record=200.))
    assert cam.data.lens==36/(2*math.tan(math.radians(h/2))) or abs(cam.data.lens-36/(2*math.tan(math.radians(h/2))))<1e-5
assert len({cam.data.as_pointer() for cam in c.enabled_cameras(scene)})==4
# v1 legacy camera inherits actual aspect once, then freezes its VFOV.
old=c.get_camera(scene,1)
del old['custom_schema_version'];del old['custom_vfov']
scene.render.resolution_x=1300;scene.render.resolution_y=900;scene.render.pixel_aspect_x=1.2
pose=old.matrix_basis.copy()
c.migrate_camera(scene,old)
v=c.parameters(old).vfov
assert abs(v-math.degrees(2*math.atan(math.tan(math.radians(45))/(1300*1.2/900))))<1e-5
scene.render.resolution_x=1920;c.migrate_camera(scene,old)
assert c.parameters(old).vfov==v and old.matrix_basis==pose
# Explicitly marked roof is allowed; unmarked roof is not.
bpy.ops.mesh.primitive_cube_add(size=1)
roof=bpy.context.object;roof.parent=root;roof.name='ExplicitRoof';roof.location=(0,0,3)
bpy.context.view_layer.update()
origin=root.matrix_world @ Vector((0,0,10));direction=root.matrix_world.to_3x3() @ Vector((0,0,-1))
assert c.raycast_surface(scene,origin,direction) is None
roof['wfrl_camera_mount_surface']=True
assert c.raycast_surface(scene,origin,direction).surface_name==roof.name
# An explicitly marked open shell cannot be certified as outside by parity.
import bmesh
bm=bmesh.new();bm.from_mesh(roof.data);bm.faces.ensure_lookup_table()
bmesh.ops.delete(bm,geom=[bm.faces[0]],context='FACES');bm.to_mesh(roof.data);bm.free()
bpy.context.view_layer.update()
assert c.validate_research_position(scene,(7,0,0),True)['status']=='unverified_open_surface'
# Research offset requires explicit acknowledgement; inside remains invalid.
for xyz,confirm in [((0,0,0),True),((7,0,0),False)]:
    try:c.validate_research_position(scene,xyz,confirm)
    except ValueError:pass
    else:raise AssertionError('bad research position accepted')
draft=c.begin_draft(scene,4)
c.apply_research(scene,draft,replace(c.parameters(draft),location=(7,0,0)),True)
c.commit_draft(scene,4,draft)
assert c.anchor(c.get_camera(scene,4)) is None
layout=c.layout_dict(scene);fingerprint=c.layout_hash(layout)
# Bad final record must leave every original intact.
bad=copy.deepcopy(layout);bad['cameras'][-1]['parameters']['vfov']=float('nan')
try:c.import_layout(scene,bad,overwrite=True)
except ValueError:pass
else:raise AssertionError('bad layout accepted')
assert c.layout_hash(c.layout_dict(scene))==fingerprint
for key,value in [('model_signature','foreign-model'),('coordinate_frame','foreign-frame'),('schema_version',99)]:
    conflict=copy.deepcopy(layout);conflict[key]=value
    try:c.import_layout(scene,conflict,overwrite=True)
    except ValueError:pass
    else:raise AssertionError('conflicting layout accepted')
    assert c.layout_hash(c.layout_dict(scene))==fingerprint
try:c.import_layout(scene,layout)
except ValueError:pass
else:raise AssertionError('replacement without confirmation')
c.import_layout(scene,layout,overwrite=True)
assert c.layout_hash(c.layout_dict(scene))==fingerprint
# Camera inherits arbitrary support pose once; rotor is irrelevant.
cam=c.get_camera(scene,2);local=cam.matrix_basis.copy();root.location.z+=4
root.rotation_euler=(.3,-.2,.4);bpy.context.view_layer.update()
expected=root.matrix_world @ local
assert max(abs(cam.matrix_world[i][j]-expected[i][j]) for i in range(4) for j in range(4))<1e-5
# Non-rigid reference is rejected.
root.scale=(1,2,1);bpy.context.view_layer.update()
try:c.begin_draft(scene,2)
except ValueError:pass
else:raise AssertionError('scaled root accepted')
root.scale=(1,1,1);bpy.context.view_layer.update()
addon.unregister()
print('FOUR_CAMERAS_NATIVE_PASS',bpy.app.version_string)
