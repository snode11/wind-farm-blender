"""Compare constrained installation candidates using complete source-grid passages."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--roll', type=float, nargs='+', default=[0.])
    parser.add_argument('--pitch', type=float, nargs='+', default=[60., 75., 82.5, 90.])
    parser.add_argument('--yaw', type=float, nargs='+', default=[150., 180., 210.])
    parser.add_argument('--location', type=float, nargs=3, action='append', help='Additional allowed nacelle-local housing position')
    parser.add_argument('--budgets', type=int, nargs='+', default=[1024, 2048])
    parser.add_argument('--seeds', type=int, nargs='+', default=[100, 101, 102])
    parser.add_argument('--minimum-points', type=int, default=3)
    parser.add_argument('--distal-span', type=float, default=.8)
    parser.add_argument('--half-window-deg', type=float, default=30.)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if (args.output.exists() or not all(1 <= n <= 4096 for n in args.budgets)
            or len(args.budgets) * len(args.seeds) > 32 or any(s < 0 for s in args.seeds)
            or args.minimum_points < 1 or not 0 < args.distal_span < 1
            or not 0 < args.half_window_deg < 180):
        raise ValueError('Invalid search profile or output already exists')
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / 'blender_frontend'), str(root)]
    import bpy
    import wfrl_blender as addon
    from wfrl_blender import farm_flex
    from wfrl_blender.radar_x100.scene import SceneSampler, installation_matrix, nacelle_side_installation, ORIGINAL_LOCATION, ORIGINAL_ROTATION
    from wfrl.radar_x100 import installation, simulation, sampling
    from scripts.radar_x100.coverage import passages
    from scripts.radar_x100.export_simulation import sample_times
    addon.register()
    try:
        source_files = [Path(__file__), Path(simulation.__file__), Path(sampling.__file__),
            Path(installation.__file__), root/'blender_frontend/wfrl_blender/radar_x100/scene.py']
        code_identity = {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
        addon.load_demo_scene(args.package or farm_flex.default_package())
        sampler = SceneSampler(farm_flex.active_for(bpy.context.scene), mount_from_sensor=np.eye(4))
        side_location, side_rotation = nacelle_side_installation(sampler)
        locations = [('ORIGINAL', ORIGINAL_LOCATION), ('NACELLE_SIDE', side_location)]
        locations += [(f'CUSTOM_{i}', p) for i, p in enumerate(args.location or [])]
        choices = installation.candidates(locations, args.roll, args.pitch, args.yaw,
            [('ORIGINAL', ORIGINAL_LOCATION, ORIGINAL_ROTATION), ('NACELLE_SIDE', side_location, side_rotation)])
        poses = [installation_matrix(c['location_m'], c['rotation_deg']) for c in choices]
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
        times = np.array(list(sample_times(*sampler.interval, 30)))
        phase = np.interp(times, sampler.flex.times, sampler.flex.poses[:, sampler.k, 1])
        windows = [passages(times, phase + 120 * b, args.half_window_deg) for b in range(3)]
        profiles = [(seed, budget) for seed in dict.fromkeys(args.seeds) for budget in sorted(set(args.budgets))]
        counts = np.full((len(choices), len(profiles), len(times), 3), -1, dtype=np.int32)
        fov_count = np.zeros((len(choices), len(profiles)), dtype=np.int64)
        blocked_count = fov_count.copy()
        errors = []
        for frame_index, stamp in enumerate(times):
            try:
                sample = sampler.sample(float(stamp))
                nacelle = sampler.mount_world_at(float(stamp))
                world = {seed: plan.points(sample) for seed, plan in plans.items()}
                for c, pose in enumerate(poses):
                    sensor = nacelle @ pose
                    observed = replace(sample, world_from_sensor=sensor)
                    for seed, plan in plans.items():
                        xyz = (world[seed] - sensor[:3, 3]) @ sensor[:3, :3]
                        gate = simulation.in_fov(xyz)
                        mask = np.zeros(len(xyz), dtype=bool)
                        # The same geometry BVH is reused by every pose at this source time.
                        mask[gate] = sampler.visibility(observed, world[seed][gate])
                        for p, (profile_seed, budget) in enumerate(profiles):
                            if seed != profile_seed:
                                continue
                            for blade in range(3):
                                counts[c, p, frame_index, blade] = np.sum(mask[:budget] & (labels[seed][:budget] == blade))
                            target = labels[seed][:budget] >= 0
                            fov_count[c, p] += np.sum(gate[:budget] & target)
                            blocked_count[c, p] += np.sum(gate[:budget] & ~mask[:budget] & target)
            except Exception as exc:
                counts[:, :, frame_index] = -1
                errors.append(dict(source_time_s=float(stamp), reason=str(exc)))
            if frame_index % 300 == 0:
                print(f'X100_SEARCH_PROGRESS {frame_index + 1}/{len(times)}', flush=True)
        rows = []
        for c, choice in enumerate(choices):
            variants = [dict(seed=seed, budget=budget, coverage=installation.summarize(counts[c, p], windows, args.minimum_points),
                occluded_fraction=float(blocked_count[c, p] / fov_count[c, p]) if fov_count[c, p] else None)
                for p, (seed, budget) in enumerate(profiles)]
            rows.append(choice | dict(profiles=variants))
        ranked = installation.rank(rows)
        winner = ranked[0]['id'] if not errors and ranked[0]['score'] and ranked[0]['score'][0] > 0 else None
        report = dict(schema=installation.SCHEMA, source_id=sampler.source_id, geometry_id=sampler.geometry_id,
            sampling_method='AREA', reference_time_s=sampler.interval[0], source_interval_s=sampler.interval,
            sampling_algorithm=sampling.AREA_ALGORITHM, code_identity=code_identity,
            source_grid_hz=30, source_frames=len(times), minimum_points=args.minimum_points,
            distal_span=args.distal_span, half_window_deg=args.half_window_deg,
            sampling_plans={str(seed): plan.identity for seed, plan in plans.items()},
            ranking=['worst profile/blade coverage', 'worst profile/passage coverage', 'longest known gap', 'mean coverage', 'occlusion'],
            candidate_scope='finite declared positions and XYZ angles; mechanical/RF feasibility not evaluated',
            truth_boundary='material labels and rotor phase used only by offline coverage evaluation',
            errors=errors, winner_id=winner, candidates=ranked)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)
        print('X100_SEARCH_COMPLETE', str(args.output), 'winner', winner, flush=True)
        if errors:
            raise RuntimeError('Coverage contains unknown frames; no installation is accepted')
    finally:
        addon.unregister()


if __name__ == '__main__':
    main()
