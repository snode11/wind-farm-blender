"""Finite-grid installation ranking with explicit denominators and missing data."""
import itertools
import numpy as np
from .schema import digest
from .sampling import AREA_ALGORITHM

SCHEMA = 'x100-installation-search.v1'


def candidates(locations, rolls, pitches, yaws, baselines=()):
    result, seen = [], set()
    for name, location, rotation in itertools.chain(baselines,
            ((name, location, angles) for name, location in locations
             for angles in itertools.product(rolls, pitches, yaws))):
        location, rotation = tuple(map(float, location)), tuple(map(float, rotation))
        if len(location) != 3 or len(rotation) != 3 or not np.isfinite([location, rotation]).all():
            raise ValueError('Installation needs finite position and XYZ angles')
        key = location, rotation
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(id=digest(key)[:16], location_m=location, rotation_deg=rotation, location_name=name))
    if not result or len(result) > 256:
        raise ValueError('Installation search must contain 1..256 candidates')
    return result


def summarize(counts, passages, minimum_points=3):
    """counts[t, blade] is observed distal support; -1 is unknown, never a miss."""
    counts = np.asarray(counts)
    if counts.ndim != 2 or counts.shape[1] != 3 or minimum_points < 1:
        raise ValueError('Invalid coverage support matrix')
    rows, blades = [], []
    for blade in range(3):
        total = hits = unknown = 0
        for passage in passages[blade]:
            if passage['status'] != 'COMPLETE':
                continue
            ids = np.asarray(passage['indices'], dtype=int)
            values = counts[ids, blade]
            n = len(values)
            missing = int(np.sum(values < 0))
            support = values >= minimum_points
            gap = longest = 0
            for value, good in zip(values, support):
                gap = gap + 1 if value >= 0 and not good else 0
                longest = max(longest, gap)
            rows.append(dict(blade=blade + 1, start_s=passage['start_s'], end_s=passage['end_s'],
                N=n, supported=int(support.sum()), unknown=missing,
                evaluation='N/A' if not n else 'UNKNOWN' if missing else 'EVALUATED',
                fraction=float(support.sum() / n) if n else None, longest_gap_frames=longest))
            total += n
            hits += int(support.sum())
            unknown += missing
        blades.append(dict(blade=blade + 1, N=total, supported=hits, unknown=unknown,
            fraction=hits / total if total else None))
    eligible = all(b['N'] and not b['unknown'] for b in blades) and all(p['N'] for p in rows)
    return dict(status='EVALUATED' if eligible else 'INSUFFICIENT_OR_UNKNOWN',
        blades=blades, passages=rows,
        worst_blade_fraction=min(b['fraction'] for b in blades) if eligible else None,
        worst_passage_fraction=min(p['fraction'] for p in rows) if eligible else None,
        longest_gap_frames=max((p['longest_gap_frames'] for p in rows), default=None))


def rank(results):
    scored = []
    for candidate in results:
        profiles = candidate['profiles']
        eligible = bool(profiles) and all(
            p['coverage']['status'] == 'EVALUATED'
            and p['coverage'].get('passages')
            and all(row['N'] > 0 for row in p['coverage']['passages'])
            for p in profiles)
        score = None
        if eligible:
            score = (min(p['coverage']['worst_blade_fraction'] for p in profiles),
                min(p['coverage']['worst_passage_fraction'] for p in profiles),
                -max(p['coverage']['longest_gap_frames'] for p in profiles),
                float(np.mean([b['fraction'] for p in profiles for b in p['coverage']['blades']])),
                -max(p['occluded_fraction'] or 0 for p in profiles))
        scored.append(candidate | dict(score=score))
    return sorted(scored, key=lambda c: (c['score'] is not None, c['score'] or (), c['id']), reverse=True)


def selected_installation(report, source_id, geometry_id):
    if (report.get('schema') != SCHEMA or report.get('source_id') != source_id
            or report.get('geometry_id') != geometry_id or report.get('sampling_method') != 'AREA'
            or report.get('sampling_algorithm') != AREA_ALGORITHM):
        raise ValueError('安装搜索结果与当前几何来源不匹配')
    winner = report.get('winner_id')
    if report.get('errors'):
        raise ValueError('安装搜索含未知帧，不能应用推荐安装')
    rows = rank(report.get('candidates', []))
    if (not rows or rows[0]['id'] != winner or rows[0]['score'] is None or rows[0]['score'][0] <= 0):
        raise ValueError('搜索结果没有覆盖完整、有效的推荐安装')
    location, rotation = rows[0]['location_m'], rows[0]['rotation_deg']
    if len(location) != 3 or len(rotation) != 3 or not np.isfinite([location, rotation]).all():
        raise ValueError('搜索结果包含无效安装参数')
    return location, rotation
