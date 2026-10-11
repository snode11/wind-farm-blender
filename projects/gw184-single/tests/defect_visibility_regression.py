"""Blender background visibility regression. It never renders images."""
from pathlib import Path
import json
import math
import sys

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parents[1]
sys.path[:0] = [str(PROJECT), str(ROOT / 'blender_frontend')]
import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
from gw184.model import read_config
from gw184.scene import build, at_time
from gw184.defect_visibility import analyze, _camera_descriptor, _project
from gw184.defect_timeline import FrameMapping, scan
from gw184.reference_surface import ReferenceSurface
from gw184.defect_schema import new_document, new_defect
from gw184.defect_geometry import DefectGeometry


def mesh_object(scene, name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


def box(scene, name, location, size):
    vertices = [(location[0]+x*size[0]/2, location[1]+y*size[1]/2, location[2]+z*size[2]/2)
                for x,y,z in ((-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1),
                              (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1))]
    return mesh_object(scene, name, vertices, [(3,2,1,0), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)])


def support():
    a,b,c,d=(-.4,-.8,1),(.4,-.8,1),(.4,.8,1),(-.4,.8,1)
    return dict(defect_id='D-block',revision=1,blade_id=1,support_id='D-block:r1:opening',
                support_kind='opening_footprint',primary=True,support_definition={'version':'known-block-v1'},
                triangles_local=[(a,b,c),(a,c,d)],normals_local=[(0,0,1)]*2,
                centreline_local=[(0,-.8,1),(0,.8,1)],width_sections_local=[((-.4,0,1),(.4,0,1))])


def block_regression():
    _, motion, _ = read_config()
    scene=bpy.data.scenes.new('Defect.KnownGroove')
    bpy.context.window.scene=scene
    scene.render.resolution_x=200;scene.render.resolution_y=200;scene.render.resolution_percentage=100
    rotor=bpy.data.objects.new('DummyRotor',None);scene.collection.objects.link(rotor)
    # The floor and four rim boxes make a true recess with a healthy opening at z=1.
    floor=box(scene,'Groove.Floor',(0,0,.35),(2,2,.7))
    box(scene,'Groove.Left',(-.7,0,.85),(.6,2,.3))
    box(scene,'Groove.Right',(.7,0,.85),(.6,2,.3))
    box(scene,'Groove.Top',(0,.9,.85),(.8,.2,.3))
    box(scene,'Groove.Bottom',(0,-.9,.85),(.8,.2,.3))
    camera_data=bpy.data.cameras.new('C-block');camera_data.lens=35;camera_data.sensor_width=36
    camera=bpy.data.objects.new('C-block',camera_data);scene.collection.objects.link(camera);camera.location=(0,0,5)
    rig=dict(scene=scene,rotor=rotor,pitch_roots=[],blades=[floor],motion=motion,cameras=[camera])
    bpy.context.view_layer.update()
    rows=analyze(rig,[support()],100,sampling_step_px=2)
    assert rows[0]['status']=='VISIBLE', rows
    assert rows[0]['visible_fraction_in_frame']>.999999
    obstruction=box(scene,'Occluder',(-.3,0,2),(.6,3,.1))
    bpy.context.view_layer.update()
    half=analyze(rig,[support()],100,sampling_step_px=1)[0]
    assert half['status']=='PARTIAL', half
    assert .4<half['visible_fraction_in_frame']<.6, half
    obstruction['gw184_defect_role']='editor_marker'
    assert analyze(rig,[support()],100,sampling_step_px=2)[0]['status']=='VISIBLE'
    camera.location=(0,0,-5);camera.rotation_euler.x=math.pi
    bpy.context.view_layer.update()
    back=analyze(rig,[support()],100,sampling_step_px=2)[0]
    assert back['status']=='OCCLUDED' and 'BACK_FACING_SUPPORT' in back['reasons'], back
    return dict(front=rows[0]['status'],partial=half['visible_fraction_in_frame'],back=back['status'])


def gw184_regression():
    g,m,c=read_config()
    rig=build(g,m,c,width=960,apply_legacy_defects=False)
    at_time(rig,101.125)
    scene=rig['scene']
    # Independent Blender normalized-camera projection against actual camera data.
    worst=0.
    for camera in rig['cameras']:
        descriptor=_camera_descriptor(camera,scene)
        for local in ((0,0,-10),(.1,-.1,-20),(-2,1,-100)):
            point=camera.matrix_world @ Vector(local)
            ours,_=_project(tuple(point),descriptor)
            reference=world_to_camera_view(scene,camera,point)
            expected=(reference.x*descriptor['width']-.5,(1-reference.y)*descriptor['height']-.5)
            worst=max(worst,math.dist(ours,expected))
    assert worst<.05,worst
    surface=ReferenceSurface(g)
    backend=DefectGeometry(rig,surface)
    document=new_document(surface)
    document['defects']=[new_defect(surface,'fine_crack',id='VIS-FINE'),
                         new_defect(surface,'open_crack',id='VIS-OPEN')]
    payload=backend.prepare(document)
    backend.activate(payload)
    records=analyze(rig,payload['supports'],100,run_id='native-regression',state_version=1)
    assert len(records)==len(payload['supports'])*3
    assert all(r['status']!='INVALID' for r in records), records
    primary=[r for r in records if r['primary']]
    assert any(r['status'] in ('VISIBLE','PARTIAL') for r in primary), primary
    assert all(r['appearance_review']['status']=='NOT_REVIEWED' for r in records)
    # Scan uses actual timeline (including resume/cancel), and restores actual pose.
    matrix=rig['rotor'].matrix_basis.copy()
    pitch=[p.matrix_basis.copy() for p in rig['pitch_roots']]
    mapping=FrameMapping(100,.1,30,30)
    result=scan(rig,payload['supports'],mapping)
    assert result['completed'] and result['completed_frames']==3
    assert result['frame_mapping']['first_sample']['blender_frame']==3001
    assert max(abs(a-b) for row1,row2 in zip(matrix,rig['rotor'].matrix_basis) for a,b in zip(row1,row2))<1e-6
    for before,obj in zip(pitch,rig['pitch_roots']):
        assert max(abs(a-b) for row1,row2 in zip(before,obj.matrix_basis) for a,b in zip(row1,row2))<1e-6
    cancelled=scan(rig,[],mapping,cancel=lambda:True)
    assert cancelled['cancelled'] and not cancelled['completed'] and cancelled['completed_frames']==0
    return dict(independent_projection_max_error_px=worst,scan_frames=result['completed_frames'],
                support_count=len(payload['supports']),primary_statuses=[r['status'] for r in primary],
                cancel_restored=True)


result=dict(blender=bpy.app.version_string,known_groove=block_regression(),gw184=gw184_regression(),rendered=False)
print('DEFECT_VISIBILITY_PASS '+json.dumps(result),flush=True)
