"""Read-only, score-only residual diagnosis on every valid saved TLS pair.

Truth-oracle counterfactuals below explain a recorded error algebraically. They
are not estimators, compensations, acceptance tests, or causal attribution.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import platform
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.lidar.compare_dual_beam_methods import (
    NUMERICAL_TOLERANCE_M, NEAR_SLANT_RANGE_M, independent_clearance,
    load_verified, statistics, structural_reference_points, tower_at,
)
from wfrl.camera_video.data import blade_root_frame
from wfrl.lidar.dual_beam_replay import digest
from wfrl.lidar.physics import first_hit


def _vector(value):
    value = np.asarray(value, float)
    if value.shape != (3,) or not np.isfinite(value).all():
        raise ValueError('Expected a finite three-vector')
    return value


def _unit(value):
    value = _vector(value)
    if not math.isclose(float(np.linalg.norm(value)), 1., abs_tol=1e-10):
        raise ValueError('Expected a unit direction')
    return value


def angle_degrees(a, b):
    a, b = _vector(a), _vector(b)
    norms = float(np.linalg.norm(a)*np.linalg.norm(b))
    if norms <= 1e-12:
        raise ValueError('Angle has a zero direction')
    return float(np.degrees(np.arccos(np.clip(float(a@b)/norms, -1., 1.))))


def descriptive(values):
    """Finite scalar observations, with no probabilistic or causal claim."""
    values = sorted(float(value) for value in values)
    if not all(math.isfinite(value) for value in values):
        raise ValueError('Nonfinite diagnostic value')
    n = len(values)
    return dict(count=n, mean=sum(values)/n if n else None,
                minimum=values[0] if n else None, maximum=values[-1] if n else None,
                p95=values[math.ceil(.95*n)-1] if n else None,
                p95_method='nearest-rank')


def geometric_decomposition(hub, direction, fixed_length, reference_tip, clearance):
    """Exact position and own-height clearance direction/length decomposition.

    C(H+l*u), C(H+L*v) use truth l/v, so these are score-only oracles. The
    scalar interaction includes tower geometry and own-height section changes;
    it is not an independently measured physical source of error.
    """
    hub, direction, reference_tip = _vector(hub), _unit(direction), _vector(reference_tip)
    fixed_length = float(fixed_length)
    actual_length = float(np.linalg.norm(reference_tip-hub))
    if not math.isfinite(fixed_length) or fixed_length <= 0 or actual_length <= 1e-12:
        raise ValueError('Invalid radial length')
    true_direction = (reference_tip-hub)/actual_length
    actual_length_point = hub+actual_length*direction
    true_direction_point = hub+fixed_length*true_direction
    estimate = hub+fixed_length*direction
    c_ref, c_direction, c_length, c_estimate = (
        float(clearance(point)) for point in
        (reference_tip, actual_length_point, true_direction_point, estimate)
    )
    if not all(math.isfinite(c) for c in (c_ref, c_direction, c_length, c_estimate)):
        raise ValueError('Nonfinite oracle clearance')
    direction_vector = actual_length*(direction-true_direction)
    length_vector = (fixed_length-actual_length)*true_direction
    interaction_vector = (fixed_length-actual_length)*(direction-true_direction)
    direction_error = c_direction-c_ref
    length_error = c_length-c_ref
    interaction_error = c_estimate-c_direction-c_length+c_ref
    error = c_estimate-c_ref
    return dict(
        evidence_state='TRUTH_ORACLE_DIAGNOSTIC_ONLY',
        actual_hub_tip_radial_length_m=actual_length,
        fixed_minus_actual_radial_length_m=fixed_length-actual_length,
        true_hub_tip_direction=true_direction.tolist(),
        estimated_direction_to_true_chord_deg=angle_degrees(direction, true_direction),
        reference_clearance_m=c_ref, estimate_clearance_m=c_estimate, error_m=error,
        actual_length_estimated_direction=dict(point_m=actual_length_point.tolist(),
            clearance_m=c_direction, error_m=direction_error),
        fixed_length_true_direction=dict(point_m=true_direction_point.tolist(),
            clearance_m=c_length, error_m=length_error),
        clearance_contributions_m=dict(direction=direction_error, length=length_error,
            interaction=interaction_error, closure_residual=error-direction_error-length_error-interaction_error),
        position_contributions_m=dict(direction=direction_vector.tolist(), length=length_vector.tolist(),
            interaction=interaction_vector.tolist(),
            closure_residual=(estimate-reference_tip-direction_vector-length_vector-interaction_vector).tolist()),
        definition='direction=l*(u-v); length=(L-l)*v; interaction=(L-l)*(u-v). Scalar direction=C(H+l*u)-C(T), length=C(H+L*v)-C(T), interaction=C(H+L*u)-C(H+l*u)-C(H+L*v)+C(T); every point selects its own global-height tower section.',
    )


def surface_residuals(hub, direction, true_direction, observations):
    """Rebuild measured points from allowed raw fields; never use saved hits."""
    hub, direction, true_direction = _vector(hub), _unit(direction), _unit(true_direction)
    result = {}
    for name in ('S2', 'S3'):
        observation = observations[name]
        distance = float(observation['slant_range_m'])
        if not math.isfinite(distance) or distance <= 0:
            raise ValueError('Invalid measured range')
        point = _vector(observation['origin_m'])+distance*_unit(observation['direction'])
        q = point-hub
        chord_projection = float(q@true_direction)
        chord_offset = q-chord_projection*true_direction
        tls_projection = float(q@direction)
        tls_offset = q-tls_projection*direction
        result[name] = dict(point_m=point.tolist(),
            true_chord_projected_radius_m=chord_projection,
            surface_offset_from_true_hub_tip_chord_m=chord_offset.tolist(),
            surface_offset_from_true_hub_tip_chord_norm_m=float(np.linalg.norm(chord_offset)),
            fitted_direction_projected_radius_m=tls_projection,
            fitted_line_residual_m=tls_offset.tolist(),
            fitted_line_residual_norm_m=float(np.linalg.norm(tls_offset)))
    result['fitted_line_sum_squared_residual_m2'] = sum(
        result[name]['fitted_line_residual_norm_m']**2 for name in ('S2', 'S3'))
    return result


def triangle_station_diagnostic(origin, direction, distance, surface, triangles,
                                station_count, vertices_per_station, rest_local, root_hub, root_axes,
                                rigid_tip_local):
    """Match first-hit triangle; map its actual vertex indices to source stations.

    No nearest-centroid or range-based guessed station is used. Barycentric
    interpolation refers to that same material triangle in the unloaded source.
    """
    if len(surface) != station_count*vertices_per_station or rest_local.shape != np.asarray(surface).shape:
        raise ValueError('Station topology shape mismatch')
    hit = first_hit(origin, direction, surface, triangles)
    if hit is None or abs(hit[0]-distance) > NUMERICAL_TOLERANCE_M:
        raise ValueError('Diagnostic first-hit range differs from recorded observation')
    triangle_index = int(hit[2])
    vertices = np.asarray(triangles[triangle_index], int)
    if np.any(vertices < 0) or np.any(vertices >= len(surface)):
        raise ValueError('Invalid matched triangle topology')
    stations = vertices//vertices_per_station
    if int(stations.max()-stations.min()) > 1:
        raise ValueError('Matched triangle crosses nonadjacent source stations')
    tri = np.asarray(surface)[vertices]
    edges = np.column_stack((tri[1]-tri[0], tri[2]-tri[0]))
    uv, _, rank, _ = np.linalg.lstsq(edges, np.asarray(hit[1])-tri[0], rcond=None)
    weights = np.array([1.-uv.sum(), *uv])
    if rank != 2 or np.min(weights) < -1e-8 or np.max(weights) > 1.+1e-8:
        raise ValueError('First-hit triangle barycentric coordinates invalid')
    material_local = weights@rest_local[vertices]
    rigid_point = root_hub+root_axes@material_local
    tip_direction = rigid_tip_local/np.linalg.norm(rigid_tip_local)
    rigid_offset_local = material_local-(material_local@tip_direction)*tip_direction
    rest_centers = rest_local.reshape(station_count, vertices_per_station, 3).mean(axis=1)
    return dict(
        evidence_state='SOURCE_TRIANGLE_MATERIAL_DIAGNOSTIC_ONLY',
        first_hit_triangle_index=triangle_index, triangle_vertex_indices=vertices.tolist(),
        source_station_indices_zero_based=stations.tolist(),
        source_station_neighborhood_one_based=sorted(set((stations+1).tolist())),
        source_station_neighborhood_reference_axial_m=rest_centers[sorted(set(stations.tolist())), 2].tolist(),
        barycentric_weights=weights.tolist(),
        source_material_reference_axial_m=float(weights@rest_centers[stations, 2]),
        material_point_unloaded_blade_root_local_m=material_local.tolist(),
        same_material_point_rigid_prebent_world_m=rigid_point.tolist(),
        loaded_minus_rigid_material_point_world_m=(np.asarray(hit[1])-rigid_point).tolist(),
        loaded_minus_rigid_material_point_norm_m=float(np.linalg.norm(np.asarray(hit[1])-rigid_point)),
        unloaded_surface_offset_from_prebent_tip_chord_local_m=rigid_offset_local.tolist(),
        unloaded_surface_offset_from_prebent_tip_chord_norm_m=float(np.linalg.norm(rigid_offset_local)),
        first_hit_range_residual_m=float(hit[0]-distance),
        mapping='source vertex index // vertices_per_station; first positive intersection on recorded blade; station numbers are AeroDyn surface neighborhoods, not measured structural states',
    )


def rigid_reference_diagnostic(hub, axes, tip_local, reference_tip, clearance):
    """Instantaneous rigid frame, independent unloaded prebend, then flex truth.

    A straight axial reference removes the transverse components of tip_local.
    This controlled geometric comparison is not a new machine model or replay.
    """
    straight_local = np.array([0., 0., tip_local[2]])
    straight = hub+axes@straight_local
    prebent = hub+axes@tip_local
    c_straight, c_prebent, c_flexible = (float(clearance(point)) for point in (straight, prebent, reference_tip))
    local_truth = axes.T@(reference_tip-hub)
    return dict(evidence_state='SOURCE_REFERENCE_GEOMETRY_DIAGNOSTIC_ONLY',
        rigid_straight_tip_m=straight.tolist(), rigid_prebent_tip_m=prebent.tolist(),
        rigid_prebent_tip_blade_root_local_m=tip_local.tolist(),
        flexible_true_tip_blade_root_local_m=local_truth.tolist(),
        prebend_tip_offset_blade_root_local_m=(tip_local-straight_local).tolist(),
        flexible_minus_rigid_prebent_blade_root_local_m=(local_truth-tip_local).tolist(),
        flexible_minus_rigid_prebent_tip_norm_m=float(np.linalg.norm(local_truth-tip_local)),
        straight_to_prebent_chord_angle_deg=angle_degrees(straight-hub, prebent-hub),
        prebent_to_flexible_chord_angle_deg=angle_degrees(prebent-hub, reference_tip-hub),
        straight_to_flexible_chord_angle_deg=angle_degrees(straight-hub, reference_tip-hub),
        own_height_clearance_m=dict(straight=c_straight, prebent=c_prebent, flexible=c_flexible),
        own_height_clearance_differences_m=dict(prebend_minus_straight=c_prebent-c_straight,
            flexible_minus_prebend=c_flexible-c_prebent, flexible_minus_straight=c_flexible-c_straight),
        scope='Same instantaneous root frame, pitch, azimuth, nacelle and layout. Independent unloaded structural tip provides prebend; saved terminal flexible transform provides truth only. Difference includes all saved blade deformation relative to that rigid frame and does not separate modal causes.',
    )


def _check_close(actual, expected, context):
    if not np.allclose(actual, expected, atol=NUMERICAL_TOLERANCE_M, rtol=0):
        raise ValueError(context+' differs from source geometry or raw reconstruction')


def _pitch_group(pitch):
    # Fixed descriptive bins, not selected from this segment's errors.
    if pitch < 0: return 'pitch < 0 deg'
    if pitch < 2: return '0 <= pitch < 2 deg'
    if pitch < 5: return '2 <= pitch < 5 deg'
    return 'pitch >= 5 deg'


def _phase_group(delta):
    if delta < -15 or delta > 15: return 'outside +/-15 deg'
    if delta < -5: return '-15 <= phase < -5 deg'
    if delta < 5: return '-5 <= phase < 5 deg'
    return '5 <= phase <= 15 deg'


def sample_diagnosis(source, index, row, reference_tip, tip_local, baseline_row=None):
    """Read a saved prediction and truth geometry; never call an estimator."""
    pair = row['reconstruction']
    if not pair['valid'] or pair.get('method') != 'hub-tls.v1':
        raise ValueError('Expected a valid explicitly identified TLS candidate')
    blade = int(pair['blade_id']); hub = _vector(pair['hub_m']); direction = _unit(pair['direction'])
    geometric_hub, axes = blade_root_frame(source.scalars, source.poses[index], blade, source.nacelles[index])
    geometric_hub += source.layout
    _check_close(hub, geometric_hub, 'Saved hub')
    tower = tower_at(source, index)
    clearance = lambda point: independent_clearance(point, tower, source.tower_triangles)
    oracle = geometric_decomposition(hub, direction, pair['effective_length_m'], reference_tip, clearance)
    _check_close(pair['tip_estimate_m'], hub+pair['effective_length_m']*direction, 'Saved TLS tip')
    _check_close(pair['clearance_estimate'], oracle['estimate_clearance_m'], 'Saved estimate clearance')
    _check_close(row['evaluation']['tip_reference_m'], reference_tip, 'Saved structural reference')
    _check_close(row['evaluation']['clearance_reference_m'], oracle['reference_clearance_m'], 'Saved reference clearance')
    residuals = surface_residuals(hub, direction, oracle['true_hub_tip_direction'], row['observations'])
    tr = source.transforms[index, blade-1]
    surface = (np.einsum('sij,svj->svi', tr[..., :3], source.blade_reference[blade-1])
               + tr[:, None, :, 3]).reshape(-1, 3)+source.layout
    rest_nacelle = np.column_stack((np.eye(3), np.zeros(3)))
    rest_hub, rest_axes = blade_root_frame(source.scalars, [0.]*6, blade, rest_nacelle)
    rest_local = (source.blade_reference[blade-1].reshape(-1, 3)-rest_hub)@rest_axes
    station_count, vertices_per_station = source.blade_reference.shape[1:3]
    for name in ('S2', 'S3'):
        observation = row['observations'][name]
        if observation['blade_id'] != blade:
            raise ValueError('Observed blade differs from TLS pair')
        residuals[name]['source_material'] = triangle_station_diagnostic(
            observation['origin_m'], observation['direction'], observation['slant_range_m'],
            surface, source.blade_triangles, station_count, vertices_per_station, rest_local,
            hub, axes, tip_local)
        _check_close(pair['p2_m' if name == 'S2' else 'p3_m'], residuals[name]['point_m'], 'Saved measured point')
        residuals[name]['surface_offset_from_true_chord_blade_root_local_m'] = (
            axes.T@np.asarray(residuals[name]['surface_offset_from_true_hub_tip_chord_m'])).tolist()
        residuals[name]['fitted_line_residual_blade_root_local_m'] = (
            axes.T@np.asarray(residuals[name]['fitted_line_residual_m'])).tolist()
    pose = source.poses[index]
    phase = float((pose[1]+(blade-1)*120.) % 360.-180.)
    pitch = float(pose[blade+2])
    group = 'near' if row['observations']['S3']['slant_range_m'] < NEAR_SLANT_RANGE_M else 'far'
    result = dict(turbine_id=source.turbine_id, time_s=float(row['time_s']), source_index=index,
        blade_id=blade, passage_id=row['passage_id'], s3_range_group=group,
        pitch_deg=pitch, azimuth_deg=float(pose[1]), blade_phase_to_down_deg=phase,
        pitch_group=_pitch_group(pitch), phase_group=_phase_group(phase),
        error_m=oracle['error_m'], estimated_direction_to_true_chord_deg=oracle['estimated_direction_to_true_chord_deg'],
        separation_m=float(np.linalg.norm(np.asarray(residuals['S3']['point_m'])-residuals['S2']['point_m'])),
        geometry=oracle, measured_surface=residuals,
        rigid_reference=rigid_reference_diagnostic(hub, axes, tip_local, reference_tip, clearance),
        baseline=None)
    if baseline_row is not None:
        if baseline_row['time_s'] != row['time_s'] or baseline_row['observations'] != row['observations']:
            raise ValueError('Baseline raw observation/time differs from candidate')
        old = baseline_row['reconstruction']
        if old['valid']:
            if old['blade_id'] != blade or old.get('method', 'hub-axis.v1') != 'hub-axis.v1':
                raise ValueError('Baseline valid blade/method differs')
            baseline_clearance = clearance(old['tip_estimate_m'])
            _check_close(old['clearance_estimate'], baseline_clearance, 'Saved baseline clearance')
            old_error = baseline_clearance-oracle['reference_clearance_m']
            result['baseline'] = dict(valid=True, error_m=old_error,
                absolute_error_change_m=abs(result['error_m'])-abs(old_error),
                candidate_absolute_error_worse=abs(result['error_m']) > abs(old_error),
                estimated_direction_to_true_chord_deg=angle_degrees(old['direction'], oracle['true_hub_tip_direction']))
        else:
            result['baseline'] = dict(valid=False, reason=old['reason'])
    return result


def summarize_samples(rows):
    errors = [row['error_m'] for row in rows]
    result = dict(metrics=statistics(errors),
        direction_to_true_chord_deg=descriptive(row['estimated_direction_to_true_chord_deg'] for row in rows),
        fixed_minus_actual_radial_length_m=descriptive(row['geometry']['fixed_minus_actual_radial_length_m'] for row in rows),
        actual_hub_tip_radial_length_m=descriptive(row['geometry']['actual_hub_tip_radial_length_m'] for row in rows),
        clearance_contributions_m={name:statistics(row['geometry']['clearance_contributions_m'][name] for row in rows)
                                   for name in ('direction', 'length', 'interaction')},
        measured_surface={name:{
            'offset_from_true_chord_norm_m':descriptive(row['measured_surface'][name]['surface_offset_from_true_hub_tip_chord_norm_m'] for row in rows),
            'fitted_line_residual_norm_m':descriptive(row['measured_surface'][name]['fitted_line_residual_norm_m'] for row in rows),
            'unloaded_offset_from_prebent_chord_norm_m':descriptive(row['measured_surface'][name]['source_material']['unloaded_surface_offset_from_prebent_tip_chord_norm_m'] for row in rows),
            'loaded_minus_rigid_material_point_norm_m':descriptive(row['measured_surface'][name]['source_material']['loaded_minus_rigid_material_point_norm_m'] for row in rows),
            'source_reference_axial_m':descriptive(row['measured_surface'][name]['source_material']['source_material_reference_axial_m'] for row in rows),
        } for name in ('S2','S3')},
        rigid_reference={name:descriptive(row['rigid_reference'][name] for row in rows)
                         for name in ('flexible_minus_rigid_prebent_tip_norm_m', 'straight_to_prebent_chord_angle_deg', 'prebent_to_flexible_chord_angle_deg')},
        rigid_reference_clearance_differences_m={name:descriptive(row['rigid_reference']['own_height_clearance_differences_m'][name] for row in rows)
            for name in ('prebend_minus_straight','flexible_minus_prebend','flexible_minus_straight')})
    common = [row for row in rows if row['baseline'] is not None and row['baseline']['valid']]
    if common:
        result['baseline_comparison'] = dict(common_samples=len(common),
            baseline=statistics(row['baseline']['error_m'] for row in common),
            candidate=statistics(row['error_m'] for row in common),
            absolute_error_worse_count=sum(row['baseline']['candidate_absolute_error_worse'] for row in common))
    return result


def diagnose_packages(candidate, baseline=None):
    config, manifest = candidate['overlay']['config'], candidate['overlay']['manifest']
    if config.get('reconstruction_method') != 'hub-tls.v1':
        raise ValueError('Residual diagnosis requires the TLS candidate package')
    if baseline is not None:
        for field in ('source_hashes','segment','source_fps','turbine_ids','reference_definition','clearance_definition'):
            if manifest[field] != baseline['overlay']['manifest'][field]:
                raise ValueError('Baseline '+field+' differs from candidate')
        if baseline['overlay']['config'].get('reconstruction_method','hub-axis.v1') != 'hub-axis.v1':
            raise ValueError('Baseline must be the old method')
        without_identity = lambda c:{k:v for k,v in c.items() if k not in ('reconstruction_method','algorithm_version')}
        if without_identity(config) != without_identity(baseline['overlay']['config']):
            raise ValueError('Baseline effective calibration differs from candidate')
    rows, source_grid_counts, valid_counts = [], {}, {}
    for turbine in manifest['turbine_ids']:
        source = candidate['sources'][turbine]
        reference = json.loads((source.path/'blade-reference.json').read_text())
        tip_local = _vector(reference['tip_local_m'])
        reference_points = structural_reference_points(source)
        samples = candidate['overlay']['results'][turbine]['samples']
        baseline_samples = baseline['overlay']['results'][turbine]['samples'] if baseline else None
        source_grid_counts[turbine] = len(samples)
        valid_counts[turbine] = sum(row['reconstruction']['valid'] for row in samples)
        if len(samples) != len(source.times) or (baseline_samples is not None and len(baseline_samples) != len(samples)):
            raise ValueError('Full source grid differs')
        for index, sample in enumerate(samples):
            if sample['time_s'] != float(source.times[index]):
                raise ValueError('Source time differs from candidate')
            if baseline_samples is not None and baseline_samples[index]['observations'] != sample['observations']:
                raise ValueError('Baseline raw observations differ, including invalid rows')
            if sample['reconstruction']['valid']:
                blade = sample['reconstruction']['blade_id']
                rows.append(sample_diagnosis(source, index, sample, reference_points[index,blade-1], tip_local,
                    baseline_samples[index] if baseline_samples is not None else None))
    grouped = defaultdict(lambda:defaultdict(list))
    for row in rows:
        labels = dict(turbine=row['turbine_id'], blade=str(row['blade_id']), s3_near_far=row['s3_range_group'],
            pitch=row['pitch_group'], phase_to_down=row['phase_group'],
            turbine_near_far=row['turbine_id']+'/'+row['s3_range_group'],
            turbine_blade=row['turbine_id']+'/blade'+str(row['blade_id']))
        for name in ('S2','S3'):
            stations = row['measured_surface'][name]['source_material']['source_station_neighborhood_one_based']
            labels[name+'_station_neighborhood'] = '-'.join(map(str,stations))
        for name,label in labels.items():
            grouped[name][label].append(row)
    pooled = summarize_samples(rows)
    p95 = pooled['metrics']['p95_abs_error_m']
    tail = [row for row in rows if p95 is not None and abs(row['error_m']) >= p95]
    worse = [row for row in rows if row['baseline'] is not None and row['baseline'].get('candidate_absolute_error_worse',False)]
    return dict(schema='wfrl.dual-beam-tls-residual-diagnostic.v1',
        status='SAVED_GEOMETRY_RESIDUAL_DIAGNOSIS_COMPLETE',
        performance_status='PENDING_ACCEPTANCE', physical_validation='NOT_RUN',
        evidence_states=dict(source='VERIFIED_IMMUTABLE_SAVED_GEOMETRY',
            prediction='READ_EXISTING_TLS_OUTPUT_NO_ESTIMATOR_CALL', scoring='RECOMPUTED_EACH_POINT_OWN_HEIGHT',
            counterfactuals='TRUTH_ORACLE_DIAGNOSTIC_ONLY', groups='DESCRIPTIVE_ONLY_NOT_CAUSAL'),
        source_hashes=manifest['source_hashes'], candidate_method=config['reconstruction_method'],
        source_grid_counts=source_grid_counts, valid_pair_counts=valid_counts,
        all_valid_pairs_included=len(rows)==sum(valid_counts.values()), diagnosed_pairs=len(rows),
        fixed_group_definitions=dict(s3_near_far='near iff S3 slant range < 12 m; all groups retained',
            pitch_edges_deg=[0.,2.,5.], phase_edges_deg=[-15.,-5.,5.,15.],
            station_neighborhood='matched first-hit triangle vertices divided by source vertices-per-station; no guessed structural station'),
        pooled=pooled, groups={name:{label:summarize_samples(values) for label,values in groups.items()} for name,groups in grouped.items()},
        tail_definition='all samples at or above pooled nearest-rank P95 absolute error; ties retained',
        tail_threshold_abs_error_m=p95, tail_sample_count=len(tail), tail_samples=sorted(tail,key=lambda row:abs(row['error_m']),reverse=True),
        worst_absolute_samples=sorted(rows,key=lambda row:abs(row['error_m']),reverse=True)[:20],
        lowest_signed_samples=sorted(rows,key=lambda row:row['error_m'])[:20],
        highest_signed_samples=sorted(rows,key=lambda row:row['error_m'],reverse=True)[:20],
        candidate_worse_sample_count=len(worse) if baseline else None,
        candidate_worse_samples=sorted(worse,key=lambda row:row['baseline']['absolute_error_change_m'],reverse=True),
        samples=rows,
        limitations=[
            'Only the original saved 117-177 s case is diagnosed; no independent condition or hardware validation is established.',
            'Small fitted-line residual does not prove an accurate flexible hub-tip direction; two surface points and hub constrain a line approximation.',
            'Oracle direction/length/interaction are algebraic score decompositions using true tip; they cannot be used as production compensation.',
            'Rigid prebent versus straight reference isolates controlled unloaded tip geometry in the same instantaneous root frame; flexible minus prebent combines all saved blade deformation, not identified modal causes.',
            'Station neighborhoods and material-point displacement use saved forward surface geometry only in this diagnostic; neither becomes an estimator input.',
            'Group differences are descriptive associations in one case; no pitch, azimuth, turbine or near/far correlation is claimed causal.',
            'Fresh sample validity, defaults, S1, holds and method selection are not modified; no subset is dropped or selected by truth error.',
        ])


def markdown_report(report):
    number = lambda value:'—' if value is None else f'{value:.6f}'
    pooled = report['pooled']['metrics']
    lines = ['# 双束 TLS 剩余误差诊断', '',
        '状态：保存网格诊断已运行；正式性能验收仍为 `PENDING_ACCEPTANCE`，现场验证 `NOT_RUN`。', '',
        f"全部有效配对均纳入：{report['diagnosed_pairs']} / {sum(report['valid_pair_counts'].values())}；没有按误差、机组或近远组剔除。", '',
        f"重算 MAE {number(pooled['mae_m'])} m，P95 {number(pooled['p95_abs_error_m'])} m，bias {number(pooled['bias_m'])} m；最大低估 {number(pooled['max_underestimate_m'])} m。", '',
        '每个反事实点均按自身全局高度重新截取同刻变形塔筒。以下方向、长度和交互项使用真实叶尖构造，仅用于评分后诊断；不可作为估计器输入或补偿。', '',
        '| 范围 | 样本 | bias / m | MAE / m | 方向项平均 / m | 长度项平均 / m | 交互项平均 / m | 方向夹角平均 / ° |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for label, summary in [('全部', report['pooled']), *report['groups']['turbine'].items()]:
        metric = summary['metrics']; c = summary['clearance_contributions_m']
        lines.append(f"| {label} | {metric['error_samples']} | {number(metric['bias_m'])} | {number(metric['mae_m'])} | {number(c['direction']['bias_m'])} | {number(c['length']['bias_m'])} | {number(c['interaction']['bias_m'])} | {number(summary['direction_to_true_chord_deg']['mean'])} |")
    lines += ['', '刚性零载参考使用同一时刻的根部姿态、pitch、方位角、机舱和布置。直叶尖仅移除独立零载结构叶尖的横向预弯分量；柔性差包含保存变换中的全部叶片变形，不能由此单独归因给某个模态。', '',
        '| 范围 | 固定长度 − 真径向长度平均 / m | 预弯相对直参考净空平均差 / m | 柔性相对刚性预弯净空平均差 / m | 柔性相对刚性预弯叶尖位移平均 / m |',
        '| --- | ---: | ---: | ---: | ---: |']
    for label, summary in [('全部', report['pooled']), *report['groups']['turbine'].items()]:
        rigid = summary['rigid_reference_clearance_differences_m']
        lines.append(f"| {label} | {number(summary['fixed_minus_actual_radial_length_m']['mean'])} | {number(rigid['prebend_minus_straight']['mean'])} | {number(rigid['flexible_minus_prebend']['mean'])} | {number(summary['rigid_reference']['flexible_minus_rigid_prebent_tip_norm_m']['mean'])} |")
    lines += ['', '首交三角形由同刻已验证源表面重算，斜距需在 1e-8 m 内一致，再通过其顶点索引映射源 AeroDyn 展向站位邻域。三角形重心坐标仅追踪同一物质点的零载参考与加载位移；不猜测站位，不把站位输入算法。', '',
        f"误差尾部：|e| ≥ {number(report['tail_threshold_abs_error_m'])} m，含 {report['tail_sample_count']} 对。TLS 比旧法绝对误差更大的样本：{report['candidate_worse_sample_count'] if report['candidate_worse_sample_count'] is not None else '未提供旧法包'}。全部尾部及更差样本的上下文保存在 JSON。", '',
        '| 机组 / 时间 / 叶片 | 误差 / m | 近远组 | pitch / ° | 朝下相位 / ° | S2 站位邻域 | S3 站位邻域 | 方向项 / m | 长度项 / m |',
        '| --- | ---: | --- | ---: | ---: | --- | --- | ---: | ---: |']
    for row in report['worst_absolute_samples'][:10]:
        c=row['geometry']['clearance_contributions_m']
        station=lambda name:'-'.join(map(str,row['measured_surface'][name]['source_material']['source_station_neighborhood_one_based']))
        lines.append(f"| {row['turbine_id']} / {row['time_s']:.3f} / {row['blade_id']} | {number(row['error_m'])} | {row['s3_range_group']} | {number(row['pitch_deg'])} | {number(row['blade_phase_to_down_deg'])} | {station('S2')} | {station('S3')} | {number(c['direction'])} | {number(c['length'])} |")
    lines += ['', 'JSON 包含全部样本、每束表面点对真实弦线的偏移、TLS 拟合残差、实际径向长度、独立零载参考比较，以及机组/叶片/近远组/pitch/相位/站位邻域的描述统计。分组关联不用于推断因果；该诊断没有证明两束足以辨识柔性状态。', '',
        '下一步可使用独立结构资料建立前向表面观测模型，分析量测 Jacobian 与净空敏感性；本报告不拟合补偿、不切换默认、不声称独立性能验收。', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('Diagnostic output must be a new directory')
    estimator_path = ROOT/'wfrl/lidar/dual_beam.py'
    estimator_before = digest(estimator_path)
    tracked = [args.package/name for name in ('manifest.json','results.json','calibration.json')]
    if args.baseline:
        tracked += [args.baseline/name for name in ('manifest.json','results.json','calibration.json')]
    inputs_before = {str(path.resolve()):digest(path) for path in tracked}
    candidate = load_verified(args.package)
    baseline = load_verified(args.baseline) if args.baseline else None
    report = diagnose_packages(candidate, baseline)
    if digest(estimator_path) != estimator_before or any(digest(Path(path)) != sha for path,sha in inputs_before.items()):
        raise ValueError('Estimator or input package changed during read-only diagnosis')
    report['provenance'] = dict(input_hashes=inputs_before,
        implementation_hashes={name:digest(ROOT/name) for name in (
            'scripts/lidar/diagnose_dual_beam_tls_residuals.py',
            'scripts/lidar/compare_dual_beam_methods.py', 'wfrl/camera_video/data.py',
            'wfrl/lidar/physics.py','wfrl/lidar/dual_beam_replay.py')},
        estimator_sha256_before=estimator_before, estimator_sha256_after=digest(estimator_path),
        estimator_and_packages_unchanged=True, runtime=dict(python=platform.python_version(), numpy=np.__version__))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'diagnostics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    (args.output/'report.md').write_text(markdown_report(report))
    print(json.dumps(dict(output=str(args.output.resolve()), diagnosed_pairs=report['diagnosed_pairs'],
        mae_m=report['pooled']['metrics']['mae_m'], worse_pairs=report['candidate_worse_sample_count']),ensure_ascii=False))
    return report


if __name__ == '__main__':
    main()
