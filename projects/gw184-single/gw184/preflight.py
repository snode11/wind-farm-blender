"""Geometry, pose and projection checks; this module never invokes a renderer."""
import math
import shutil
import bpy
import bmesh
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix
from .model import blade_mesh, centreline_at, motion_at
from .scene import pose, bake
from .coverage import tree_for, audit_pose, sweep


def mesh_checks(rig):
    records=[]
    for obj in rig['blades']:
        bm=bmesh.new();bm.from_mesh(obj.data)
        record=dict(object=obj.name,vertices=len(bm.verts),faces=len(bm.faces),
            nonmanifold_edges=sum(not e.is_manifold for e in bm.edges),
            inconsistent_edges=sum(not e.is_contiguous for e in bm.edges),
            minimum_face_area_m2=min(f.calc_area() for f in bm.faces),
            signed_volume_m3=bm.calc_volume(signed=True))
        bm.free()
        assert record['nonmanifold_edges']==0,record
        assert record['inconsistent_edges']==0,record
        assert record['signed_volume_m3']>0,record
        assert record['minimum_face_area_m2']>1e-12,record
        records.append(record)
    assert not any(p.material_index for p in rig['blades'][2].data.polygons)
    return records


def defect_dimensions(rig):
    out=[]
    for record in rig['defects']:
        obj=rig['scene'].objects[record['object']]
        centre=Vector(record['centre_local_m'])
        axes=[Vector(record[k]) for k in ('tangent_local','radial_local','normal_local')]
        interior=[p for p in obj.data.polygons if p.material_index==1]
        indices={v for p in interior for v in p.vertices}
        coords=[[(obj.data.vertices[i].co-centre).dot(axis) for axis in axes] for i in indices]
        bounds=[[min(p[i] for p in coords),max(p[i] for p in coords)] for i in range(3)]
        extents=[b-a for a,b in bounds]
        spec=record['spec']
        if spec['kind']=='crack':
            assert abs(extents[0]-spec['length_m'])<.02,(spec,extents)
            assert abs(extents[1]-spec['max_width_m'])<.012,(spec,extents)
        else:
            assert all(abs(v-spec['diameter_m'])<.012 for v in extents[:2]),(spec,extents)
        assert abs(-bounds[2][0]-spec['depth_m'])<.001,(spec,bounds)
        out.append(dict(**record,interior_tangent_bounds_m=bounds,
            measured_tangent_extents_m=[b-a for a,b in bounds],
            maximum_depth_below_centre_tangent_m=-bounds[2][0],
            limits='Bounds of actual cut interior vertices in local tangent coordinates; curved-surface aperture differs from nominal cutter dimensions.'))
    return out


def pose_checks(rig):
    tip=Vector(centreline_at(rig['geometry']['blade_span_m'],rig['geometry']))
    hub=Vector((0,0,rig['geometry']['hub_height_m']))
    root=Vector((0,0,rig['geometry']['root_radius_m']))
    camera_matrices=[c.matrix_world.copy() for c in rig['cameras']]
    errors=[]
    for angle,pitch in ((0,0),(0,90),(30,0),(37,45),(240,90)):
        pose(rig,angle,pitch)
        for i,blade in enumerate(rig['blades']):
            actual=blade.matrix_world @ tip
            expected=hub+Matrix.Rotation(math.radians(angle+120*i),3,'X') @ (
                root+Matrix.Rotation(math.radians(pitch),3,'Z') @ tip)
            errors.append((actual-expected).length)
            # Blender transforms are float32; allow 0.1 mm over a 92 m radius.
            assert errors[-1]<1e-4,(angle,pitch,i,actual,expected)
            nearest=min((v.co-tip).length for v in blade.data.vertices)
            assert nearest<1e-6,'Actual blade has no prebent tip vertex'
        assert all(c.matrix_world==matrix for c,matrix in zip(rig['cameras'],camera_matrices))
    return dict(result='PASS',tip_local_m=list(tip),
        reference_diameter_m=2*(root.z+tip.z),maximum_transform_error_m=max(errors),
        feathered_tip_rotor_plane_diameter_m=2*math.hypot(root.z+tip.z,tip.x),
        checks='static bend and all three blade pitch/azimuth transforms; actual mesh tip; fixed camera transforms')


