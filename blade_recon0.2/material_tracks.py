"""Fit-only fixed material-point RGB correspondence from a contour estimate.

The contour seed supplies a local *prediction*, never an observed UV. Every
non-self observation requires independent forward/backward RGB localization
and the unchanged whole sensor ROI descriptor competition. Each original
anchor is bound once; failed camera branches are retained and never restarted.
"""
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import argparse
import copy
import json

import cv2
import numpy as np

from input_adapter import InputSequence
from model import Rotor, STATE_NAMES
from raster import rasterize
from surface_tracks import (TrackConfig, _roi, _quality,
                            declared_input_path, sha256)
from texture_geometry import ray_binding, binding_visibility, quality_reason


GRAY_KERNEL_PX = 7
LK_GRADIENT_HALO_PX = 1


def material_gray(rgb, config):
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError('material tracker requires original uint8 HxWx3 RGB')
    return cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY),
                            (GRAY_KERNEL_PX,)*2, config.gray_sigma_px)


def _has_key(value, key):
    if isinstance(value, dict):
        return key in value or any(_has_key(v, key) for v in value.values())
    return isinstance(value, list) and any(_has_key(v, key) for v in value)


def validate_seed(seed, inputs, records):
    """Require the exact same complete fit inputs, clock, cameras and model."""
    if _has_key(seed, 'texture_geometry'):
        raise ValueError('material tracker seed must be contour-only, without texture_geometry')
    if seed.get('turbine') != inputs.cfg.to_dict() or seed.get('state_names') != STATE_NAMES:
        raise ValueError('estimated seed healthy model/state contract mismatch')
    def audit(log):
        keys = [(a['path'], a['role'], a.get('sha256'), a.get('bytes')) for a in log]
        if len(set(keys)) != len(keys) or any(not a[2] for a in keys):
            raise ValueError('estimated seed/input access log requires unique hashed files')
        return set(keys)
    if audit(seed.get('input_access_log', [])) != audit(inputs.access_log):
        raise ValueError('estimated seed input access hashes differ from current fit data')
    rows = seed.get('frames', [])
    if len(rows) != len(records):
        raise ValueError('estimated seed frame plan differs from current fit data')
    for row, record in zip(rows, records):
        if (row.get('frame') != record.frame or
                not np.isclose(row.get('t', np.nan), record.t, atol=1e-9, rtol=0)):
            raise ValueError('estimated seed frame/time identity mismatch')
        s = np.asarray(row.get('state'), float)
        if (s.shape != (len(STATE_NAMES),) or not np.isfinite(s).all() or
                row.get('reconstruction_valid') is not True or
                row.get('solver', {}).get('success') is not True):
            raise ValueError('estimated seed requires successful valid contour states')
        cameras = row.get('cameras', [])
        if len(cameras) != len(record.cameras):
            raise ValueError('estimated seed camera plan differs from fit data')
        for cam, saved in zip(record.cameras, cameras):
            T = np.eye(4); T[:3, :3] = cam.R; T[:3, 3] = cam.t
            if (saved.get('name') != cam.name or saved.get('W') != cam.W or
                    saved.get('H') != cam.H or
                    not np.array_equal(np.asarray(saved.get('K')), cam.K) or
                    not np.array_equal(np.asarray(saved.get('T_cv_from_model')), T)):
                raise ValueError('estimated seed camera calibration differs from fit data')


def local_homography(anchor_cam, target_cam, anchor_vertices, target_vertices, faces, binding):
    """Four fixed barycentric plane locations on one immutable bound face."""
    b, f = binding['blade_0based'], binding['face']
    weights = np.asarray([[1., 0., 0.], [0., 1., 0.], [0., 0., 1.], [-1., 1., 1.]])
    points = []
    for cam, vertices in ((anchor_cam, anchor_vertices), (target_cam, target_vertices)):
        triangle = np.asarray(vertices)[b].reshape(-1, 3)[faces[f]]
        uv, depth = cam.project(weights @ triangle)
        if not np.isfinite(uv).all() or (depth <= .05).any():
            return None, {'status': 'INVALID_LOCAL_PLANE_PROJECTION'}
        points.append(uv)
    for uv in points:
        first, second = uv[1]-uv[0], uv[2]-uv[0]
        if abs(float(first[0]*second[1]-first[1]*second[0])) < 1e-5:
            return None, {'status': 'DEGENERATE_LOCAL_PLANE_PROJECTION'}
    H = cv2.getPerspectiveTransform(points[0].astype(np.float32), points[1].astype(np.float32))
    if not np.isfinite(H).all() or abs(np.linalg.det(H)) < 1e-12:
        return None, {'status': 'INVALID_LOCAL_HOMOGRAPHY'}
    return H, {'status': 'ESTIMATED_LOCAL_TRIANGLE_PREDICTION',
        'anchor_plane_uv': points[0].tolist(), 'target_plane_uv': points[1].tolist(),
        'fixed_plane_barycentric_weights': weights.tolist(),
        'anchor_to_target_homography': H.tolist()}


