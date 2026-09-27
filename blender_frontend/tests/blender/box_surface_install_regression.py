"""Actual nacelle: support contact and standoff, rigid optics, cancel/undo and layout I/O."""
from pathlib import Path
import copy
import importlib
import json
import os
import sys
import bpy
from mathutils import Vector, Matrix

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')),str(ROOT)]
MODULE=os.environ.get('WFRL_ADDON_MODULE','wfrl_blender')
addon=importlib.import_module(MODULE)
rig=importlib.import_module(MODULE+'.stacked_camera_rig')
core=importlib.import_module(MODULE+'.custom_cameras')
projection=importlib.import_module(MODULE+'.camera_projection')
history=importlib.import_module(MODULE+'.custom_camera_history')
panel=importlib.import_module(MODULE+'.panels.stacked_camera_rig')
addon.register();addon.load_demo_scene()
scene=bpy.context.scene
baseline=core.layout_dict(scene)
baseline_hash=core.live_layout_hash(scene)
box=scene.objects[rig.PREFIX]
old_rotation=rig.pose_rotation(baseline['rig_pose'])
geometry=core._geometry(scene)[0]
tree=geometry[-1]
cases=[]
for origin,direction in [((0,0,10),(0,0,-1)),((1,-10,1.5),(0,1,0)),((0,-10,2.3),(0,1,0))]:
    point,normal,index,distance=tree.ray_cast(Vector(origin),Vector(direction))
    assert point is not None,(origin,direction)
    anchor=core.SurfaceAnchor(tuple(point),tuple(normal),0.,geometry[0])
    transaction=panel.Placement(scene)
    for spin in [0.,37.,90.,-45.]:
        transaction.preview(anchor,spin)
        bpy.context.view_layer.update()
        pose=rig.pose(scene)
        rotation=rig.pose_rotation(pose)
        delta=rotation @ old_rotation.transposed()
        # The support end contacts the surface; housing stays 27.4 cm outside.
        back=scene.objects[rig.PREFIX+'.MountArm']
        transform=scene.objects[core.ROOT_NAME].matrix_world.inverted() @ back.matrix_world
        distances=[((transform@v.co)-point).dot(normal) for v in back.data.vertices]
        assert abs(min(distances))<3e-5,distances
        assert abs(max(distances)-.28)<3e-5
        housing=scene.objects[rig.PREFIX+'.Back']
        transform=scene.objects[core.ROOT_NAME].matrix_world.inverted() @ housing.matrix_world
        gap=min(((transform@v.co)-point).dot(normal) for v in housing.data.vertices)
        assert abs(gap-.274)<3e-5,gap
        assert not scene.objects[rig.PREFIX+'.MountArm'].hide_render
        for record in baseline['cameras']:
            slot=record['slot_id'];p=record['parameters']
            cam=core.get_camera(scene,slot)
            actual=core.parameters(cam)
            expected_rotation=delta @ Matrix(projection.rotation(p['yaw'],p['pitch'],p['roll']))
            assert max(abs(expected_rotation[i][j]-cam.matrix_basis[i][j]) for i in range(3) for j in range(3))<3e-5
            assert (actual.fov,actual.vfov,actual.output_long_edge_px)==(p['fov'],p['vfov'],p['output_long_edge_px'])
        cams=[core.get_camera(scene,s) for s in core.SLOTS]
        assert abs((cams[0].location-cams[2].location).length-.16)<2e-5
        # Serialize actual geometry and transforms, including the surface contact.
        layout=json.loads(json.dumps(core.layout_dict(scene)))
        core.validate_layout(scene,layout)
    transaction.finish()
    assert core.live_layout_hash(scene)==baseline_hash,'cancel changed layout/history'
    assert not scene.objects[rig.PREFIX+'.MountArm'].hide_render
    cases.append({'point':list(point),'normal':list(normal),'spins':[0,37,90,-45]})

# One confirmation produces exactly one undo step, despite multiple previews.
transaction=panel.Placement(scene)
transaction.preview(anchor,15.)
transaction.preview(anchor,30.)
count=len(history.HISTORY.entries)
transaction.finish(confirm=True)
assert len(history.HISTORY.entries)==count+1
saved=core.layout_dict(scene)
saved_hash=core.live_layout_hash(scene)
history.undo(scene)
assert core.live_layout_hash(scene)==baseline_hash
core.import_layout(scene,json.loads(json.dumps(saved)),overwrite=True)
assert core.live_layout_hash(scene)==saved_hash
# Per-camera aim changes do not move the other centres, including tilted rigs.
from dataclasses import replace
others={s:core.parameters(core.get_camera(scene,s)) for s in (1,3)}
draft=core.begin_draft(scene,2)
core.apply_research(scene,draft,replace(core.parameters(draft),pitch=-15.),confirmed=True)
core.commit_draft(scene,2,draft)
assert all(core.parameters(core.get_camera(scene,s))==p for s,p in others.items())
history.undo(scene)
# Failed candidate never mutates the preview.
transaction=panel.Placement(scene)
before=core.live_layout_hash(scene)
bad=core.SurfaceAnchor((0,0,0),(0,0,1),0.,geometry[0])
try:transaction.preview(bad,0)
except ValueError:pass
else:raise AssertionError('invalid interior installation accepted')
assert core.live_layout_hash(scene)==before
transaction.finish()
# A forged mount cannot be imported by keeping camera coordinates valid.
bad=copy.deepcopy(saved);bad['rig_pose']['surface_mount']['point'][0]+=1
try:core.validate_layout(scene,bad)
except ValueError:pass
else:raise AssertionError('inconsistent contact accepted')
# Previously exported back-mounted layouts remain readable without moving them.
back_pose=rig.surface_pose(scene,anchor,0.,'BACK')
back_layout=rig.transformed_layout(scene,baseline,back_pose)
del back_layout['rig_pose']['surface_mount']['type']
core.restore_layout(scene,back_layout)
assert scene.objects[rig.PREFIX+'.MountArm'].hide_render
# Legacy yaw-only layouts continue to restore, including visible support arm.
core.restore_layout(scene,baseline)
assert core.live_layout_hash(scene)==baseline_hash
out=Path(os.environ.get('WFRL_TEST_OUTPUT','/tmp/wfrl-box-surface-test'))
out.mkdir(parents=True,exist_ok=True)
(out/'validation.json').write_text(json.dumps({'status':'PASS','module':MODULE,'blender':bpy.app.version_string,
    'cases':cases,'checks':['support contact and 0.274 m housing clearance','rigid camera transform','cancel','single undo','JSON round trip',
    'independent camera aim','invalid placement unchanged','legacy layout']},indent=2))
print('BOX_SURFACE_INSTALL_PASS',flush=True)
