"""BTS 文件选择、清单身份校验与轮毂高度统计。

WindType=3 的实际风速由 BTS 决定，不能使用 InflowWind 的 HWindSpeed。
``bts_info`` 保留训练使用的统计口径；``bts_statistics`` 逐横向点计算时间方差。
库的输入、选择语法与适用范围见 docs/training/TurbSim湍流盒.md。
"""
import hashlib
import json
import os
from numbers import Integral, Real
from pathlib import Path
import struct

import numpy as np

# wfcrl 用包内模板目录，不是仓库的 inputs/template/（后者没有 .bts）。
# 出处：simul_utils.py:125 的 TEMPLATE_DIR.format("fastfarm") + "FarmInputs/"。
_TEMPLATE_REL = os.path.join(
    "simulators", "fastfarm", "inputs", "template", "FarmInputs")
MANIFEST_SCHEMA = "wfrl-turbulence-library.v1"
DEFAULT_TURBULENCE_DIR = Path(__file__).resolve().parents[1] / "data" / "turbulence"


def turbulence_dir(directory=None):
    """显式目录、WFRL_TURBULENCE_DIR、仓库 data/turbulence，依次选择。"""
    return Path(directory or os.environ.get("WFRL_TURBULENCE_DIR")
                or DEFAULT_TURBULENCE_DIR).expanduser().resolve()


def turbulence_key(wind_speed, ti, seed):
    """规范化 (m/s, TI百分数或IEC类别, 有符号32位种子)。"""
    if (isinstance(wind_speed, (bool, np.bool_)) or not isinstance(wind_speed, Real)
            or not np.isfinite(wind_speed) or wind_speed <= 0):
        raise ValueError("wind_speed 必须为有限正数，单位 m/s")
    if isinstance(ti, str) and ti.upper() in ("A", "B", "C"):
        ti = ti.upper()
    elif (isinstance(ti, (bool, np.bool_)) or not isinstance(ti, Real)
          or not np.isfinite(ti) or not 0 < ti < 100):
        raise ValueError("TI 必须为 0 到 100 之间的百分数，或 IEC 类别 A / B / C")
    else:
        ti = float(ti)
    if (isinstance(seed, (bool, np.bool_)) or not isinstance(seed, Integral)
            or not -(2 ** 31) <= seed < 2 ** 31):
        raise ValueError("seed 必须为有符号 32 位整数")
    return float(wind_speed), ti, int(seed)


def turbulence_name(wind_speed, ti, seed):
    """稳定文件名含风速、TI百分数或类别、种子；小数点写成 p。"""
    speed, ti, seed = turbulence_key(wind_speed, ti, seed)

    def number(value):
        return str(float(value)).removesuffix(".0").replace(".", "p").replace("+", "")

    intensity = f"iec{ti}" if isinstance(ti, str) else f"ti{number(ti)}"
    return f"ts_u{number(speed)}_{intensity}_s{seed}"


_SHA256_CACHE = {}


def file_sha256(path):
    """SHA-256 of a file, reused while its path, inode, size and mtime are unchanged.

    Each reset re-checks a selected ~40 MB box against its manifest; replacing or
    rewriting the file changes the stat key and forces a fresh digest.
    """
    path = Path(path).resolve()
    stat = path.stat()
    key = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
    cached = _SHA256_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    result = digest.hexdigest()
    _SHA256_CACHE[key] = result
    return result


def load_turbulence_manifest(directory=None):
    """读取库索引，拒绝损坏、重名、重复组合与越界路径，不读大型 BTS。"""
    root = turbulence_dir(directory)
    path = root / "manifest.json"
    if not path.exists():
        return {"schema": MANIFEST_SCHEMA, "boxes": []}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["schema"] != MANIFEST_SCHEMA or not isinstance(manifest["boxes"], list):
            raise ValueError("schema 或 boxes 无效")
        names, keys = set(), set()
        for entry in manifest["boxes"]:
            key = turbulence_key(entry["wind_speed_mps"], entry["ti"], entry["seed"])
            name, filename = entry["name"], entry["file"]
            if name != turbulence_name(*key) or filename != name + ".bts":
                raise ValueError("文件名与输入参数不一致")
            (root / filename).resolve().relative_to(root)
            if name in names or key in keys:
                raise ValueError(f"重复盒子 {name}")
            names.add(name)
            keys.add(key)
            checksum = entry["sha256"]
            if (not isinstance(checksum, str) or len(checksum) != 64
                    or any(c not in "0123456789abcdef" for c in checksum)):
                raise ValueError(f"SHA-256 无效: {name}")
            if type(entry["size_bytes"]) is not int or entry["size_bytes"] <= 0:
                raise ValueError(f"文件大小无效: {name}")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"湍流清单无效 {path}: {exc}") from exc
    return manifest


