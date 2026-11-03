"""Evaluate causal radar estimates against independent saved structural-tip motion."""
import argparse
import hashlib
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys
import numpy as np


def distribution(values):
    if not values:
        return dict(count=0, mean_m=None, p95_m=None, max_m=None)
    return dict(count=len(values), mean_m=float(np.mean(values)), p95_m=float(np.quantile(values,.95)), max_m=float(np.max(values)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--search-report', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--candidates', type=int, default=1024)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if args.output.exists() or not 1 <= args.candidates <= 4096:
        raise ValueError('Invalid budget or output already exists')
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root/'blender_frontend'), str(root)]
    import bpy
    import wfrl_blender as addon
    from wfrl_blender import farm_flex
    from wfrl_blender.radar_x100 import runtime, scene as scene_api
    from wfrl.radar_x100 import analysis, tracking, sampling, simulation
    from scripts.radar_x100.export_simulation import sample_times
    from scripts.radar_x100.coverage import passages
    addon.register()
    try:
        source_files = [Path(__file__), Path(tracking.__file__), Path(sampling.__file__),
            Path(simulation.__file__), Path(runtime.__file__), Path(scene_api.__file__)]
        code_identity = {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
        addon.load_demo_scene(args.package or farm_flex.default_package())
        scene = bpy.context.scene
        scene.wfrl_x100_candidates = args.candidates
        session = runtime.start(scene)
        if args.search_report:
            runtime.apply_mount_preset(scene, 'SEARCH', search_report=json.loads(args.search_report.read_text()))
        sampler = session.sampler
        times = np.array(list(sample_times(*sampler.interval,30)))
        phase = np.interp(times, sampler.flex.times, sampler.flex.poses[:,sampler.k,1])
        expected = np.zeros((len(times),3), dtype=bool)
        for b in range(3):
            for passage in passages(times,phase+120*b,30):
                if passage['status']=='COMPLETE':
                    expected[passage['indices'],b] = True
        rows, unknown = [], []
        summary = [dict(statuses=Counter(), expected=int(expected[:,b].sum()), supported=0,
                        tip_error=[], clearance_error=[], association_correct=0, association_assigned=0) for b in range(3)]
        for index,t in enumerate(times):
            scene['wfrl_clearance_time_s'] = float(t)
            session.update()
            if session.blade_result is None:
                unknown.append(dict(source_time_s=float(t), reason=session.blade_error))
                continue
            frame = session.frame
            observed_triangles = np.asarray(frame.material_ids)
            row = dict(source_time_s=float(t), blades=[])
            for b, estimate in enumerate(session.blade_result['blades']):
                item = dict(blade=b+1, expected=bool(expected[index,b]), status=estimate['status'],
                    points=estimate['points'], tip_error_m=None, clearance_error_m=None,
                    fit_residual_m=estimate['fit_residual_m'], extrapolation_m=estimate['extrapolation_m'])
                if expected[index,b]:
                    summary[b]['statuses'][estimate['status']] += 1
                    assigned = np.array(estimate['indices'], dtype=int)
                    lo,hi = sampler.triangle_offsets[b:b+2]
                    summary[b]['association_assigned'] += len(assigned)
                    summary[b]['association_correct'] += int(np.sum((observed_triangles[assigned]>=lo)&(observed_triangles[assigned]<hi)))
                if estimate['tip_world'] is not None:
                    prior = sampler.blade_priors(float(t))[b]
                    obj = sampler.blades[b][0]
                    # Structural material-point truth is evaluated after inference and never fed back.
                    truth = sampler.flex.deform_points(obj, [[0.,0.,prior.tip_span_m]], float(t), world=True, high_precision=True)[0]
                    error = float(np.linalg.norm(truth-estimate['tip_world']))
                    point = analysis.local([truth],session.context.world_from_sensor)
                    reference_distance = session.side_query.query(point, ((-1e6,)*3,(1e6,)*3))['distance'][0]
                    clearance_error = abs(float(reference_distance)-estimate['estimated_clearance_m'])
                    item.update(tip_error_m=error,clearance_error_m=clearance_error)
                    if expected[index,b]:
                        summary[b]['supported'] += 1
                        summary[b]['tip_error'].append(error)
                        summary[b]['clearance_error'].append(clearance_error)
                row['blades'].append(item)
            rows.append(row)
            if index % 300 == 0:
                print(f'X100_ESTIMATION_PROGRESS {index+1}/{len(times)}',flush=True)
        metrics=[]
        for b,s in enumerate(summary):
            metrics.append(dict(blade=b+1,N=s['expected'],supported=s['supported'],
                supported_fraction=s['supported']/s['expected'] if s['expected'] else None,
                rejection_counts=dict(s['statuses']),tip_error=distribution(s['tip_error']),
                clearance_error=distribution(s['clearance_error']),
                association_precision=s['association_correct']/s['association_assigned'] if s['association_assigned'] else None,
                association_denominator=s['association_assigned']))
        report=dict(schema='x100-blade-evaluation.v1',source_id=sampler.source_id,geometry_id=sampler.geometry_id,
            source_grid_hz=30,source_frames=len(times),candidate_budget=args.candidates,
            calibration_id=sampler.calibration_id,mount_from_sensor=sampler.mount_from_sensor.tolist(),
            sampling_plan_id=session.frame.provenance['sampling_plan_id'], sampling_algorithm=sampling.AREA_ALGORITHM,
            code_identity=code_identity,
            inference_config=asdict(session.blade_tracker.config),metrics=metrics,unknown=unknown,frames=rows,
            truth_boundary='saved structural tip and material triangle labels used only for evaluation after estimation',
            evaluation_scope='same-source geometry experiment; no field accuracy or safety acceptance')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(report,stream,ensure_ascii=False,allow_nan=False,indent=2)
        print('X100_ESTIMATION_COMPLETE',args.output,flush=True)
        if unknown:
            raise RuntimeError('Evaluation contains unavailable inference frames')
    finally:
        addon.unregister()


if __name__=='__main__':
    main()
