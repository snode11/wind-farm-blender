"""Fit the X100 detection calibration to a measured vendor PLY sequence.

Usage: python scripts/radar_x100/calibrate_from_ply.py <ply directory> <report.json> [statistics cache.json]

Measured quantities (vendor output only, no truth):
  - point jitter of persistent static scatterers (range / azimuth / elevation std);
  - points per extended object and its extent (1 m linked components);
  - sporadic points per frame that belong neither to persistent scatterers nor to the far target;
  - far-target (drone) detection rate per range bin along its interpolated track, its vendor RCS and SNR;
  - the vendor mag versus snr relation.
Fitted model quantities, in this order: measurement error, an extraction scale on the four merge
widths (extended-object point count), the per-cell false-alarm probability (sporadic rate), the
processing gain (drone detection rate versus range at its vendor RCS), the vendor SNR offset and
the mag mapping. Vendor axes are taken as +Y boresight, +X right, +Z up.
"""
from dataclasses import asdict, replace
from pathlib import Path
import argparse
import json
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
from wfrl.radar_x100 import ply  # noqa: E402
from wfrl.radar_x100.detection import (DetectionConfig, generate_detections, probability_detection,  # noqa: E402
                                       received_power_w, _cell_grid)
from wfrl.radar_x100.schema import RadarFrame, FrameTime, POINT_DTYPE  # noqa: E402

FAR_M, PERSIST_FRACTION, VOXEL_M, LINK_M, FPS = 150., .5, .5, 1., 33.


def polar(points):
    x, y, z = (points[k].astype(float) for k in 'xyz')
    r = np.sqrt(x * x + y * y + z * z)
    return r, np.degrees(np.arctan2(x, y)), np.degrees(np.arcsin(np.clip(z / np.maximum(r, 1e-9), -1, 1)))


def xyz(points):
    return np.c_[points['x'], points['y'], points['z']].astype(float)


def components(p, link):
    near = np.linalg.norm(p[:, None] - p[None], axis=2) < link
    labels, count = -np.ones(len(p), int), 0
    for i in range(len(p)):
        if labels[i] >= 0:
            continue
        stack, labels[i] = [i], count
        while stack:
            j = stack.pop()
            for m in np.flatnonzero(near[j] & (labels < 0)):
                labels[m] = count
                stack.append(m)
        count += 1
    return labels, count


def vendor_statistics(frames):
    n = len(frames)
    if n < 2:
        raise ValueError('Calibration requires at least two frames')
    for index, frame in enumerate(frames):
        if (frame.ndim != 1 or frame.dtype.names is None
                or not all(name in frame.dtype.names for name in ('x', 'y', 'z', 'snr', 'mag', 'rcs'))
                or not all(np.isfinite(frame[name]).all() for name in ('x', 'y', 'z', 'snr', 'mag', 'rcs'))):
            raise ValueError(f'Invalid calibration point fields in frame {index}')
    counts = {}
    for f in frames:
        for key in set(map(tuple, np.round(xyz(f) / VOXEL_M).astype(int))):
            counts[key] = counts.get(key, 0) + 1
    persistent = np.array([k for k, c in counts.items() if c > n * PERSIST_FRACTION], float) * VOXEL_M
    jitter = []
    for centre in persistent:
        rows = [p[d.argmin()] for p in map(xyz, frames)
                if len(p) and (d := np.linalg.norm(p - centre, axis=1)).min() < .6]
        if len(rows) >= 2 and len(rows) > n * .05:
            r, az, el = polar(np.array([tuple(v) for v in rows], dtype=[('x', float), ('y', float), ('z', float)]))
            jitter.append((r.std(), az.std(), el.std()))
    jitter = np.array(jitter)
    if not len(jitter):
        raise ValueError('Calibration requires repeated persistent scatterers for jitter')
    sizes, extents, sporadic = [], [], []
    for index, f in enumerate(frames):
        p = xyz(f)
        r, az, el = polar(f)
        near_persistent = (np.linalg.norm(p[:, None] - persistent[None], axis=2).min(axis=1) < 1.
                           if len(persistent) else np.zeros(len(p), bool))
        sporadic.append(int((~near_persistent & (r < FAR_M)).sum()))
        if index % 5:
            continue
        labels, count = components(p, LINK_M)
        for c in range(count):
            s = labels == c
            sizes.append(int(s.sum()))
            extents.append((np.ptp(r[s]), np.ptp(az[s]), np.ptp(el[s])))
    sizes, extents = np.array(sizes), np.array(extents)
    big = sizes >= 10
    if not big.any():
        raise ValueError('Calibration requires extended objects with at least 10 points')
    # Far target: one range per frame; detection rate along the interpolated track.
    detected = {}
    far_snr, far_rcs = [], []
    for index, f in enumerate(frames):
        r, _, _ = polar(f)
        far = r > FAR_M
        if far.any():
            detected[index] = float(np.median(r[far]))
            far_snr += f['snr'][far].astype(float).tolist()
            far_rcs += f['rcs'][far].astype(float).tolist()
    idx = np.array(sorted(detected))
    if len(idx) < 2:
        raise ValueError('Calibration requires a far-target track with at least two detections')
    span = np.arange(idx[0], idx[-1] + 1)
    track = np.interp(span, idx, [detected[i] for i in idx])
    hit = np.isin(span, idx)
    bins = []
    for lo in range(150, 400, 50):
        s = (track >= lo) & (track < lo + 50)
        if s.sum() >= 30:
            bins.append(dict(range_m=[lo, lo + 50], centre_m=float(np.median(track[s])),
                             frames=int(s.sum()), detected=int(hit[s].sum()), rate=float(hit[s].mean())))
    if not bins:
        raise ValueError('Calibration requires a far-target range bin with at least 30 frames')
    allp = np.concatenate(frames)
    snr = allp['snr'].astype(float)
    ok = np.isfinite(snr)
    if len(snr[ok]) < 2 or np.ptp(snr[ok]) == 0:
        raise ValueError('Calibration requires distinct finite SNR values for the magnitude fit')
    slope, intercept = np.polyfit(snr[ok], allp['mag'][ok].astype(float), 1)
    resid = allp['mag'][ok] - (slope * snr[ok] + intercept)
    return dict(
        frames=n, points_per_frame_mean=float(np.mean([len(f) for f in frames])),
        persistent_scatterers=len(persistent),
        jitter_median=dict(range_m=float(np.median(jitter[:, 0])), azimuth_deg=float(np.median(jitter[:, 1])),
                           elevation_deg=float(np.median(jitter[:, 2]))),
        extended_objects=dict(count=int(big.sum()), points_median=float(np.median(sizes[big])),
                              extent_median=dict(range_m=float(np.median(extents[big, 0])),
                                                 azimuth_deg=float(np.median(extents[big, 1])),
                                                 elevation_deg=float(np.median(extents[big, 2])))),
        sporadic_points_per_frame=float(np.mean(sporadic)),
        far_target=dict(track_frames=len(span), detected_frames=len(idx), bins=bins,
                        snr_median=float(np.nanmedian(far_snr)), rcs_median_dbsm=float(np.nanmedian(far_rcs))),
        snr_min=float(np.nanmin(snr)), snr_max=float(np.nanmax(snr)),
        mag_fit=dict(slope=float(slope), intercept=float(intercept), residual_std=float(resid.std())))


