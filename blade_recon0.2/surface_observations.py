"""RGB-only surface regions and frozen triangle bindings for correspondence audits.

This module is deliberately outside recon's geometric objective. RGB detection
never receives model points, source texture, truth poses, projected source ROIs,
or object-ID masks. A binding is a candidate until independent views validate it.
"""
import hashlib

import cv2
import numpy as np

from raster import rasterize, vertex_frames


DETECTION = {'sensor_roi_fraction_xyxy': [.25, .20, .75, .80],
             'gaussian_sigma_px': 1.0, 'anomaly_threshold_gray': 15.0,
             'minimum_component_pixels': 30, 'dominance_ratio_min': 2.0,
             'missing_only_local_median_kernel_px': 201}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def detect_region(rgb, polarity, params=None):
    """Strongest bounded anomaly in a fixed inspection sensor ROI.

    The ROI is a camera operating condition, not a source-coordinate crop.
    Border-touching components and ambiguous competing components are rejected.
    Missing returns a zero mask; callers must keep it in the planned denominator.
    """
    p = dict(DETECTION if params is None else params)
    if polarity not in ('dark', 'bright'):
        raise ValueError('region polarity must be dark or bright')
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError('detector requires original uint8 RGB')
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = np.rint(np.asarray(p['sensor_roi_fraction_xyxy']) * [w, h, w, h]).astype(int)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    blur = cv2.GaussianBlur(gray, (0, 0), p['gaussian_sigma_px'])
    bg = float(np.median(blur[y0:y1, x0:x1]))
    response = (bg - blur) if polarity == 'dark' else (blur - bg)

    def bounded_components(response):
        candidate = np.zeros((h, w), np.uint8)
        candidate[y0:y1, x0:x1] = response[y0:y1, x0:x1] >= p['anomaly_threshold_gray']
        n, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
        components = []
        for k in range(1, n):
            x, y, ww, hh, area = stats[k]
            if (area < p['minimum_component_pixels'] or x <= x0 or y <= y0
                    or x + ww >= x1 or y + hh >= y1):
                continue
            m = labels == k
            components.append((float(response[m].sum()), k))
        components.sort(reverse=True)
        return labels, components

    labels, components = bounded_components(response)
    background_method = 'sensor ROI median'
    if not components:
        # A smooth illumination slope can join an anomaly to the ROI boundary
        # under a single constant background. Use a fixed RGB-only local median
        # only for this missing case; observed/ambiguous regions are unchanged.
        local = cv2.medianBlur(np.clip(blur, 0, 255).astype(np.uint8),
                               p['missing_only_local_median_kernel_px']).astype(np.float32)
        response = local - blur if polarity == 'dark' else blur - local
        labels, components = bounded_components(response)
        background_method = 'fixed local RGB median after missing global component'
    mask = np.zeros((h, w), bool)
    status = 'MISSING_RGB_FEATURE'
    if components:
        if len(components) > 1 and components[0][0] < p['dominance_ratio_min'] * components[1][0]:
            status = 'AMBIGUOUS_RGB_FEATURE'
        else:
            mask = labels == components[0][1]
            status = 'RGB_FEATURE_OBSERVED'
    ys, xs = np.nonzero(mask)
    boundary = mask & (cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)) == 0)
    by, bx = np.nonzero(boundary)
    centroid = None
    if len(xs):
        weight = response[ys, xs]
        centroid = [float(np.dot(xs, weight) / weight.sum()),
                    float(np.dot(ys, weight) / weight.sum())]
    detail = {'status': status, 'background_gray': bg, 'sensor_roi_xyxy': [int(v) for v in (x0, y0, x1, y1)],
              'feature_pixels': int(mask.sum()), 'centroid_uv': centroid,
              'boundary_uv': np.column_stack([bx, by]).tolist(),
              'candidate_count': len(components), 'polarity': polarity,
              'background_method': background_method,
              'method': 'fixed sensor ROI RGB luminance anomaly; no model/source projection'}
    return mask, detail


def bind_observed_boundary(cam, rotor, state, mask):
    """Bind actual RGB boundary pixels to visible reconstructed triangles.

    Stable (blade, face, perspective barycentric) identities are retained across
    frames. No nearest-source-point search, truth alignment or reacquisition.
    """
    vertices, _ = rotor.forward(np.asarray(state, float))
    faces = rotor.tpl.faces()
    normals = np.stack([vertex_frames(v)[0] for v in vertices])
    ras = rasterize(cam, vertices, faces, normals)
    boundary = mask & (cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)) == 0)
    selected = boundary[ras.ys, ras.xs]
    expected = int(boundary.sum())
    if not selected.any():
        return {'status': 'NO_VISIBLE_RECONSTRUCTED_SURFACE', 'coverage': 0., 'boundary_pixels': expected}
    blade, count = np.unique(ras.blade[selected], return_counts=True)
    dominant = int(blade[np.argmax(count)])
    selected &= ras.blade == dominant
    total = int(selected.sum())
    return {'status': 'CANDIDATE_FIXED_TRIANGLE_BINDING', 'blade_0based': dominant,
            'coverage': total / max(expected, 1), 'boundary_pixels': expected,
            'bound_pixels': total, 'face': ras.face[selected].tolist(),
            'barycentric': ras.bary[selected].tolist(),
            'anchor_uv': np.column_stack([ras.xs[selected], ras.ys[selected]]).tolist(),
            'binding_method': 'RGB pixel -> nearest visible reconstructed raster triangle; no source truth'}


def project_binding(cam, rotor, state, binding):
    """Replay a fixed triangle binding using a new recovered state/calibration."""
    vertices, _ = rotor.forward(np.asarray(state, float))
    face = np.asarray(binding['face'], int)
    bary = np.asarray(binding['barycentric'], float)
    blade = binding['blade_0based']
    points = np.einsum('pck,pc->pk', vertices[blade].reshape(-1, 3)[rotor.tpl.faces()[face]], bary)
    uv, depth = cam.project(points)
    return uv, depth, points
