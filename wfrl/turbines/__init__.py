"""机型定义：渲染 + 相机仿真这一层的机型单一来源。

一份机型文件（wfrl/turbines/<name>.yaml）承载三样东西：
  · 外壳外形（机舱盒体 + 导流罩尺寸）—— 之前写死在 animate.py 的推算系数里
  · 挂机点位库（命名点位 → 机舱系三维偏移）—— 之前散在 sensors.py 的 mount 分支
  · 元数据 / 气弹几何来源指针

复杂的气弹几何（叶片截面 / 翼型 / 塔筒锥度 / 振型）不在机型文件里重复：它本就
躺在 FAST.Farm 模板里，由 geometry_source 指向、交给 viz/geometry.py 解析。机型
文件只补模板没有的"外壳外形"和"挂机点位"。

设计边界：本层只进渲染与相机位姿，**不进物理求解**。物理侧的 hub_height/rotor_d
仍由 scene/schema.py 常量与 FAST.Farm 模板决定，和这份文件无关（与 RL 训练解耦）。

用法：
    spec = load_turbine_spec("nrel5mw")
    spec.nacelle_dims(geo)          # (length, width, height)（缺省回退推算式）
    spec.spinner_radius(geo)        # 导流罩半径
    spec.mount_offset("clearance_cam")   # (dx_down, dy_lat, dz_vert) 或 None
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import yaml

_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE: Dict[str, "TurbineSpec"] = {}


class TurbineSpecError(ValueError):
    """机型定义文件不合法。"""


@dataclass
class TurbineSpec:
    name: str
    description: str = ""
    geometry_source: str = "fastfarm_template"
    reference: dict = field(default_factory=dict)
    shell: dict = field(default_factory=dict)
    mounts: Dict[str, dict] = field(default_factory=dict)
    source_path: Optional[str] = None

    # ---- 外壳外形（缺省回退到既有推算式，保证零行为变化）-------------------
    def nacelle_dims(self, geo=None) -> Tuple[float, float, float]:
        """机舱盒体 (length, width, height)，单位 m。

        机型文件给了绝对值就用；没给则回退到 animate.py 的历史推算式
        （length=1.6·|OverHang|、width=height=2.2·HubRad），需要 geo。
        """
        nac = (self.shell or {}).get("nacelle") or {}
        length = nac.get("length")
        width = nac.get("width")
        height = nac.get("height")
        if None in (length, width, height):
            if geo is None:
                raise TurbineSpecError(
                    f"机型 {self.name} 的 shell.nacelle 不全，且未提供 geo 回退")
            length = abs(geo.overhang) * 1.6 if length is None else length
            width = geo.hub_rad * 2.2 if width is None else width
            height = geo.hub_rad * 2.2 if height is None else height
        return float(length), float(width), float(height)

    def spinner_radius(self, geo=None) -> float:
        r = (self.shell or {}).get("spinner_radius")
        if r is None:
            if geo is None:
                raise TurbineSpecError(
                    f"机型 {self.name} 缺 shell.spinner_radius，且未提供 geo 回退")
            r = geo.hub_rad * 1.6
        return float(r)

    def nacelle_x_bias(self) -> float:
        return float((self.shell or {}).get("nacelle_x_bias", 0.15))

    # ---- 挂机点位库 -------------------------------------------------------
    def mount_offset(self, name: str) -> Optional[Tuple[float, float, float]]:
        """命名挂机点位 → (dx_down, dy_lat, dz_vert)（真实尺度 m）。

        名字不在库里返回 None（调用方决定回退到内置语义还是报错）。
        """
        m = (self.mounts or {}).get(name)
        if m is None:
            return None
        return (float(m.get("dx_down", 0.0)),
                float(m.get("dy_lat", 0.0)),
                float(m.get("dz_vert", 0.0)))

    def mount_names(self):
        return sorted((self.mounts or {}).keys())


def _validate(d: dict, path: str) -> None:
    if not isinstance(d, dict):
        raise TurbineSpecError(f"{path}: 机型文件顶层必须是映射")
    if not d.get("name"):
        raise TurbineSpecError(f"{path}: 缺 name 字段")
    shell = d.get("shell") or {}
    nac = shell.get("nacelle") or {}
    for k in ("length", "width", "height"):
        if k in nac:
            try:
                v = float(nac[k])
            except (TypeError, ValueError) as e:
                raise TurbineSpecError(
                    f"{path}: shell.nacelle.{k} 必须是数字") from e
            if v <= 0:
                raise TurbineSpecError(f"{path}: shell.nacelle.{k} 必须为正")
    for mname, m in (d.get("mounts") or {}).items():
        if not isinstance(m, dict):
            raise TurbineSpecError(f"{path}: mounts.{mname} 必须是映射")
        for k in ("dx_down", "dy_lat", "dz_vert"):
            if k in m:
                try:
                    float(m[k])
                except (TypeError, ValueError) as e:
                    raise TurbineSpecError(
                        f"{path}: mounts.{mname}.{k} 必须是数字") from e


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