def transform_pixel(H, uv):
    p = H @ np.r_[uv, 1.]
    if not np.isfinite(p).all() or abs(p[2]) < 1e-12:
        return None
    return p[:2]/p[2]


def _footprint_known(support, center, size, target_to_anchor=None):
    """All bilinear RGB samples used by a footprint require measured support."""
    r = size//2
    yy, xx = np.mgrid[-r:r+1, -r:r+1]
    xy = np.column_stack([xx.reshape(-1), yy.reshape(-1)]) + np.asarray(center)
    if target_to_anchor is not None:
        # warpPerspective creates an integer grid before getRectSubPix/LK
        # interpolates it. Expand that first interpolation's neighboring grid
        # centers *before* mapping them to the source; mapping the subpixel
        # centers directly can miss actual source samples during shrinking.
        lo, hi = np.floor(xy), np.ceil(xy)
        xy = np.concatenate([np.column_stack([xs, ys]) for xs in (lo[:, 0], hi[:, 0])
                             for ys in (lo[:, 1], hi[:, 1])])
        homogeneous = np.column_stack([xy, np.ones(len(xy))]) @ target_to_anchor.T
        if not np.isfinite(homogeneous).all() or (np.abs(homogeneous[:, 2]) < 1e-12).any():
            return False
        xy = homogeneous[:, :2]/homogeneous[:, 2, None]
    h, w = support.shape
    if (not np.isfinite(xy).all() or (xy < 0).any() or
            (xy[:, 0] > w-1).any() or (xy[:, 1] > h-1).any()):
        return False
    lo, hi = np.floor(xy).astype(int), np.ceil(xy).astype(int)
    return bool(all(support[ys, xs].all() for ys in (lo[:, 1], hi[:, 1])
                    for xs in (lo[:, 0], hi[:, 0])))


def measured_instance_support(row, blade):
    """Binary foreground alone cannot establish blade instance identity."""
    raw = row['known'] & row['occupancy'] & (row['estimated_blade'] == blade)
    # A gray pixel is measured only when its entire explicitly finite blur
    # footprint comes from known raw RGB on this estimated blade instance.
    return cv2.erode(raw.astype(np.uint8), np.ones((GRAY_KERNEL_PX,)*2, np.uint8),
                     borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)


def _raw_halo_diagnostic(row, blade, pixel):
    """Explain eroded support using raw input pixels, without inferring occlusion."""
    x, y = pixel
    h, w = row['known'].shape
    radius = GRAY_KERNEL_PX//2
    failures, counts = [], Counter()
    for dy in range(-radius, radius+1):
        for dx in range(-radius, radius+1):
            rx, ry = x+dx, y+dy
            if not (0 <= rx < w and 0 <= ry < h):
                checks = ['RAW_BLUR_HALO_OUT_OF_IMAGE']
                values = {'known': None, 'occupancy': None, 'estimated_blade_0based': None}
            else:
                values = {'known': bool(row['known'][ry, rx]),
                          'occupancy': bool(row['occupancy'][ry, rx]),
                          'estimated_blade_0based': int(row['estimated_blade'][ry, rx])}
                checks = []
                if not values['known']: checks.append('RAW_KNOWN_FALSE')
                if not values['occupancy']: checks.append('RAW_OCCUPANCY_FALSE')
                if values['estimated_blade_0based'] != blade:
                    checks.append('RAW_ESTIMATED_BLADE_MISMATCH')
            if checks:
                counts.update(checks)
                failures.append({'raw_original_pixel_xy': [int(rx), int(ry)],
                                 'blur_halo_offset_xy': [dx, dy],
                                 'failed_checks': checks, **values})
    return {'expected_blade_0based': int(blade), 'gray_kernel_px': GRAY_KERNEL_PX,
            'raw_halo_radius_px': radius, 'raw_failed_pixel_count': len(failures),
            'raw_failure_counts': dict(counts), 'raw_failed_pixels': failures}


