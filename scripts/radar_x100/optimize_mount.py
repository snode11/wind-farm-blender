"""Compare constrained installation candidates using complete source-grid passages."""
import argparse
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import numpy as np


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / 'blender_frontend'), str(root)]
    from scripts.radar_x100.coverage import (passage_grid, add_detection_arguments,
                                             detection_configuration, evaluate_detection_grid)
    from wfrl.radar_x100 import installation, simulation, sampling, detection
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--roll', type=float, nargs='+', default=[0.], help='XYZ Euler angles for additional --location entries')
    parser.add_argument('--pitch', type=float, nargs='+', default=[70., 76., 90.])
    parser.add_argument('--yaw', type=float, nargs='+', default=[180.])
    parser.add_argument('--lateral-offsets', type=float, nargs='+', default=[0., 1., 2.])
    parser.add_argument('--tilt', type=float, nargs=2, action='append', help='Longitudinal and lateral down-axis tilts, within +/-20 degrees')
    parser.add_argument('--candidate-ids', nargs='+', help='Evaluate this explicit subset of the generated catalog')
    parser.add_argument('--passages-per-blade', type=int, default=0, help='0: every complete passage; positive: first N per blade, screening only')
    parser.add_argument('--location', type=float, nargs=3, action='append', help='Additional nacelle-local position, subject to mechanical screening')
    parser.add_argument('--budgets', type=int, nargs='+', default=[1024, 2048])
    parser.add_argument('--seeds', type=int, nargs='+', default=[100, 101, 102])
    parser.add_argument('--minimum-points', type=int, default=3)
    parser.add_argument('--distal-span', type=float, default=.8)
    parser.add_argument('--half-window-deg', type=float, default=30.)
    parser.add_argument('--roi', type=float, nargs=6, default=installation.DEFAULT_ROI,
                        help='Nacelle-local analysis bounds: xmin ymin zmin xmax ymax zmax')
    add_detection_arguments(parser)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else None)
    if (args.output.exists() or not all(1 <= n <= 4096 for n in args.budgets)
            or len(args.budgets) * len(args.seeds) > 32 or any(s < 0 for s in args.seeds)
            or args.minimum_points < 3 or len(args.seeds) * len(args.noise_seeds) > 32 or not 0 < args.distal_span < 1
            or not 0 < args.half_window_deg < 180 or args.passages_per_blade < 0):
        raise ValueError('Invalid search profile or output already exists')
    config = detection_configuration(args)
    metadata = installation.profile_metadata(args.roi)
    import bpy
    import wfrl_blender as addon
    from wfrl_blender import farm_flex, atomic_json
    from wfrl_blender.radar_x100.scene import SceneSampler, installation_matrix, installation_catalog
    from scripts.radar_x100.export_simulation import sample_times
    addon.register()
    try:
        source_files = [Path(__file__), Path(simulation.__file__), Path(sampling.__file__), Path(detection.__file__),
            Path(installation.__file__), root/'blender_frontend/wfrl_blender/radar_x100/scene.py',
            root/'scripts/radar_x100/coverage.py']
        code_identity = {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
        addon.load_demo_scene(args.package or farm_flex.default_package())
        sampler = SceneSampler(farm_flex.active_for(bpy.context.scene), mount_from_sensor=np.eye(4))
        options = dict(lateral_offsets=args.lateral_offsets)
        if args.tilt:
            options['tilts'] = args.tilt
        catalog = installation_catalog(sampler, **options)
        locations = [(f'CUSTOM_{i}', p) for i, p in enumerate(args.location or [])]
        choices = installation.candidates(locations, args.roll, args.pitch, args.yaw,
            [*catalog['baselines'], *catalog['search_candidates']])
        if args.candidate_ids:
            if set(args.candidate_ids) - {c['id'] for c in choices}:
                raise ValueError('Candidate IDs are not in the declared search catalog')
            choices = [c for c in choices if c['id'] in args.candidate_ids]
        poses = [installation_matrix(c['location_m'], c['rotation_deg']) for c in choices]
        mechanics = [sampler.installation_constraints(pose) for pose in poses]
        motion = [[] for _ in choices]
        plans = {seed: sampler.sampling_plan(max(args.budgets), seed) for seed in args.seeds}
        labels = {}
        for seed, plan in plans.items():
            label = np.full(len(plan.triangle_ids), -1, dtype=int)
            for blade, row in enumerate(sampler.blades):
                lo, hi = sampler.triangle_offsets[blade:blade+2]
                selected = np.flatnonzero((plan.triangle_ids >= lo) & (plan.triangle_ids < hi))
                rest = row[1]
                faces = sampler.parts[blade][1][plan.triangle_ids[selected] - lo]
                span = np.einsum('ni,ni->n', rest[faces, 2], plan.barycentric[selected])
                normalized = (span - rest[:, 2].min()) / np.ptp(rest[:, 2])
                label[selected[normalized >= args.distal_span]] = blade
            labels[seed] = label
        source_times = np.array(list(sample_times(*sampler.interval, 30)))
        phase = np.interp(source_times, sampler.flex.times, sampler.flex.poses[:, sampler.k, 1])
        times, source_indices, windows = passage_grid(source_times, phase, args.half_window_deg, args.passages_per_blade)
        source_lookup = {source: i for i, source in enumerate(source_indices)}
        profiles = [(seed, budget) for seed in dict.fromkeys(args.seeds) for budget in sorted(set(args.budgets))]
        counts = np.full((len(choices), len(profiles), len(times), 3), -1, dtype=np.int32)
        fov_count = np.zeros((len(choices), len(profiles)), dtype=np.int64)
        blocked_count = fov_count.copy()
        errors = []
        for source_index, stamp in enumerate(source_times):
            frame_index = source_lookup.get(source_index)
            try:
                sample = sampler.sample(float(stamp))
                nacelle = sampler.mount_world_at(float(stamp))
                for c, row in enumerate(sampler.motion_clearances(sample, poses)):
                    motion[c].append(row)
                if frame_index is None:
                    continue
                world = {seed: plan.points(sample) for seed, plan in plans.items()}
                for c, pose in enumerate(poses):
                    if mechanics[c]['status'] != 'PASS_RESEARCH' and choices[c]['location_name'] not in {'ORIGINAL', 'NACELLE_FRONT_DOWN', 'NACELLE_REAR_DOWN'}:
                        continue
                    sensor = nacelle @ pose
                    observed = replace(sample, world_from_sensor=sensor)
                    for seed, plan in plans.items():
                        xyz = (world[seed] - sensor[:3, 3]) @ sensor[:3, :3]
                        gate = simulation.in_fov(xyz)
                        local = (world[seed] - nacelle[:3, 3]) @ nacelle[:3, :3]
                        inside = (local >= args.roi[:3]).all(axis=1) & (local <= args.roi[3:]).all(axis=1)
                        mask = np.zeros(len(xyz), dtype=bool)
                        # The same geometry BVH is reused by every pose at this source time.
                        mask[gate] = sampler.visibility(observed, world[seed][gate])
                        for p, (profile_seed, budget) in enumerate(profiles):
                            if seed != profile_seed:
                                continue
                            for blade in range(3):
                                counts[c, p, frame_index, blade] = np.sum(mask[:budget] & inside[:budget] & (labels[seed][:budget] == blade))
                            target = (labels[seed][:budget] >= 0) & inside[:budget]
                            fov_count[c, p] += np.sum(gate[:budget] & target)
                            blocked_count[c, p] += np.sum(gate[:budget] & ~mask[:budget] & target)
            except Exception as exc:
                if frame_index is not None:
                    counts[:, :, frame_index] = -1
                for rows in motion:
                    if len(rows) == source_index:
                        rows.append(None)
                errors.append(dict(source_time_s=float(stamp), reason=str(exc)))
            if source_index % 300 == 0:
                print(f'X100_SEARCH_GEOMETRY {source_index + 1}/{len(source_times)}', flush=True)
        rows = []
        truth_cache = {}
        for c, choice in enumerate(choices):
            variants = [dict(seed=seed, budget=budget, geometric_coverage=installation.summarize(counts[c, p], windows, args.minimum_points),
                distal_visible_points=counts[c, p].tolist(),
                occluded_fraction=float(blocked_count[c, p] / fov_count[c, p]) if fov_count[c, p] else None)
                for p, (seed, budget) in enumerate(profiles)]
            mechanics[c]['motion_clearance'] = installation.summarize_motion(motion[c])
            if mechanics[c]['status'] == 'PASS_RESEARCH':
                mechanics[c]['status'] = mechanics[c]['motion_clearance']['status']
            current = sampler.with_installation('NACELLE', poses[c])
            admissible = (mechanics[c]['status'] == 'PASS_RESEARCH'
                          and mechanics[c]['motion_clearance']['status'] == 'PASS_RESEARCH')
            evaluate = admissible or choice['location_name'] in {'ORIGINAL', 'NACELLE_FRONT_DOWN', 'NACELLE_REAR_DOWN'}
            print(f"X100_SEARCH_DETECTIONS {c + 1}/{len(choices)} {choice['id']} {choice['location_name']} {mechanics[c]['status']}", flush=True)
            detection_profiles = [evaluate_detection_grid(current, times, roi=args.roi,
                integration_budget=args.integration_budget, output_point_limit=args.output_point_limit,
                sampling_seed=seed, noise_seed=noise_seed, config=config, min_points=args.detection_min_points,
                session_id='installation-' + choice['id'], passage_windows=windows, source_indices=source_indices,
                distal_span=args.distal_span, truth_cache=truth_cache)
                for seed in dict.fromkeys(args.seeds) for noise_seed in dict.fromkeys(args.noise_seeds)] if evaluate else []
            rows.append(choice | dict(profiles=variants, mechanical=mechanics[c], detection_profiles=detection_profiles,
                detection_evaluation='EVALUATED' if evaluate else 'NOT_EVALUATED_MECHANICAL_CONSTRAINTS'))
            atomic_json.write_json(args.output.with_suffix('.partial.json'), dict(completed_candidates=rows,
                total_candidates=len(choices)), mkdir=True)
        ranked = installation.rank(rows)
        best = ranked[0] if ranked[0]['score'] is not None else None
        winner = best['id'] if best and not errors and not args.passages_per_blade else None
        continuous = bool(winner and all(p['passage_detection_support']['continuous_support'] for p in best['detection_profiles']))
        report = dict(schema=installation.SCHEMA, source_id=sampler.source_id, geometry_id=sampler.geometry_id,
            **metadata, original_assessment=catalog['original_assessment'], shell_geometry=catalog['shell_geometry'],
            peer_placement_references=catalog['peer_placement_references'],
            sampling_method='AREA', reference_time_s=sampler.interval[0], source_interval_s=sampler.interval,
            sampling_algorithm=sampling.AREA_ALGORITHM, code_identity=code_identity,
            source_grid_hz=30, source_frames=len(source_times), grid_times_s=source_times.tolist(),
            evaluation_grid_times_s=times.tolist(), evaluation_source_indices=source_indices, passage_windows=windows,
            evaluation_scope='ALL_COMPLETE_PASSAGES' if not args.passages_per_blade else 'FIRST_COMPLETE_PASSAGES_SCREENING_ONLY',
            passages_per_blade=args.passages_per_blade, minimum_points=args.minimum_points,
            distal_span=args.distal_span, half_window_deg=args.half_window_deg,
            sampling_plans={str(seed): plan.identity for seed, plan in plans.items()},
            ranking=['mechanical research checks and known passage detection/tower-error evidence required',
                'worst passage detection fraction', 'longest known missed detection duration',
                'exact nadir detection support', 'worst blade detection fraction',
                'tower-distance RMSE', 'mean non-false-alarm ROI detections per frame', 'geometric coverage tie-break only'],
            candidate_scope='finite exterior positions and XYZ angles under declared research allowances; no physical/RF approval',
            truth_boundary='material labels and rotor phase used only by offline coverage evaluation',
            errors=errors, winner_id=winner, best_evaluated_id=best['id'] if best else None,
            recommended_observation_mode='DETECTIONS' if continuous else 'GEOMETRY',
            default_mode_basis='Every complete passage grid frame and exact nadir has support across all evaluated profiles'
                if continuous else 'Continuous detection support over every complete source passage and exact nadir is not established',
            candidates=ranked)
        if args.output.exists():
            raise FileExistsError(args.output)
        atomic_json.write_json(args.output, report, mkdir=True)
        print('X100_SEARCH_COMPLETE', str(args.output), 'winner', winner, flush=True)
        if errors:
            raise RuntimeError('Coverage contains unknown frames; no installation is accepted')
    finally:
        addon.unregister()


if __name__ == '__main__':
    main()
