"""Sampled front-surface visibility with real scene occlusion; never an algorithm score."""
import math
import numpy as np
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from .scene import pose


def tree_for(objects):
    verts, faces=[],[]
    deps=bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        if obj.type!='MESH' or obj.hide_render:
            continue
        evaluated=obj.evaluated_get(deps)
        mesh=evaluated.to_mesh()
        offset=len(verts)
        verts.extend(evaluated.matrix_world @ v.co for v in mesh.vertices)
        mesh.calc_loop_triangles()
        faces.extend(tuple(offset+i for i in tri.vertices) for tri in mesh.loop_triangles)
        evaluated.to_mesh_clear()
    return BVHTree.FromPolygons(verts,faces,all_triangles=True)


def intervals(spans, flags):
    """Ranges of sampled station centres, not interpolated guaranteed intervals."""
    result=[]
    start=None
    for i,value in enumerate(list(flags)+[False]):
        if value and start is None:
            start=i
        elif not value and start is not None:
            result.append([round(float(spans[start]),4),round(float(spans[i-1]),4)])
            start=None
    return result


def audit_pose(rig, angle, pitch, include_points=False):
    pose(rig,angle,pitch)
    blade=rig['blades'][0]
    n=rig['geometry']['ring_points']
    # Interior triangle barycentres avoid numerically unstable rays at shared edges.
    blade.data.calc_loop_triangles()
    chosen=list(blade.data.loop_triangles)[::4]
    local=[sum((blade.data.vertices[i].co for i in tri.vertices),Vector())/3 for tri in chosen]
    points=[blade.matrix_world @ p for p in local]
    # Test the actual triangle plane, not the parent n-gon's averaged normal.
    normals=[]
    for tri in chosen:
        a,b,c=(blade.data.vertices[i].co for i in tri.vertices)
        normals.append((b-a).cross(c-a).normalized())
    spans=np.array([round(p.z,3) for p in local])
    target=BVHTree.FromPolygons([v.co for v in blade.data.vertices],
        [tuple(t.vertices) for t in blade.data.loop_triangles],all_triangles=True)
    blockers=tree_for([o for o in rig['scene'].objects if o != blade])
    hfov,vfov=[2*math.atan(s/(2*rig['layout']['lens_mm'])) for s in rig['layout']['sensor_mm']]
    records=[]
    for camera in rig['cameras']:
        origin=camera.matrix_world.translation
        local_origin=blade.matrix_world.inverted() @ origin
        inverse=camera.matrix_world.inverted()
        surface,framed,clear,uv,obstructed=[],[],[],[],[]
        for point,local_point,normal in zip(points,local,normals):
            delta=point-origin
            distance=delta.length
            direction=delta.normalized()
            local_delta=local_point-local_origin
            local_direction=local_delta.normalized()
            _,_,_,near=target.ray_cast(local_origin,local_direction,local_delta.length+.002)
            # Interior samples need only 1 mm float tolerance. The outward normal
            # rejects thin back surfaces that a loose distance test could accept.
            facing=normal.dot(-local_direction)>1e-7 and near is not None and abs(near-local_delta.length)<.001
            view=inverse @ point
            depth=-view.z
            u=view.x/(max(depth,1e-9)*math.tan(hfov/2))
            v=view.y/(max(depth,1e-9)*math.tan(vfov/2))
            inside=depth>.01 and abs(u)<=1 and abs(v)<=1
            blocked=False
            if facing:
                _,_,_,hit=blockers.ray_cast(origin,direction,max(.001,distance-.001))
                blocked=hit is not None
            surface.append(facing)
            framed.append(inside)
            clear.append(facing and not blocked)
            obstructed.append(facing and blocked)
            uv.append((u,v))
        surface,framed,clear,obstructed=map(np.array,(surface,framed,clear,obstructed))
        records.append(dict(surface=surface,framed=framed,clear=clear,
                            visible=clear & framed,obstructed=obstructed,uv=np.array(uv)))
    required=np.logical_or.reduce([r['surface'] for r in records])
    covered=np.logical_or.reduce([r['visible'] for r in records])
    inframe=np.logical_or.reduce([r['surface'] & r['framed'] for r in records])
    geometrically_framed=np.logical_or.reduce([r['framed'] for r in records])
    distinct=np.unique(spans)
    def flags(mask,all_required=False):
        if all_required:
            return [bool(required[spans==s].any() and np.all(mask[spans==s][required[spans==s]])) for s in distinct]
        return [bool(mask[spans==s].any()) for s in distinct]
    missing=required & ~covered
    denominator=int(required.sum())
    rows=[]
    for i,r in enumerate(records):
        # Projected station bounding box at 85/89 m, native output pixels.
        pixels={}
        for goal in (85,89):
            station=min(rig['stations'],key=lambda s:abs(s['span_m']-goal))
            nearest=station['span_m']
            ring=[blade.matrix_world @ v.co for v in blade.data.vertices if abs(v.co.z-nearest)<1e-4]
            inverse=rig['cameras'][i].matrix_world.inverted()
            view=[inverse @ p for p in ring]
            uv=np.array([(p.x/(-p.z*math.tan(hfov/2)),p.y/(-p.z*math.tan(vfov/2))) for p in view])
            bounds=(uv.max(axis=0)-uv.min(axis=0))*.5*np.array([3840,2160])
            pixels[str(goal)]=dict(actual_span_m=float(nearest),bbox_width_px=float(bounds[0]),bbox_height_px=float(bounds[1]),
                                   fully_in_frame=bool((np.abs(uv)<=1).all() and all(p.z<0 for p in view)))
        rows.append(dict(name=rig['cameras'][i].name,
            any_visible_span_samples_m=intervals(distinct,flags(r['visible'])),
            tip_region_projected_bbox_4k=pixels))
    overlaps=[]
    for a,b in ((0,1),(1,2)):
        common=records[a]['visible'] & records[b]['visible']
        overlaps.append(dict(pair=f'C{a+1}-C{b+1}',sample_count=int(common.sum()),
                             span_samples_m=intervals(distinct,flags(common))))
    # Conservative framing check includes every contour vertex, not just face samples.
    all_points=np.array([tuple(blade.matrix_world @ v.co) for v in blade.data.vertices])
    contour_inside=np.zeros(len(all_points),dtype=bool)
    for camera in rig['cameras']:
        inv=np.array(camera.matrix_world.inverted())
        v=all_points @ inv[:3,:3].T + inv[:3,3]
        depth=-v[:,2]
        contour_inside |= (depth>.01) & (np.abs(v[:,0])<=depth*math.tan(hfov/2)) & (np.abs(v[:,1])<=depth*math.tan(vfov/2))
    contour_missing=int((~contour_inside).sum())
    result=dict(azimuth_deg=angle,pitch_deg=pitch,blade='GW184.Blade1',
        required_front_surface_samples=denominator,covered_samples=int(covered.sum()),
        coverage_fraction=float(covered.sum()/denominator) if denominator else None,
        missing_samples=int(missing.sum()),outside_all_frames_samples=int((required & ~geometrically_framed).sum()),
        in_frame_self_hidden_samples=int((required & geometrically_framed & ~inframe).sum()),
        in_frame_but_occluded_samples=int((missing & inframe).sum()),
        fully_covered=bool(denominator and not missing.any() and not contour_missing),
        contour_vertices_outside_all_frames=contour_missing,
        missing_span_samples_m=intervals(distinct,flags(missing)),
        all_required_samples_covered_span_m=intervals(distinct,flags(covered,True)),
        cameras=rows,overlaps=overlaps)
    if include_points:
        result['diagnostic_points']=[dict(camera=f'C{i+1}',
            points=[dict(u=float(r['uv'][j,0]),v=float(r['uv'][j,1]),span_m=float(spans[j]),
                         status=('overlap' if sum(int(x['visible'][j]) for x in records)>1 else
                                 'visible' if r['visible'][j] else 'occluded'))
                    for j in range(len(points)) if r['surface'][j] and r['framed'][j]])
            for i,r in enumerate(records)]
    return result


