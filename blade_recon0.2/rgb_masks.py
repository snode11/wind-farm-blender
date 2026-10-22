"""RGB-only instance occupancy from an independently acquired background.

The reference must use the same camera, illumination and background, with the
moving target absent. Geometry, pose, object-ID and silhouette labels are not
inputs. A reference is not estimated by taking a short target-present temporal
median, because persistent blade roots would be erased by that operation.
"""
from dataclasses import asdict, dataclass
import cv2
import numpy as np


@dataclass(frozen=True)
class BackgroundMaskConfig:
    rgb_sigma_px: float = 0.8
    rgb_delta_min: float = 12.0
    reference_noise_multiplier: float = 3.0
    reference_noise_sigma_px: float = 3.0
    neutral_saturation_max: int = 60
    neutral_value_min: int = 90
    red_saturation_min: int = 80
    red_value_min: int = 80
    close_kernel_px: int = 3
    component_area_min_px: int = 64


def _rgb_array(value):
    a = np.asarray(value)
    if a.ndim != 3 or a.shape[2] != 3 or a.dtype != np.uint8:
        raise ValueError('RGB observations must be uint8 HxWx3 arrays')
    return a


def known_observation_domain(shape, unknown_polygons=()):
    """Rasterize declared RGB unknown regions without changing occupancy.

    Unknown is an observation domain, not foreground or background. This helper
    never fills missing support; callers retain both the measured mask and this
    separate domain. Polygon coordinates use original image pixel centers.
    """
    if len(shape) != 2 or min(shape) <= 0:
        raise ValueError('known domain requires positive HxW dimensions')
    h, w = (int(v) for v in shape)
    known = np.ones((h, w), np.uint8)
    for polygon in unknown_polygons:
        p = np.asarray(polygon, float)
        if (p.ndim != 2 or p.shape[1] != 2 or len(p) < 3 or not np.isfinite(p).all()
                or (p < 0).any() or (p[:, 0] > w-1).any() or (p[:, 1] > h-1).any()):
            raise ValueError('unknown polygon must lie in original image pixels')
        cv2.fillPoly(known, [np.rint(p).astype(np.int32)], 0)
    return known.astype(bool)


def background_instance_mask(rgb, reference_rgb, config=BackgroundMaskConfig(), *, reference_repeat_rgb=None):
    """Return visible foreground occupancy, preserving bounded interior paint.

    Filling only enclosed holes prevents cracks or cosmetic paint from becoming
    contour observations. It does not extend foreground outside a measured
    contour or bridge target absence at an image edge.
    """
    rgb = _rgb_array(rgb)
    reference_rgb = _rgb_array(reference_rgb)
    if rgb.shape != reference_rgb.shape:
        raise ValueError('RGB and reference must have identical original dimensions')
    if config.close_kernel_px < 1 or config.close_kernel_px % 2 != 1:
        raise ValueError('close_kernel_px must be a positive odd integer')
    a = cv2.GaussianBlur(rgb.astype(np.float32), (0, 0), config.rgb_sigma_px)
    b = cv2.GaussianBlur(reference_rgb.astype(np.float32), (0, 0), config.rgb_sigma_px)
    delta = np.linalg.norm(a-b, axis=2)
    threshold = np.full(delta.shape, config.rgb_delta_min, np.float32)
    if reference_repeat_rgb is not None:
        repeat = _rgb_array(reference_repeat_rgb)
        if repeat.shape != rgb.shape:
            raise ValueError('repeat reference must have identical original dimensions')
        repeat = cv2.GaussianBlur(repeat.astype(np.float32), (0,0), config.rgb_sigma_px)
        noise_power = cv2.GaussianBlur(np.sum((b-repeat)**2, axis=2),
            (0,0), config.reference_noise_sigma_px)
        threshold = np.maximum(threshold, config.reference_noise_multiplier*np.sqrt(noise_power))
    hsv = cv2.cvtColor(np.clip(a, 0, 255).astype(np.uint8), cv2.COLOR_RGB2HSV)
    h,s,v = cv2.split(hsv)
    neutral = (s <= config.neutral_saturation_max) & (v >= config.neutral_value_min)
    red = ((h < 10) | (h > 170)) & (s >= config.red_saturation_min) & (v >= config.red_value_min)
    occupancy = ((delta >= threshold) & (neutral | red)).astype(np.uint8)*255
    if config.close_kernel_px > 1:
        occupancy = cv2.morphologyEx(occupancy, cv2.MORPH_CLOSE,
            np.ones((config.close_kernel_px, config.close_kernel_px), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(occupancy, 8)
    kept = np.zeros_like(occupancy)
    for label in range(1,n):
        if stats[label,cv2.CC_STAT_AREA] >= config.component_area_min_px:
            kept[labels == label] = 255
    # Preserve every eight-connected exterior background channel, including
    # diagonal pixel gaps that an external contour's filled polygon closes.
    # Padding gives all image-edge background a single exterior flood seed.
    background = np.pad((kept == 0).astype(np.uint8), 1, constant_values=1)
    cv2.floodFill(background, None, (0, 0), 2, flags=8)
    bounded_holes = background[1:-1, 1:-1] == 1
    result = kept.copy()
    result[bounded_holes] = 255
    return result, {'config':asdict(config), 'foreground_pixels':int(np.count_nonzero(result)),
        'hole_policy':'fill bounded background only; preserve eight-connected image-edge background',
        'filled_bounded_hole_pixels':int(np.count_nonzero(bounded_holes)),
        'reference_noise_calibrated':reference_repeat_rgb is not None,
        'rgb_delta_threshold_mean':float(threshold.mean()),
        'rgb_delta_threshold_max':float(threshold.max()),
        'rgb_only':True, 'reference_kind':'independent target-absent RGB',
        'missing_boundary_policy':'retain measured support; do not infer source geometry'}