def _footprint_diagnostic(support, center, size, target_to_anchor=None, *, row=None, blade=None):
    """Mirror the existing Boolean footprint and retain its exact sample provenance.

    This is diagnostic data only. ``_footprint_known`` remains the authoritative
    gate. Sampling and short-circuit order mirror that function, including its
    two-stage warp interpolation and its conservative floor/ceil neighbors.
    ``estimated_blade`` failures are estimate-support failures, not proof of a
    physical occluder or a different real material identity.
    """
    r = size//2
    yy, xx = np.mgrid[-r:r+1, -r:r+1]
    offsets = np.column_stack([xx.reshape(-1), yy.reshape(-1)])
    requested = offsets + np.asarray(center)
    xy = requested
    parent = np.arange(len(xy))
    warp_xy, warp_corner = None, None
    detail = {'center_uv': np.asarray(center, float).tolist(), 'size_px': int(size),
              'requested_sample_count': len(xy), 'support_image_wh': [support.shape[1], support.shape[0]],
              'two_stage_warp_interpolation': target_to_anchor is not None,
              'passed': False, 'first_failed_check': None,
              'support_interpretation': 'measured input domains and contour-estimated blade support; no physical occlusion conclusion'}

    def provenance(index):
        record = {'requested_uv': requested[parent[index]].astype(float).tolist(),
                  'footprint_offset_xy': offsets[parent[index]].astype(int).tolist()}
        if warp_xy is not None:
            record.update(warp_integer_pixel_xy=warp_xy[index].astype(int).tolist(),
                          warp_grid_neighbor_xy=warp_corner[index])
        return record

    if target_to_anchor is not None:
        lo, hi = np.floor(xy), np.ceil(xy)
        corners = [(xs, ys, label) for xs, xname in ((lo[:, 0], 'floor'), (hi[:, 0], 'ceil'))
                   for ys, yname in ((lo[:, 1], 'floor'), (hi[:, 1], 'ceil'))
                   for label in ([xname, yname],)]
        xy = np.concatenate([np.column_stack([xs, ys]) for xs, ys, _ in corners])
        parent = np.tile(parent, 4)
        warp_xy = xy
        warp_corner = [label for _, _, label in corners for _ in range(len(requested))]
        homogeneous = np.column_stack([xy, np.ones(len(xy))]) @ target_to_anchor.T
        invalid = ~np.isfinite(homogeneous).all(axis=1)
        zero_depth = np.abs(homogeneous[:, 2]) < 1e-12
        if invalid.any() or zero_depth.any():
            index = int(np.flatnonzero(invalid | zero_depth)[0])
            check = 'WARP_NONFINITE_HOMOGENEOUS' if invalid[index] else 'WARP_ZERO_HOMOGENEOUS_DENOMINATOR'
            detail.update(first_failed_check=check, failure_counts={check: int((invalid | zero_depth).sum())},
                          first_failed_sample={**provenance(index),
                            'homogeneous_coordinate': [float(v) if np.isfinite(v) else None for v in homogeneous[index]]})
            return detail
        xy = homogeneous[:, :2]/homogeneous[:, 2, None]
    detail['mapped_sample_count'] = len(xy)
    h, w = support.shape
    finite = np.isfinite(xy).all(axis=1)
    outside = (xy < 0).any(axis=1) | (xy[:, 0] > w-1) | (xy[:, 1] > h-1)
    if (~finite).any() or outside.any():
        index = int(np.flatnonzero(~finite | outside)[0])
        check = 'SOURCE_INTERPOLATION_NONFINITE' if not finite[index] else 'SOURCE_INTERPOLATION_OUT_OF_IMAGE'
        detail.update(first_failed_check=check,
                      failure_counts={'SOURCE_INTERPOLATION_NONFINITE': int((~finite).sum()),
                                      'SOURCE_INTERPOLATION_OUT_OF_IMAGE': int((outside & finite).sum())},
                      first_failed_sample={**provenance(index),
                        'mapped_source_uv': [float(v) if np.isfinite(v) else None for v in xy[index]]})
        return detail
    lo, hi = np.floor(xy).astype(int), np.ceil(xy).astype(int)
    failed = []
    # Preserve the original y-then-x Boolean corner order.
    for ys, yname in ((lo[:, 1], 'floor'), (hi[:, 1], 'ceil')):
        for xs, xname in ((lo[:, 0], 'floor'), (hi[:, 0], 'ceil')):
            for index in np.flatnonzero(~support[ys, xs]):
                index = int(index)
                failed.append((index, int(xs[index]), int(ys[index]), [xname, yname]))
    if not failed:
        detail['passed'] = True
        return detail
    index, x, y, corner = failed[0]
    first = {**provenance(index), 'mapped_source_uv': xy[index].astype(float).tolist(),
             'source_bilinear_neighbor_xy': corner, 'support_original_pixel_xy': [x, y]}
    detail.update(first_failed_check='MEASURED_GRAY_SUPPORT_FALSE',
                  failed_bilinear_sample_count=len(failed),
                  failed_unique_support_pixel_count=len({(x, y) for _, x, y, _ in failed}),
                  first_failed_sample=first)
    if row is not None:
        raw = _raw_halo_diagnostic(row, blade, (x, y))
        first['raw_blur_halo'] = raw
        if raw['raw_failed_pixels']:
            detail['first_failed_check'] = raw['raw_failed_pixels'][0]['failed_checks'][0]
        # First sample explains the actual short circuit. Other sample classes
        # remain useful when a footprint crosses several measured/estimated domains.
        domains = Counter()
        for px, py in {(x, y) for _, x, y, _ in failed}:
            if not row['known'][py, px]: domains['CENTER_KNOWN_FALSE'] += 1
            if not row['occupancy'][py, px]: domains['CENTER_OCCUPANCY_FALSE'] += 1
            if row['estimated_blade'][py, px] != blade:
                domains['CENTER_ESTIMATED_BLADE_MISMATCH'] += 1
        detail['failed_support_center_counts'] = dict(domains)
    return detail


