"""场景运行时：把场景、仿真后端、传感器串成一个可步进的对象。

    rt = SceneRuntime(load_scene("scenes/turb3_row.yaml"), max_steps=200)
    rt.reset()
    while not rt.done:
        frame = rt.step(yaw_delta=...)     # {topic: data}，只含**已启用**的通道

这一层是 UI 与训练共用的入口。它不碰 Qt，也不碰渲染 —— 渲染由 studio 层订阅
`frame` 完成。这样同一份场景既能开窗看，也能无头训练。

通道开关（`enable`/`disable`）落在这里而不是 UI 里：关掉一路通道意味着**该传感器
不再采样**，省下的是真实计算（lidar 的视线插值、camera 的离屏渲染），不只是不画。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from wfrl.channels import registry
from wfrl.scene.schema import Scene


class SceneRuntime:
    def __init__(self, scene: Scene, max_steps: int = 200,
                 load_coef: float = 1.0, reward_shaper=None,
                 warmup_steps: int = 0):
        scene.validate()
        self.scene = scene
        self.max_steps = int(max_steps)
        self.warmup_steps = int(warmup_steps)
        self.load_coef = float(load_coef)
        self.reward_shaper = reward_shaper
        self.sensors = registry.build(scene)
        self.enabled = {t: True for s in self.sensors for t in s.topics}
        self.driver = None
        self.done = False
        self._t = 0
        self.u_inf = float("nan")
        self._last_frame: Dict[str, Any] = {}
        # driver 一步的原始测量与喂给传感器的上下文。渲染层要的是**机组位姿**
        # （偏航/桨距/转速），那是几何不是数据通道：把 bladeload 那一路关掉，
        # 叶片不该停在原地不转。所以位姿走 last_measure，不走 frame。
        self.last_measure: Dict[str, Any] = {}
        self.last_ctx: Dict[str, Any] = {}

    # ---- 通道开关 --------------------------------------------------------
    def topics(self) -> List[str]:
        return list(self.enabled)

    def sensor_of(self, topic: str):
        for s in self.sensors:
            if topic in s.topics:
                return s
        return None

    def set_enabled(self, topic: str, on: bool):
        if topic in self.enabled:
            self.enabled[topic] = bool(on)

    def _active_sensors(self):
        return [s for s in self.sensors
                if any(self.enabled.get(t, False) for t in s.topics)]

    # ---- 生命周期 --------------------------------------------------------
    def reset(self) -> Dict[str, Any]:
        from wfrl.fastfarm_driver import FastFarmDriver
        if self.scene.backend != "fastfarm":
            raise NotImplementedError(
                "SceneRuntime 目前只驱动 fastfarm 后端；floris 场景请走 viz.field")
        self.close()
        # driver 走 env_id 那条老路会绕回预注册布局表，所以把场景生成的 case
        # 直接注入 —— 场景层的意义正在于此。
        self.driver = FastFarmDriver.__new__(FastFarmDriver)
        self._build_driver()
        m = self.driver.reset(
            wind_speed=None if self.scene.turbulence else self.scene.wind_speed,
            wind_direction=None)
        self.u_inf = float(getattr(self.driver, "u_inf", np.nan))
        self.done = False
        self._t = 0
        for _ in range(self.warmup_steps):        # 丢弃启动瞬态
            m = self.driver.step(np.zeros(self.scene.n))
        return self._emit(m)

    def _build_driver(self):
        """按场景构造 driver，绕开 env_id → 预注册布局的那条路。"""
        from wfcrl.rewards import DoNothingReward
        from wfrl import fastfarm_driver as fd
        d = self.driver
        fd.check_mpi_launcher()
        d.env_id = f"scene:{self.scene.name}"
        d.max_steps = self.max_steps
        d.controls = list(self.scene.controls)
        d.wind_time_series = self.scene.turbulence
        d.env = self.scene.make_env(
            max_iter=self.max_steps, load_coef=self.load_coef,
            reward_shaper=self.reward_shaper or DoNothingReward())
        d.case_dir = d.wake_vtk = None
        try:
            from wfrl.viz import wakevtk
            d.case_dir = wakevtk.case_dir_of(d.env.mdp.interface)
        except Exception:                                    # noqa: BLE001
            pass
        d._uenv = getattr(d.env, "unwrapped", d.env)
        d.agents = list(d.env.possible_agents)
        d.n = len(d.agents)
        d._idx = {ag: i for i, ag in enumerate(d.agents)}
        d.xcoords = np.asarray(self.scene.xcoords, float)
        d.ycoords = np.asarray(self.scene.ycoords, float)
        d.dt = float(self.scene.dt)
        d.hub_height = self.scene.hub_height
        d.bts = None
        if self.scene.turbulence:
            from wfrl.inflow import bts_info
            d.bts = bts_info(self.scene.turbulence, z_hub=self.scene.hub_height)
        aspace = d.env.action_space(d.agents[0])
        d.act_bounds = {c: (float(np.ravel(aspace[c].low)[0]),
                            float(np.ravel(aspace[c].high)[0]))
                        for c in d.controls}
        d.act_low, d.act_high = d.act_bounds.get("yaw", (-5.0, 5.0))
        d.obs_keys = list(d.env.observation_space(d.agents[0]).keys())
        d.done = False
        d._steps = 0
        d._started = False
        # 偏航占空比滑动窗口（driver.__init__ 被 __new__ 绕过，这里手动补齐）
        import collections as _c
        from wfrl.fastfarm_driver import (YAW_DUTY_WINDOW, YAW_DUTY_LIMIT)
        d.yaw_duty_sliding = True
        d.yaw_duty_window = YAW_DUTY_WINDOW
        d.yaw_duty_limit = YAW_DUTY_LIMIT
        d._yaw_win = _c.deque(maxlen=YAW_DUTY_WINDOW)
        if "yaw" in d.controls:
            rate = dict(getattr(d._uenv.mdp, "ACTUATORS_RATE", {}))
            rate.pop("yaw", None)
            d._uenv.mdp.ACTUATORS_RATE = rate

    def step(self, yaw_delta=None, pitch_delta=None, torque_delta=None,
             grab=None) -> Dict[str, Any]:
        if self.driver is None:
            raise RuntimeError("先调用 reset()")
        m = self.driver.step(yaw_delta, pitch_delta, torque_delta)
        self._t += 1
        self.done = bool(m.get("done")) or self._t >= self.max_steps
        return self._emit(m, grab=grab)

    def _emit(self, m: Dict[str, Any], grab=None) -> Dict[str, Any]:
        """把 driver 的一步测量喂给各传感器，收集成 {topic: data}。"""
        ctx = dict(m)
        ctx["_source"] = self.driver
        ctx["_grab"] = grab
        ctx["u_inf"] = self.u_inf
        ctx["t"] = self._t * self.scene.dt
        self.last_measure = dict(m)
        self.last_ctx = ctx
        frame: Dict[str, Any] = {}
        for s in self._active_sensors():
            out = s.step(ctx)
            if out is None:                       # 抽稀跳过，保持上一帧
                continue
            for topic, data in out.items():
                if self.enabled.get(topic, False):
                    frame[topic] = data
        frame["_meta"] = {"t": self._t, "u_inf": self.u_inf,
                          "reward": m.get("reward"), "done": self.done,
                          "farm_power": float(np.nansum(m.get("power", [0.0])))}
        self._last_frame = frame
        return frame

    def close(self, purge: bool = True):
        if self.driver is not None:
            try:
                self.driver.close(purge=purge)
            except Exception as e:                          # noqa: BLE001
                print(f"[runtime] 关闭出错（忽略）: {e}", flush=True)
            self.driver = None

    # ---- 给 UI 的一览 ----------------------------------------------------
    def channel_table(self):
        """(topic, 传感器, 保真度, 启用, 来源说明)，UI 通道面板直接渲染。"""
        rows = []
        for s in self.sensors:
            for t in s.topics:
                rows.append((t, s.type_name, s.fidelity.value,
                             self.enabled.get(t, False), s.provenance))
        return rows
