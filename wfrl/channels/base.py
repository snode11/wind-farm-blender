"""传感器基类与注册表：定义位姿、采样率、数据来源、保真度和场景约束。"""
from __future__ import annotations

import enum
import math
from collections.abc import Mapping
from numbers import Integral, Real
from typing import Any, Dict, List, Optional

import numpy as np


def finite_parameter(value, name):
    """将配置中的有限实数转为 float，拒绝布尔与非数值。"""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise SceneErrorProxy(f"{name} 必须是有限数值")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise SceneErrorProxy(f"{name} 必须是有限数值") from exc
    if not math.isfinite(result):
        raise SceneErrorProxy(f"{name} 必须是有限数值")
    return result


class Fidelity(enum.Enum):
    """数据的物理可信度，用于区分仿真直读、派生量与合成量。"""
    DIRECT = "直读"      # 仿真器真算出来的
    DERIVED = "导出"     # 由直读量/湍流盒按明确公式推的
    SYNTH = "合成"       # 我们造的模型，未经验证


class Sensor:
    """传感器基类。子类至少要覆盖 `topics` 和 `sample`。"""

    type_name: str = "abstract"
    fidelity: Fidelity = Fidelity.SYNTH
    #: 这一路数据的来源说明，一句话，UI 悬停显示，汇报里逐字引用
    provenance: str = ""
    #: 采样率 (Hz)；None 表示每个控制步都出数
    rate_hz: Optional[float] = None
    #: 需要仿真侧提供哪些量才能工作。缺了就在 validate 阶段报错，而不是运行时 KeyError
    requires: tuple = ()
    allowed_params: tuple = ()

    def __init__(self, scene, spec, indices: List[int]):
        self.validate(scene, spec)
        self.scene = scene
        self.spec = spec
        try:
            self.indices = list(indices)
        except TypeError as exc:
            raise SceneErrorProxy("传感器机组索引必须为整数列表") from exc
        if any(isinstance(i, (bool, np.bool_)) or not isinstance(i, Integral)
               or not 0 <= i < scene.n for i in self.indices):
            raise SceneErrorProxy("传感器机组索引超出场景范围或不是整数")
        self.params = dict(spec.params or {})
        self._k = 0                      # 控制步计数，用于抽稀
        self._last = None

    # ---- 元信息 ----------------------------------------------------------
    @property
    def topics(self) -> List[str]:
        """本传感器发布的通道名。"""
        return [self.type_name]

    @property
    def label(self) -> str:
        n = len(self.indices)
        tag = "全场" if n == self.scene.n else f"{n} 台"
        return f"{self.type_name} ({tag}, {self.fidelity.value})"

    def decimate(self) -> bool:
        """按 rate_hz 抽稀：返回 True 表示本控制步该出数。

        采样周期 1/rate_hz 换算成控制步数，向**上**取整。取整方向不能省：dt=3 s、
        rate=0.2 Hz 时真实周期是 1.67 步，四舍五入会变成 2 步（0.17 Hz，慢了）
        而向上取整给 2 步 —— 两者这里恰好一样，但 rate=0.4 Hz（0.83 步）四舍五入
        得 1 步、向上取整也得 1 步，且下限 1 保证传感器不会比控制步更快。
        比控制步慢的传感器（周期 > 1 步）才真正被抽稀。
        """
        if self.rate_hz is None:
            self._k += 1
            return True
        rate = finite_parameter(self.rate_hz, f"{self.type_name}.rate_hz")
        dt = finite_parameter(self.scene.dt, "dt")
        if rate <= 0 or dt <= 0:
            raise SceneErrorProxy("rate_hz 和 dt 必须大于 0")
        interval = (1.0 / rate) / dt
        if not math.isfinite(interval):
            # 有限的周期步数可能因先计算极小采样率的倒数而溢出。
            interval = (1.0 / dt) / rate
        if not math.isfinite(interval):
            raise SceneErrorProxy("采样周期超出有限控制步范围")
        period = max(1, int(math.ceil(interval)))
        self.period_steps = period
        self._k += 1
        return (self._k - 1) % period == 0

    def poses(self, ctx) -> np.ndarray:
        """传感器自身的世界坐标 (m,3)，默认挂在轮毂。"""
        xy = np.array([[self.scene.turbines[i].x, self.scene.turbines[i].y]
                       for i in self.indices], dtype=float).reshape(-1, 2)
        z = np.full((len(self.indices), 1), self.scene.hub_height)
        return np.hstack([xy, z])

    # ---- 数据 ------------------------------------------------------------
    def sample(self, ctx) -> Dict[str, Any]:
        """从仿真上下文取一帧。返回 {topic: data}。

        `ctx` 是 driver 一步的测量字典（yaw/power/load/pitch/... ），外加
        `ctx["_source"]` 指回数据源本身，供需要额外信息的传感器（如 lidar 要
        读湍流盒）使用。
        """
        raise NotImplementedError

    def step(self, ctx) -> Optional[Dict[str, Any]]:
        """带抽稀的采样；调度或采样异常通过各通道的 error 返回。

        返回 None 表示本步不出数（保持上一帧）。
        """
        try:
            if not self.decimate():
                return None
            self._last = self.sample(ctx)
        except Exception as e:                              # noqa: BLE001
            # 单个传感器炸掉不该让整场仿真停。把错误发到自己的通道上，UI 显示红字。
            self._last = {t: {"error": repr(e)} for t in self.topics}
        return self._last


    @classmethod
    def validate(cls, scene, spec):
        """场景加载期的静态检查。默认检查 requires 能不能被后端满足。"""
        params = spec.params
        if params is not None and not isinstance(params, Mapping):
            raise SceneErrorProxy(f"{cls.type_name}.params 必须为映射")
        unknown = set(params or {}) - set(cls.allowed_params)
        if unknown:
            raise SceneErrorProxy(f"{cls.type_name}.params 不支持参数 {sorted(map(str, unknown))}")
        if cls.rate_hz is not None:
            rate = finite_parameter(cls.rate_hz, f"{cls.type_name}.rate_hz")
            dt = finite_parameter(scene.dt, "dt")
            if rate <= 0 or dt <= 0:
                raise SceneErrorProxy("rate_hz 和 dt 必须大于 0")
        if scene.backend == "floris" and "aeroelastic" in cls.requires:
            raise SceneErrorProxy(
                f"传感器 {cls.type_name} 需要气弹后端，当前 backend=floris")


class SceneErrorProxy(ValueError):
    """避免 channels 反向 import scene 造成循环依赖；schema 会捕获 ValueError。"""


class SensorRegistry:
    def __init__(self):
        self._types: Dict[str, type] = {}

    def register(self, cls):
        if cls.type_name in self._types:
            raise KeyError(f"传感器类型重名: {cls.type_name}")
        self._types[cls.type_name] = cls
        return cls

    def available(self):
        return set(self._types)

    def get(self, name) -> type:
        if not isinstance(name, str) or name not in self._types:
            raise SceneErrorProxy(f"未注册的传感器: {name!r}")
        return self._types[name]

    def build(self, scene) -> List[Sensor]:
        """按场景里的 sensors 段实例化。"""
        out = []
        for spec in scene.sensors:
            cls = self.get(spec.type)
            out.append(cls(scene, spec, spec.turbine_indices(scene.turbines)))
        return out

    def describe(self):
        """给 UI/汇报用的一览表：(类型, 保真度, 来源说明)。"""
        return sorted((c.type_name, c.fidelity.value, c.provenance)
                      for c in self._types.values())


registry = SensorRegistry()