def defect_projection(rig, record, angle, pitch):
    pose(rig,angle,pitch)
    obj=rig['scene'].objects[record['object']]
    obj.data.calc_loop_triangles()
    interior=[t for t in obj.data.loop_triangles if obj.data.polygons[t.polygon_index].material_index==1]
    points=[obj.matrix_world @ (sum((obj.data.vertices[i].co for i in t.vertices),Vector())/3) for t in interior]
    indices={i for t in interior for i in t.vertices}
    vertices=[obj.matrix_world @ obj.data.vertices[i].co for i in indices]
    scene_tree=tree_for(rig['scene'].objects)
    rows=[]
    for camera in rig['cameras']:
        projected=[world_to_camera_view(rig['scene'],camera,p) for p in vertices]
        box=[(max(p[i] for p in projected)-min(p[i] for p in projected))*size for i,size in enumerate((3840,2160))]
        visible=0
        for point in points:
            uv=world_to_camera_view(rig['scene'],camera,point)
            if uv.z<=0 or not(0<=uv.x<=1 and 0<=uv.y<=1):continue
            delta=point-camera.location
            _,_,_,distance=scene_tree.ray_cast(camera.location,delta.normalized(),delta.length+.002)
            if distance is not None and abs(distance-delta.length)<.001:
                visible+=1
        rows.append(dict(camera=camera.name,interior_bbox_4k_px=box,
            all_in_frame=all(p.z>0 and 0<=p.x<=1 and 0<=p.y<=1 for p in projected),
            visible_interior_face_samples=visible,total_interior_face_samples=len(points)))
    return dict(id=record['spec']['id'],azimuth_deg=angle,pitch_deg=pitch,cameras=rows)


def projection_checks(rig):
    from .scene import camera_metadata
    metadata=camera_metadata(rig)
    for camera,record in zip(rig['cameras'],metadata['cameras']):
        for point in (Vector((0,0,130)),Vector((1,2,170)),Vector((-3,-2,200))):
            ndc=world_to_camera_view(rig['scene'],camera,point)
            inv=camera.matrix_world.inverted() @ point;K=record['K']
            x=K[0][0]*inv.x/-inv.z+K[0][2];y=K[1][1]*-inv.y/-inv.z+K[1][2]
            assert abs(x-(ndc.x*3840-.5))<.005
            assert abs(y-((1-ndc.y)*2160-.5))<.005
    return 'PASS: independent pinhole projection agrees with Blender within 0.005 px'


def clearance_checks(rig):
    # All azimuths at 10-degree spacing and the full 0..90 pitch range at 15 degrees.
    # Discrete surface intersections, not continuous swept-volume or load clearance.
    fixed=[o for o in rig['scene'].objects if o.name in ('GW184.Nacelle','GW184.Tower') or 'Housing.' in o.name]
    blocker=tree_for(fixed);collisions=[]
    for pitch in range(0,91,15):
        for angle in range(0,360,10):
            pose(rig,angle,pitch)
            for blade in rig['blades']:
                if tree_for([blade]).overlap(blocker):
                    collisions.append(dict(blade=blade.name,azimuth_deg=angle,pitch_deg=pitch))
        print(f'GW184 mesh clearance pitch={pitch} checked',flush=True)
    return dict(poses=252,blade_checks=756,azimuth_step_deg=10,pitch_step_deg=15,
        colliding_poses=collisions,targets=[o.name for o in fixed],
        limits='Sampled mesh intersections only; hub/root-collar attachment intentionally excluded. No dynamic deformation or continuous clearance guarantee.')


def save_and_check_animation(rig, path):
    scene=rig['scene'];bake(rig)
    scene['render_authorized']=False
    scene['preflight_status']='Geometry preparation only; fixed cameras cover root/mid/outer blade; image appearance unrendered'
    scene['prebend_tip_m']=rig['geometry']['prebend_m']
    # A saved solid viewport looking from the nacelle rear toward the rotor.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D':
                space=area.spaces.active;space.shading.type='SOLID'
                space.clip_end=3000
                space.region_3d.view_location=Vector((0,0,112))
                space.region_3d.view_distance=215
                space.region_3d.view_rotation=Vector((-math.cos(math.pi/6),-math.sin(math.pi/6),-.36)).to_track_quat('-Z','Y')
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene=bpy.context.scene;states=[]
    for t in (0,10,32.5,55,77.5,100,160,220,242.5,265,287.5,310,319,320,55):
        scene.frame_set(round(t*30)+1);state=motion_at(t,rig['motion'])
        rotor=scene.objects['GW184.Rotor']
        assert abs(math.degrees(rotor.rotation_euler.x)-state['azimuth_deg'])<.002
        for i in range(1,4):
            pitch=scene.objects[f'GW184.Blade{i}.Pitch']
            assert abs(math.degrees(pitch.rotation_euler.z)-state['pitch_deg'])<.0001
            blade=scene.objects[f'GW184.Blade{i}']
            assert blade.parent==pitch
        states.append(state)
    # No need to save again: on-disk scene remains at parked t=0.
    return dict(result='PASS',reopened_scene=str(path),states=states,
                checks='all three pitch roots, rotor, reverse seeking, 320s endpoint; permanent defects follow blade parents')


