"""Evaluate causal radar estimates against independent saved structural-tip motion."""
import argparse
import gzip
import hashlib
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys
import numpy as np


def _json_value(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def error_metrics(errors, expected):
    """Missing estimates remain in the coverage denominator, never as zero error."""
    values = np.array([e for e in errors if e is not None], float)
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite evaluation error')
    return dict(expected_frames=expected, estimated_frames=len(values),
        estimable_fraction=len(values)/expected if expected else None,
        rmse_m=float(np.sqrt(np.mean(values**2))) if len(values) else None,
        max_abs_error_m=float(np.max(np.abs(values))) if len(values) else None,
        bias_m=float(np.mean(values)) if len(values) else None)


def separation_metrics(truth, predicted):
    """Rows=true, columns=predicted; mixed unresolved truth (-1) is explicit."""
    truth, predicted = np.asarray(truth, int), np.asarray(predicted, int)
    if truth.shape != predicted.shape or np.any((predicted < 0) | (predicted > 2)):
        raise ValueError('Invalid separation evaluation labels')
    known = truth >= 0
    matrix = np.bincount(3*truth[known]+predicted[known], minlength=9).reshape(3, 3)
    classes = {}
    for i, name in enumerate(('OTHER', 'BLADE', 'TOWER')):
        classes[name] = dict(truth_points=int(matrix[i].sum()), predicted_points=int(matrix[:, i].sum()),
            precision=float(matrix[i, i]/matrix[:, i].sum()) if matrix[:, i].sum() else None,
            recall=float(matrix[i, i]/matrix[i].sum()) if matrix[i].sum() else None)
    return dict(point_count=len(truth), evaluated_points=int(known.sum()), unresolved_truth_points=int((~known).sum()),
        accuracy=float(np.trace(matrix)/matrix.sum()) if matrix.sum() else None,
        confusion_matrix=matrix.tolist(), class_order=['OTHER', 'BLADE', 'TOWER'], classes=classes)


def resolution_truth(observation, frame, triangle_offsets, blade_count, tower_count, config):
    """Evaluation-only material attribution of the actual power-merged cells.

    Reconstruct the same deterministic quadrature/clusters, then identify cells
    by their exact summed model power. A class must carry >=90% of a cell's
    power; mixed/duplicate-power ambiguous cells remain unresolved. Pure-noise
    cells are OTHER. No label or signal-only diagnostic leaves the evaluator.
    """
    from wfrl.radar_x100 import detection, simulation
    sample, plan, mask = observation.sample, observation.plan, observation.mask
    xyz, velocity = observation.xyz, observation.radial
    faces = sample.vertices[sample.triangles[plan.triangle_ids[mask]]]
    normals = np.cross(faces[:, 1]-faces[:, 0], faces[:, 2]-faces[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    normals = np.divide(normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 0)
    world_los = (xyz/np.linalg.norm(xyz, axis=1)[:, None]) @ sample.world_from_sensor[:3, :3].T
    incidence = np.abs(np.einsum('ij,ij->i', normals, world_los))
    rcs = detection.surface_rcs(plan.area_weights_m2[mask], incidence, config=config)
    part = np.searchsorted(triangle_offsets, plan.triangle_ids[mask], side='right')-1
    labels = np.where(part < blade_count, 1, np.where(part < blade_count+tower_count, 2, 0))
    inside = simulation.in_fov(xyz) & (velocity >= config.velocity_min_m_s) & (velocity <= config.velocity_max_m_s)
    polar = detection._polar(xyz[inside])
    power = detection.received_power_w(polar[:, 0], rcs[inside], config=config,
        azimuth_deg=polar[:, 1], elevation_deg=polar[:, 2])
    positive = power > 0
    coordinates = detection._resolution_coordinates(polar[positive], velocity[inside][positive])
    inverse, count = detection._resolution_clusters(coordinates, detection._cell_grid(config)[2])
    power, labels = power[positive], labels[inside][positive]
    totals = np.bincount(inverse, weights=power, minlength=count)
    by_class = np.array([np.bincount(inverse, weights=power*(labels == c), minlength=count) for c in range(3)])
    fractions = np.divide(by_class, totals[None, :], out=np.zeros_like(by_class), where=totals[None, :] > 0)
    cell_labels = np.where(fractions.max(axis=0) >= .9, fractions.argmax(axis=0), -1)
    lookup = {}
    for total, label in zip(totals, cell_labels):
        lookup[float(total)] = int(label) if total not in lookup or lookup[total] == label else -1
    result = []
    for diagnostic in frame.model_diagnostics.values:
        if diagnostic['false_alarm']:
            result.append(0)
        else:
            power = float(diagnostic['model_power_w'])
            if power not in lookup:
                raise ValueError('Evaluation could not reproduce resolution-cell power')
            result.append(lookup[power])
    return np.array(result, dtype=np.int8)


def _shape_payloads(sampler):
    """Use immutable unloaded lofts and the independent prebend reference only."""
    from wfrl_blender.turbine_geometry import geometry_data
    scalars = geometry_data()['scalars']
    reference = sampler.flex.reference
    tip = np.array(reference['tip_local_m'] if reference is not None else [0., 0., scalars['TipRad']])
    shapes, meshes = {}, {}
    for blade, (_, rest, *_) in enumerate(sampler.blades, 1):
        spans, counts = np.unique(rest[:, 2], return_counts=True)
        ring_size = int(np.max(counts))
        spans = spans[counts == ring_size]
        sections = np.array([rest[rest[:, 2] == z, :2] for z in spans])
        # The source loft closes at its cosmetic apex, while the independent
        # structural tip is a separate point declared by blade-reference.json.
        spans = np.r_[spans, tip[2]]
        sections = np.concatenate((sections, np.broadcast_to(rest[-1, :2], (1, ring_size, 2))))
        axis = np.zeros((len(spans), 2))
        if reference is not None:
            curve = np.asarray(reference['curve'])
            for a in range(2):
                axis[:, a] = np.interp(spans-scalars['HubRad'], curve[:, 2], curve[:, a])
        shapes[str(blade)] = dict(spans_m=spans, sections_xy_m=sections, reference_xy_m=axis,
            tip_local_m=tip, source='FarmFlex immutable unloaded SourceLoft (AeroDyn sections/airfoils) + '
                'validated blade-reference.json; constrained root poses; no loaded sections/displacement channels',
            rest_sha256=hashlib.sha256(rest.tobytes()).hexdigest())
        meshes[str(blade)] = dict(vertices=rest, triangles=sampler.parts[blade-1][1])
    return shapes, meshes


def collect_detection_observations(args):
    """Background-only geometry acquisition; evaluation truth is kept separate."""
    import bpy
    if not bpy.app.background:
        raise RuntimeError('Detection evaluation requires Blender --background')
    import wfrl_blender as addon
    from wfrl_blender import farm_flex
    from wfrl_blender.radar_x100.scene import SceneSampler, SideWallQuery, installation_matrix
    from wfrl.radar_x100 import analysis, detection, installation, simulation
    if not args.search_report:
        raise ValueError('--detections requires --search-report or --observations')
    path = args.search_report.resolve()
    search = json.loads(path.read_text(encoding='utf-8'))
    if search.get('evaluation_scope') != 'ALL_COMPLETE_PASSAGES' or not search.get('winner_id'):
        raise ValueError('A full-passage installation winner is required')
    winner = next(c for c in search['candidates'] if c['id'] == search['winner_id'])
    if len(winner['detection_profiles']) != 1:
        raise ValueError('Select a report with one explicit detection profile')
    profile = winner['detection_profiles'][0]
    config = detection.DetectionConfig(**profile['model']['parameters'])
    root = Path(__file__).resolve().parents[2]
    for name, digest in search['code_identity'].items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Installation evaluation source changed: ' + name)
    addon.register()
    try:
        addon.load_demo_scene(args.package or farm_flex.default_package())
        sampler = SceneSampler(farm_flex.active_for(bpy.context.scene),
            mount_from_sensor=installation_matrix(winner['location_m'], winner['rotation_deg']))
        if (sampler.source_id != search['source_id'] or sampler.geometry_id != search['geometry_id']):
            raise ValueError('Installation report belongs to another source/geometry')
        shapes, meshes = _shape_payloads(sampler)
        roi = search['analysis_roi']['lower_m'] + search['analysis_roi']['upper_m']
        plan = sampler.sampling_plan(profile['integration_budget'], profile['sampling_seed'])
        if plan.identity != profile['sampling_plan_id']:
            raise ValueError('Installation sampling plan changed')
        membership = {}
        for b, windows in enumerate(search['passage_windows'], 1):
            for passage, window in enumerate(windows):
                for i in window['source_indices']:
                    membership[i] = b, passage
        frames = []
        for index, (stamp, sequence) in enumerate(zip(profile['grid_times_s'], profile['source_indices'])):
            with sampler.sampling_transaction():
                frame = simulation.simulate_detections(sampler.sample, stamp, sensor_id='X100-T1',
                    session_id='clearance-evaluation', sequence=sequence, interval_s=sampler.interval,
                    integration_budget=profile['integration_budget'], output_point_limit=profile['output_point_limit'],
                    sampling_seed=profile['sampling_seed'], noise_seed=profile['noise_seed'], config=config,
                    visibility=sampler.visibility, material_at=sampler.material_sample, sampling_plan=plan)
                if frame is None or frame.completeness != 'COMPLETE':
                    raise ValueError(f'Missing engineering detection frame {sequence}')
                context = sampler.analysis_context(frame, ('clearance', sequence))
                query = SideWallQuery(context)
                priors = sampler.blade_priors(stamp)
                world = analysis.transform(frame.xyz, context.world_from_sensor)
                mask = installation.roi_mask(frame, context.world_from_analysis, roi)
                diagnostics = frame.model_diagnostics.values
                selected = mask & (diagnostics['false_alarm'] == 0)
                distances = query.query(frame.xyz[selected], (roi[:3], roi[3:]))['distance']
                baseline = float(np.min(distances)) if len(distances) else None
                archived = profile['frames'][index]
                saved = archived['tower_distance_estimate_m']
                if (baseline is None) != (saved is None) or baseline is not None and abs(baseline-saved) > 1e-8:
                    raise ValueError(f'Installation baseline did not reproduce at {sequence}')
                blade, passage = membership[sequence]
                tip_local = shapes[str(blade)]['tip_local_m']
                tip_truth = sampler.flex.deform_points(sampler.blades[blade-1][0], [tip_local], stamp,
                    world=True, high_precision=True)[0]
                tip_distance = query.query(analysis.local([tip_truth], context.world_from_sensor),
                    ((-1e6,)*3, (1e6,)*3))['distance'][0]
                observation = simulation._observe(sampler.sample, stamp, interval_s=sampler.interval,
                    seed=profile['sampling_seed'], candidate_budget=profile['integration_budget'], difference_s=.001,
                    visibility=sampler.visibility, material_at=sampler.material_sample, sampling_plan=plan)
                truth = resolution_truth(observation, frame, sampler.triangle_offsets, 3,
                    max(1, len(sampler.towers)), config)
                ranges = np.linalg.norm(frame.xyz, axis=1)
                sigma = np.sqrt(config.range_std_m**2 + ranges**2*np.radians(
                    math_hypot(config.azimuth_std_deg, config.elevation_std_deg))**2)
                frames.append(dict(time_s=stamp, source_index=sequence, blade=blade, passage=passage,
                    world_points=world, radial_velocity_m_s=frame.radial_velocity_m_s,
                    observed_power_w=diagnostics['model_observed_power_w'],
                    observed_snr_linear=diagnostics['model_observed_power_w']/config.detector_noise_power_w,
                    position_std_m=np.maximum(sigma, .01), priors=[asdict(p) for p in priors],
                    tower_vertices=context.vertices, tower_triangles=context.triangles,
                    world_from_analysis=context.world_from_analysis, roi_mask=mask,
                    truth=dict(labels=truth, false_alarm=diagnostics['false_alarm'], tip_world=tip_truth,
                        tip_clearance_m=float(tip_distance), baseline_clearance_m=baseline,
                        distal_surface_clearance_m=archived['blade_truth'][str(blade)]['tower_distance_truth_m'])))
            if index % 50 == 0:
                print(f'X100_CLEARANCE_OBSERVATIONS {index+1}/{len(profile["grid_times_s"])}', flush=True)
        return dict(schema='x100-clearance-observations.v1', source_id=sampler.source_id,
            geometry_id=sampler.geometry_id, installation_report=str(path),
            installation_report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            installation={k: winner[k] for k in ('id', 'location_name', 'location_m', 'rotation_deg')},
            source_code_identity=search['code_identity'], detection_config=asdict(config),
            integration_budget=profile['integration_budget'], output_point_limit=profile['output_point_limit'],
            sampling_seed=profile['sampling_seed'], noise_seed=profile['noise_seed'], sampling_plan_id=plan.identity,
            source_grid_hz=30, passages=sum(map(len, search['passage_windows'])),
            passage_windows=search['passage_windows'], roi=roi, distal_span=search['distal_span'],
            shapes=shapes, meshes=meshes, frames=frames)
    finally:
        addon.unregister()


def math_hypot(a, b):
    return float(np.hypot(a, b))


def _surface_clearance(row, prior, shape, mesh, record, data):
    """Fit-derived distal surface diagnostic with the legacy clipping definition."""
    from wfrl.radar_x100 import analysis, installation
    rest, faces = np.asarray(mesh['vertices']), np.asarray(mesh['triangles'])
    world = analysis.transform(shape.deform(rest, row['coefficients']), prior.world_from_blade)
    triangles = world[faces]
    local = analysis.local(triangles, np.asarray(record['world_from_analysis']))
    normalized = (rest[faces, 2]-rest[:, 2].min())/np.ptp(rest[:, 2])
    roi = data['roi']
    eligible = ((normalized.max(axis=1) >= data['distal_span'])
        & (local.max(axis=1) >= roi[:3]).all(axis=1) & (local.min(axis=1) <= roi[3:]).all(axis=1))
    clipped = []
    for triangle, xyz, span in zip(triangles[eligible], local[eligible], normalized[eligible]):
        clipped.extend(installation.clip_triangle(triangle, np.column_stack((xyz, span)),
            [*roi[:3], data['distal_span']], [*roi[3:], 1.]))
    if not clipped:
        return None
    clipped = np.asarray(clipped)
    clipped = clipped[np.linalg.norm(np.cross(clipped[:, 1]-clipped[:, 0], clipped[:, 2]-clipped[:, 0]), axis=1) > 1e-10]
    if not len(clipped):
        return None
    return installation.mesh_clearance(clipped, np.asarray(record['tower_vertices'])[np.asarray(record['tower_triangles'])])


def evaluate_detection_observations(data):
    """Inference receives only the explicit observable/prior whitelist below."""
    from wfrl.radar_x100 import clearance, tracking
    shapes = {int(b): clearance.BladeShape(**{k: v for k, v in s.items() if k != 'rest_sha256'})
              for b, s in data['shapes'].items()}
    tracker = clearance.ClearanceTracker(shapes)
    rows, predicted_all, truth_all, predicted_roi, truth_roi = [], [], [], [], []
    for index, record in enumerate(data['frames']):
        priors = [tracking.BladePrior(**p) for p in record['priors']]
        result = tracker.update(record['world_points'], record['radial_velocity_m_s'], priors,
            record['tower_vertices'], record['tower_triangles'], time_s=record['time_s'],
            identity=(data['source_id'], data['geometry_id'], data['installation']['id']), frame_key=record['source_index'],
            observed_power_w=record['observed_power_w'], observed_snr_linear=record['observed_snr_linear'],
            position_std_m=record['position_std_m'])
        # Truth is accessed only after inference, and cannot update the tracker.
        truth = record['truth']
        predicted = result['labels']
        labels, roi = np.asarray(truth['labels']), np.asarray(record['roi_mask'], bool)
        predicted_all.extend(predicted); truth_all.extend(labels)
        predicted_roi.extend(predicted[roi]); truth_roi.extend(labels[roi])
        row = result['blades'][record['blade']-1]
        baseline, reference = truth['baseline_clearance_m'], truth['distal_surface_clearance_m']
        estimate = row['clearance_m']
        fitted_surface = (_surface_clearance(row, priors[record['blade']-1], shapes[record['blade']],
            data['meshes'][str(record['blade'])], record, data) if estimate is not None else None)
        rows.append(dict(time_s=record['time_s'], source_index=record['source_index'], blade=record['blade'],
            passage=record['passage'], estimate=row, separation=separation_metrics(labels, predicted),
            point_labels=predicted, blade_assignment=result['blade_assignment'],
            tip_truth_world=truth['tip_world'], tip_truth_clearance_m=truth['tip_clearance_m'],
            baseline_clearance_m=baseline, distal_surface_truth_m=reference, fitted_surface_clearance_m=fitted_surface,
            baseline_tip_error_m=baseline-truth['tip_clearance_m'] if baseline is not None else None,
            tip_clearance_error_m=estimate-truth['tip_clearance_m'] if estimate is not None else None,
            tip_position_error_m=float(np.linalg.norm(np.asarray(row['tip_world'])-truth['tip_world'])) if estimate is not None else None,
            baseline_surface_error_m=baseline-reference if baseline is not None and reference is not None else None,
            fitted_surface_error_m=fitted_surface-reference if fitted_surface is not None and reference is not None else None,
            interval95_contains_truth=(row['clearance_interval95_m'][0] <= truth['tip_clearance_m'] <= row['clearance_interval95_m'][1]) if estimate is not None else None))
        if index % 100 == 0:
            print(f'X100_CLEARANCE_ESTIMATION {index+1}/{len(data["frames"])}', flush=True)
    def summarize(selected):
        n = len(selected)
        common = [r for r in selected if r['baseline_tip_error_m'] is not None and r['tip_clearance_error_m'] is not None]
        surface_common = [r for r in selected if r['baseline_surface_error_m'] is not None and r['fitted_surface_error_m'] is not None]
        supported = [r for r in selected if r['interval95_contains_truth'] is not None]
        return dict(N=n, baseline_tip=error_metrics([r['baseline_tip_error_m'] for r in selected], n),
            estimated_tip=error_metrics([r['tip_clearance_error_m'] for r in selected], n),
            tip_position=error_metrics([r['tip_position_error_m'] for r in selected], n),
            baseline_surface=error_metrics([r['baseline_surface_error_m'] for r in selected], n),
            fitted_surface=error_metrics([r['fitted_surface_error_m'] for r in selected], n),
            common_tip_frames=dict(baseline=error_metrics([r['baseline_tip_error_m'] for r in common], len(common)),
                estimate=error_metrics([r['tip_clearance_error_m'] for r in common], len(common))),
            common_surface_frames=dict(baseline=error_metrics([r['baseline_surface_error_m'] for r in surface_common], len(surface_common)),
                estimate=error_metrics([r['fitted_surface_error_m'] for r in surface_common], len(surface_common))),
            interval95_coverage=float(np.mean([r['interval95_contains_truth'] for r in supported])) if supported else None,
            status_counts=dict(Counter(r['estimate']['status'] for r in selected)))
    root = Path(__file__).resolve().parents[2]
    code_paths = [Path(__file__), Path(clearance.__file__), Path(tracking.__file__)]
    return dict(schema='x100-clearance-evaluation.v1',
        **{k: v for k, v in data.items() if k not in ('frames', 'shapes', 'meshes', 'schema')},
        inference_code_identity={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths},
        shape_priors={b: {k: v for k, v in shape.items() if k in ('source', 'rest_sha256', 'tip_local_m')} for b, shape in data['shapes'].items()},
        inference_config=asdict(tracker.config), overall=summarize(rows),
        blades=[dict(blade=b, **summarize([r for r in rows if r['blade'] == b])) for b in (1, 2, 3)],
        passage_metrics=[dict(blade=b, passage=p, **summarize([r for r in rows if r['blade'] == b and r['passage'] == p]))
            for b, windows in enumerate(data['passage_windows'], 1) for p in range(len(windows))],
        separation=separation_metrics(truth_all, predicted_all), separation_roi=separation_metrics(truth_roi, predicted_roi),
        definitions=dict(tip='Independent unloaded structural reference tip transported by saved geometry; includes prebend',
            surface='Minimum ROI-clipped distal surface distance; distinct from structural tip clearance',
            baseline='coverage.py minimum ROI non-false-alarm point distance, reproduced within 1e-8 m on every frame',
            inference_domain='All engineering detections; root-coordinate distal fit. ROI is only the legacy output metric.',
            truth='Evaluation only: >=90% material-class power per reconstructed resolution cell; mixed cells unresolved; noise-only OTHER',
            quality='Observed detector power and observed power/noise ratio only; true signal SNR/power and false_alarm excluded from inference',
            uncertainty='Approximate uncalibrated 95% intervals, empirical coverage reported; no hardware or safety claim'), frames=rows)


def detection_main(args):
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root/'blender_frontend'), str(root)]
    from wfrl_blender import atomic_json
    if args.observations:
        with gzip.open(args.observations, 'rt', encoding='utf-8') as stream:
            data = json.load(stream)
        if data.get('schema') != 'x100-clearance-observations.v1':
            raise ValueError('Unsupported observation cache')
    else:
        data = collect_detection_observations(args)
    if args.save_observations:
        args.save_observations.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(args.save_observations, 'xt', encoding='utf-8') as stream:
            json.dump(data, stream, default=_json_value, allow_nan=False)
    report = evaluate_detection_observations(data)
    # Convert NumPy values before the common atomic writer checks strict JSON.
    report = json.loads(json.dumps(report, default=_json_value, allow_nan=False))
    atomic_json.write_json(args.output, report, mkdir=True)
    print('X100_CLEARANCE_COMPLETE', args.output, json.dumps(report['overall']), flush=True)


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
    parser.add_argument('--detections', action='store_true', help='Separate engineering echoes and estimate structural-tip clearance')
    parser.add_argument('--observations', type=Path, help='Replay an explicit .json.gz engineering observation cache without Blender')
    parser.add_argument('--save-observations', type=Path, help='Save observables and separate evaluation truth for reproducible offline analysis')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    if args.output.exists() or not 1 <= args.candidates <= 4096:
        raise ValueError('Invalid budget or output already exists')
    if args.save_observations and args.save_observations.exists():
        raise FileExistsError(args.save_observations)
    if args.detections:
        detection_main(args)
        return
    if args.observations or args.save_observations:
        raise ValueError('Observation caches require --detections')
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
