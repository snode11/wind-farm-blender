"""Read-only point styling and sensor-coordinate projections for X100 displays."""
import math
import numpy as np
from .core import schema

VIEWS = ('SENSOR', 'FRONT', 'TOP', 'SIDE', 'NACELLE')
COLORS = ('RANGE', 'VELOCITY', 'HEIGHT', 'RCS', 'SNR', 'MS')
COLOR_LABELS = dict(RANGE='距离 (m)', VELOCITY='径向速度 (m/s)', HEIGHT='高度 (m)',
                    RCS='RCS（原始值）', SNR='SNR（原始值）', MS='动静（原始值）')
VIEW_LABELS = dict(SENSOR='传感器', FRONT='正视', TOP='俯视', SIDE='侧视', NACELLE='机舱XZ')
SENSOR_AXES = 'SENSOR_X_FORWARD_Y_LEFT_Z_UP'


class CanvasCache:
    """One current value per auxiliary layer and drawing context; no frame history."""
    SLOTS = ('chrome', 'grid', 'legend', 'analysis')

    def __init__(self):
        self.context = None
        self.items = {}
        self.builds = dict.fromkeys(self.SLOTS, 0)
        self.hits = dict.fromkeys(self.SLOTS, 0)

    def bind(self, context):
        if context == self.context:
            return False
        self.clear()
        self.context = context
        return True

    def get(self, slot, key, build):
        if slot not in self.SLOTS:
            raise ValueError('Unknown auxiliary canvas layer')
        cached = self.items.get(slot)
        if cached is not None and cached[0] == key:
            self.hits[slot] += 1
            return cached[1]
        value = build()
        self.items[slot] = key, value
        self.builds[slot] += 1
        return value

    def clear(self):
        self.items.clear()
        self.context = None


def source_label(frame):
    suffix = ' · 显式声明' if frame.provenance.get('source_declaration') == 'EXPLICIT' else ''
    return frame.source_kind + suffix


def world_xyz(frame):
    xyz = frame.xyz[frame.finite_mask]
    transform = frame.spatial.get('world_from_sensor')
    if transform is not None and frame.spatial.get('alignment') in {'SIMULATION', 'STATIC_CALIBRATION', 'VERIFIED'}:
        matrix = np.asarray(transform)
        xyz = xyz @ matrix[:3, :3].T + matrix[:3, 3]
    return xyz


def style_key(scene):
    return (scene.wfrl_x100_color, scene.wfrl_x100_color_scale,
            scene.wfrl_x100_velocity_limit)


def point_style(frame, color_by, *, color_scale='FIXED', velocity_limit=10.):
    """Use stable categories and optional symmetric velocity limits; unknown values are gray."""
    if color_scale not in {'FIXED', 'FRAME'} or not math.isfinite(velocity_limit) or velocity_limit <= 0:
        raise ValueError('Invalid point color scale')
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
        if color_by == 'MS':
            lo, hi = 0., 1.
        elif color_by == 'VELOCITY' and color_scale == 'FIXED':
            lo, hi = -velocity_limit, velocity_limit
        scaled = np.clip((values[finite] - lo) / max(hi - lo, 1e-9), 0, 1)
        colors[finite, :3] = np.column_stack((scaled, 1 - np.abs(2 * scaled - 1), 1 - scaled))
        limits = lo, hi
    return colors, limits, int((~finite).sum())


