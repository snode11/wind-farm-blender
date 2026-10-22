"""Ideal rectangular pinhole projection. All matrices are row-major, metres.

Blender camera axes: right +X, up +Y, forward -Z. Pixel centres start at
(0, 0) at the top left; borders lie at -.5 and size-.5. No bpy dependency.
"""
import math

DEFAULT_LONG_EDGE = 1920
MIN_SHORT_EDGE = 16
DEFAULT_VFOV = math.degrees(2 * math.atan(math.tan(math.radians(75 / 2)) / (16 / 9)))


def finite(*values):
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
        raise ValueError('投影参数必须是有限数值')


def validate(hfov, vfov, near=.005, far=30000):
    finite(hfov, vfov, near, far)
    if not (1 <= hfov <= 170 and 1 <= vfov <= 170):
        raise ValueError('HFOV / VFOV 必须在 1–170°，均为完整视场角')
    if not 0 < near < far:
        raise ValueError('裁剪距离须满足 0 < near < far')


def aspect(hfov, vfov):
    validate(hfov, vfov)
    return math.tan(math.radians(hfov / 2)) / math.tan(math.radians(vfov / 2))


def resolution(hfov, vfov, long_edge=DEFAULT_LONG_EDGE, max_size=None):
    ratio = aspect(hfov, vfov)
    if isinstance(long_edge, bool) or not isinstance(long_edge, int) or long_edge < MIN_SHORT_EDGE:
        raise ValueError('输出长边须为至少 16 的整数像素')
    w, h = (long_edge, round(long_edge / ratio)) if ratio >= 1 else (round(long_edge * ratio), long_edge)
    if min(w, h) < MIN_SHORT_EDGE:
        raise ValueError('画幅比例过大，短边不足 16 px；请提高长边预算或修改视场角')
    if max_size is not None and max(w, h) > max_size:
        raise ValueError(f'输出 {w}×{h} 超过当前 GPU 纹理上限 {max_size}')
    return w, h


def pixel_scales(hfov, vfov, width, height):
    q = aspect(hfov, vfov) * height / width
    return (q, 1.) if q >= 1 else (1., 1. / q)


def projection(hfov, vfov, near=.005, far=30000):
    validate(hfov, vfov, near, far)
    x, y = 1 / math.tan(math.radians(hfov / 2)), 1 / math.tan(math.radians(vfov / 2))
    return ((x, 0., 0., 0.), (0., y, 0., 0.),
            (0., 0., -(far + near) / (far - near), -2 * far * near / (far - near)),
            (0., 0., -1., 0.))


def intrinsics(hfov, vfov, width, height):
    validate(hfov, vfov)
    return ((width / (2 * math.tan(math.radians(hfov / 2))), 0., (width - 1) / 2),
            (0., height / (2 * math.tan(math.radians(vfov / 2))), (height - 1) / 2), (0., 0., 1.))


def rotation(yaw, pitch, roll):
    finite(yaw, pitch, roll)
    if not -90 <= pitch <= 90:
        raise ValueError('俯仰角须在 −90–90°')
    y, p, r = map(math.radians, (yaw, pitch, roll))
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    d, right, up = (cp * cy, cp * sy, sp), (sy, -cy, 0), (-sp * cy, -sp * sy, cp)
    return tuple((cr * right[i] + sr * up[i], -sr * right[i] + cr * up[i], -d[i]) for i in range(3))


def optical_axis(yaw, pitch):
    r = rotation(yaw, pitch, 0)
    return tuple(-row[2] for row in r)


def frustum_corners(hfov, vfov, depth):
    validate(hfov, vfov)
    finite(depth)
    if depth <= 0:
        raise ValueError('视锥深度必须大于零')
    x, y = depth * math.tan(math.radians(hfov / 2)), depth * math.tan(math.radians(vfov / 2))
    return ((-x, -y, -depth), (x, -y, -depth), (x, y, -depth), (-x, y, -depth))


def linked_fov(hfov, vfov, factor):
    validate(hfov, vfov)
    finite(factor)
    if factor <= 0:
        raise ValueError('联动视场比例必须为正')
    values = tuple(math.degrees(2 * math.atan(math.tan(math.radians(v / 2)) * factor)) for v in (hfov, vfov))
    validate(*values)
    return values


def cv_from_world(view_matrix):
    return tuple(tuple(v * (1 if i in (0, 3) else -1) for v in row) for i, row in enumerate(view_matrix))


MAX_SAMPLES = 100000


def sample_times(start, end, step, limit=MAX_SAMPLES):
    finite(start, end, step)
    if step <= 0 or end < start:
        raise ValueError('采样间隔须大于零，结束时间不能早于开始时间')
    count = math.floor((end - start) / step + 1e-9) + 1
    if count > limit:
        raise ValueError(f'一次最多采集 {limit} 个时刻')
    return [min(end, start + i * step) for i in range(count)]
