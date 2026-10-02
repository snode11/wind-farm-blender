"""Manual-grounded accuracy, sample coverage and S1 timing audit; no fitting."""
import argparse
from collections import Counter
from copy import deepcopy
import itertools
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.camera_video.data import SourceGeometry
from wfrl.lidar.dual_beam import reconstruct, validate_calibration
from wfrl.lidar.dual_beam_replay import resolve_package, error_statistics, DualBeamReader, digest
from wfrl.lidar.molas_cl import SPEC
from wfrl.lidar.moving_tower import horizontal_clearance
from wfrl.lidar.replay import ReplayReader
from types import SimpleNamespace


def signed_statistics(errors):
    return dict(**error_statistics(errors), min_signed_error_m=min(errors,default=None),
                max_underestimate_m=max(0.,-min(errors)) if errors else None)


def sample_audit(data, fps):
    rows, events = data['samples'], data['events']
    passages = [p for p in data['summary']['passages'] if not p['boundary_truncated']]
    raw = {r['time_s'] for r in rows if r['s1_observation_state'] == 'triggered'}
    event_hits = {t for event in events for t in event['hit_times_s']}
    delays = [e['output_time_s']-e['start_s'] for e in events]
    if raw != event_hits or any(d != 0 for d in delays):
        raise ValueError('S1 events do not preserve first saved hit timing')
    missing_s1 = [p['passage_id'] for p in passages if not any(
        r['passage_id'] == p['passage_id'] and r['s1_observation_state'] == 'triggered'
        and r['observations']['S1']['blade_id'] == p['blade_id'] for r in rows)]
    reductions = []
    for stride in (2, 4):
        for phase in range(stride):
            selected = rows[phase::stride]
            hits = {r['time_s'] for r in selected if r['s1_observation_state'] == 'triggered'}
            detected = [e for e in events if hits.intersection(e['hit_times_s'])]
            detection_delays = [min(hits.intersection(e['hit_times_s']))-e['start_s'] for e in detected]
            covered = {r['passage_id'] for r in selected if r['reconstruction']['valid']
                       and r['reconstruction']['blade_id'] == r['expected_blade_id']}
            reductions.append(dict(fps=fps/stride, phase=phase,
                source_events=len(events), retained_events=len(detected),
                additional_missed_events=len(events)-len(detected),
                maximum_extra_delay_relative_to_40hz_s=max(detection_delays, default=None),
                complete_passages=len(passages),
                paired_passages=sum(p['passage_id'] in covered for p in passages)))
    return dict(raw_s1_hits=len(raw), s1_events=len(events),
        s1_events_with_pair_unavailable_at_start=sum(not next(r for r in rows if r['time_s']==e['start_s'])['reconstruction']['valid'] for e in events),
        event_matches_raw_hits=True, saved_sample_logic_delay_s=max(delays, default=None),
        complete_passages=len(passages), s1_no_hit_passages=missing_s1,
        s1_no_hit_passage_count=len(missing_s1),
        continuous_event_recall=None, continuous_alarm_latency_s=None,
        physical_protection_latency_s=None, source_interval_s=1/fps,
        scope='S1 passage counts describe saved geometry only, not a required S1 safety trigger on every passage. Decimation is loss relative to 40 Hz, not proof that 40 Hz is sufficient.',
        decimation=reductions)


