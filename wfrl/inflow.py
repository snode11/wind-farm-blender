"""TurbSim `.bts` 湍流盒子的元信息 —— 主要为了拿对 P/u³ 的那个分母。

`WindType=3` 下 wfcrl **从不写** InflowWind 的 `HWindSpeed`（`simul_utils.py:172-191`
只设 `WindType`/`FileName_BTS`，`HWindSpeed` 那支在 else 分支里），所以
`read_inflow_info()` 返回的是模板里的旧值，和实际来流没关系。归一化功率拿它做分母
会静默地错 —— 这是本模块存在的唯一理由。

真值只能从 `.bts` 自己算，而且必须取**轮毂高度那一层**：模板这只盒子全场均值
8.75 m/s，插到 90 m 只有 8.00 m/s，差的 9.5% 全在竖向剪切上（5 m 处 4.49 →
345 m 处 10.47）。拿全场均值当自由来流，P/u³ 会整体偏低 ~25%。
网格是 5 m 起、10 m 一层 ⇒ 没有 90 m 这一层，最近的 85 m 是 7.909、95 m 是 8.087，
所以插值不是讲究而是必要（取最近层就差 0.09 m/s）。
"""
import os

import numpy as np

# wfcrl 用包内模板目录，不是仓库的 inputs/template/（后者没有 .bts）。
# 出处：simul_utils.py:125 的 TEMPLATE_DIR.format("fastfarm") + "FarmInputs/"。
_TEMPLATE_REL = os.path.join(
    "simulators", "fastfarm", "inputs", "template", "FarmInputs")


def template_dir():
    """wfcrl 包内的 fastfarm 模板 FarmInputs 目录。"""
    import wfcrl
    return os.path.join(os.path.dirname(wfcrl.__file__), _TEMPLATE_REL)


def resolve_bts(name):
    """把 `wind_time_series` 的裸文件名解析成绝对路径（找不到抛错，别静默）。"""
    if os.path.isabs(name) and os.path.exists(name):
        return name
    p = os.path.join(template_dir(), name)
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"找不到湍流盒子 {name!r}；wfcrl 在模板目录里找它：{template_dir()}")
    return p


class BtsInfo:
    """`.bts` 的关键标量；读一次 29 MB 文件，缓存住。

    `u_hub` 是轮毂高度**水平均值**，即自由来流；`ti` 是同一层的湍流强度。
    `t_span` 是盒子的时间跨度 —— 超过它 InflowWind 会外推/报错，回合别开太长。
    """

    __slots__ = ("path", "u_hub", "ti", "t_span", "dt", "z", "y", "z_used")

    def __repr__(self):
        return (f"BtsInfo({os.path.basename(self.path)}: u_hub={self.u_hub:.2f} "
                f"m/s, TI={self.ti:.1f}%, {self.t_span:.0f}s)")


_CACHE = {}


def bts_info(name, z_hub=90.0):
    """读 `.bts`，返回 `BtsInfo`。同一个 (文件, 高度) 只读一次。"""
    key = (name, float(z_hub))
    if key in _CACHE:
        return _CACHE[key]
    from openfast_toolbox.io import TurbSimFile

    path = resolve_bts(name)
    ts = TurbSimFile(path)
    u = np.asarray(ts["u"])[0]                 # (nt, ny, nz)，只要纵向分量
    z, t = np.asarray(ts["z"]), np.asarray(ts["t"])

    # 网格未必落在 z_hub 上（模板是 5 m 起、10 m 一层 ⇒ 85/95，没有 90），
    # 所以在两层之间按均值线性插值，而不是取最近层 —— 实测 85 m 7.909、95 m 8.087，
    # 取最近层会差 0.09 m/s（TI 仍用最近层，方差不好插）。
    prof = u.mean(axis=(0, 1))                 # (nz,) 各高度的时空均值
    u_hub = float(np.interp(z_hub, z, prof))
    iz = int(np.argmin(np.abs(z - z_hub)))     # TI 用最近层，不插值（方差不好插）
    layer = u[:, :, iz]
    info = BtsInfo()
    info.path, info.u_hub = path, u_hub
    info.ti = float(100.0 * layer.std() / layer.mean())
    info.t_span = float(t[-1] - t[0])
    info.dt = float(t[1] - t[0]) if len(t) > 1 else float("nan")
    info.y, info.z, info.z_used = np.asarray(ts["y"]), z, float(z[iz])
    _CACHE[key] = info
    return info
