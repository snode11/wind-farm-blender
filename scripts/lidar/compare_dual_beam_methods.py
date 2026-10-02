"""Auditable saved-grid comparison; source geometry is read, never copied or simulated.

Clearance errors are recomputed from estimate minus reference. An independent
triangle/plane section implementation verifies both fields, and recomputes the
reference at every fixed-window source time before passage minima are compared.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.camera_video.data import SourceGeometry, blade_root_frame
from wfrl.lidar.dual_beam_replay import DualBeamReader, digest, resolve_package

NUMERICAL_TOLERANCE_M = 1e-8
NEAR_SLANT_RANGE_M = 12.0  # Descriptive group only; never a validity gate.


def statistics(errors):
    """Pooled errors, including an explicit signed lower tail."""
    errors = [float(e) for e in errors]
    if not all(math.isfinite(e) for e in errors):
        raise ValueError('Nonfinite comparison error')
    n = len(errors)
    ordered = sorted(abs(e) for e in errors)
    low = min(errors) if n else None
    return dict(error_samples=n, mae_m=sum(ordered)/n if n else None,
                bias_m=sum(errors)/n if n else None,
                p95_abs_error_m=ordered[math.ceil(.95*n)-1] if n else None,
                p95_method='nearest-rank', max_abs_error_m=ordered[-1] if n else None,
                max_overestimate_m=max(0., max(errors)) if n else None,
                min_signed_error_m=low,
                max_underestimate_m=max(0., -low) if n else None)


def independent_clearance(point, points, triangles):
    """Signed horizontal distance using independently intersected mesh edges.

    No moving_tower scorer, circle fit, saved reference distance or saved nearest
    wall is used. The estimate and reference each select their own z plane.
    """
    point, points = np.asarray(point, float), np.asarray(points, float)
    triangles = np.asarray(triangles)
    if point.shape != (3,) or not np.isfinite(point).all() or not np.isfinite(points).all():
        raise ValueError('Nonfinite/invalid scoring geometry')
    faces = points[triangles]
    faces = faces[(faces[..., 2].min(axis=1) <= point[2]+1e-12)
                  & (faces[..., 2].max(axis=1) >= point[2]-1e-12)]
    segments, seen = [], set()
    for face in faces:
        hits = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = face[i], face[j]
            za, zb = a[2]-point[2], b[2]-point[2]
            if abs(za) <= 1e-12:
                hits.append(a[:2])
            if abs(zb) <= 1e-12:
                hits.append(b[:2])
            if za*zb < 0:
                hits.append((a + za/(za-zb)*(b-a))[:2])
        unique = []
        for hit in hits:
            if not any(np.linalg.norm(hit-h) <= 1e-10 for h in unique):
                unique.append(hit)
        if len(unique) < 2:
            continue
        pairs = [(a, b) for i, a in enumerate(unique) for b in unique[i+1:]]
        a, b = max(pairs, key=lambda ab: float(np.dot(ab[0]-ab[1], ab[0]-ab[1])))
        key = tuple(sorted(tuple(np.round(v, 10)) for v in (a, b)))
        if key not in seen and np.linalg.norm(b-a) > 1e-12:
            segments.append((a, b)); seen.add(key)
    if not segments:
        raise ValueError('Point height does not intersect independent tower section')
    segments = np.asarray(segments)
    starts, ends = segments[:, 0], segments[:, 1]
    edges = ends-starts
    alpha = np.clip(np.einsum('ij,ij->i', point[:2]-starts, edges)
                    / np.einsum('ij,ij->i', edges, edges), 0., 1.)
    walls = starts + alpha[:, None]*edges
    distances = np.linalg.norm(walls-point[:2], axis=1)
    nearest = int(np.argmin(distances))
    crossing = (starts[:, 1] > point[1]) != (ends[:, 1] > point[1])
    a, b = starts[crossing], ends[crossing]
    crossing_x = a[:, 0]+(point[1]-a[:, 1])*(b[:, 0]-a[:, 0])/(b[:, 1]-a[:, 1])
    inside = np.count_nonzero(crossing_x > point[0]) % 2 == 1
    return float(distances[nearest])*(-1. if inside else 1.)


def expected_window(pose, half_angle_deg):
    phases = float(pose[1])+np.arange(3)*120.
    cycles = np.floor(phases/360.).astype(int)
    delta = phases % 360.-180.
    blades = np.flatnonzero(abs(delta) <= half_angle_deg)
    if len(blades) > 1:
        raise ValueError('Fixed windows overlap between blades')
    if not len(blades):
        return None, None
    blade = int(blades[0])+1
    return blade, f'blade{blade}-cycle{cycles[blade-1]}'


def structural_reference_points(source):
    """The independent unloaded structural tip, not the surface centroid label."""
    reference = json.loads((source.path/'blade-reference.json').read_text())
    tip_local = np.asarray(reference['tip_local_m'], float)
    rest_nacelle = np.column_stack((np.eye(3), np.zeros(3)))
    rest_tips = []
    for blade in (1, 2, 3):
        hub, axes = blade_root_frame(source.scalars, [0.]*6, blade, rest_nacelle)
        rest_tips.append(hub+axes@tip_local)
    transforms = source.transforms[:, :, -1]
    return (np.einsum('nbij,bj->nbi', transforms[..., :3], rest_tips)
            + transforms[..., 3] + source.layout)


def tower_at(source, index):
    transforms = source.tower_transforms[index, source.tower_station]
    return (np.einsum('nij,nj->ni', transforms[..., :3], source.tower_reference)
            + transforms[..., 3] + source.layout)


def load_verified(path):
    path = Path(path).resolve()
    source_path, overlay = resolve_package(path)
    if overlay is None:
        raise ValueError('Expected a versioned dual-beam package: '+str(path))
    sources = {}
    for turbine in overlay['manifest']['turbine_ids']:
        source = SourceGeometry(source_path, turbine)
        motion = SimpleNamespace(start_s=float(source.times[0]), end_s=float(source.times[-1]),
                                 package=SimpleNamespace(manifest=source.manifest, motion=source.motion))
        # Validate full source grid, row identity, S1 independence and all prefix
        # state using the actual application reader, not a loose JSON read.
        DualBeamReader(motion, overlay, turbine)
        sources[turbine] = source
    return dict(path=path, overlay=overlay, sources=sources)


def numerical_reproduction(actual, historical, tolerance=NUMERICAL_TOLERANCE_M):
    """Check all historical fields; additional explicit version fields are allowed.

    Times, integer counts, identities, classes and booleans are exact. Floating
    coordinates/ranges/clearances use the frozen absolute 1e-8 tolerance.
    """
    mismatches, numeric_differences = [], []
    def visit(a, h, path):
        if isinstance(h, dict):
            if not isinstance(a, dict):
                mismatches.append(dict(path=path, reason='type')); return
            for key, value in h.items():
                if key not in a:
                    mismatches.append(dict(path=path+'.'+key, reason='missing'))
                else:
                    visit(a[key], value, path+'.'+key)
        elif isinstance(h, list):
            if not isinstance(a, list) or len(a) != len(h):
                mismatches.append(dict(path=path, reason='list_length')); return
            for i, (av, hv) in enumerate(zip(a, h)):
                visit(av, hv, f'{path}[{i}]')
        elif type(h) is float and type(a) in (int, float):
            difference = abs(a-h)
            numeric_differences.append(difference)
            field = path.rsplit('.', 1)[-1].split('[', 1)[0]
            exact_time = field in ('time_s', 'hit_times_s', 'start_s', 'end_s', 'last_hit_s', 'output_time_s', 'expires_at_s')
            if not math.isfinite(a) or (difference != 0 if exact_time else difference > tolerance):
                mismatches.append(dict(path=path, reason='numeric', absolute_difference=difference))
        elif type(a) != type(h) or a != h:
            mismatches.append(dict(path=path, reason='exact', actual=a, historical=h))
    visit(actual, historical, 'results')
    return dict(passed=not mismatches, tolerance_m=tolerance,
                numeric_fields_checked=len(numeric_differences),
                max_abs_numeric_difference=max(numeric_differences, default=0.),
                mismatch_count=len(mismatches), mismatches=mismatches)


def coverage(records, dt):
    """Every fixed-window source time counts, including unscored/rejected rows."""
    expected = [r for r in records if r['passage_id'] is not None]
    passages = defaultdict(list)
    for record in expected:
        passages[record['passage_id']].append(record)
    gaps, details = [], []
    for passage, rows in passages.items():
        run = []
        passage_gaps = []
        for row in rows+[None]:
            if row is not None and not row['fresh_valid']:
                run.append(row)
            elif run:
                gap = dict(passage_id=passage, first_s=run[0]['time_s'], last_s=run[-1]['time_s'],
                           samples=len(run), grid_duration_s=len(run)*dt,
                           reasons=dict(Counter(r['unknown_reason'] for r in run)))
                passage_gaps.append(gap); gaps.append(gap); run=[]
        details.append(dict(passage_id=passage, blade_id=rows[0]['expected_blade_id'],
                            boundary_truncated=rows[0]['boundary_truncated'],
                            expected_samples=len(rows), fresh_valid_samples=sum(r['fresh_valid'] for r in rows),
                            unscorable_fresh_samples=sum(r['fresh_valid'] and r['error_m'] is None for r in rows),
                            unknown_samples=sum(not r['fresh_valid'] for r in rows), gaps=passage_gaps))
    complete = [p for p in details if not p['boundary_truncated']]
    measured = sum(p['fresh_valid_samples'] > 0 for p in complete)
    return dict(expected_samples=len(expected), fresh_valid_samples=sum(r['fresh_valid'] for r in expected),
                fresh_coverage=sum(r['fresh_valid'] for r in expected)/len(expected) if expected else None,
                unknown_samples=sum(not r['fresh_valid'] for r in expected),
                unknown_reasons=dict(Counter(r['unknown_reason'] for r in expected if not r['fresh_valid'])),
                complete_passages=len(complete), covered_complete_passages=measured,
                complete_passage_coverage=measured/len(complete) if complete else None,
                boundary_truncated_passages=len(details)-len(complete),
                longest_missing_samples=max((g['samples'] for g in gaps), default=0),
                longest_missing_grid_duration_s=max((g['grid_duration_s'] for g in gaps), default=0.),
                missing_grid_duration_definition='missing saved samples times source dt; not a continuous-time event claim',
                unknown_times_s=[r['time_s'] for r in expected if not r['fresh_valid']], passages=details)


def paired_statistics(baseline, candidate):
    return dict(baseline=statistics(r['error_m'] for r in baseline),
                candidate=statistics(r['error_m'] for r in candidate))


def comparison_groups(baseline, candidate):
    """Use IDs, never a turbine-MAE average, for common/own-valid grouping."""
    common_keys = set(baseline) & set(candidate)
    common_b, common_c = [baseline[k] for k in sorted(common_keys)], [candidate[k] for k in sorted(common_keys)]
    result = dict(common=paired_statistics(common_b, common_c),
                  own_valid=paired_statistics(list(baseline.values()), list(candidate.values())))
    for label, field in (('by_turbine', 'turbine_id'), ('by_turbine_blade', 'turbine_blade'),
                         ('by_s3_range', 's3_group'), ('by_passage', 'passage_key')):
        values = sorted({r[field] for r in [*baseline.values(), *candidate.values()]})
        result[label] = {}
        for value in values:
            own_b = {k:r for k,r in baseline.items() if r[field] == value}
            own_c = {k:r for k,r in candidate.items() if r[field] == value}
            common = set(own_b) & set(own_c)
            result[label][str(value)] = dict(
                own_valid=paired_statistics(list(own_b.values()), list(own_c.values())),
                common=paired_statistics([own_b[k] for k in sorted(common)], [own_c[k] for k in sorted(common)]))
    differences = [dict(turbine_id=baseline[k]['turbine_id'], time_s=baseline[k]['time_s'],
                        blade_id=baseline[k]['blade_id'], s3_group=baseline[k]['s3_group'],
                        baseline_error_m=baseline[k]['error_m'], candidate_error_m=candidate[k]['error_m'],
                        absolute_error_change_m=abs(candidate[k]['error_m'])-abs(baseline[k]['error_m']))
                   for k in sorted(common_keys)]
    result['worsened_samples'] = sorted((r for r in differences if r['absolute_error_change_m'] > 0),
                                        key=lambda r: -r['absolute_error_change_m'])
    result['worsened_sample_count'] = len(result['worsened_samples'])
    result['common_sample_differences'] = differences
    result['only_baseline_valid'] = [baseline[k] for k in sorted(set(baseline)-set(candidate))]
    result['only_candidate_valid'] = [candidate[k] for k in sorted(set(candidate)-set(baseline))]
    result['worst_samples'] = {name:sorted(values.values(), key=lambda r:-abs(r['error_m']))[:25]
                              for name, values in (('baseline', baseline), ('candidate', candidate))}
    return result


def compare_packages(baseline, candidate, historical=None):
    b, c = baseline['overlay'], candidate['overlay']
    for field in ('source_hashes', 'segment', 'source_fps', 'turbine_ids', 'reference_definition', 'clearance_definition'):
        if b['manifest'].get(field) != c['manifest'].get(field):
            raise ValueError('Comparison packages differ in '+field)
    bconfig, cconfig = dict(b['config']), dict(c['config'])
    bmethod = bconfig.pop('reconstruction_method', 'hub-axis.v1')
    cmethod = cconfig.pop('reconstruction_method', 'hub-axis.v1')
    bconfig.pop('algorithm_version', None)
    cconfig.pop('algorithm_version', None)
    if bmethod != 'hub-axis.v1' or cmethod != 'hub-tls.v1':
        raise ValueError('Comparison requires hub-axis.v1 baseline and hub-tls.v1 candidate')
    if bconfig != cconfig:
        raise ValueError('Effective configuration differs beyond the reconstruction method')
    if bconfig['expected_window_half_angle_deg'] != 15.:
        raise ValueError('This frozen comparison requires the ±15 degree window')
    scored = {'baseline':{}, 'candidate':{}}
    coverage_report, windows, minima, checks = {}, [], [], Counter()
    residuals = []
    for turbine in b['manifest']['turbine_ids']:
        source = baseline['sources'][turbine]
        brow, crow = b['results'][turbine]['samples'], c['results'][turbine]['samples']
        if len(brow) != len(crow) or len(brow) != len(source.times):
            raise ValueError('Comparison sample grids differ')
        if b['results'][turbine]['events'] != c['results'][turbine]['events']:
            raise ValueError('S1 events differ between methods')
        refs = structural_reference_points(source)
        expected = [expected_window(p, bconfig['expected_window_half_angle_deg']) for p in source.poses]
        truncated = {expected[0][1], expected[-1][1]}-{None}
        records = {'baseline':[], 'candidate':[]}
        passages = defaultdict(list)
        for index, (br, cr) in enumerate(zip(brow, crow)):
            time = float(source.times[index]); blade, passage = expected[index]
            for name, row in (('baseline', br), ('candidate', cr)):
                if row['time_s'] != time or (row['expected_blade_id'], row['passage_id']) != (blade, passage):
                    raise ValueError('Saved expected window differs from source phase')
            if br['observations'] != cr['observations'] or br['s1_observation_state'] != cr['s1_observation_state']:
                raise ValueError(f'Raw observations/S1 differ at {turbine}/{time}')
            if b['results'][turbine]['cumulative'][index]['alarm'] != c['results'][turbine]['cumulative'][index]['alarm']:
                raise ValueError('S1 cumulative alarm differs between methods')
            checks['identical_raw_observation_rows'] += 1
            checks['identical_s1_states'] += 1
            tower = tower_at(source, index) if blade is not None or br['reconstruction']['valid'] or cr['reconstruction']['valid'] else None
            window_reference, reference_reason = None, 'outside_fixed_window'
            if blade is not None:
                try:
                    window_reference = independent_clearance(refs[index, blade-1], tower, source.tower_triangles)
                    reference_reason = 'valid'
                except ValueError:
                    reference_reason = 'no_tower_section_at_reference_height'
                checks['reference_window_samples_recomputed'] += 1
            window = dict(turbine_id=turbine, time_s=time, expected_blade_id=blade, passage_id=passage,
                          boundary_truncated=passage in truncated, reference_clearance_m=window_reference,
                          reference_reason=reference_reason)
            for name, row in (('baseline', br), ('candidate', cr)):
                pair = row['reconstruction']; evaluation = row['evaluation']
                valid = pair['valid'] and pair['blade_id'] == blade and blade is not None
                error, estimate, reference = None, pair['clearance_estimate'], evaluation.get('clearance_reference_m')
                if pair['valid']:
                    actual_blade = pair['blade_id']
                    geometric_reference = independent_clearance(refs[index, actual_blade-1], tower, source.tower_triangles)
                    geometric_estimate = independent_clearance(pair['tip_estimate_m'], tower, source.tower_triangles)
                    if reference is None or estimate is None:
                        raise ValueError('Valid pair lacks an independently scoreable clearance')
                    for saved, recomputed, field in ((reference, geometric_reference, 'reference'),
                                                     (estimate, geometric_estimate, 'estimate')):
                        residual = abs(saved-recomputed); residuals.append(residual)
                        if residual > NUMERICAL_TOLERANCE_M:
                            raise ValueError(f'Independent {field} scorer mismatch at {name}/{turbine}/{time}: {residual}')
                    if not np.allclose(evaluation['tip_reference_m'], refs[index, actual_blade-1], atol=NUMERICAL_TOLERANCE_M, rtol=0):
                        raise ValueError('Saved structural reference point differs from source')
                    # Deliberately do not read clearance_error_m, summary or audit.
                    error = float(estimate-reference)
                    s3 = row['observations']['S3']['slant_range_m']
                    sample = dict(turbine_id=turbine, time_s=time, blade_id=actual_blade,
                                  turbine_blade=f'{turbine}/blade{actual_blade}',
                                  passage_key=f'{turbine}/{passage}' if passage is not None else f'{turbine}/outside',
                                  passage_id=passage, s3_slant_range_m=s3,
                                  s3_group='near' if s3 < NEAR_SLANT_RANGE_M else 'far',
                                  estimate_clearance_m=estimate, reference_clearance_m=reference,
                                  error_m=error, expected_window=valid)
                    scored[name][(turbine, time)] = sample
                    checks['independent_'+name+'_scored_samples'] += 1
                reason = (pair['reason'] if not pair['valid'] else
                          'paired_wrong_blade' if blade is not None and pair['blade_id'] != blade else 'outside_fixed_window')
                record = dict(time_s=time, expected_blade_id=blade, passage_id=passage,
                              boundary_truncated=passage in truncated, fresh_valid=valid,
                              unknown_reason=None if valid else reason, error_m=error if valid else None)
                records[name].append(record)
                window[name] = dict(fresh_valid=valid, reason=record['unknown_reason'],
                                    estimate_clearance_m=estimate if valid else None, error_m=record['error_m'])
            if blade is not None:
                windows.append(window); passages[passage].append(window)
        coverage_report[turbine] = {name:coverage(values, 1./source.manifest['source_fps']) for name, values in records.items()}
        for passage, samples in passages.items():
            if passage in truncated:
                continue
            references = [r for r in samples if r['reference_clearance_m'] is not None]
            ref = min(references, key=lambda r:r['reference_clearance_m']) if references else None
            complete_reference = len(references) == len(samples)
            minimum = dict(turbine_id=turbine, passage_id=passage, blade_id=samples[0]['expected_blade_id'],
                           expected_samples=len(samples), reference_samples=len(references),
                           reference_complete=complete_reference,
                           reference_min_m=ref['reference_clearance_m'] if ref else None,
                           reference_min_time_s=ref['time_s'] if ref else None)
            for name in ('baseline', 'candidate'):
                predictions = [r for r in samples if r[name]['fresh_valid']]
                pred = min(predictions, key=lambda r:r[name]['estimate_clearance_m']) if predictions else None
                minimum[name] = dict(valid_samples=len(predictions), unknown=not pred or not complete_reference,
                                     predicted_min_m=pred[name]['estimate_clearance_m'] if pred else None,
                                     predicted_min_time_s=pred['time_s'] if pred else None,
                                     error_m=pred[name]['estimate_clearance_m']-ref['reference_clearance_m'] if pred and complete_reference else None)
            minima.append(minimum)
    pool_coverage = {}
    for name in ('baseline', 'candidate'):
        values = [r[name] for r in coverage_report.values()]
        expected_n, valid_n = sum(r['expected_samples'] for r in values), sum(r['fresh_valid_samples'] for r in values)
        complete_n, covered_n = sum(r['complete_passages'] for r in values), sum(r['covered_complete_passages'] for r in values)
        pool_coverage[name] = dict(expected_samples=expected_n, fresh_valid_samples=valid_n,
                                   fresh_coverage=valid_n/expected_n if expected_n else None,
                                   unknown_samples=sum(r['unknown_samples'] for r in values),
                                   unknown_reasons=dict(sum((Counter(r['unknown_reasons']) for r in values), Counter())),
                                   complete_passages=complete_n, covered_complete_passages=covered_n,
                                   complete_passage_coverage=covered_n/complete_n if complete_n else None,
                                   boundary_truncated_passages=sum(r['boundary_truncated_passages'] for r in values),
                                   longest_missing_grid_duration_s=max((r['longest_missing_grid_duration_s'] for r in values), default=0.))
    report = dict(schema='wfrl.dual-beam-method-comparison.v1', status='REVIEW_ONLY', performance_status='PENDING_ACCEPTANCE',
                  baseline_method=bmethod, candidate_method=cmethod,
                  baseline_package=os.path.relpath(baseline['path']), candidate_package=os.path.relpath(candidate['path']),
                  package_manifest_hashes=dict(baseline=digest(baseline['path']/'manifest.json'), candidate=digest(candidate['path']/'manifest.json')),
                  source_hashes=b['manifest']['source_hashes'], source_fps=b['manifest']['source_fps'],
                  scoring_definition='estimate clearance minus independently verified structural reference clearance; each point uses its own global height',
                  fixed_window_half_angle_deg=15., s3_group_definition='near: slant range < 12 m; far: >= 12 m; descriptive only',
                  numerical_tolerance_m=NUMERICAL_TOLERANCE_M,
                  integrity=dict(reader_validation_passed=True, raw_observations_and_s1_identical=True,
                                 checks=dict(checks), max_independent_scoring_residual_m=max(residuals, default=0.)),
                  metrics=comparison_groups(scored['baseline'], scored['candidate']),
                  coverage=dict(pooled=pool_coverage, by_turbine=coverage_report),
                  fixed_window_samples=windows, complete_passage_minima=minima,
                  passage_minimum_statistics={name:statistics(p[name]['error_m'] for p in minima if p[name]['error_m'] is not None) for name in ('baseline', 'candidate')},
                  passage_minimum_definition='min prediction over valid fresh samples minus min structural reference over entire complete ±15 degree saved window; not continuous-time minimum')
    if historical is not None:
        if historical['overlay']['manifest']['source_hashes'] != b['manifest']['source_hashes']:
            raise ValueError('Historical reproduction source differs')
        report['historical_reproduction'] = numerical_reproduction(b['results'], historical['overlay']['results'])
        if not report['historical_reproduction']['passed']:
            raise ValueError('Historical baseline reproduction failed: '+json.dumps(report['historical_reproduction'], ensure_ascii=False))
    return report


def markdown_report(report):
    def number(value):
        return 'unknown' if value is None else f'{value:.6f}'
    common = report['metrics']['common']
    lines = ['# 双束旧法与 TLS：完整保存网格对照', '',
             '`REVIEW_ONLY / PENDING_ACCEPTANCE`。默认方法仍为 hub-axis.v1；本报告不构成独立工况或设备验收。', '',
             '逐样本误差从估计净空减结构参考净空重算；两者分别按自身全局高度，使用独立三角形截面评分器核对。', '',
             '|公共有效集合|样本|MAE / m|bias / m|P95 / m|最大高估 / m|最小有符号误差 / m|',
             '|---|---:|---:|---:|---:|---:|---:|']
    for name in ('baseline', 'candidate'):
        s = common[name]
        lines.append('|'+name+'|'+str(s['error_samples'])+'|'+ '|'.join(number(s[k]) for k in ('mae_m','bias_m','p95_abs_error_m','max_overestimate_m','min_signed_error_m'))+'|')
    lines += ['', f"TLS 绝对误差更大的公共样本：{report['metrics']['worsened_sample_count']}；全部列于 comparison.json。", '',
              '|固定 ±15° 窗口|新鲜有效/预期|覆盖|未知|完整经过覆盖|最长缺测网格 / s|',
              '|---|---:|---:|---:|---:|---:|']
    for name, s in report['coverage']['pooled'].items():
        lines.append(f"|{name}|{s['fresh_valid_samples']}/{s['expected_samples']}|{100*s['fresh_coverage']:.2f}%|{s['unknown_samples']}|{s['covered_complete_passages']}/{s['complete_passages']}|{s['longest_missing_grid_duration_s']:.3f}|")
    lines += ['', '|完整经过最低净空误差（独立指标）|经过|MAE / m|P95 / m|最大高估 / m|最小有符号误差 / m|',
              '|---|---:|---:|---:|---:|---:|']
    for name, s in report['passage_minimum_statistics'].items():
        lines.append('|'+name+'|'+str(s['error_samples'])+'|'+'|'.join(number(s[k]) for k in ('mae_m','p95_abs_error_m','max_overestimate_m','min_signed_error_m'))+'|')
    if report.get('historical_reproduction'):
        lines += ['', f"历史基线复现：PASS；冻结绝对容差 1e-8 m，最大数值差 {report['historical_reproduction']['max_abs_numeric_difference']:.3g}。类别、计数、时间和有效性逐项一致。"]
    lines += ['', '全窗口参考最低值从每个预期时刻源几何重算；预测最低值仅用新鲜有效瞬时配对。保持读数不增样本，边界截断经过单列。',
              'comparison.json 包含自身/公共集合、各机组/叶片/近远/经过、全部更差样本、最差样本、未知时刻、原因、缺测间隙、全窗口参考及每次最低值时刻。',
              '独立评分残差最大值：'+number(report['integrity']['max_independent_scoring_residual_m'])+' m。原始三束观测、S1 事件及逐时累计报警均一致。',
              '该结果仅来自保存网格；未模拟连续时间，也未提供新物理运行、现场设备或正式性能门槛证据。']
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--historical', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    baseline, candidate = load_verified(args.baseline), load_verified(args.candidate)
    historical = load_verified(args.historical) if args.historical else None
    report = compare_packages(baseline, candidate, historical)
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    report['baseline_package'] = os.path.relpath(baseline['path'], output)
    report['candidate_package'] = os.path.relpath(candidate['path'], output)
    report['package_path_reference'] = 'comparison output directory'
    report['evidence_tool_hashes'] = {
        'scripts/lidar/compare_dual_beam_methods.py':digest(Path(__file__)),
        'tests/lidar/test_dual_beam_comparison.py':digest(ROOT/'tests/lidar/test_dual_beam_comparison.py')}
    (output/'comparison.json').write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
    (output/'report.md').write_text(markdown_report(report))
    print(output)


if __name__ == '__main__':
    main()
