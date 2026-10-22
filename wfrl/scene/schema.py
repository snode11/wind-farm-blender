"""场景描述与校验。

一份场景 YAML 完整决定一次仿真：布局、机型、来流、传感器挂载。校验在**生成算例
之前**做完 —— FAST.Farm 的失败方式是无声挂死（MPI spawn 等不到子进程），等到那
一步才发现坐标写错，代价是几分钟墙钟加一个孤儿进程。

字段：

    name: turb3_row
    backend: fastfarm          # fastfarm | floris
    turbine: nrel5mw           # 目前只有这一个机型（模板里烧死的）
    dt: 3                      # 控制步长 (s)
    layout:
      - {id: T1, x: 0,    y: 0}
      - {id: T2, x: 504,  y: 0}
      - {id: T3, x: 1008, y: 0}
    inflow:
      speed: 8.0               # m/s；给 turbulence 时此项被 .bts 覆盖
      direction: 270.0         # deg；仅 floris 后端可改（见 interface.py:437-441）
      turbulence: null         # .bts 文件名，相对 FarmInputs/
    controls: [yaw]            # yaw | pitch | torque
    sensors:
      - {type: lidar, mount: nacelle, turbines: all}
      - {type: bladeload, mount: blade_root, turbines: "T1,T2"}

挂载目标的键是 `turbines` 而**不是** `on` —— YAML 1.1 把裸 `on` 解析成布尔真值
（`yaml.safe_load` 给出的键是 `True`，不是 `"on"`），字段会被静默丢弃、传感器
默默挂到全场。写了 `on` 会在加载期报错而不是将错就错。

设计上不做的事：不支持多机型混排（模板只有 NREL 5MW 一套 ElastoDyn/AeroDyn 输入）；
不支持地形高程进入物理（FAST.Farm 平地假设），地形只进渲染。这两条在 `Scene.notes`
里显式列出，UI 会显示，免得看图的人以为算进去了。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

# NREL 5MW，与 simulators/fastfarm/inputs/template 里的 ElastoDyn 一致。
ROTOR_D = 126.0
HUB_HEIGHT = 90.0
RATED_WS = 11.4
# mdp.py:46 的风速合法区间；超出会被环境静默夹住，不如在这里报错。
WS_BOUNDS = (3.0, 28.0)
# 低于这个间距，FAST.Farm 的高分辨率盒子会互相重叠，算例生成不报错但结果无意义。
MIN_SPACING_D = 1.5

BACKENDS = ("fastfarm", "floris")
TURBINES = ("nrel5mw",)
CONTROLS = ("yaw", "pitch", "torque")
# 地形是**纯渲染装饰**，不进流场（FAST.Farm 本算例平地）。取值对齐
# terrain.PRESETS 的键；None 表示不画地形（空地面）。
TERRAINS = (None, "mountains", "gobi", "flat")


class SceneError(ValueError):
    """场景文件不合法。消息里必须带上出错的字段路径，UI 直接显示这条。"""


@dataclass
class Turbine:
    id: str
    x: float
    y: float

    @property
    def xy(self):
        return (self.x, self.y)


@dataclass
class SensorSpec:
    type: str
    mount: str = "nacelle"
    turbines: str = "all"                 # "all" 或逗号分隔的机组 id
    params: Dict[str, Any] = field(default_factory=dict)

    def turbine_indices(self, turbines: List[Turbine]) -> List[int]:
        if self.turbines == "all":
            return list(range(len(turbines)))
        want = {s.strip() for s in str(self.turbines).split(",") if s.strip()}
        idx = [i for i, t in enumerate(turbines) if t.id in want]
        missing = want - {t.id for t in turbines}
        if missing:
            raise SceneError(
                f"sensors[{self.type}].turbines 指向不存在的机组: {sorted(missing)}")
        return idx


@dataclass
class Scene:
    name: str = "untitled"
    backend: str = "fastfarm"
    turbine: str = "nrel5mw"
    dt: float = 3.0
    turbines: List[Turbine] = field(default_factory=list)
    wind_speed: float = 8.0
    wind_direction: float = 270.0
    turbulence: Optional[str] = None
    controls: List[str] = field(default_factory=lambda: ["yaw"])
    sensors: List[SensorSpec] = field(default_factory=list)
    terrain: Optional[str] = None          # 渲染装饰，见 TERRAINS；不进物理
    source_path: Optional[str] = None

    # ---- 派生量 ----------------------------------------------------------
    @property
    def n(self) -> int:
        return len(self.turbines)

    @property
    def xcoords(self) -> List[float]:
        return [t.x for t in self.turbines]

    @property
    def ycoords(self) -> List[float]:
        return [t.y for t in self.turbines]

    @property
    def rotor_diameter(self) -> float:
        return ROTOR_D

    @property
    def hub_height(self) -> float:
        return HUB_HEIGHT

    @property
    def notes(self) -> List[str]:
        """这份场景里**没有**被物理算进去的东西。UI 与汇报都要显示。"""
        out = ["地形高程仅用于渲染，FAST.Farm 按平地求解"]
        if self.backend == "fastfarm":
            out.append("风向在 FAST.Farm 后端不可设（interface.py:437-441），"
                       "场景里的 direction 只影响渲染朝向")
        if self.turbulence:
            out.append(f"来流由湍流盒 {self.turbulence} 决定，speed 字段被忽略")
        if len(TURBINES) == 1:
            out.append("模板只含 NREL 5MW 一种机型，不支持混排")
        return out

    # ---- 校验 ------------------------------------------------------------
    def validate(self) -> "Scene":
        if self.backend not in BACKENDS:
            raise SceneError(f"backend 必须是 {BACKENDS}，收到 {self.backend!r}")
        if self.turbine not in TURBINES:
            raise SceneError(f"turbine 必须是 {TURBINES}，收到 {self.turbine!r}")
        if self.n < 1:
            raise SceneError("layout 至少要有一台机组")
        if self.dt <= 0:
            raise SceneError(f"dt 必须为正，收到 {self.dt}")

        ids = [t.id for t in self.turbines]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise SceneError(f"layout 机组 id 重复: {sorted(dup)}")

        # 间距：低于 1.5D 时高分辨率盒子重叠，算例照样生成但结果无意义
        lim = MIN_SPACING_D * ROTOR_D
        for i in range(self.n):
            for j in range(i + 1, self.n):
                a, b = self.turbines[i], self.turbines[j]
                d = math.hypot(a.x - b.x, a.y - b.y)
                if d < lim:
                    raise SceneError(
                        f"机组 {a.id} 与 {b.id} 间距 {d:.1f} m < {lim:.0f} m "
                        f"({MIN_SPACING_D}D)，高分辨率盒子会重叠")

        lo, hi = WS_BOUNDS
        if not (lo <= self.wind_speed <= hi):
            raise SceneError(
                f"inflow.speed={self.wind_speed} 超出 [{lo}, {hi}] m/s")
        if not (0.0 <= self.wind_direction < 360.0):
            raise SceneError(
                f"inflow.direction={self.wind_direction} 须在 [0, 360)")

        bad = [c for c in self.controls if c not in CONTROLS]
        if bad:
            raise SceneError(f"controls 含未知量 {bad}，可选 {CONTROLS}")
        if not self.controls:
            raise SceneError("controls 不能为空")

        if self.turbulence and self.backend != "fastfarm":
            raise SceneError("inflow.turbulence 只有 fastfarm 后端支持")

        if self.terrain not in TERRAINS:
            raise SceneError(
                f"terrain 必须是 {TERRAINS}（渲染装饰），收到 {self.terrain!r}")

        from wfrl.channels import registry as _reg
        for s in self.sensors:
            if s.type not in _reg.available():
                raise SceneError(
                    f"未知传感器 {s.type!r}，可用: {sorted(_reg.available())}")
            s.turbine_indices(self.turbines)      # 顺带查 on 里的 id
            _reg.get(s.type).validate(self, s)
        return self

    # ---- 转成 WFCRL 的 FarmCase ------------------------------------------
    def make_case(self, max_iter: int = 200):
        """生成 WFCRL 的 FarmCase。绕开 registration.py 的环境名白名单 ——
        那张表把布局钉死在预注册的常量上，正是场景层要解开的东西。"""
        from wfcrl.environments.data_cases import FastFarmCase, FlorisCase
        if self.backend == "fastfarm":
            case = FastFarmCase(
                num_turbines=self.n, xcoords=self.xcoords, ycoords=self.ycoords,
                dt=int(self.dt), buffer_window=1, t_init=100, max_iter=max_iter,
                set_wind_direction=True,
            )
            case.wind_time_series = self.turbulence
            return case
        case = FlorisCase(
            num_turbines=self.n, xcoords=self.xcoords, ycoords=self.ycoords,
            dt=int(self.dt) if self.dt >= 1 else 60, buffer_window=1,
            t_init=0, max_iter=max_iter,
        )
        return case

    def make_env(self, max_iter: int = 200, load_coef: float = 1.0,
                 reward_shaper=None, log: bool = True):
        """按场景直接构造多智能体环境。"""
        from wfcrl.environments.registration import get_default_control
        from wfcrl.interface import FastFarmInterface, FlorisInterface
        from wfcrl.multiagent_env import MAWindFarmEnv
        from wfcrl.rewards import DoNothingReward
        from wfcrl.wrappers import AECLogWrapper

        self.validate()
        case = self.make_case(max_iter=max_iter)
        itf = FastFarmInterface if self.backend == "fastfarm" else FlorisInterface
        env = MAWindFarmEnv(
            interface=itf, farm_case=case,
            controls=get_default_control(self.controls),
            start_iter=math.ceil(case.t_init / case.dt),
            max_num_steps=max_iter, load_coef=load_coef,
            reward_shaper=reward_shaper or DoNothingReward(),
        )
        return AECLogWrapper(env) if log else env

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "backend": self.backend,
            "turbine": self.turbine,
            "dt": self.dt,
            "layout": [{"id": t.id, "x": t.x, "y": t.y} for t in self.turbines],
            "inflow": {"speed": self.wind_speed,
                       "direction": self.wind_direction,
                       "turbulence": self.turbulence},
            "controls": list(self.controls),
            "sensors": [{"type": s.type, "mount": s.mount, "turbines": s.turbines,
                         **({"params": s.params} if s.params else {})}
                        for s in self.sensors],
            "terrain": self.terrain,
        }


def from_dict(d: Dict[str, Any], source_path: str = None) -> Scene:
    if not isinstance(d, dict):
        raise SceneError("场景文件顶层必须是映射（YAML 字典）")
    layout = d.get("layout") or []
    if not isinstance(layout, list):
        raise SceneError("layout 必须是列表")
    turbines = []
    for k, item in enumerate(layout):
        if not isinstance(item, dict):
            raise SceneError(f"layout[{k}] 必须是映射")
        try:
            turbines.append(Turbine(id=str(item.get("id", f"T{k+1}")),
                                    x=float(item["x"]), y=float(item["y"])))
        except (KeyError, TypeError, ValueError) as e:
            raise SceneError(f"layout[{k}] 坐标不合法: {e}") from e

    inflow = d.get("inflow") or {}
    if not isinstance(inflow, dict):
        raise SceneError("inflow 必须是映射")

    sensors = []
    for k, item in enumerate(d.get("sensors") or []):
        if not isinstance(item, dict) or "type" not in item:
            raise SceneError(f"sensors[{k}] 必须是映射且含 type")
        # YAML 1.1 把裸 on/off/yes/no 解析成布尔，键会变成 True/False。若不拦，
        # `on: T1` 会被当成未知键丢掉，传感器静默挂到全场 —— 图看着对，数是错的。
        booly = [kk for kk in item if isinstance(kk, bool)]
        if booly:
            raise SceneError(
                f"sensors[{k}] 出现被 YAML 解析成布尔的键（写了 on/off/yes/no?）；"
                f"挂载目标的字段名是 turbines")
        unknown = set(item) - {"type", "mount", "turbines", "params"}
        if unknown:
            raise SceneError(f"sensors[{k}] 含未知字段 {sorted(unknown)}")
        sensors.append(SensorSpec(
            type=str(item["type"]), mount=str(item.get("mount", "nacelle")),
            turbines=str(item.get("turbines", "all")),
            params=dict(item.get("params") or {})))

    sc = Scene(
        name=str(d.get("name", "untitled")),
        backend=str(d.get("backend", "fastfarm")).lower(),
        turbine=str(d.get("turbine", "nrel5mw")).lower(),
        dt=float(d.get("dt", 3.0)),
        turbines=turbines,
        wind_speed=float(inflow.get("speed", 8.0)),
        wind_direction=float(inflow.get("direction", 270.0)),
        turbulence=inflow.get("turbulence") or None,
        controls=[str(c).lower() for c in (d.get("controls") or ["yaw"])],
        sensors=sensors,
        terrain=(str(d["terrain"]).lower() if d.get("terrain") else None),
        source_path=source_path,
    )
    return sc.validate()


def load_scene(path: str) -> Scene:
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise SceneError(f"场景文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as fp:
        try:
            d = yaml.safe_load(fp)
        except yaml.YAMLError as e:
            raise SceneError(f"YAML 解析失败: {e}") from e
    return from_dict(d, source_path=path)


def dump_scene(scene: Scene, path: str) -> str:
    scene.validate()
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        yaml.safe_dump(scene.to_dict(), fp, allow_unicode=True, sort_keys=False)
    return path
