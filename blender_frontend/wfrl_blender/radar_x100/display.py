"""Read-only point styling and sensor-coordinate projections for X100 displays."""
import math
import numpy as np

VIEWS = ('SENSOR', 'FRONT', 'TOP', 'SIDE')
COLORS = ('RANGE', 'VELOCITY', 'HEIGHT', 'RCS', 'SNR', 'MS')
COLOR_LABELS = dict(RANGE='距离 (m)', VELOCITY='径向速度 (m/s)', HEIGHT='高度 (m)',
                    RCS='RCS（原始值）', SNR='SNR（原始值）', MS='动静（原始值）')
VIEW_LABELS = dict(SENSOR='传感器', FRONT='正视', TOP='俯视', SIDE='侧视')
SENSOR_AXES = 'SENSOR_X_FORWARD_Y_LEFT_Z_UP'


def world_xyz(frame):
    xyz = frame.xyz[frame.finite_mask]
    transform = frame.spatial.get('world_from_sensor')
    if transform is not None and frame.spatial.get('alignment') in {'SIMULATION', 'STATIC_CALIBRATION', 'VERIFIED'}:
        matrix = np.asarray(transform)
        xyz = xyz @ matrix[:3, :3].T + matrix[:3, 3]
    return xyz


def point_style(frame, color_by):
    """Match the scene overlay; limits describe this frame, unknown values are gray."""
    mask = frame.finite_mask
    if color_by == 'RANGE':
        values = np.linalg.norm(frame.xyz[mask], axis=1)
    elif color_by == 'HEIGHT':
        values = world_xyz(frame)[:, 2]
    elif color_by == 'VELOCITY':
        values = frame.radial_velocity_m_s[mask] if frame.radial_velocity_m_s is not None else np.full(int(mask.sum()), np.nan)
    else:
        values = frame.points[color_by.lower()][mask]
    finite = np.isfinite(values)
    colors = np.tile([.5, .5, .5, 1.], (len(values), 1)).astype(np.float32)
    limits = None
    if finite.any():
        lo, hi = float(values[finite].min()), float(values[finite].max())
        scaled = (values[finite] - lo) / max(hi - lo, 1e-9)
        colors[finite, :3] = np.column_stack((scaled, 1 - np.abs(2 * scaled - 1), 1 - scaled))
        limits = lo, hi
    return colors, limits, int((~finite).sum())


def project(xyz, view):
    """Project only supplied points; no resampling, accumulation or world-pose lookup."""
    xyz = np.asarray(xyz)
    keep = np.ones(len(xyz), dtype=bool)
    if view == 'SENSOR':
        keep = xyz[:, 0] > 0
        xyz = xyz[keep]
        azimuth = np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0]))
        elevation = np.degrees(np.arctan2(xyz[:, 2], np.hypot(xyz[:, 0], xyz[:, 1])))
        return np.column_stack((-azimuth, elevation)), keep
    if view == 'FRONT':
        return np.column_stack((-xyz[:, 1], xyz[:, 2])), keep
    if view == 'TOP':
        return np.column_stack((-xyz[:, 1], xyz[:, 0])), keep
    if view == 'SIDE':
        return xyz[:, (0, 2)], keep
    raise ValueError('Unknown X100 display view: ' + str(view))


def fit_bounds(points, view):
    if view == 'SENSOR':
        return (-66., -26., 132., 52.)
    points = np.concatenate((np.asarray(points).reshape(-1, 2), np.zeros((1, 2))))
    lo, hi = points.min(axis=0), points.max(axis=0)
    span = np.maximum(hi - lo, 2.) * 1.15
    center = (hi + lo) / 2
    return tuple(center - span / 2) + tuple(span)


def fit_canvas(bounds, rectangle, zoom=1., pan=(0., 0.)):
    x, y, width, height = rectangle
    bx, by, bw, bh = bounds
    factor = min(width / bw, height / bh) * zoom
    return factor, x + width / 2 - ((bx + bw / 2) + pan[0]) * factor, y + height / 2 - ((by + bh / 2) + pan[1]) * factor


def ticks(lo, hi, target=6):
    raw = max(hi - lo, 1e-9) / target
    power = 10 ** math.floor(math.log10(raw))
    step = next(value * power for value in (1, 2, 5, 10) if value * power >= raw)
    return np.arange(math.ceil(lo / step), math.floor(hi / step) + 1) * step
