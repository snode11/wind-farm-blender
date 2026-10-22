"""传感器基类与注册表。

一个 Sensor 要回答四个问题：
  1. 它挂在哪（`poses`，用于在 3D 里画它自己）
  2. 它多久出一次数（`rate_hz`，低于控制步频的要按步数抽稀）
  3. 它的数据从哪来、可信到什么程度（`sample()` + `fidelity`）
  4. 它在 3D 里长什么样（`make_actors` / `update_actors`）

第 4 条是可选的：功率这类没有几何表现的传感器只发数、不加 actor。
"""
from __future__ import annotations

import enum
import math
from typing import Any, Dict, List, Optional

import numpy as np


class Fidelity(enum.Enum):
    """数据的物理可信度。UI 用它上色，汇报用它分节。"""
    DIRECT = "直读"      # 仿真器真算出来的
    DERIVED = "导出"     # 由直读量/湍流盒按明确公式推的
    SYNTH = "合成"       # 我们造的模型，未经验证

    @property
    def color(self):
        return {"直读": (60, 140, 70), "导出": (20, 80, 140),
                "合成": (170, 110, 30)}[self.value]


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
    #: 若 make_actors 建了带色带的 mesh，写上色带标题 —— 通道关掉时色带要跟着收。
    #: 留一条"lidar u (m/s)"的色带在画面上，等于宣称那一路还在采。
    scalar_bar_title: Optional[str] = None

    def __init__(self, scene, spec, indices: List[int]):
        self.scene = scene
        self.spec = spec
        self.indices = list(indices)
        self.params = dict(spec.params)
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
        self._k += 1
        if self.rate_hz is None:
            return True
        period = max(1, int(math.ceil(1.0 / (self.rate_hz * self.scene.dt))))
        self.period_steps = period
        return (self._k - 1) % period == 0

    def poses(self, ctx) -> np.ndarray:
        """传感器自身的世界坐标 (m,3)，默认挂在轮毂。"""
        xy = np.array([[self.scene.turbines[i].x, self.scene.turbines[i].y]
                       for i in self.indices], dtype=float)
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
        """带抽稀的采样。返回 None 表示本步不出数（保持上一帧）。"""
        if not self.decimate():
            return None
        try:
            self._last = self.sample(ctx)
        except Exception as e:                              # noqa: BLE001
            # 单个传感器炸掉不该让整场仿真停。把错误发到自己的通道上，UI 显示红字。
            self._last = {t: {"error": repr(e)} for t in self.topics}
        return self._last

    # ---- 渲染（可选）-----------------------------------------------------
    def to_display(self, pts, ctx) -> np.ndarray:
        """物理坐标 (m,k,3) → 画面坐标 (m*k,3)。

        机组在 3D 里按 TURB_SCALE 放大画（整场跨数千米、机组只有百米量级，1:1
        就是几根针）。传感器几何不跟着同一个变换的话，轮毂真值 90 m 会画在
        塔筒半腰上 —— 画面上的轮毂在 450 m —— 前视雷达就悬在地面附近，指着
        没有转子的地方。

        变换与 `viz/animate._build_real` 一致：**绕各自机组的塔基等比放大**，
        所以 xy 相对机位放大、z 相对地面放大。`m` 必须与 `self.indices` 对齐。
        采样数据本身仍是物理量，只有画出来的这一份被缩放。
        """
        p = np.asarray(pts, dtype=float)
        s = float(ctx.get("_turb_scale", 1.0) or 1.0)
        if p.ndim == 2:                      # 已经是点列，无从对齐机组 ⇒ 只缩 z
            out = p.copy()
            out[:, 2] *= s
            return out
        anchor = np.array([[self.scene.turbines[i].x,
                            self.scene.turbines[i].y, 0.0]
                           for i in self.indices])[:, None, :]
        return (anchor + s * (p - anchor)).reshape(-1, 3)

    def make_actors(self, plotter, ctx):
        """在 3D 场景里建自己的 actor。返回 actor 列表，无几何表现则返回 []。"""
        return []

    def update_actors(self, actors, data, ctx):
        """新数据到达时更新 actor。默认什么都不做。"""

    @classmethod
    def validate(cls, scene, spec):
        """场景加载期的静态检查。默认检查 requires 能不能被后端满足。"""
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
        if name not in self._types:
            raise KeyError(f"未注册的传感器: {name}")
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