def _image_visibility_diagnostic(predicted, shape, descriptor_size):
    """Image bounds are separate from the estimated ray/first-hit visibility gate."""
    h, w = shape
    finite = predicted is not None and np.asarray(predicted).shape == (2,) and np.isfinite(predicted).all()
    radius = descriptor_size//2
    bbox = None if not finite else np.r_[np.asarray(predicted)-radius, np.asarray(predicted)+radius]
    return {'prediction_finite': bool(finite), 'target_image_wh': [w, h],
            'predicted_center_in_image': bool(finite and 0 <= predicted[0] <= w-1 and 0 <= predicted[1] <= h-1),
            'predicted_descriptor_bbox_xyxy': None if bbox is None else bbox.astype(float).tolist(),
            'predicted_descriptor_fully_in_image': bool(finite and (bbox[:2] >= 0).all() and bbox[2] <= w-1 and bbox[3] <= h-1),
            'interpretation': 'original image bounds only; estimated ray visibility is reported separately'}


def match_rgb(anchor, target, p, H, blade, config=TrackConfig()):
    """Normalize local surface motion, then measure RGB in both directions."""
    predicted = transform_pixel(H, p)
    detail = {'predicted_uv': None if predicted is None else predicted.tolist(),
              'observation_method': 'fixed anchor local triangle warp plus independent bidirectional RGB LK',
              'first_failed_check': None, 'footprint_checks': [],
              'check_order': 'existing gate precedence; RGB quality is calculated before LK/FB/support overrides',
              'support_failure_interpretation': 'input-domain or contour-estimate support rejection; not proof of physical occlusion'}
    detail['image_visibility'] = _image_visibility_diagnostic(predicted, target['gray'].shape, config.descriptor_size_px)
    if predicted is None:
        detail['first_failed_check'] = 'LOCAL_PIXEL_PROJECTION'
        return None, {}, 'INVALID_LOCAL_PIXEL_PROJECTION', detail
    source_support = measured_instance_support(anchor, blade)
    target_support = measured_instance_support(target, blade)
    inverse = np.linalg.inv(H)
    lk_support_size = config.lk_window_px+2*LK_GRADIENT_HALO_PX

    def footprint(stage, row, support, center, size, matrix=None):
        # Keep the existing Boolean function as the decision authority.
        passed = _footprint_known(support, center, size, matrix)
        diagnostic = _footprint_diagnostic(support, center, size, matrix, row=row, blade=blade)
        diagnostic.update(stage=stage, camera=row['camera'], frame=row['frame'],
                          boolean_gate_passed=passed,
                          boolean_diagnostic_agree=bool(passed == diagnostic['passed']),
                          lk_gradient_halo_px=LK_GRADIENT_HALO_PX if size == lk_support_size else 0)
        detail['footprint_checks'].append(diagnostic)
        if not passed:
            detail['first_failed_check'] = stage
            detail['first_failed_subcheck'] = diagnostic['first_failed_check']
        return passed

    # 31px LK is performed at the original anchor resolution, without a
    # coarse pyramid that could cross to another blade or artificial border.
    if not footprint('ANCHOR_LK_START_SUPPORT', anchor, source_support, p, lk_support_size):
        return None, {}, 'ANCHOR_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT', detail
    if not footprint('TARGET_PREDICTED_DESCRIPTOR_SUPPORT', target, target_support, predicted, config.descriptor_size_px):
        return None, {}, 'TARGET_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT', detail
    if not footprint('REFERENCE_ANCHOR_DESCRIPTOR_SUPPORT', anchor, source_support, predicted, config.descriptor_size_px, inverse):
        return None, {}, 'REFERENCE_ANCHOR_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT', detail
    ag, tg = anchor['gray'], target['gray']
    aligned = cv2.warpPerspective(tg, H, (ag.shape[1], ag.shape[0]),
                                  flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    start = np.asarray(p, np.float32).reshape(1, 1, 2)
    lk = dict(winSize=(config.lk_window_px,)*2, maxLevel=0,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                  config.lk_iterations, config.lk_epsilon_px), minEigThreshold=1e-5)
    local, forward, _ = cv2.calcOpticalFlowPyrLK(ag, aligned, start, None, **lk)
    if local is None or not np.isfinite(local).all():
        detail['first_failed_check'] = 'LK_FORWARD_FINITE_RESULT'
        return None, {}, 'LK_FAILED', detail
    returned, backward, _ = cv2.calcOpticalFlowPyrLK(aligned, ag, local, None, **lk)
    uv = transform_pixel(H, local.reshape(2))
    if returned is None or uv is None or not np.isfinite(returned).all():
        detail['first_failed_check'] = 'LK_BACKWARD_FINITE_RESULT_OR_TARGET_PROJECTION'
        return None, {}, 'LK_FAILED', detail
    fb = float(np.linalg.norm(returned.reshape(2)-p))
    detail.update(local_normalized_uv=local.reshape(2).astype(float).tolist(),
                  diagnostic_candidate_uv=uv.tolist(),
                  rgb_correction_px=(uv-predicted).tolist(),
                  returned_anchor_uv=returned.reshape(2).astype(float).tolist())
    reference = cv2.warpPerspective(ag.astype(np.float32), H, (tg.shape[1], tg.shape[0]),
                                    flags=cv2.INTER_LINEAR)
    quality, reason = _quality(reference, tg, predicted, uv, _roi(tg.shape, config), config)
    detail['rgb_quality_reason_before_overrides'] = reason
    quality.update(forward_backward_error_px=fb,
        forward_backward_coordinate_type='original_anchor_image_pixel_centers',
        forward_backward_method='bidirectional RGB LK after fixed local triangle normalization',
        descriptor_reference='original fixed anchor RGB warped by estimated local healthy triangle')
    if not (forward[0, 0] and backward[0, 0]):
        reason = 'LK_FAILED'
        detail.update(first_failed_check='LK_STATUS', forward_lk_status=int(forward[0, 0]),
                      backward_lk_status=int(backward[0, 0]))
    elif fb > config.forward_backward_max_px:
        reason = 'FORWARD_BACKWARD_MISMATCH'
        detail['first_failed_check'] = 'FORWARD_BACKWARD_MAX_PX'
    elif not footprint('TARGET_OBSERVED_DESCRIPTOR_SUPPORT', target, target_support, uv, config.descriptor_size_px):
        reason = 'TARGET_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT'
    elif not footprint('WARP_ANCHOR_DESCRIPTOR_SUPPORT', anchor, source_support, uv, config.descriptor_size_px, inverse):
        reason = 'WARP_ANCHOR_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT'
    # Check the full 31px windows used by LK in each direction too.
    elif not footprint('LK_FORWARD_ANCHOR_SUPPORT', anchor, source_support, local.reshape(2), lk_support_size):
        reason = 'LK_ANCHOR_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT'
    elif not footprint('LK_RETURNED_ANCHOR_SUPPORT', anchor, source_support, returned.reshape(2), lk_support_size):
        reason = 'LK_RETURNED_ANCHOR_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT'
    elif not (footprint('LK_TARGET_START_WARP_SUPPORT', target, target_support, p, lk_support_size, H) and
              footprint('LK_TARGET_FORWARD_WARP_SUPPORT', target, target_support, local.reshape(2), lk_support_size, H)):
        reason = 'LK_TARGET_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT'
    if reason is not None and detail['first_failed_check'] is None:
        detail['first_failed_check'] = 'RGB_QUALITY:' + reason
    return uv, quality, reason, detail


