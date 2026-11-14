"""机型定义：渲染 + 相机仿真这一层的机型单一来源。

一份机型文件（wfrl/turbines/<name>.yaml）承载三样东西：
  · 外壳外形（机舱盒体 + 导流罩尺寸）
  · 挂机点位库（命名点位 → 机舱系三维偏移）
  · 元数据 / 气弹几何来源指针

复杂的气弹几何（叶片截面 / 翼型 / 塔筒锥度 / 振型）不在机型文件里重复：它本就
躺在 FAST.Farm 模板里，由 geometry_source 指向。传感器安装尺寸由
`wfrl.turbines.dimensions` 读取；机型文件补充外壳外形和挂机点位。

设计边界：本层只进渲染与相机位姿，**不进物理求解**。物理侧的 hub_height/rotor_d
仍由 scene/schema.py 常量与 FAST.Farm 模板决定，和这份文件无关（与 RL 训练解耦）。

用法：
    spec = load_turbine_spec("nrel5mw")
    spec.nacelle_dims(geo)          # (length, width, height)（缺省回退推算式）
    spec.spinner_radius(geo)        # 导流罩半径
    spec.mount_offset("clearance_cam")   # (dx_down, dy_lat, dz_vert) 或 None
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import yaml

_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE: Dict[str, "TurbineSpec"] = {}


class TurbineSpecError(ValueError):
    """机型定义文件不合法。"""


def _mapping(value, field_name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TurbineSpecError(f"{field_name} 必须是映射")
    return value


def _number(value, field_name, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError('boolean is not a dimension')
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TurbineSpecError(f"{field_name} 必须是有限数字") from exc
    if not math.isfinite(result):
        raise TurbineSpecError(f"{field_name} 必须是有限数字")
    if positive and result <= 0:
        raise TurbineSpecError(f"{field_name} 必须为正")
    return result


@dataclass
class TurbineSpec:
    name: str
    description: str = ""
    geometry_source: str = "fastfarm_template"
    reference: dict = field(default_factory=dict)
    shell: dict = field(default_factory=dict)
    mounts: Dict[str, dict] = field(default_factory=dict)
    source_path: Optional[str] = None

    # ---- 外壳外形（缺省尺寸由 geo 推算）-----------------------------------
    def nacelle_dims(self, geo=None) -> Tuple[float, float, float]:
        """机舱盒体 (length, width, height)，单位 m。

        机型文件给了绝对值就用；缺省尺寸按以下公式推算
        （length=1.6·|OverHang|、width=height=2.2·HubRad），需要 geo。
        显式与推算尺寸均须为有限正数，无效值抛出 TurbineSpecError。
        """
        shell = _mapping(self.shell, f"机型 {self.name}: shell")
        nac = _mapping(shell.get("nacelle"), f"机型 {self.name}: shell.nacelle")
        length = nac.get("length")
        width = nac.get("width")
        height = nac.get("height")
        if None in (length, width, height):
            if geo is None:
                raise TurbineSpecError(
                    f"机型 {self.name} 的 shell.nacelle 不全，且未提供 geo 回退")
            if length is None:
                length = abs(_number(getattr(geo, 'overhang', None),
                                     f"机型 {self.name}: shell.nacelle.length (geo.overhang)")) * 1.6
            if width is None or height is None:
                hub_rad = _number(getattr(geo, 'hub_rad', None),
                                  f"机型 {self.name}: shell.nacelle (geo.hub_rad)", positive=True)
                width = hub_rad * 2.2 if width is None else width
                height = hub_rad * 2.2 if height is None else height
        return tuple(_number(value, f"机型 {self.name}: shell.nacelle.{key}", positive=True)
                     for key, value in zip(('length', 'width', 'height'), (length, width, height)))

    def spinner_radius(self, geo=None) -> float:
        """导流罩半径（m），须为有限正数；缺省时按 1.6·HubRad 推算。"""
        r = _mapping(self.shell, f"机型 {self.name}: shell").get("spinner_radius")
        if r is None:
            if geo is None:
                raise TurbineSpecError(
                    f"机型 {self.name} 缺 shell.spinner_radius，且未提供 geo 回退")
            r = _number(getattr(geo, 'hub_rad', None),
                        f"机型 {self.name}: shell.spinner_radius (geo.hub_rad)", positive=True) * 1.6
        return _number(r, f"机型 {self.name}: shell.spinner_radius", positive=True)

    def nacelle_x_bias(self) -> float:
        return _number(_mapping(self.shell, f"机型 {self.name}: shell").get("nacelle_x_bias", 0.15),
                       f"机型 {self.name}: shell.nacelle_x_bias")

    # ---- 挂机点位库 -------------------------------------------------------
    def mount_offset(self, name: str) -> Optional[Tuple[float, float, float]]:
        """命名挂机点位 → (dx_down, dy_lat, dz_vert)（真实尺度 m）。

        名字不在库里返回 None（调用方决定回退到内置语义还是报错）。
        """
        m = _mapping(self.mounts, f"机型 {self.name}: mounts").get(name)
        if m is None:
            return None
        m = _mapping(m, f"机型 {self.name}: mounts.{name}")
        return tuple(_number(m.get(key, 0.0), f"机型 {self.name}: mounts.{name}.{key}")
                     for key in ('dx_down', 'dy_lat', 'dz_vert'))

    def mount_names(self):
        return sorted((self.mounts or {}).keys())


def _validate(d: dict, path: str) -> None:
    if not isinstance(d, dict):
        raise TurbineSpecError(f"{path}: 机型文件顶层必须是映射")
    if not d.get("name"):
        raise TurbineSpecError(f"{path}: 缺 name 字段")
    shell = _mapping(d.get("shell"), f"{path}: shell")
    nac = _mapping(shell.get("nacelle"), f"{path}: shell.nacelle")
    for k in ("length", "width", "height"):
        if k in nac:
            _number(nac[k], f"{path}: shell.nacelle.{k}", positive=True)
    if shell.get('spinner_radius') is not None:
        _number(shell['spinner_radius'], f"{path}: shell.spinner_radius", positive=True)
    if 'nacelle_x_bias' in shell:
        _number(shell['nacelle_x_bias'], f"{path}: shell.nacelle_x_bias")
    reference = _mapping(d.get('reference'), f"{path}: reference")
    for k in ('hub_height', 'rotor_diameter', 'tower_top'):
        if k in reference:
            _number(reference[k], f"{path}: reference.{k}", positive=True)
    for mname, m in _mapping(d.get("mounts"), f"{path}: mounts").items():
        if not isinstance(m, dict):
            raise TurbineSpecError(f"{path}: mounts.{mname} 必须是映射")
        for k in ("dx_down", "dy_lat", "dz_vert"):
            if k in m:
                _number(m[k], f"{path}: mounts.{mname}.{k}")


def load_turbine_spec(name_or_path: str) -> TurbineSpec:
    """按机型名（在 wfrl/turbines/ 下找 <name>.yaml）或直接路径加载，缓存。"""
    if os.path.sep in name_or_path or name_or_path.endswith((".yaml", ".yml")):
        path = os.path.abspath(name_or_path)
        key = path
    else:
        path = os.path.join(_DIR, f"{name_or_path}.yaml")
        key = name_or_path
    if key in _CACHE:
        return _CACHE[key]
    if not os.path.exists(path):
        raise TurbineSpecError(f"机型定义文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as fp:
        try:
            d = yaml.safe_load(fp)
        except yaml.YAMLError as e:
            raise TurbineSpecError(f"{path}: YAML 解析失败: {e}") from e
    _validate(d, path)
    spec = TurbineSpec(
        name=str(d["name"]),
        description=str(d.get("description", "")),
        geometry_source=str(d.get("geometry_source", "fastfarm_template")),
        reference=dict(d.get("reference") or {}),
        shell=dict(d.get("shell") or {}),
        mounts=dict(d.get("mounts") or {}),
        source_path=path,
    )
    _CACHE[key] = spec
    return spec


def available_turbines():
    """wfrl/turbines/ 下所有机型名。"""
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(_DIR)
        if f.endswith((".yaml", ".yml")))
