"""Offline geometric coverage and simulated detection support on the complete 30 Hz grid."""
from pathlib import Path
import argparse
from contextlib import nullcontext
from dataclasses import asdict
import json
import sys
import numpy as np


def add_detection_arguments(parser):
    parser.add_argument('--integration-budget', type=int, default=16384)
    parser.add_argument('--output-point-limit', type=int, default=4096)
    parser.add_argument('--noise-seeds', type=int, nargs='+', default=[100])
    parser.add_argument('--detection-min-points', type=int, default=1)
    parser.add_argument('--detection-config', type=Path, help='JSON DetectionConfig research parameters')


def detection_configuration(args):
    from wfrl.radar_x100.detection import DetectionConfig
    from wfrl.radar_x100.sampling import MAX_INTEGRATION_SAMPLES
    if (not 1 <= args.integration_budget <= MAX_INTEGRATION_SAMPLES
            or not 1 <= args.output_point_limit <= 4096 or args.detection_min_points < 1
            or not args.noise_seeds or len(args.noise_seeds) > 32 or any(s < 0 for s in args.noise_seeds)):
        raise ValueError('Invalid detection evaluation profile')
    values = json.loads(args.detection_config.read_text(encoding='utf-8')) if args.detection_config else {}
    return DetectionConfig(**values)


def evaluate_detection_grid(sampler, times, *, roi, integration_budget=16384,
                            output_point_limit=4096, sampling_seed=100, noise_seed=100,
                            config=None, min_points=1, session_id='installation-evaluation',
                            passage_windows=None, source_indices=None, distal_span=.8, truth_cache=None):
    """Evaluate all expected times; output ROI has no effect on simulated returns.

    Target-associated means a non-noise-only resolution cell in the ROI. Merged
    cells have no blade/material identity, so this is not blade identification.
    """
    from wfrl.radar_x100 import simulation, detection, installation
    times = np.asarray(times, dtype=float)
    if times.ndim != 1 or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError('Expected an explicit increasing source-time grid')
    installation.profile_metadata(roi)
    config = detection.DetectionConfig() if config is None else config
    frames, masks, errors, records = [], [], [], []
    sequences = list(range(len(times))) if source_indices is None else list(source_indices)
    if len(sequences) != len(times):
        raise ValueError('Source indices must match the evaluation grid')
    truth_cache = {} if truth_cache is None else truth_cache
    membership = [[] for _ in times]
    if passage_windows is not None:
        for blade, windows in enumerate(passage_windows):
            for window in windows:
                for i in window['indices']:
                    membership[i].append(blade)
    plan = None
    plan_error = None
    try:
        plan = sampler.sampling_plan(integration_budget, sampling_seed)
    except Exception as exc:
        plan_error = str(exc)
    def observe(stamp, sequence, blades):
        frame = mask = None
        try:
            if plan_error is not None:
                raise ValueError('Reference sampling plan unavailable: ' + plan_error)
            transaction = getattr(sampler, 'sampling_transaction', nullcontext)
            with transaction():
                frame = simulation.simulate_detections(sampler.sample, float(stamp),
                    sensor_id='X100-T1', session_id=session_id, sequence=sequence, interval_s=sampler.interval,
                    integration_budget=integration_budget, output_point_limit=output_point_limit,
                    sampling_seed=sampling_seed, noise_seed=noise_seed, config=config,
                    visibility=sampler.visibility, material_at=sampler.material_sample, sampling_plan=plan)
                if frame is not None and frame.completeness == 'COMPLETE':
                    mask = installation.roi_mask(frame, sampler.mount_world_at(float(stamp), mount='NACELLE'), roi)
                record = None
                if mask is not None:
                    stats = detection.detection_statistics(frame, point_mask=mask)
                    record = dict(source_time_s=float(stamp), source_index=int(sequence),
                        target_associated_points=stats['target_associated_points'], false_alarm_points=stats['false_alarm_points'])
                    if blades:
                        from wfrl_blender.radar_x100.scene import SideWallQuery
                        context = sampler.analysis_context(frame, ('offline-passage', sequence))
                        selected = mask & (frame.model_diagnostics.values['false_alarm'] == 0)
                        result = SideWallQuery(context).query(frame.xyz[selected], (roi[:3], roi[3:]))
                        distances = result['distance'][np.isfinite(result['distance'])]
                        estimate = float(distances.min()) if len(distances) else None
                        record.update(tower_distance_estimate_m=estimate, blade_truth={})
                        for blade in set(blades):
                            key = (sampler.source_id, sampler.geometry_id, float(stamp), blade, distal_span, tuple(roi))
                            if key not in truth_cache:
                                truth_cache[key] = sampler.passage_truth(sampler.latest_sample, blade, distal_span, roi, context)
                            truth = truth_cache[key]
                            record['blade_truth'][str(blade + 1)] = dict(tower_distance_truth_m=truth,
                                tower_distance_truth_status='AVAILABLE' if truth is not None else 'NO_DISTAL_SURFACE_IN_ROI',
                                tower_distance_error_m=estimate - truth if estimate is not None and truth is not None else None)
                return frame, mask, record
        except Exception as exc:
            errors.append(dict(source_index=sequence, source_time_s=float(stamp), reason=str(exc)))
        return None, None, None

    for index, (sequence, stamp) in enumerate(zip(sequences, times)):
        frame, mask, record = observe(stamp, sequence, membership[index])
        frames.append(frame)
        masks.append(mask)
        records.append(record)
    summary = detection.summarize_detections(frames, point_masks=masks, min_points=min_points)
    result = dict(sampling_seed=sampling_seed, noise_seed=noise_seed, integration_budget=integration_budget,
        output_point_limit=output_point_limit, sampling_plan_id=plan.identity if plan is not None else None,
        model=dict(id=detection.MODEL_ID, parameters=asdict(config), assumptions=list(detection.ASSUMPTIONS)),
        definition='At least min_points non-false-alarm model cells inside the output ROI; no blade identity or hardware reliability claim',
        grid_times_s=times.tolist(), source_indices=sequences, frames=records, simulated_detection_support=summary, errors=errors)
    if passage_windows is not None:
        nadirs = []
        for blade, windows in enumerate(passage_windows):
            blade_nadirs = []
            for window in windows:
                record = None
                if window.get('nadir_time_s') is not None:
                    _, _, record = observe(window['nadir_time_s'], window['nadir_sequence'], [blade])
                    if record is not None:
                        record = record | record['blade_truth'][str(blade + 1)]
                blade_nadirs.append(record)
            nadirs.append(blade_nadirs)
        result['passage_detection_support'] = installation.summarize_passage_detections(
            times, records, passage_windows, nadirs, min_points=min_points)
    return result