def track_fixed_anchors(original, rows, vertices, faces, config=TrackConfig()):
    """Retain the complete original anchor x frame x camera observation plan."""
    contexts = {(r['frame'], r['camera']): r for r in rows}
    frames = sorted({r['frame'] for r in rows}, key=lambda f: next(r['t'] for r in rows if r['frame'] == f))
    names = list(dict.fromkeys(r['camera'] for r in rows))
    tracks = []
    for old in original:
        anchor = copy.deepcopy(old['anchor']); p = np.asarray(anchor['uv'], float)
        source = contexts.get((anchor['frame'], anchor['camera']))
        if source is None or anchor['frame'] != frames[0] or p.shape != (2,) or not np.isfinite(p).all():
            raise ValueError('every fixed anchor must reference the declared first fit frame')
        if (anchor['rgb_sha256'] != source['rgb_sha256'] or
                not np.isclose(anchor['t'], source['t'], atol=1e-9, rtol=0)):
            raise ValueError('fixed anchor RGB/time identity mismatch')
        self_observations = [o for o in old['observations'] if o['frame'] == anchor['frame']
                             and o['camera'] == anchor['camera']]
        if len(self_observations) != 1:
            raise ValueError('fixed anchor requires one original self observation')
        self_obs = self_observations[0]
        if (self_obs.get('rgb_sha256') != source['rgb_sha256'] or
                not np.isclose(self_obs.get('t', np.nan), source['t'], atol=1e-9, rtol=0)):
            raise ValueError('fixed anchor self observation RGB/time identity mismatch')
        if self_obs.get('uv') is not None and not np.array_equal(np.asarray(self_obs['uv']), p):
            raise ValueError('fixed anchor self observation cannot relocate its original anchor')
        self_reason = quality_reason(self_obs)
        self_trace = {'status': 'ORIGINAL_SELF_RGB_OBSERVATION',
                      'original_self_status': self_obs.get('status'),
                      'original_self_quality': copy.deepcopy(self_obs.get('quality', {})),
                      'original_self_quality_reason': self_reason,
                      'image_visibility': _image_visibility_diagnostic(p, source['gray'].shape, config.descriptor_size_px),
                      'first_failed_check': 'ORIGINAL_SELF_QUALITY' if self_reason else None}
        binding = None if self_reason else ray_binding(source['cam'], vertices[frames[0]], faces, p)
        if binding is None and self_reason is None:
            self_reason = 'ANCHOR_NOT_ON_ESTIMATED_HEALTHY_SURFACE'
            self_trace['first_failed_check'] = 'ANCHOR_ESTIMATED_RAY_BINDING'
        self_trace['estimated_binding_diagnostic'] = binding
        if binding is not None:
            visible = binding_visibility(source['cam'], vertices[frames[0]], faces, binding)
            self_trace['estimated_visibility'] = visible
            if not visible['visible']:
                self_reason = 'ANCHOR_' + visible['reason']
                self_trace['first_failed_check'] = 'ANCHOR_ESTIMATED_VISIBILITY'
            else:
                support = measured_instance_support(source, binding['blade_0based'])
                size = config.lk_window_px+2*LK_GRADIENT_HALO_PX
                passed = _footprint_known(support, p, size)
                diagnostic = _footprint_diagnostic(support, p, size, row=source,
                                                   blade=binding['blade_0based'])
                diagnostic.update(stage='ANCHOR_SELF_LK_SUPPORT', camera=source['camera'],
                    frame=source['frame'], boolean_gate_passed=passed,
                    boolean_diagnostic_agree=bool(passed == diagnostic['passed']),
                    lk_gradient_halo_px=LK_GRADIENT_HALO_PX)
                self_trace['footprint_checks'] = [diagnostic]
                if not passed:
                    self_reason = 'ANCHOR_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT'
                    self_trace['first_failed_check'] = 'ANCHOR_SELF_LK_SUPPORT'
                    self_trace['first_failed_subcheck'] = diagnostic['first_failed_check']
        track = {'id': old['id'], 'anchor': anchor, 'observations': [],
                 'estimated_binding_diagnostic': binding,
                 'estimated_binding_policy': 'prediction only; geometry consumer independently binds its own contour estimate once'}
        active = {name: self_reason is None for name in names}
        termination = {name: self_reason for name in names}

        def identity(row, status, first_check=None):
            return {'track_id': old['id'], 'camera': row['camera'], 'frame': row['frame'],
                    't': row['t'], 'status': status, 'first_failed_check': first_check}

        self_status = self_obs.get('status') if quality_reason(self_obs) else self_reason
        self_origin = identity(source, self_status, self_trace['first_failed_check']) if self_reason else None
        if self_origin is not None:
            self_origin['termination_reason'] = self_reason
            self_origin['original_self_status'] = self_obs.get('status')
            self_origin['original_self_quality'] = copy.deepcopy(self_obs.get('quality', {}))
            self_origin['original_self_quality_reason'] = self_trace['original_self_quality_reason']
        origins = {name: copy.deepcopy(self_origin) for name in names}
        chains = {name: [copy.deepcopy(self_origin)] if self_reason else [] for name in names}
        for frame in frames:
            for name in names:
                row = contexts[(frame, name)]
                obs = {k: row[k] for k in ('frame', 't', 'camera', 'rgb_sha256')}
                obs.update(uv=None, status='IDENTITY_TERMINATED', accept=False, known=False, quality={})
                if frame == anchor['frame'] and name == anchor['camera']:
                    obs = copy.deepcopy(self_obs)
                    obs['material_trace'] = copy.deepcopy(self_trace)
                    if self_reason and quality_reason(self_obs) is None:
                        obs['diagnostic_candidate_uv'] = obs['uv']
                        obs.update(uv=None, status=self_reason, accept=False, known=False)
                    if self_reason:
                        obs.update(termination_origin=copy.deepcopy(origins[name]),
                                   termination_chain=copy.deepcopy(chains[name]))
                elif not active[name]:
                    obs['termination_reason'] = termination[name]
                    current = identity(row, 'IDENTITY_TERMINATED', 'BRANCH_ALREADY_TERMINATED')
                    chains[name].append(current)
                    obs.update(termination_origin=copy.deepcopy(origins[name]),
                               termination_chain=copy.deepcopy(chains[name]),
                               material_trace={'status': 'BRANCH_ALREADY_TERMINATED',
                                   'first_failed_check': 'BRANCH_ALREADY_TERMINATED',
                                   'origin_first_failed_check': origins[name]['first_failed_check']})
                else:
                    visibility = binding_visibility(row['cam'], vertices[frame], faces, binding)
                    trace = {'estimated_visibility': visibility,
                             'estimated_binding_diagnostic': binding}
                    uv, quality = None, {}
                    if not visibility['visible']:
                        reason = visibility['reason']
                        trace['first_failed_check'] = 'ESTIMATED_BINDING_VISIBILITY'
                    else:
                        H, prediction = local_homography(source['cam'], row['cam'],
                            vertices[frames[0]], vertices[frame], faces, binding)
                        trace['local_prediction'] = prediction
                        if H is None:
                            reason = prediction['status']
                            trace['first_failed_check'] = 'LOCAL_HOMOGRAPHY_PREDICTION'
                        else:
                            uv, quality, reason, rgb_detail = match_rgb(source, row, p, H,
                                                        binding['blade_0based'], config)
                            trace['rgb_localization'] = rgb_detail
                            trace['image_visibility'] = rgb_detail['image_visibility']
                            trace['first_failed_check'] = rgb_detail['first_failed_check']
                    obs.update(quality=quality, material_trace=trace)
                    if reason is None:
                        obs.update(uv=uv.tolist(), status='RGB_TRACK_OBSERVED', accept=True, known=True)
                    else:
                        obs['status'] = reason
                        if uv is not None:
                            obs['diagnostic_candidate_uv'] = uv.tolist()
                        active[name] = False; termination[name] = reason
                        origins[name] = identity(row, reason, trace['first_failed_check'])
                        origins[name]['termination_reason'] = reason
                        chains[name] = [copy.deepcopy(origins[name])]
                        obs.update(termination_origin=copy.deepcopy(origins[name]),
                                   termination_chain=copy.deepcopy(chains[name]))
                track['observations'].append(obs)
        tracks.append(track)
    return tracks


