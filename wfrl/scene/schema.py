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
import stat
import tempfile
from dataclasses import dataclass, field
from numbers import Real
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


def _number(value, path):
    if (isinstance(value, bool) or not isinstance(value, Real)
            or getattr(getattr(value, "dtype", None), "kind", None) in ("m", "M")):
        raise SceneError(f"{path} 必须是有限数值")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SceneError(f"{path} 必须是有限数值") from exc
    if not math.isfinite(result):
        raise SceneError(f"{path} 必须是有限数值")
    return result


def _text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise SceneError(f"{path} 必须是非空字符串")
    return value


def _list(value, path):
    if not isinstance(value, list):
        raise SceneError(f"{path} 必须是列表")
    return value


def _mapping(value, path, fields):
    if not isinstance(value, dict):
        raise SceneError(f"{path} 必须是映射")
    unknown = set(value) - set(fields)
    if unknown:
        raise SceneError(f"{path} 含未知字段 {sorted(map(repr, unknown))}")
    return value


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
        _text(self.turbines, f"sensors[{self.type}].turbines")
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
        _text(self.name, "name")
        _text(self.backend, "backend")
        _text(self.turbine, "turbine")
        if self.backend not in BACKENDS:
            raise SceneError(f"backend 必须是 {BACKENDS}，收到 {self.backend!r}")
        if self.turbine not in TURBINES:
            raise SceneError(f"turbine 必须是 {TURBINES}，收到 {self.turbine!r}")
        if not _list(self.turbines, "layout"):
            raise SceneError("layout 至少要有一台机组")
        if _number(self.dt, "dt") <= 0:
            raise SceneError(f"dt 必须为有限正数，收到 {self.dt}")
        if int(self.dt) != self.dt:
            raise SceneError(f"dt 必须为整数秒；当前 WFCRL case 不支持小数步长，收到 {self.dt}")
        for index, turbine in enumerate(self.turbines):
            if not isinstance(turbine, Turbine):
                raise SceneError(f"layout[{index}] 必须是 Turbine")
            _text(turbine.id, f"layout[{index}].id")
            _number(turbine.x, f"layout[{index}].x")
            _number(turbine.y, f"layout[{index}].y")

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
        if not (lo <= _number(self.wind_speed, "inflow.speed") <= hi):
            raise SceneError(
                f"inflow.speed={self.wind_speed} 超出 [{lo}, {hi}] m/s")
        if not (0.0 <= _number(self.wind_direction, "inflow.direction") < 360.0):
            raise SceneError(
                f"inflow.direction={self.wind_direction} 须在 [0, 360)")

        _list(self.controls, "controls")
        bad = [c for c in self.controls if c not in CONTROLS]
        if bad:
            raise SceneError(f"controls 含未知量 {bad}，可选 {CONTROLS}")
        if not self.controls:
            raise SceneError("controls 不能为空")

        if self.turbulence is not None:
            _text(self.turbulence, "inflow.turbulence")
            if self.backend != "fastfarm":
                raise SceneError("inflow.turbulence 只有 fastfarm 后端支持")

        if self.terrain is not None:
            _text(self.terrain, "terrain")
        if self.terrain not in TERRAINS:
            raise SceneError(
                f"terrain 必须是 {TERRAINS}（渲染装饰），收到 {self.terrain!r}")

        from wfrl.channels import registry as _reg
        for index, s in enumerate(_list(self.sensors, "sensors")):
            path = f"sensors[{index}]"
            if not isinstance(s, SensorSpec):
                raise SceneError(f"{path} 必须是 SensorSpec")
            _text(s.type, path + ".type")
            _text(s.mount, path + ".mount")
            _text(s.turbines, path + ".turbines")
            if s.type not in _reg.available():
                raise SceneError(
                    f"{path}.type 未知传感器 {s.type!r}，可用: {sorted(_reg.available())}")
            try:
                s.turbine_indices(self.turbines)
                _reg.get(s.type).validate(self, s)
            except ValueError as exc:
                raise SceneError(f"{path}: {exc}") from exc
        return self

    # ---- 转成 WFCRL 的 FarmCase ------------------------------------------
    def make_case(self, max_iter: int = 200):
        """生成 WFCRL 的 FarmCase。绕开 registration.py 的环境名白名单 ——
        那张表把布局钉死在预注册的常量上，正是场景层要解开的东西。"""
        self.validate()
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
            dt=int(self.dt), buffer_window=1,
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
    """读取配置；省略可选字段使用默认值，显式错误值与未知字段拒绝。"""
    if not isinstance(d, dict):
        raise SceneError("场景文件顶层必须是映射（YAML 字典）")
    _mapping(d, "scene", {"name", "backend", "turbine", "dt", "layout", "inflow",
                          "controls", "sensors", "terrain"})
    layout = _list(d.get("layout", []), "layout")
    turbines = []
    for k, item in enumerate(layout):
        path = f"layout[{k}]"
        _mapping(item, path, {"id", "x", "y"})
        for key in ("x", "y"):
            if key not in item:
                raise SceneError(f"{path}.{key} 缺失")
        turbines.append(Turbine(id=_text(item.get("id", f"T{k+1}"), path + ".id"),
                                x=_number(item["x"], path + ".x"),
                                y=_number(item["y"], path + ".y")))

    inflow = _mapping(d.get("inflow", {}), "inflow", {"speed", "direction", "turbulence"})

    sensors = []
    for k, item in enumerate(_list(d.get("sensors", []), "sensors")):
        if not isinstance(item, dict) or "type" not in item:
            raise SceneError(f"sensors[{k}] 必须是映射且含 type")
        # YAML 1.1 把裸 on/off/yes/no 解析成布尔，键会变成 True/False。若不拦，
        # `on: T1` 会被当成未知键丢掉，传感器静默挂到全场 —— 图看着对，数是错的。
        booly = [kk for kk in item if isinstance(kk, bool)]
        if booly:
            raise SceneError(
                f"sensors[{k}] 出现被 YAML 解析成布尔的键（写了 on/off/yes/no?）；"
                f"挂载目标的字段名是 turbines")
        path = f"sensors[{k}]"
        _mapping(item, path, {"type", "mount", "turbines", "params"})
        params = item.get("params", {})
        if params is not None and not isinstance(params, dict):
            raise SceneError(f"{path}.params 必须是映射")
        sensors.append(SensorSpec(
            type=_text(item["type"], path + ".type"),
            mount=_text(item.get("mount", "nacelle"), path + ".mount"),
            turbines=_text(item.get("turbines", "all"), path + ".turbines"),
            params=dict(params or {})))

    sc = Scene(
        name=_text(d.get("name", "untitled"), "name"),
        backend=_text(d.get("backend", "fastfarm"), "backend").lower(),
        turbine=_text(d.get("turbine", "nrel5mw"), "turbine").lower(),
        dt=_number(d.get("dt", 3.0), "dt"),
        turbines=turbines,
        wind_speed=_number(inflow.get("speed", 8.0), "inflow.speed"),
        wind_direction=_number(inflow.get("direction", 270.0), "inflow.direction"),
        turbulence=inflow.get("turbulence"),
        controls=[_text(c, f"controls[{i}]").lower()
                  for i, c in enumerate(_list(d.get("controls", ["yaw"]), "controls"))],
        sensors=sensors,
        terrain=(_text(d["terrain"], "terrain").lower() if d.get("terrain") is not None else None),
        source_path=source_path,
    )
    return sc.validate()


