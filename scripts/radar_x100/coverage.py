"""Offline two-installation finite-target coverage on the complete declared 30 Hz grid."""
from pathlib import Path
import argparse
import importlib
import json
import sys
import uuid
import numpy as np


def runs(mask):
    groups=[]
    for value in mask:
        if value is None:
            groups.append([None,0])
            continue
        if value:
            if not groups or groups[-1][0] is False: groups.append([True,0])
        else:
            if not groups or groups[-1][0] is True: groups.append([False,0])
        groups[-1][1]+=1
    return dict(longest_visible_frames=max([n for v,n in groups if v]+[0]),
                longest_gap_frames=max([n for v,n in groups if v is False]+[0]))


def passages(times, angles, half_window):
    """Split stopped/reversing source intervals; preserve boundary-truncated encounters."""
    delta=np.diff(angles)
    direction=np.sign(delta)
    changes=np.flatnonzero(np.diff(direction)!=0)+1
    boundaries=[0,*changes,len(times)-1]
    result=[]
    for a,b in zip(boundaries,boundaries[1:]):
        if b<=a: continue
        lo,hi=sorted([angles[a],angles[b]])
        if lo==hi:
            result.append(dict(status='STOPPED',start_s=float(times[a]),end_s=float(times[b]),indices=[]))
            continue
        first=int(np.ceil((lo-half_window-180)/360))
        last=int(np.floor((hi+half_window-180)/360))
        for turn in range(first,last+1):
            center=180+360*turn
            indices=np.flatnonzero((np.arange(len(times))>=a)&(np.arange(len(times))<=b)&(abs(angles-center)<=half_window))
            complete=lo<=center-half_window and hi>=center+half_window
            result.append(dict(status='COMPLETE' if complete else 'TRUNCATED',
                direction='FORWARD' if angles[b]>angles[a] else 'REVERSE',
                reversal_boundary=bool(a>0 or b<len(times)-1), center_deg=center,
                source_segment_s=[float(times[a]),float(times[b])],
                start_s=float(times[indices[0]]) if len(indices) else None,
                end_s=float(times[indices[-1]]) if len(indices) else None,indices=indices.tolist()))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--package',type=Path)
    parser.add_argument('--candidates',type=int,default=512)
    parser.add_argument('--seed',type=int,default=100)
    parser.add_argument('--targets-per-group',type=int,default=256)
    parser.add_argument('--half-window-deg',type=float,default=30.)
    parser.add_argument('--distal-span',type=float,nargs=2,default=(.8,.95))
    parser.add_argument('--roi',type=float,nargs=6,default=(-100,-100,0,100,100,150))
    parser.add_argument('--bins',type=int,default=6)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if (args.output.exists() or not 1<=args.candidates<=4096 or not 1<=args.targets_per_group<=4096
        or not 0<args.half_window_deg<180 or not 1<=args.bins<=360
        or not all(0<=x<=1 for x in args.distal_span)
        or not np.isfinite(args.roi).all() or not np.isfinite(args.half_window_deg)
        or np.any(np.array(args.roi[:3])>=args.roi[3:])):
        raise ValueError('Invalid research profile or output already exists')
    root=Path(__file__).resolve().parents[2]
    sys.path[:0]=[str(root/'blender_frontend'),str(root)]
    import bpy
    import wfrl_blender as addon
    from wfrl_blender import farm_flex
    from wfrl_blender.radar_x100.scene import SceneSampler,installation_matrix,nacelle_side_installation,ORIGINAL_LOCATION,ORIGINAL_ROTATION
    from wfrl.radar_x100 import simulation
    from scripts.radar_x100.export_simulation import sample_times
    addon.register()
    addon.load_demo_scene(args.package or farm_flex.default_package())
    sampler=SceneSampler(farm_flex.active_for(bpy.context.scene),mount_from_sensor=np.eye(4))
    side_location,side_rotation=nacelle_side_installation(sampler)
    samplers={name:sampler.with_installation('NACELLE',installation_matrix(location,rotation))
        for name,location,rotation in [('ORIGINAL',ORIGINAL_LOCATION,ORIGINAL_ROTATION),('NACELLE_SIDE',side_location,side_rotation)]}
    times=np.array(list(sample_times(*sampler.interval,30)))
    rng=np.random.default_rng(args.seed)
    catalogs=[]
    actual_targets=[]
    plan = sampler.sampling_plan(args.candidates, args.seed)
    groups=[]
    for part, row in enumerate(sampler.blades):
        rest=row[1]; faces=sampler.parts[part][1]; spans=rest[faces].mean(axis=1)[:,2]
        span=(spans-rest[:,2].min())/(rest[:,2].max()-rest[:,2].min())
        selected=[]; counts=[]
        for threshold in args.distal_span:
            eligible=np.flatnonzero(span>=threshold)
            chosen=rng.choice(eligible,min(len(eligible),args.targets_per_group),replace=False)
            counts.append(len(chosen));selected.extend(chosen.tolist())
        catalogs.append(np.unique(selected)+sampler.triangle_offsets[part])
        begin, end = sampler.triangle_offsets[part:part+2]
        points = np.flatnonzero((plan.triangle_ids >= begin) & (plan.triangle_ids < end))
        material_span = np.einsum('ni,ni->n', rest[faces[plan.triangle_ids[points]-begin],2], plan.barycentric[points])
        material_span = (material_span-rest[:,2].min()) / np.ptp(rest[:,2])
        actual_targets.append(points[material_span >= min(args.distal_span)])
        groups.append(dict(blade=part+1,requested_per_group=args.targets_per_group,actual_per_group=counts,unique=len(catalogs[-1])))
    measurements={name:[] for name in samplers}
    sensor_counts={name:[] for name in samplers}
    roi_lo,roi_hi=np.array(args.roi[:3]),np.array(args.roi[3:])
    for sequence,t in enumerate(times):
        for name,current in samplers.items():
            try:
                frame=simulation.simulate(current.sample,float(t),sensor_id='X100-T1',session_id='coverage',
                    sequence=sequence,interval_s=sampler.interval,candidate_budget=args.candidates,seed=args.seed,
                    visibility=current.visibility,material_at=current.material_sample, sampling_plan=plan)
                sample=current.latest_sample
                sensor_world=simulation.rigid_matrix(frame.spatial['world_from_sensor'])
                world=frame.xyz.astype(float)@sensor_world[:3,:3].T+sensor_world[:3,3]
                base=(world-sampler.root_world[:3,3])@sampler.root_world[:3,:3]
                inside=(base>=roi_lo).all(axis=1)&(base<=roi_hi).all(axis=1)
                output_ids=np.asarray(frame.provenance['material_point_indices'])
                rows=[]
                for catalog, output_catalog in zip(catalogs,actual_targets):
                    target=sample.vertices[sample.triangles[catalog]].mean(axis=1)
                    local=(target-sampler.root_world[:3,3])@sampler.root_world[:3,:3]
                    eligible=(local>=roi_lo).all(axis=1)&(local<=roi_hi).all(axis=1)
                    xyz=(target-sensor_world[:3,3])@sensor_world[:3,:3]
                    gate=eligible&simulation.in_fov(xyz)
                    visible=np.zeros(len(target),bool)
                    visible[gate]=current.visibility(sample,target[gate])
                    rows.append(dict(E=int(eligible.sum()),V=int(visible.sum()),
                        O=int(np.sum(inside&np.isin(output_ids,output_catalog)))))
                measurements[name].append(rows)
                sensor_counts[name].append(dict(output_points=len(frame.points),
                    candidates_in_fov=frame.provenance['candidates_in_fov'],occluded=frame.provenance['occluded']))
            except Exception as exc:
                measurements[name].append(dict(UNKNOWN=str(exc)))
                sensor_counts[name].append(None)
    phase=np.interp(times,sampler.flex.times,sampler.flex.poses[:,sampler.k,1])
    report=dict(profile=vars(args)|{'output':str(args.output),'package':str(args.package)},
        source_id=sampler.source_id,geometry_id=sampler.geometry_id,source_grid_hz=30,
        source_frames=len(times),target_groups=groups,total_unique_targets=sum(map(len,catalogs)),
        target_method='finite centroid reference catalog; independent area-sampled observed material coordinates',
        sampling_method=plan.method,sampling_plan_id=plan.identity,
        truth_boundary='evaluation only; no online analysis inputs or target associations',
        tip_identifiability='NOT_EVALUATED',safety_acceptance='NOT_EVALUATED',installations={})
    for name, rows in measurements.items():
        encounters=[]
        for b in range(3):
            for passage in passages(times,phase+120*b,args.half_window_deg):
                indices=passage.pop('indices');N=len(indices)
                known=[i for i in indices if isinstance(rows[i],list)]
                V=[isinstance(rows[i],list) and rows[i][b]['V']>0 for i in indices]
                O=[isinstance(rows[i],list) and rows[i][b]['O']>0 for i in indices]
                E=sum(rows[i][b]['E']>0 for i in known)
                counts=dict(N=N,known=len(known),UNKNOWN=N-len(known),residence=E,
                    visible=sum(V),output=sum(O),output_without_reference_visible=sum(o and not v for o,v in zip(O,V)))
                bins=[]
                edges=np.linspace(-args.half_window_deg,args.half_window_deg,args.bins+1)
                for lo,hi in zip(edges[:-1],edges[1:]):
                    ids=[i for i in indices if lo<=phase[i]+120*b-passage.get('center_deg',0)<hi or
                         (hi==edges[-1] and phase[i]+120*b-passage.get('center_deg',0)==hi)]
                    valid_ids=[i for i in ids if isinstance(rows[i],list)]
                    bins.append(dict(angle_deg=[float(lo),float(hi)],N=len(ids),UNKNOWN=len(ids)-len(valid_ids),
                        residence=sum(rows[i][b]['E']>0 for i in valid_ids),
                        visible=sum(rows[i][b]['V']>0 for i in valid_ids),
                        output=sum(rows[i][b]['O']>0 for i in valid_ids)))
                encounters.append(passage|dict(blade=b+1,counts=counts,
                    grid_times_s=[float(times[i]) for i in indices],
                    nominal_angle_window_deg=[passage.get('center_deg',180)-args.half_window_deg,passage.get('center_deg',180)+args.half_window_deg],
                    visible_fraction=sum(V)/N if N else None,output_fraction=sum(O)/N if N else None,
                    conditional_output_given_reference_visible=sum(o and v for o,v in zip(O,V))/sum(V) if sum(V) else None,
                    conditional_denominator=sum(V),evaluation='UNKNOWN' if N>len(known) else 'EVALUATED',
                    output_runs=runs([value if isinstance(rows[i],list) else None for i,value in zip(indices,O)]),
                    visibility_runs=runs([value if isinstance(rows[i],list) else None for i,value in zip(indices,V)]),angle_bins=bins))
        report['installations'][name]=dict(calibration_id=samplers[name].calibration_id,
            mount_from_sensor=samplers[name].mount_from_sensor.tolist(),passages=encounters,
            frames=[dict(source_time_s=float(t),source_index=i,sensor=sensor_counts[name][i],
                blade_counts=rows[i] if isinstance(rows[i],list) else None,
                total_reference_budget=sum(map(len,catalogs)),
                total_resident_targets=sum(r['E'] for r in rows[i]) if isinstance(rows[i],list) else None,
                UNKNOWN=rows[i].get('UNKNOWN') if isinstance(rows[i],dict) else None) for i,t in enumerate(times)])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f: json.dump(report,f,ensure_ascii=False,allow_nan=False,indent=2)
    print('X100_COVERAGE_PASS',args.output)
    addon.unregister()


if __name__=='__main__': main()