def sweep(rig, angles=range(-20,21), pitches=(0,45,90)):
    results=[]
    for pitch in pitches:
        for angle in angles:
            result=audit_pose(rig,angle,pitch)
            results.append(result)
        print(f'GW184 coverage pitch={pitch}: {sum(r["fully_covered"] for r in results if r["pitch_deg"]==pitch)} complete samples',flush=True)
    windows=[]
    framing_windows=[]
    for pitch in pitches:
        rows=[r for r in results if r['pitch_deg']==pitch]
        ranges=intervals([r['azimuth_deg'] for r in rows],[r['fully_covered'] for r in rows])
        windows.append(dict(pitch_deg=pitch,angle_samples_deg=ranges,
                            nominal_8rpm_duration_s=[(b-a)/48 for a,b in ranges]))
        framing_windows.append(dict(pitch_deg=pitch,
            angle_samples_deg=intervals([r['azimuth_deg'] for r in rows],
                [r['contour_vertices_outside_all_frames']==0 for r in rows])))
    return dict(schema='gw184.coverage.v1',
        method='All contour vertices for framing plus every fourth triangulated face barycentre; actual triangle outward-normal filter, blade-local target BVH self-visibility and world all-other-mesh BVH occlusion; tolerance 0.001m',
        limits='Sampled front-visible surface, not all hidden/back surfaces; not a continuous proof, mechanical validation, or stitching success. Interval endpoints are tested samples. Pixel boxes are geometric extents, not usable feature counts.',
        angle_step_deg=1,windows=windows,contour_framing_windows=framing_windows,poses=results)