class _SceneLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self._checked_mappings = set()

    def flatten_mapping(self, node):
        if node in self._checked_mappings:
            return
        self._checked_mappings.add(node)
        keys = set()
        for key_node, _ in node.value:
            # YAML merge 的显式覆盖仍按 SafeLoader 的合并规则处理。
            if key_node.tag == "tag:yaml.org,2002:merge":
                key = "<<"
            else:
                key = self.construct_object(key_node)
            try:
                duplicate = key in keys
                keys.add(key)
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "unhashable mapping key", key_node.start_mark) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"duplicate key {key!r}", key_node.start_mark)
        super().flatten_mapping(node)


def load_scene(path: str) -> Scene:
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise SceneError(f"场景文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as fp:
        try:
            d = yaml.load(fp, Loader=_SceneLoader)
        except yaml.YAMLError as e:
            raise SceneError(f"YAML 解析失败: {e}") from e
    return from_dict(d, source_path=path)


def dump_scene(scene: Scene, path: str) -> str:
    """以 UTF-8 YAML 原子替换目标；写入失败保留已有文件。"""
    scene.validate()
    path = os.path.abspath(path)
    target = os.path.realpath(path)
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wfrl-scene-", dir=directory) as temporary:
        staged = os.path.join(temporary, "scene")
        with open(staged, "w", encoding="utf-8") as fp:
            yaml.safe_dump(scene.to_dict(), fp, allow_unicode=True, sort_keys=False)
            fp.flush()
            os.fsync(fp.fileno())
        if os.path.exists(target):
            os.chmod(staged, stat.S_IMODE(os.stat(target).st_mode))
        os.replace(staged, target)
    return path
