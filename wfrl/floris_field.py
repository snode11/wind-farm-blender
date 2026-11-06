"""FLORIS 接口与水平切面数值提取，供研究探针和静态绘图使用。"""
import numpy as np


def get_floris(env):
    """从 WFCRL 环境及其包装器取得 FLORIS interface。"""
    obj = env
    for _ in range(6):
        if hasattr(obj, "mdp"):
            return obj.mdp.interface.fi
        obj = getattr(obj, "env", obj)
    raise RuntimeError("找不到 mdp / FLORIS interface")


def extract_wake_plane(fi, hub_h, yaw_angles=None):
    """返回 (X, Y, Z, U)，各为 (nx, ny, 1)，第一维沿 X。"""
    if yaw_angles is None:
        yaw_angles = fi.floris.farm.yaw_angles
    plane = fi.calculate_horizontal_plane(height=hub_h, yaw_angles=yaw_angles)
    df = plane.df
    n = int(round(np.sqrt(len(df))))

    def grid(col):
        return df[col].values.reshape(n, n).T[:, :, None]

    return grid("x1"), grid("x2"), np.full((n, n, 1), hub_h), grid("u")
