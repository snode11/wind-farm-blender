"""FLORIS 接口与水平切面数值提取，供研究探针和静态绘图使用。"""
from numbers import Real

import numpy as np


def get_floris(env):
    """从 WFCRL 环境及其包装器取得 FLORIS interface。"""
    obj = env
    seen = set()
    while obj is not None and id(obj) not in seen:
        seen.add(id(obj))
        interface = getattr(getattr(obj, "mdp", None), "interface", None)
        fi = getattr(interface, "fi", None)
        if fi is not None:
            return fi
        obj = getattr(obj, "env", None)
    raise ValueError("找不到 mdp / FLORIS interface")


def extract_wake_plane(fi, hub_h, yaw_angles=None):
    """返回 (X, Y, Z, U)，各为 (nx, ny, 1)，第一维沿 X。"""
    if isinstance(hub_h, (bool, np.bool_)) or not isinstance(hub_h, Real):
        raise ValueError("hub_h 必须为有限高度，单位 m")
    try:
        hub_h = float(hub_h)
    except (ValueError, OverflowError) as exc:
        raise ValueError("hub_h 必须为有限高度，单位 m") from exc
    if not np.isfinite(hub_h):
        raise ValueError("hub_h 必须为有限高度，单位 m")
    if yaw_angles is None:
        try:
            yaw_angles = fi.floris.farm.yaw_angles
        except AttributeError as exc:
            raise ValueError("FLORIS 缺少默认 yaw_angles") from exc
    calculate = getattr(fi, "calculate_horizontal_plane", None)
    if not callable(calculate):
        raise ValueError("FLORIS 缺少水平切面接口")
    plane = calculate(height=hub_h, yaw_angles=yaw_angles)
    try:
        x, y, u = (np.asarray(plane.df[col].values, dtype=float) for col in ("x1", "x2", "u"))
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("FLORIS 切面必须含数值列 x1、x2、u") from exc
    if x.ndim != 1 or y.shape != x.shape or u.shape != x.shape or not x.size:
        raise ValueError("FLORIS 切面列须为等长非空向量")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("FLORIS 切面坐标必须有限")
    xs, ix = np.unique(x, return_inverse=True)
    ys, iy = np.unique(y, return_inverse=True)
    if xs.size * ys.size != x.size or np.unique(ix * ys.size + iy).size != x.size:
        raise ValueError("FLORIS 切面必须为无重复或缺失点的矩形网格")
    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    velocities = np.empty(xx.shape, dtype=float)
    velocities[ix, iy] = u
    return (xx[:, :, None], yy[:, :, None], np.full((*xx.shape, 1), hub_h),
            velocities[:, :, None])