def template_dir():
    """wfcrl 包内的 fastfarm 模板 FarmInputs 目录。"""
    import wfcrl
    return os.path.join(os.path.dirname(wfcrl.__file__), _TEMPLATE_REL)


def resolve_bts(name, *, library_dir=None, template_directory=None):
    """路径、库名称或 (风速, TI百分数/IEC类别, 种子) → 绝对 BTS 路径。

    字符串 ``bts:8,8,42`` 对应三元组；``bts:8,A,42`` 选择类别 A。
    模板内的既有文件优先且不依赖清单；库选择校验文件大小及 SHA-256。
    """
    root = turbulence_dir(library_dir)
    selection = os.fspath(name) if isinstance(name, os.PathLike) else name
    if isinstance(selection, str) and not selection.startswith("bts:"):
        path = Path(selection)
        if path.is_absolute():
            if not path.is_file():
                raise FileNotFoundError(f"找不到湍流盒子 {path}")
            return str(path)
    template = Path(template_directory or template_dir())
    if isinstance(selection, str) and not selection.startswith("bts:"):
        candidate = template / selection
        if candidate.is_file():
            return str(candidate.resolve())
    if isinstance(selection, str) and selection.startswith("bts:"):
        parts = selection[4:].split(",")
        try:
            if len(parts) != 3:
                raise ValueError("需要三个参数")
            intensity = parts[1].strip().upper()
            selection = (float(parts[0]), intensity if intensity in ("A", "B", "C")
                         else float(intensity), int(parts[2]))
        except ValueError as exc:
            raise ValueError("BTS 选择语法为 bts:风速,TI百分数或IEC类别,种子") from exc
    key = None
    if isinstance(selection, tuple) and len(selection) == 3:
        key = turbulence_key(*selection)
    elif not isinstance(selection, str) or not selection:
        raise ValueError("BTS 选择必须为名称、路径或 (风速, TI, 种子)")
    entries = load_turbulence_manifest(root)["boxes"]
    for entry in entries:
        matches = (key == turbulence_key(entry["wind_speed_mps"], entry["ti"], entry["seed"])
                   if key is not None else selection in (entry["name"], entry["file"]))
        if matches:
            path = root / entry["file"]
            if not path.is_file():
                raise FileNotFoundError(f"湍流清单中的文件不存在: {path}")
            if path.stat().st_size != entry["size_bytes"] or file_sha256(path) != entry["sha256"]:
                raise ValueError(f"湍流盒子与 manifest 的 SHA-256/大小不一致: {path}")
            return str(path)
    choices = [f"{e['name']} ({e['wind_speed_mps']:g} m/s, TI={e['ti']}, seed={e['seed']})"
               for e in entries]
    choices.extend(p.name for p in sorted(template.glob("*.bts")))
    raise FileNotFoundError(
        f"未知湍流盒子 {name!r}；库 {root}；可选项：" + ("; ".join(choices) or "无，请先生成"))


def bts_statistics(name, z_hub=90.0):
    """轮毂高度逐时刻插值，按横向点计算纵向时间统计（总体标准差）。"""
    info = bts_info(name, z_hub=z_hub)
    from openfast_toolbox.io import TurbSimFile

    ts = TurbSimFile(info.path)
    z = np.asarray(ts["z"])
    upper = min(int(np.searchsorted(z, z_hub)), len(z) - 1)
    lower = max(upper - 1, 0)
    fraction = 0.0 if upper == lower else (z_hub - z[lower]) / (z[upper] - z[lower])
    u = ts["u"][0]
    layer = (1.0 - fraction) * u[:, :, lower] + fraction * u[:, :, upper]
    mean = layer.mean(axis=0)
    std = layer.std(axis=0, ddof=0)
    if np.any(mean <= 0):
        raise ValueError("BTS 逐横向点均值必须为正数")
    ti = 100.0 * std / mean
    iy = int(np.argmin(abs(info.y)))
    return {
        "definition": "longitudinal u; vertical linear interpolation at hub height; population std per lateral point",
        "hub_height_m": float(z_hub), "mean_speed_mps": float(mean.mean()),
        "mean_ti_percent": float(ti.mean()), "ti_range_percent": [float(ti.min()), float(ti.max())],
        "centerline": {"y_m": float(info.y[iy]), "mean_speed_mps": float(mean[iy]),
                       "std_mps": float(std[iy]), "ti_percent": float(ti[iy])},
        "per_lateral_point": [{"y_m": float(y), "mean_speed_mps": float(m),
                               "std_mps": float(s), "ti_percent": float(i)}
                              for y, m, s, i in zip(info.y, mean, std, ti)],
        "time_span_s": info.t_span, "dt_s": info.dt, "periodic": int(ts["ID"]) == 8,
        "y_range_m": [float(info.y[0]), float(info.y[-1])],
        "z_range_m": [float(info.z[0]), float(info.z[-1])],
    }