def accuracy_audit(source, data, config):
    _, _, limits = validate_calibration(config)
    method = config.get('reconstruction_method', 'hub-axis.v1')
    errors, angles, gains, offsets, sensitivities, failures = [], [], [], [], [], []
    endpoint_errors, endpoint_reasons = [], Counter()
    for index, row in enumerate(data['samples']):
        pair, evaluation = row['reconstruction'], row['evaluation']
        if not pair['valid']:
            continue
        h = np.asarray(pair['hub_m']); u = np.asarray(pair['direction'])
        actual = np.asarray(evaluation['tip_reference_m'])-h
        angle = float(np.degrees(np.arccos(np.clip(u @ actual / np.linalg.norm(actual), -1, 1))))
        error = pair['clearance_estimate'] - evaluation['clearance_reference_m']
        errors.append(error); angles.append(angle)
        gains.append(config['effective_length_m']/pair['separation_m'])
        mid = (np.asarray(pair['p2_m'])+pair['p3_m'])/2-h
        offsets.append(float(np.linalg.norm(mid-u*(mid@u))))
        tt=source.tower_transforms[index, source.tower_station]
        tower=np.einsum('nij,nj->ni', tt[...,:3], source.tower_reference)+tt[...,3]+source.layout
        clearance=lambda p: horizontal_clearance(p,tower,source.tower_triangles)[0]
        # Declared deterministic endpoint probes. These are NOT a probability
        # distribution or a certified global bound; hit membership is held fixed.
        probes=[]
        for dr2, dr3, da2, da3 in itertools.product((-1, 1), repeat=4):
            observations=[]
            for name, dr, da in (('S2',dr2,da2), ('S3',dr3,da3)):
                obs=deepcopy(row['observations'][name])
                obs['slant_range_m'] += dr*SPEC['range_accuracy_m']
                local=source.nacelles[index,:,:3].T@np.asarray(obs['direction'])
                theta=np.radians(da*SPEC['relative_angle_tolerance_deg'])
                rotation=np.array([[np.cos(theta),0,np.sin(theta)],[0,1,0],[-np.sin(theta),0,np.cos(theta)]])
                obs['direction']=(source.nacelles[index,:,:3]@rotation@local).tolist()
                obs['direction']=(np.asarray(obs['direction'])/np.linalg.norm(obs['direction'])).tolist()
                observations.append(obs)
            estimate=reconstruct(*observations,h,config['effective_length_m'],clearance,
                                 time_s=row['time_s'],limits=limits,method=method)
            endpoint_reasons[estimate['reason']] += 1
            value = estimate['clearance_estimate']
            endpoint_error = None if value is None else value-evaluation['clearance_reference_m']
            if endpoint_error is not None:
                endpoint_errors.append(endpoint_error)
            probes.append(dict(signs=[dr2,dr3,da2,da3],valid=estimate['valid'],reason=estimate['reason'],
                               clearance_estimate_m=value,error_m=endpoint_error))
        finite=[p['clearance_estimate_m'] for p in probes if p['valid']]
        sensitivities.append(dict(time_s=row['time_s'],blade_id=pair['blade_id'],
            maximum_endpoint_change_m=max((abs(p-pair['clearance_estimate']) for p in finite),default=None),
            rejected_probes=len(probes)-len(finite),probes=probes))
        failures.append(dict(time_s=row['time_s'], blade_id=pair['blade_id'],
            error_m=error, estimated_direction_to_reference_chord_deg=angle,
            separation_m=pair['separation_m']))
    return dict(**signed_statistics(errors),
        reconstruction_method=method,
        estimated_direction_to_reference_chord_deg=dict(mean=float(np.mean(angles)) if angles else None,maximum=max(angles,default=None)),
        geometric_length_over_point_separation=dict(minimum=min(gains,default=None),maximum=max(gains,default=None),
            scope='geometric ratio; not a TLS noise amplification bound'),
        measured_midpoint_distance_to_hub_direction_m=dict(mean=float(np.mean(offsets)) if offsets else None,maximum=max(offsets,default=None)),
        fixed_pair_sensor_sensitivity=sensitivities,
        endpoint_error_statistics=signed_statistics(endpoint_errors),endpoint_reasons=dict(endpoint_reasons),
        perturbation_definition=dict(sign_order=['S2_range','S3_range','S2_angle','S3_angle'],
            range_amplitude_m=SPEC['range_accuracy_m'],angle_amplitude_deg=SPEC['relative_angle_tolerance_deg'],
            coordinate_frame='source nacelle material frame rotation about +y',combinations=16,
            domain='original valid pairs, fixed physical first-hit membership'),
        sensitivity_scope='16 endpoint combinations of +/-0.2 m per range and +/-0.2 deg S2/S3 relative direction; no physical ray re-hit, no distribution or confidence interval, no whole-system bound.',
        worst_samples=sorted(failures,key=lambda r:abs(r['error_m']),reverse=True)[:10])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    path,overlay=resolve_package(args.package)
    args.output.mkdir(parents=True,exist_ok=False)
    result=dict(status='RESEARCH_AUDIT_COMPLETE_NOT_ACCURACY_ACCEPTANCE',
        package_manifest_sha256=digest(args.package/'manifest.json'),
        implementation_hashes={name:digest(ROOT/name) for name in
            ('scripts/lidar/audit_dual_beam_accuracy.py','wfrl/lidar/dual_beam.py','wfrl/lidar/molas_cl.py')},
        manual_spec=SPEC, calibration=overlay['config'],
        sensor_chain='40 Hz ideal first intersections, no hardware raw pulse stream or DP aggregation',
        accuracy_acceptance='NOT_ESTABLISHED', hardware_validation='NOT_RUN', turbines={})
    pooled=[]
    for tid,data in overlay['results'].items():
        source=SourceGeometry(path,tid)
        accuracy=accuracy_audit(source,data,overlay['config'])
        sampling=sample_audit(data,source.manifest['source_fps'])
        payload=json.loads((path/'data.json').read_text())[tid]
        reader=DualBeamReader(ReplayReader(SimpleNamespace(manifest={**source.manifest,'turbine_id':tid},**payload)),overlay,tid)
        durations=[]
        for t in source.times[::12]:
            start=time.perf_counter();reader.at(float(t));durations.append(time.perf_counter()-start)
        result['turbines'][tid]=dict(accuracy=accuracy,sampling=sampling,
            reader_cpu_query_s=dict(samples=len(durations),max=max(durations),median=float(np.median(durations)),
                                    scope='host CPU query only, not GUI, sensor or protection latency'))
        pooled += [r['reconstruction']['clearance_estimate']-r['evaluation']['clearance_reference_m']
                   for r in data['samples'] if r['reconstruction']['valid']]
        print(tid,accuracy['mae_m'],sampling['s1_events'],flush=True)
    result['pooled_accuracy']=signed_statistics(pooled)
    result['identifiability']={'two_ranges_do_not_determine_flexible_tip':True,
        'reason':'A deformation perturbation proportional to s^2(s-s2)(s-s3) preserves the root displacement/slope and both measured locations but changes the tip. Surface-to-axis mapping and curvature require additional validated information.',
        'not_available':['manufacturer full inversion algorithm','unit emitter spacing and calibrated directions','synchronized raw echo timestamps and blade association','independent validation runs and physical measurements'],
        'disallowed_shortcuts':['bias subtraction fitted to this segment','truth tip or terminal transforms fed to estimator','40 Hz interpolation labeled 20 kHz','DP averages labeled simultaneous hits']}
    (args.output/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(args.output/'audit.json')


if __name__=='__main__':
    main()