def prepare(data, seed_recon, anchors, output):
    data, output = Path(data).resolve(), Path(output).resolve()
    manifest_path = declared_input_path(data, 'input_bundle.json', 'declared_solver_input')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != 'blade-surface-input.v1':
        raise ValueError('material tracker requires explicit blade-surface-input.v1')
    for frame in manifest['frames']:
        if frame.get('split', 'fit') != 'fit' or any(c.get('split', 'fit') != 'fit' for c in frame['cameras']):
            raise ValueError('heldout/non-fit observations cannot enter material tracker')
    # Validate every declared observation path before the eager strict adapter.
    for frame in manifest['frames']:
        for cam in frame['cameras']:
            declared_input_path(data, cam['rgb'], 'RGB')
            declared_input_path(data, cam['mask'], 'derived_occupancy_mask')
    if manifest.get('annotations'):
        declared_input_path(data, manifest['annotations'], 'declared_RGB_unknown_domains')
    for path in (Path(seed_recon).resolve(), Path(anchors).resolve()):
        if any(p.lower() == 'evaluation' or p.lower().startswith('truth') or 'object_id' in p.lower()
               or p.lower().startswith('source-surface') for p in path.parts):
            raise ValueError('forbidden estimated seed/anchor input path')
    inputs = InputSequence(data)
    records = []
    try:
        while (record := inputs.read()) is not None:
            records.append(record)
        seed = json.loads(Path(seed_recon).read_text())
        validate_seed(seed, inputs, records)
        original_payload = json.loads(Path(anchors).read_text())
        if (original_payload.get('schema') != 'blade-texture-geometry.v1' or
                original_payload.get('pixel_coordinates') != 'original_image_pixel_centers'):
            raise ValueError('fixed anchors require existing RGB track schema and original pixel coordinates')
        original = original_payload['tracks']
        if any(not isinstance(t.get('id'), str) or not t['id'] for t in original) or len({t['id'] for t in original}) != len(original):
            raise ValueError('original fixed anchor IDs must be unique')
        rotor = Rotor(inputs.cfg); faces = rotor.tpl.faces()
        vertices = {r['frame']: rotor.forward(r['state'])[0] for r in seed['frames']}
        config = TrackConfig(affine_rgb_seed=False)
        method = ('fixed original RGB anchors; once-bound contour-estimated healthy triangle local '
                  'homography prediction; 31px bidirectional RGB LK; unchanged fixed sensor ROI '
                  'independent descriptor peak gates; measured known same-blade footprints')
        protocol = {'schema': 'blade-material-track-protocol.v1', 'config': asdict(config),
            'method': method, 'binding_visibility': 'estimated_surface.v1',
            'gray_blur_kernel_px': GRAY_KERNEL_PX, 'gray_raw_support_halo_px': GRAY_KERNEL_PX//2,
            'lk_gradient_support_halo_px': LK_GRADIENT_HALO_PX,
            'warp_support_interpolation': 'integer warp grid then subpixel descriptor/LK, both bilinear footprints checked',
            'diagnostic_contract': {'schema': 'material-footprint-diagnostic.v1',
                'decision_authority': 'unchanged _footprint_known Boolean and original RGB gates',
                'support_evidence': 'exact original pixels, raw known/occupancy/estimated blade, finite blur halo and both warp interpolation stages',
                'failure_interpretation': 'estimated/input support rejection; aggregate reason does not prove physical occlusion',
                'termination_evidence': 'original failure identity plus every subsequent terminated camera-plan item'},
            'no_reacquisition': True, 'cross_camera_policy': 'every camera requires independent bidirectional RGB match',
            'planned_frames': [r.frame for r in records],
            'planned_cameras': [c.name for c in records[0].cameras],
            'planned_track_ids': [t['id'] for t in original],
            'planned_observations': len(original)*len(records)*len(records[0].cameras),
            'estimated_seed': {'path': str(Path(seed_recon).resolve()), 'sha256': sha256(seed_recon),
                'role': 'same-fit contour-only estimated local motion prediction, never observed UV'},
            'fixed_anchors': {'path': str(Path(anchors).resolve()), 'sha256': sha256(anchors)}}
        output.mkdir(parents=True, exist_ok=False)
        (output/'protocol-before-match.json').write_text(json.dumps(protocol, indent=2)+'\n')
        frozen = output/'frozen-source'; frozen.mkdir()
        for name in ('material_tracks.py', 'surface_tracks.py', 'input_adapter.py', 'model.py',
                     'raster.py', 'camera.py', 'texture_geometry.py', 'rgb_masks.py', 'contour_support.py'):
            (frozen/name).write_bytes(Path(__file__).with_name(name).read_bytes())
        legacy = frozen/'strict-legacy'; legacy.mkdir()
        (legacy/'input_bundle.py').write_bytes((Path(__file__).resolve().parents[1]/
                                             'blade_recon/input_bundle.py').read_bytes())
        rows = []
        hashes = {a['path']: a['sha256'] for a in inputs.access_log if a['role'] == 'RGB'}
        for record, declaration in zip(records, manifest['frames']):
            for k, cam in enumerate(record.cameras):
                raster = rasterize(cam, vertices[record.frame], faces)
                instance = np.where(raster.fid >= 0, raster.fid//len(faces), -1)
                rgb = record.rgbs[k]
                rows.append({'frame': record.frame, 't': record.t, 'camera': cam.name, 'cam': cam,
                    'rgb': rgb, 'gray': material_gray(rgb, config), 'occupancy': record.masks[k] > 127,
                    'known': np.ones(record.masks[k].shape, bool) if record.known_contours is None
                             else record.known_contours[k], 'estimated_blade': instance,
                    'rgb_sha256': hashes[declaration['cameras'][k]['rgb']]})
        tracks = track_fixed_anchors(original, rows, vertices, faces, config)
        counts = Counter(o['status'] for t in tracks for o in t['observations'])
        observed = [o for t in tracks for o in t['observations'] if o['accept']]
        summary = {'planned_tracks': len(tracks), 'planned_observations': protocol['planned_observations'],
            'observed_observations': len(observed), 'status_counts': dict(counts),
            'observed_by_camera': dict(Counter(o['camera'] for o in observed)),
            'observed_cross_camera': sum(o['accept'] and o['camera'] != t['anchor']['camera']
                                         for t in tracks for o in t['observations']),
            'retained_original_anchor_ids': [t['id'] for t in tracks] == protocol['planned_track_ids']}
        payload = {'schema': 'blade-texture-geometry.v1',
            'pixel_coordinates': 'original_image_pixel_centers', 'binding_visibility': 'estimated_surface.v1',
            'provenance': {'role': 'solver_rgb_observations', 'method': method,
                'allowed_inputs': ['declared_rgb', 'derived_mask', 'camera_calibration', 'frame_t'],
                'access_log': inputs.access_log, 'estimated_seed': protocol['estimated_seed'],
                'fixed_anchors': protocol['fixed_anchors'],
                'protocol_sha256': sha256(output/'protocol-before-match.json'),
                'source_geometry_or_truth_read': False, 'heldout_RGB_read': False},
            'quality_gates': {key: getattr(config, key) for key in ('localization_sigma_max_px',
                'forward_backward_max_px', 'ncc_min', 'contrast_gray_min', 'identity_margin_min')},
            'tracks': tracks, 'summary': summary}
        (output/'tracks.json').write_text(json.dumps(payload, indent=2, allow_nan=False)+'\n')
        (output/'freeze.json').write_text(json.dumps({'schema': 'blade-material-track-freeze.v1',
            'tracks_sha256': sha256(output/'tracks.json'),
            'protocol_sha256': sha256(output/'protocol-before-match.json'),
            'source_sha256': {str(p.relative_to(frozen)): sha256(p)
                              for p in sorted(frozen.rglob('*')) if p.is_file()},
            'summary': summary}, indent=2)+'\n')
        return summary
    finally:
        inputs.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--seed-recon', required=True)
    parser.add_argument('--anchors', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.data, args.seed_recon, args.anchors, args.out), indent=2))