class BtsInfo:
    """`.bts` 的关键标量；按文件身份与统计高度缓存。

    `u_hub` 是轮毂高度水平均值；`ti` 是最近高度层的时空湍流强度百分数。
    `t_span` 是文件时间跨度；周期文件可能重复，非周期文件可能越界报错。
    """

    __slots__ = ("path", "u_hub", "ti", "t_span", "dt", "z", "y", "z_used")

    def __repr__(self):
        return (f"BtsInfo({os.path.basename(self.path)}: u_hub={self.u_hub:.2f} "
                f"m/s, TI={self.ti:.1f}%, {self.t_span:.0f}s)")


_CACHE = {}


def bts_info(name, z_hub=90.0):
    """读有效 BTS 网格；文件身份与修改信息不变时复用同高度的统计。"""
    if isinstance(z_hub, (bool, np.bool_)) or not isinstance(z_hub, Real):
        raise ValueError("z_hub 必须为有限轮毂高度，单位 m")
    try:
        z_hub = float(z_hub)
    except (ValueError, OverflowError) as exc:
        raise ValueError("z_hub 必须为有限轮毂高度，单位 m") from exc
    if not np.isfinite(z_hub):
        raise ValueError("z_hub 必须为有限轮毂高度，单位 m")
    path = resolve_bts(name)
    stat = os.stat(path)
    signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    key = (path, z_hub)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    from openfast_toolbox.io import TurbSimFile
    from openfast_toolbox.io.file import BrokenFormatError, EmptyFileError, WrongFormatError

    try:
        ts = TurbSimFile(path)
        velocity = np.asarray(ts["u"])
        z, y, t = (np.asarray(ts[axis]) for axis in ("z", "y", "t"))
    except (OSError, ValueError, KeyError, TypeError, struct.error, EOFError,
            BrokenFormatError, EmptyFileError, WrongFormatError) as exc:
        raise ValueError(f"无法读取完整 BTS 文件 {path}: {exc}") from exc
    if (any(axis.ndim != 1 or not axis.size or not np.isfinite(axis).all()
            or np.any(np.diff(axis) <= 0) for axis in (z, y, t))
            or t.size < 2 or velocity.shape != (3, t.size, y.size, z.size)):
        raise ValueError(f"BTS 网格、时间轴或速度数组维度无效: {path}")
    if not z[0] <= z_hub <= z[-1]:
        raise ValueError(f"BTS 轮毂高度 z_hub 超出网格范围: {path}")
    u = velocity[0]                           # (nt, ny, nz)，只要纵向分量
    if not np.isfinite(u).all():
        raise ValueError(f"BTS 纵向风速必须有限: {path}")

    # 网格未必落在 z_hub 上（模板是 5 m 起、10 m 一层 ⇒ 85/95，没有 90），
    # 所以在两层之间按均值线性插值，而不是取最近层 —— 实测 85 m 7.909、95 m 8.087，
    # 取最近层会差 0.09 m/s（TI 仍用最近层，方差不好插）。
    prof = u.mean(axis=(0, 1))                 # (nz,) 各高度的时空均值
    u_hub = float(np.interp(z_hub, z, prof))
    iz = int(np.argmin(np.abs(z - z_hub)))     # TI 用最近层，不插值（方差不好插）
    layer = u[:, :, iz]
    if not np.isfinite(u_hub) or u_hub <= 0 or layer.mean() <= 0:
        raise ValueError(f"BTS 轮毂及 TI 参考层的平均风速必须为有限正数: {path}")
    info = BtsInfo()
    info.path, info.u_hub = path, u_hub
    info.ti = float(100.0 * layer.std() / layer.mean())
    if not np.isfinite(info.ti):
        raise ValueError(f"BTS 湍流强度必须有限: {path}")
    info.t_span = float(t[-1] - t[0])
    info.dt = float(t[1] - t[0]) if len(t) > 1 else float("nan")
    info.y, info.z, info.z_used = y, z, float(z[iz])
    _CACHE[key] = (signature, info)
    return info