def runs(mask):
    longest = {True: 0, False: 0}
    previous, length = None, 0
    for value in mask:
        if value is None:
            previous, length = None, 0
            continue
        visible = bool(value)
        length = length + 1 if visible is previous else 1
        longest[visible] = max(longest[visible], length)
        previous = visible
    return dict(longest_visible_frames=longest[True], longest_gap_frames=longest[False])


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
            crossing = None
            if lo <= center <= hi:
                segment_angles, segment_times = angles[a:b+1], times[a:b+1]
                if angles[b] < angles[a]:
                    segment_angles, segment_times = segment_angles[::-1], segment_times[::-1]
                crossing = float(np.interp(center, segment_angles, segment_times))
            result.append(dict(status='COMPLETE' if complete else 'TRUNCATED',
                direction='FORWARD' if angles[b]>angles[a] else 'REVERSE',
                reversal_boundary=bool(a>0 or b<len(times)-1), center_deg=center,
                source_segment_s=[float(times[a]),float(times[b])],
                start_s=float(times[indices[0]]) if len(indices) else None,
                end_s=float(times[indices[-1]]) if len(indices) else None,indices=indices.tolist(), nadir_time_s=crossing))
    return result


def passage_grid(times, angles, half_window=30., passages_per_blade=0):
    """Enumerate every 30 Hz sample in selected complete passages, retaining source indices."""
    if type(passages_per_blade) is not int or passages_per_blade < 0:
        raise ValueError('Passage limit must be a nonnegative integer')
    windows, sequence = [], len(times)
    for blade in range(3):
        selected = []
        for window in passages(times, angles + 120 * blade, half_window):
            if window['status'] == 'COMPLETE':
                selected.append(window | dict(nadir_sequence=sequence))
                sequence += 1
        windows.append(selected[:passages_per_blade] if passages_per_blade else selected)
    indices = sorted({i for blade in windows for window in blade for i in window['indices']})
    lookup = {source: i for i, source in enumerate(indices)}
    windows = [[window | dict(source_indices=window['indices'], indices=[lookup[i] for i in window['indices']])
                for window in blade] for blade in windows]
    return np.asarray(times)[indices], indices, windows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--package',type=Path)
    parser.add_argument('--candidates',type=int,default=512)
    parser.add_argument('--seed',type=int,default=100)
    parser.add_argument('--targets-per-group',type=int,default=256)
    parser.add_argument('--half-window-deg',type=float,default=30.)
    parser.add_argument('--distal-span',type=float,nargs=2,default=(.8,.95))
    parser.add_argument('--roi',type=float,nargs=6,default=(-15,-15,-70,-2.5,15,-40),
                        help='Nacelle-local analysis bounds: xmin ymin zmin xmax ymax zmax')
    parser.add_argument('--minimum-points',type=int,default=3,help='Geometric distal points per blade/frame, at least 3')
    parser.add_argument('--search-candidates', action='store_true', help='Include finite lateral-offset and two-axis tilt candidates')
    parser.add_argument('--bins',type=int,default=6)
    add_detection_arguments(parser)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    if (args.output.exists() or not 1<=args.candidates<=4096 or not 1<=args.targets_per_group<=4096
        or args.seed < 0 or args.minimum_points < 3 or not 0<args.half_window_deg<180 or not 1<=args.bins<=360
        or not all(0<=x<=1 for x in args.distal_span)
        or not np.isfinite(args.roi).all() or not np.isfinite(args.half_window_deg)
        or np.any(np.array(args.roi[:3])>=args.roi[3:])):
        raise ValueError('Invalid research profile or output already exists')
    root=Path(__file__).resolve().parents[2]
    sys.path[:0]=[str(root/'blender_frontend'),str(root)]
    import bpy
    import wfrl_blender as addon
    from wfrl_blender import farm_flex
    from wfrl_blender import atomic_json
    from wfrl_blender.radar_x100.scene import SceneSampler,installation_matrix,installation_catalog
    from wfrl.radar_x100 import simulation, installation
    from scripts.radar_x100.export_simulation import sample_times
    config = detection_configuration(args)
    metadata = installation.profile_metadata(args.roi)
    addon.register()
    try:
        addon.load_demo_scene(args.package or farm_flex.default_package())
        sampler=SceneSampler(farm_flex.active_for(bpy.context.scene),mount_from_sensor=np.eye(4))
        catalog = installation_catalog(sampler)
        choices = installation.candidates([], [], [], [], catalog['baselines'] + (
            catalog['search_candidates'] if args.search_candidates else []))
        samplers={c['location_name']:sampler.with_installation('NACELLE',installation_matrix(c['location_m'],c['rotation_deg']))
            for c in choices}
        mechanics = {name: sampler.installation_constraints(current.mount_from_sensor) for name, current in samplers.items()}
        motion = {name: [] for name in samplers}
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
            try:
                motion_rows = sampler.motion_clearances(sampler.sample(float(t)),
                    [current.mount_from_sensor for current in samplers.values()])
            except Exception:
                motion_rows = [None] * len(samplers)
            for name, row in zip(samplers, motion_rows):
                motion[name].append(row)
            for name,current in samplers.items():
                try:
                    frame=simulation.simulate(current.sample,float(t),sensor_id='X100-T1',session_id='coverage',
                        sequence=sequence,interval_s=sampler.interval,candidate_budget=args.candidates,seed=args.seed,
                        visibility=current.visibility,material_at=current.material_sample, sampling_plan=plan)
                    if frame is None:
                        raise ValueError('Missing geometric source sample')
                    sample=current.latest_sample
                    sensor_world=simulation.rigid_matrix(frame.spatial['world_from_sensor'])
                    world=frame.xyz.astype(float)@sensor_world[:3,:3].T+sensor_world[:3,3]
                    analysis_world = current.mount_world_at(float(t), mount='NACELLE')
                    base=(world-analysis_world[:3,3])@analysis_world[:3,:3]
                    inside=(base>=roi_lo).all(axis=1)&(base<=roi_hi).all(axis=1)
                    output_ids=np.asarray(frame.provenance['material_point_indices'])
                    rows=[]
                    for reference_catalog, output_catalog in zip(catalogs,actual_targets):
                        target=sample.vertices[sample.triangles[reference_catalog]].mean(axis=1)
                        local=(target-analysis_world[:3,3])@analysis_world[:3,:3]
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
            if sequence % 300 == 0:
                print(f'X100_COVERAGE_PROGRESS {sequence + 1}/{len(times)}', flush=True)
        phase=np.interp(times,sampler.flex.times,sampler.flex.poses[:,sampler.k,1])
        windows = [passages(times,phase+120*b,args.half_window_deg) for b in range(3)]
        detection_times, detection_indices, detection_windows = passage_grid(times, phase, args.half_window_deg)
        truth_cache = {}
        report=dict(profile={key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
            **metadata, original_assessment=catalog['original_assessment'], shell_geometry=catalog['shell_geometry'],
            peer_placement_references=catalog['peer_placement_references'],
            source_id=sampler.source_id,geometry_id=sampler.geometry_id,source_grid_hz=30,
            source_frames=len(times),grid_times_s=times.tolist(),target_groups=groups,total_unique_targets=sum(map(len,catalogs)),
            target_method='finite centroid reference catalog; independent area-sampled observed material coordinates',
            sampling_method=plan.method,sampling_plan_id=plan.identity,
            truth_boundary='evaluation only; no online analysis inputs or target associations',
            tip_identifiability='NOT_EVALUATED',safety_acceptance='NOT_EVALUATED',installations={})
        for name, rows in measurements.items():
            encounters=[]
            for b in range(3):
                for original_passage in windows[b]:
                    passage = dict(original_passage)
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
                        visible_fraction=sum(V)/N if N and N == len(known) else None,
                        output_fraction=sum(O)/N if N and N == len(known) else None,
                        visible_fraction_known=sum(V)/len(known) if known else None,
                        output_fraction_known=sum(O)/len(known) if known else None,
                        conditional_output_given_reference_visible=sum(o and v for o,v in zip(O,V))/sum(V) if sum(V) else None,
                        conditional_denominator=sum(V),
                        evaluation='N/A' if not N else 'UNKNOWN' if N>len(known) else 'EVALUATED',
                        output_runs=runs([value if isinstance(rows[i],list) else None for i,value in zip(indices,O)]),
                        visibility_runs=runs([value if isinstance(rows[i],list) else None for i,value in zip(indices,V)]),angle_bins=bins))
            counts = np.array([[row['O'] for row in value] if isinstance(value, list) else [-1]*3 for value in rows])
            geometric = installation.summarize(counts, windows, args.minimum_points)
            geometric.update(reference_passages=encounters,
                frames=[dict(source_time_s=float(t),source_index=i,sensor=sensor_counts[name][i],
                    blade_counts=rows[i] if isinstance(rows[i],list) else None,
                    total_reference_budget=sum(map(len,catalogs)),
                    total_resident_targets=sum(r['E'] for r in rows[i]) if isinstance(rows[i],list) else None,
                    UNKNOWN=rows[i].get('UNKNOWN') if isinstance(rows[i],dict) else None) for i,t in enumerate(times)])
            mechanics[name]['motion_clearance'] = installation.summarize_motion(motion[name])
            if mechanics[name]['status'] == 'PASS_RESEARCH':
                mechanics[name]['status'] = mechanics[name]['motion_clearance']['status']
            report['installations'][name]=dict(calibration_id=samplers[name].calibration_id,
                mount_from_sensor=samplers[name].mount_from_sensor.tolist(), mechanical=mechanics[name],
                geometric_coverage=geometric, detection_profiles=[evaluate_detection_grid(samplers[name], detection_times,
                    roi=args.roi, integration_budget=args.integration_budget, output_point_limit=args.output_point_limit,
                    sampling_seed=args.seed, noise_seed=seed, config=config, min_points=args.detection_min_points,
                    passage_windows=detection_windows, source_indices=detection_indices,
                    distal_span=min(args.distal_span), truth_cache=truth_cache)
                    for seed in dict.fromkeys(args.noise_seeds)])
        if args.output.exists():
            raise FileExistsError(args.output)
        atomic_json.write_json(args.output, report, mkdir=True)
        print('X100_COVERAGE_COMPLETE',args.output)
    finally:
        addon.unregister()


if __name__=='__main__': main()