def run(rig, output, write_json):
    report=dict(schema='gw184.preflight.v1',rendered=False,
        camera_clarification='RESOLVED: user delegates suitable fixed views of root/mid/outer blade; no 30-degree constraint',
        surface_appearance='UNVERIFIED: no new images/video rendered')
    report['meshes']=mesh_checks(rig)
    assert all(r['signed_volume_m3']<report['meshes'][2]['signed_volume_m3'] for r in report['meshes'][:2])
    report['poses']=pose_checks(rig)
    report['defects']=defect_dimensions(rig)
    write_json(output/'preflight.json',report)
    print('GW184 mesh and defect dimensions checked',flush=True)
    report['projection']=projection_checks(rig)
    report['defect_visibility']=[defect_projection(rig,r,-120*(r['spec']['blade']-1)+offset,pitch)
        for r in rig['defects'] for pitch in (0,45,90) for offset in (-2,0,2)]
    write_json(output/'preflight.json',report)
    report['clearance']=clearance_checks(rig)
    write_json(output/'preflight.json',report)
    coverage=sweep(rig,range(-10,11),pitches=(0,45,90))
    write_json(output/'coverage.json',coverage)
    report['coverage_windows']=coverage['windows']
    report['contour_framing_windows']=coverage['contour_framing_windows']
    report['overhead_coverage']=[{k:r[k] for k in ('pitch_deg','coverage_fraction',
        'required_front_surface_samples','missing_samples','outside_all_frames_samples',
        'in_frame_self_hidden_samples','in_frame_but_occluded_samples',
        'contour_vertices_outside_all_frames','overlaps','cameras')}
        for r in coverage['poses'] if r['azimuth_deg']==0]
    for row in report['overhead_coverage']:
        assert row['contour_vertices_outside_all_frames']==0,row
        assert row['in_frame_but_occluded_samples']==0,row
        assert all(overlap['sample_count']>0 for overlap in row['overlaps']),row
    report['coverage_limits']='Framing and surface visibility are separate. Strict surface windows retain all misses; slight view differences at the three optical centres and grazing-face numerical sensitivity are not erased. No stitching or image-quality acceptance is claimed.'
    report['environment']=dict(blender=bpy.app.version_string,ffmpeg=shutil.which('ffmpeg'),
        ffprobe=shutil.which('ffprobe'),free_disk_bytes=shutil.disk_usage(output).free)
    assert not report['clearance']['colliding_poses'],report['clearance']
    for record in rig['defects']:
        rows=[r for r in report['defect_visibility'] if r['id']==record['spec']['id'] and r['pitch_deg']==0]
        assert any(c['visible_interior_face_samples'] and c['all_in_frame'] for r in rows for c in r['cameras']),record
    # Verify the occluder test on this new mesh, not a historical .blend.
    pose(rig,0,0)
    bpy.ops.mesh.primitive_cube_add(size=1,location=(5,0,145))
    blocker=bpy.context.object;blocker.scale=(1,40,100)
    bpy.context.view_layer.update();blocked=audit_pose(rig,0,0)
    assert blocked['in_frame_but_occluded_samples']>100 and not blocked['fully_covered']
    bpy.data.objects.remove(blocker,do_unlink=True)
    report['deliberate_occluder']='PASS'
    report['animation']=save_and_check_animation(rig,output/'GW184-single.blend')
    report['status']='PRE_RENDER_GEOMETRY_AND_ANIMATION_CHECKED_NO_RENDER'
    write_json(output/'preflight.json',report)
    return report