def _template(sequence):
    return RadarFrame('X100-CAL', 'calibration', sequence, 'TEST', 'GEOMETRY', np.zeros(0, POINT_DTYPE),
                      time=FrameTime('SIMULATION:calibration', sequence / FPS, quality='SIMULATION'),
                      spatial=dict(frame='SENSOR_X_FORWARD_Y_LEFT_Z_UP', units='m',
                                   world_from_sensor=np.eye(4).tolist()))


def _patch(extent, rng, count=6000, centre_m=35.):
    """Extended scatterer patch with the measured range/azimuth/elevation extent, at rest."""
    r = centre_m + (rng.random(count) - .5) * extent['range_m']
    az = np.radians((rng.random(count) - .5) * extent['azimuth_deg'])
    el = np.radians((rng.random(count) - .5) * extent['elevation_deg'])
    return np.c_[r * np.cos(el) * np.cos(az), r * np.cos(el) * np.sin(az), r * np.sin(el)]


def fit(stats):
    try:
        json.dumps(stats, allow_nan=False)
        jitter = stats['jitter_median']
        extent = stats['extended_objects']['extent_median']
        positive = [stats['extended_objects']['points_median']]
        nonnegative = [jitter[key] for key in ('range_m', 'azimuth_deg', 'elevation_deg')]
        nonnegative += [extent[key] for key in ('range_m', 'azimuth_deg', 'elevation_deg')]
        nonnegative += [stats['sporadic_points_per_frame'], stats['mag_fit']['residual_std']]
        finite = [stats['far_target']['rcs_median_dbsm'], stats['far_target']['snr_median'],
                  stats['mag_fit']['slope'], stats['mag_fit']['intercept']]
        bins = stats['far_target']['bins']
        if not bins:
            raise ValueError('no far-target bins')
        for row in bins:
            positive += [row['centre_m'], row['frames']]
            if not 0 <= row['rate'] <= 1:
                raise ValueError('invalid far-target detection rate')
        if (not all(np.isfinite(value) and value > 0 for value in positive)
                or not all(np.isfinite(value) and value >= 0 for value in nonnegative)
                or not all(np.isfinite(value) for value in finite)):
            raise ValueError('invalid fitted quantities')
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f'Invalid calibration statistics: {exc}') from exc
    jitter = stats['jitter_median']
    base = DetectionConfig(fluctuation_model='SWERLING_1', range_std_m=jitter['range_m'],
                           azimuth_std_deg=jitter['azimuth_deg'], elevation_std_deg=jitter['elevation_deg'])
    # Extraction scale: strong extended patch, no noise-only cells, compare reported points per object.
    target = stats['extended_objects']['points_median']
    rng = np.random.default_rng(7)
    patches = [_patch(stats['extended_objects']['extent_median'], rng) for _ in range(6)]
    scan = []
    for scale in np.arange(1., 6.01, .25):
        config = replace(base, merge_range_m=.2 * scale, merge_azimuth_deg=1. * scale,
                         merge_elevation_deg=2. * scale, merge_velocity_m_s=.1 * scale,
                         processing_gain_db=80., false_alarms_enabled=False)
        reported = [len(generate_detections(p, np.zeros(len(p)), np.full(len(p), 1e-3), template=_template(i),
                                            config=config, seed=i).points) for i, p in enumerate(patches)]
        scan.append((float(scale), float(np.mean(reported))))
    scale = min(scan, key=lambda row: abs(row[1] - target))[0]
    config = replace(base, merge_range_m=.2 * scale, merge_azimuth_deg=1. * scale,
                     merge_elevation_deg=2. * scale, merge_velocity_m_s=.1 * scale)
    *_, dimensions = _cell_grid(config)
    cells = float(np.prod(dimensions))
    pfa = min(1e-3, stats['sporadic_points_per_frame'] / cells)
    config = replace(config, false_alarm_probability_per_cell=pfa)
    # Processing gain: far-target detection rate per range bin at its median vendor RCS.
    rcs = 10 ** (stats['far_target']['rcs_median_dbsm'] / 10)
    bins = stats['far_target']['bins']
    centres = np.array([b['centre_m'] for b in bins])
    rates = np.array([b['rate'] for b in bins])
    weights = np.array([b['frames'] for b in bins], float)

    def loss(gain):
        trial = replace(config, processing_gain_db=float(gain))
        snr = received_power_w(centres, rcs, config=trial) / trial.detector_noise_power_w
        return float(np.sum(weights * (probability_detection(snr, config=trial) - rates) ** 2))
    grid = np.arange(20., 90., .05)
    gain = float(grid[np.argmin([loss(g) for g in grid])])
    config = replace(config, processing_gain_db=gain)
    snr = received_power_w(centres, rcs, config=config) / config.detector_noise_power_w
    modelled = probability_detection(snr, config=config)
    # SNR offset: detected model output power of the far target versus the vendor far SNR.
    rng = np.random.default_rng(11)
    noise = config.detector_noise_power_w
    ranges = np.repeat(centres, (weights / weights.sum() * 20000).astype(int))
    power = received_power_w(ranges, rcs, config=config)
    measured = rng.exponential(power + noise)
    detected = measured >= config.threshold_noise_ratio * noise
    model_snr_db = float(np.median(10 * np.log10(measured[detected] / noise)))
    offset = stats['far_target']['snr_median'] - model_snr_db
    mag = stats['mag_fit']
    config = replace(config, vendor_snr_offset_db=offset, vendor_mag_slope=mag['slope'],
                     vendor_mag_intercept=mag['intercept'], vendor_mag_std=mag['residual_std'],
                     ms_velocity_threshold_m_s=config.merge_velocity_m_s)
    return config, dict(extraction_scan=scan, extraction_scale=scale, search_cells=cells,
                        far_target_rcs_m2=rcs, modelled_far_rates=[float(v) for v in modelled],
                        measured_far_rates=rates.tolist(), model_far_snr_median_db=model_snr_db)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('cache', type=Path, nargs='?')
    args = parser.parse_args()
    directory, report, cache = args.directory, args.report, args.cache
    if cache is not None and cache.exists():
        try:
            stats = json.loads(cache.read_bytes())
        except (ValueError, OSError) as exc:
            raise ValueError(f'{cache}: {exc}') from exc
    else:
        files = sorted(directory.glob('*_RawPoints.ply'))
        if not files:
            raise SystemExit(f'No *_RawPoints.ply files found: {directory}')
        frames = []
        for path in files:
            try:
                points = ply.read_ply(path)
                if not all(np.isfinite(points[name]).all() for name in ('x', 'y', 'z', 'snr', 'mag', 'rcs')):
                    raise ValueError('Nonfinite calibration point fields')
                frames.append(points)
            except (OSError, ValueError) as exc:
                raise ValueError(f'{path}: {exc}') from exc
        try:
            stats = vendor_statistics(frames)
        except ValueError as exc:
            raise ValueError(f'{directory}: {exc}') from exc
        if cache is not None:
            from wfrl_blender import atomic_json
            atomic_json.write_text(cache, json.dumps(stats, indent=1, allow_nan=False))
    try:
        config, details = fit(stats)
    except ValueError as exc:
        raise ValueError(f'{cache or directory}: {exc}') from exc
    fitted = {k: v for k, v in asdict(config).items() if v != getattr(DetectionConfig(), k)}
    fitted.pop('calibration_status', None)
    result = dict(dataset=str(directory.name), statistics=stats, fit=details, fitted_parameters=fitted)
    from wfrl_blender import atomic_json
    atomic_json.write_text(report, json.dumps(result, indent=1, ensure_ascii=False, allow_nan=False))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