def canvas_layout(width, height, ui_scale, *, velocity=False):
    """Keep readable text while wrapping controls into the available canvas width."""
    scale = max(1., min(float(ui_scale), 1.5, width/900., height/650.))
    w, h = width / scale, height / scale
    actions = [(v, VIEW_LABELS[v]) for v in VIEWS] + [('RESET', '复位'), ('COLOR', '着色')]
    if velocity:
        actions.append(('SCALE', '色标'))
    actions.append(('CLOSE', '关闭'))
    x, top, rows, buttons = 16., h-92., 1, []
    for action, label in actions:
        if x+70 > w-16 and x > 16:
            x, top, rows = 16., top-34., rows+1
        buttons.append((action, label, tuple(v*scale for v in (x, top, 70., 28.))))
        x += 76.
    info_top = top-24.
    bottom = 110.
    rectangle = tuple(v*scale for v in (64., bottom, max(1., w-84.), max(1., info_top-76.-bottom)))
    return dict(scale=scale, buttons=buttons, info_top=info_top*scale,
                rectangle=rectangle, compact=w < 600, rows=rows)


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
    if view in {'SIDE', 'NACELLE'}:
        return xyz[:, (0, 2)], keep
    raise ValueError('Unknown X100 display view: ' + str(view))


def fit_bounds(points, view):
    if view == 'SENSOR':
        azimuth = schema.PROFILE['azimuth_deg'] + schema.DISPLAY_DEFAULTS['sensor_margin_deg'][0]
        elevation = schema.PROFILE['elevation_deg'] + schema.DISPLAY_DEFAULTS['sensor_margin_deg'][1]
        return (-azimuth, -elevation, 2*azimuth, 2*elevation)
    points = np.concatenate((np.asarray(points).reshape(-1, 2), np.zeros((1, 2))))
    lo, hi = points.min(axis=0), points.max(axis=0)
    span = np.maximum(hi - lo, schema.DISPLAY_DEFAULTS['orthographic_min_span_m']) * schema.DISPLAY_DEFAULTS['orthographic_margin']
    center = (hi + lo) / 2
    return tuple(center - span / 2) + tuple(span)


def fit_canvas(bounds, rectangle, zoom=1., pan=(0., 0.)):
    x, y, width, height = rectangle
    bx, by, bw, bh = bounds
    factor = min(width / bw, height / bh) * zoom
    return factor, x + width / 2 - ((bx + bw / 2) + pan[0]) * factor, y + height / 2 - ((by + bh / 2) + pan[1]) * factor


def grid_geometry(view, bounds, rectangle, zoom, pan, scale):
    """Pixel-space grid, axes, border and tick labels for a single canvas transform."""
    left, bottom, width, height = rectangle
    factor, tx, ty = fit_canvas(bounds, rectangle, zoom, pan)
    xlo, xhi = (left-tx)/factor, (left+width-tx)/factor
    ylo, yhi = (bottom-ty)/factor, (bottom+height-ty)/factor
    lines, axes, labels = [], [], []
    for tick in ticks(xlo, xhi, target=max(1, min(6, int(width/(76*scale))))):
        px = tx+tick*factor
        lines.extend(((px, bottom), (px, bottom+height)))
        label = -tick if view in {'SENSOR', 'FRONT', 'TOP'} else tick
        labels.append((f'{label:g}', px-10*scale, bottom-21*scale))
    for tick in ticks(ylo, yhi, target=max(1, min(6, int(height/(26*scale))))):
        py = ty+tick*factor
        lines.extend(((left, py), (left+width, py)))
        labels.append((f'{tick:g}', 22*scale, py-4*scale))
    if left <= tx <= left+width:
        axes.extend(((tx, bottom), (tx, bottom+height)))
    if bottom <= ty <= bottom+height:
        axes.extend(((left, ty), (left+width, ty)))
    border = ((left, bottom), (left+width, bottom), (left+width, bottom), (left+width, bottom+height),
              (left+width, bottom+height), (left, bottom+height), (left, bottom+height), (left, bottom))
    return ((factor, tx, ty), ((lines, (.09, .14, .2, 1)),
            (axes, (.2, .34, .43, 1)), (border, (.22, .3, .39, 1))), labels)


def ticks(lo, hi, target=6):
    raw = max(hi - lo, 1e-9) / target
    power = 10 ** math.floor(math.log10(raw))
    step = next(value * power for value in (1, 2, 5, 10) if value * power >= raw)
    return np.arange(math.ceil(lo / step), math.floor(hi / step) + 1) * step
