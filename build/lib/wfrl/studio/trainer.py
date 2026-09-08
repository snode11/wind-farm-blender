"""后台训练线程：MAPPO 跑在工作线程，UI 只收快照。

约束是硬的：FAST.Farm 一个控制步 2~3 s 墙钟。压在 Qt 的事件循环里，界面就是
每 3 秒动一下 —— 拖不动视角、点不了按钮。所以采样与优化整个搬到工作线程，
主线程只做两件事：读最近一帧、按真实流逝时间插值转子相位。

线程边界只有一处，必须守住：**主线程不碰 driver，工作线程不碰 actor**。
PyVista 的 actor 绑在创建它的 render window 上，跨线程改 `user_matrix` 是未定义
行为（表现为偶发白屏或崩在 vtkOpenGLPolyDataMapper）。所以工作线程写 `_latest`
（纯 numpy/dict），主线程取走再去改画面。一把锁保护交接，锁里只做浅拷贝，不做
任何渲染或仿真。

用法（不依赖 Qt，可无头跑）：

    tr = Trainer(scene, iters=10, n_steps=64)
    tr.start()
    while tr.running:
        snap = tr.latest()          # None 表示还没出第一帧
        ...
    tr.stop()                       # 幂等；会等 driver 排空迭代预算

算法与 `mappo_central.py` 完全一致（PPO 裁剪代理 + 集中式 critic），网络也复用
那边的 `Actor`/`Critic`，不另开一套 —— 训练面板里跑的东西和命令行训练出来的
checkpoint 必须能互相加载。
"""
from __future__ import annotations

import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import torch

from wfrl.mappo_central import Actor, Critic, RunningMeanStd
from wfrl.safety import SafetyLimiter

DEVICE = torch.device("cpu")


@dataclass
class Snapshot:
    """工作线程 → 主线程的一帧。全部是值，不含任何 VTK/torch 对象。"""
    step: int = 0
    iters_done: int = 0
    frame: Dict[str, Any] = field(default_factory=dict)
    measure: Dict[str, Any] = field(default_factory=dict)
    ctx: Dict[str, Any] = field(default_factory=dict)
    farm_power: float = float("nan")
    reward: float = float("nan")
    events: List[Any] = field(default_factory=list)
    phase: str = "idle"          # idle | warmup | sampling | updating | done
    demo_label: str = ""         # demo 模式：当前阶段中文提示（"变桨: 90°→0° 启动"等）


class Trainer:
    """把 MAPPO 训练跑在后台线程，逐步把快照交给 UI。

    `episode_steps` 控制回合结构：>0 时每回合重起一个 FAST.Farm 进程（阶段 3
    的结论 —— 单长回合等于一次风况抽签决定整段的功率量级），=0 退回单长回合，
    只在做对照时用。
    """

    def __init__(self, scene, iters: int = 8, n_steps: int = 64,
                 episode_steps: int = 0, warmup_steps: int = 4,
                 gamma: float = 0.99, lam: float = 0.95, clip: float = 0.2,
                 lr: float = 3e-4, n_epochs: int = 10, mb_steps: int = 16,
                 ent_coef: float = 0.005, vf_coef: float = 0.5,
                 max_grad: float = 0.5, seed: int = 0,
                 enforce_duty: bool = True, ckpt_path: Optional[str] = None,
                 replay: bool = False, replay_steps: int = 400,
                 demo: bool = False, demo_cycles: int = 2,
                 on_snapshot=None):
        self.scene = scene
        self.iters = int(iters)
        self.n_steps = int(n_steps)
        self.episode_steps = int(episode_steps)
        self.warmup_steps = int(warmup_steps)
        # replay=True 时不训练：加载 ckpt_path 的策略，用确定性动作 (dist.mean，
        # 无探索噪声) 跑一个回合，逐步发快照给 UI —— 让 3D/尾流/通道面板看到策略
        # 真正学到的偏航行为（与 rollout_ckpt.py 同口径）。ckpt_path 此时是输入。
        self.replay = bool(replay)
        self.replay_steps = int(replay_steps)
        # demo=True 时不加载 checkpoint，执行预设的手动控制序列：
        # 静止(pitch=90°) → 启动(0°) → 运行+偏航(0→30°) → 停机(90°)，循环 demo_cycles 次
        self.demo = bool(demo)
        self.demo_cycles = int(demo_cycles)
        self.hp = dict(gamma=gamma, lam=lam, clip=clip, lr=lr,
                       n_epochs=n_epochs, mb_steps=mb_steps,
                       ent_coef=ent_coef, vf_coef=vf_coef, max_grad=max_grad)
        self.seed = int(seed)
        self.enforce_duty = bool(enforce_duty)
        self.ckpt_path = ckpt_path
        self.on_snapshot = on_snapshot

        self.limiter = SafetyLimiter(scene)
        self.controls = list(scene.controls)     # 动作/观测维度都从它派生
        self.obs_keys = self.controls + ["wind_speed", "wind_direction"]
        self.runtime = None
        self.actor = None
        self.critic = None

        self._lock = threading.Lock()
        self._latest: Optional[Snapshot] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self.error: Optional[str] = None
        self.history: List[Dict[str, float]] = []      # 每轮一条，画曲线用

    # ---- 线程外的接口 ---------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def latest(self) -> Optional[Snapshot]:
        """取最近一帧。主线程调，不阻塞在仿真上。"""
        with self._lock:
            return self._latest

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wfrl-train",
                                        daemon=True)
        self._thread.start()

    def pause(self, on: bool = True):
        """暂停在**控制步边界**。不能在一步中间停：FAST.Farm 的 SC_DLL 在等
        这一圈 n 个 agent 的动作，停在中间它会一直卡在 MPI_RECV。"""
        self._paused.set() if on else self._paused.clear()

    def stop(self, timeout: float = 120.0):
        """请求停止并等它排空。幂等。

        超时值给得大：`close(purge=True)` 要把 FAST.Farm 在 t=0 领到的迭代预算
        走完，剩几十步就是几十秒。中途 kill 掉换来的是一个卡在 MPI_RECV 的孤儿
        进程，下次起算例时端口还被占着。
        """
        self._stop.set()
        self._paused.clear()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout)
        self._thread = None

    # ---- 工作线程 -------------------------------------------------------
    def _publish(self, **kw):
        snap = Snapshot(**kw)
        with self._lock:
            self._latest = snap
        if self.on_snapshot is not None:
            try:
                self.on_snapshot(snap)
            except Exception:                            # noqa: BLE001
                pass                                     # 回调炸了不该停训练
        return snap

    def _run(self):
        try:
            if self.demo:
                self._demo_sequence()
            elif self.replay:
                self._replay_policy()
            else:
                self._train()
        except Exception:                                # noqa: BLE001
            self.error = traceback.format_exc()
            print(f"[trainer] 训练线程异常:\n{self.error}", flush=True)
        finally:
            if self.runtime is not None:
                try:
                    self.runtime.close(purge=True)
                except Exception as e:                   # noqa: BLE001
                    print(f"[trainer] 收尾出错（忽略）: {e}", flush=True)
                self.runtime = None
            with self._lock:
                if self._latest is not None:
                    self._latest.phase = "done"

    def _replay_policy(self):
        """加载 checkpoint，用确定性策略 (dist.mean) 跑一回合，逐步发快照。

        不做任何 PPO 更新、不建 critic、不采 buffer —— 纯前向。动作口径与
        rollout_ckpt.py 完全一致：obs 用 ckpt 存的 obs_mean/var 归一化，网络输出
        取 dist.mean（去掉训练时的探索噪声），逐通道 clip 到 driver 的动作边界，
        再过约束层（约束层是仿真器真实会施加的，回放照走以贴合训练时的执行）。

        兼容两种 checkpoint：Studio 的 _save（只有 obs_keys/obs_mean/...）与命令行
        train_fastfarm.py 的（多 obs_duty/recurrent 键）。obs_duty 缺省当 False。
        """
        from wfrl.scene.runtime import SceneRuntime
        if not self.ckpt_path or not os.path.exists(self.ckpt_path):
            raise FileNotFoundError(f"回放需要 checkpoint，未找到: {self.ckpt_path!r}")
        torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        n = self.scene.n

        ck = torch.load(self.ckpt_path, map_location="cpu", weights_only=False)
        obs_keys = ck["obs_keys"]
        obs_duty = bool(ck.get("obs_duty", False))
        mean = np.asarray(ck["obs_mean"], np.float32)
        std = np.sqrt(np.asarray(ck["obs_var"], np.float32) + 1e-8)
        # CLI 训练的 checkpoint 没有 controls 键（旧口径只控 yaw），Studio 的有。
        # 从 act_dim 推：1 维→yaw，3 维→yaw/pitch/torque（与当前 scene 对齐）。
        act_dim = ck["act_dim"]
        if "controls" in ck and ck["controls"]:
            controls = list(ck["controls"])
        elif act_dim == 1:
            controls = ["yaw"]
        elif act_dim == 3:
            controls = ["yaw", "pitch", "torque"]
        else:
            controls = list(self.scene.controls)  # fallback：从场景推
        self.actor = Actor(ck["obs_dim"], ck["act_dim"]).to(DEVICE)
        self.actor.load_state_dict(ck["actor"])
        self.actor.eval()
        log_std = ck["actor"]["log_std"].cpu().numpy()
        print(f"[trainer] 回放 {self.ckpt_path}\n"
              f"  obs_dim={ck['obs_dim']} act_dim={ck['act_dim']} "
              f"obs_duty={obs_duty} controls={controls} "
              f"std={np.round(np.exp(log_std), 3).tolist()}", flush=True)

        ep = self.replay_steps
        budget = ep + self.warmup_steps + 8
        self.runtime = SceneRuntime(self.scene, max_steps=budget,
                                    warmup_steps=0)
        want_warmup = self.warmup_steps
        self._publish(phase="warmup")
        frame = self.runtime.reset()
        m = self.runtime.last_measure
        self._publish(step=0, iters_done=0, frame=frame, measure=m,
                      ctx=self.runtime.last_ctx,
                      farm_power=float(frame["_meta"].get("farm_power") or 0.0),
                      reward=0.0, events=[], phase="warmup")
        for _ in range(want_warmup):
            if self._stop.is_set():
                return
            frame = self.runtime.step(yaw_delta=np.zeros(n))
            m = self.runtime.last_measure
            self._publish(step=0, iters_done=0, frame=frame, measure=m,
                          ctx=self.runtime.last_ctx,
                          farm_power=float(frame["_meta"].get("farm_power") or 0.0),
                          reward=0.0, events=[], phase="warmup")

        act_high = np.array([self.runtime.driver.act_bounds[c][1]
                             for c in controls], np.float32)
        lo, hi = self.runtime.driver.act_low, self.runtime.driver.act_high

        # 回放专用：记录每步数据，让训练面板能画曲线
        replay_history = {"step": [], "yaw": [], "power": [], "reward": []}

        for k in range(ep):
            while self._paused.is_set() and not self._stop.is_set():
                time.sleep(0.05)
            if self._stop.is_set():
                break
            obs = self._pack_keys(m, obs_keys, obs_duty, n)
            on = ((obs - mean) / std).astype(np.float32)
            with torch.no_grad():
                a = self.actor.dist(torch.as_tensor(on, device=DEVICE)).mean
            a_np = a.cpu().numpy()
            # 单控 yaw（多数 checkpoint）走 rollout 的 clip 口径；多控走逐通道
            # tanh*上界 的训练口径。act_dim 决定走哪条，与训练时一致。
            if len(controls) == 1:
                a_scaled = np.clip(a_np.ravel(), lo, hi)[:, None]
            else:
                a_scaled = np.tanh(a_np) * act_high
            cmd, evs = self.limiter.apply(
                {c: a_scaled[:, j] for j, c in enumerate(controls)},
                m, enforce_duty=self.enforce_duty)
            frame = self.runtime.step(
                yaw_delta=cmd.get("yaw"), pitch_delta=cmd.get("pitch"),
                torque_delta=cmd.get("torque"))
            m = self.runtime.last_measure
            evs = evs + self.limiter.monitor(m)
            r = float(frame["_meta"].get("reward") or 0.0)

            # 回放专用：记录每步数据
            yaw_vals = np.asarray(m.get("yaw", np.zeros(n)), float).ravel()
            pow_vals = np.asarray(m.get("power", np.zeros(n)), float).ravel()
            replay_history["step"].append(k + 1)
            replay_history["yaw"].append(yaw_vals[0] if len(yaw_vals) > 0 else 0.0)
            replay_history["power"].append(float(pow_vals.sum()))
            replay_history["reward"].append(r)

            self._publish(step=k + 1, iters_done=0, frame=frame, measure=m,
                          ctx=self.runtime.last_ctx,
                          farm_power=float(frame["_meta"]["farm_power"]),
                          reward=r, events=evs, phase="sampling")
            if self.runtime.done:
                break

        # 回放结束：把记录的数据转成 history 格式，让训练面板能画曲线
        if len(replay_history["step"]) > 0:
            # 每 10 步聚合一条，避免曲线过密
            step_interval = max(1, ep // 40)  # 最多 40 个点
            for i in range(0, len(replay_history["step"]), step_interval):
                end_idx = min(i + step_interval, len(replay_history["step"]))
                self.history.append({
                    "iter": i // step_interval,
                    "step": replay_history["step"][end_idx - 1],
                    "mean_power": float(np.mean(replay_history["power"][i:end_idx])),
                    "mean_reward": float(np.mean(replay_history["reward"][i:end_idx])),
                    "vloss": 0.0,  # 回放没有训练，填 0
                    "explained_var": 0.0,
                })

        self._publish(step=ep, iters_done=1, frame=frame, measure=m,
                      ctx=self.runtime.last_ctx,
                      farm_power=float(frame["_meta"].get("farm_power") or 0.0),
                      reward=0.0, events=[], phase="done")

    def _pack_keys(self, m, obs_keys, obs_duty, n):
        """回放专用打包：列顺序严格按 checkpoint 存的 obs_keys，可选拼 duty 余量。

        与 rollout_ckpt.py:pack_obs 逐位一致 —— duty headroom =
        clip((0.1 - duty_yaw)/0.1, 0, 1)。训练面板自采的 _pack 用 self.obs_keys，
        回放必须改用 ckpt 里的 obs_keys（网络输入维度由它定死）。
        """
        cols = [np.asarray(m[k], np.float32).ravel() for k in obs_keys]
        if obs_duty:
            duty = np.asarray((m.get("duty") or {}).get("yaw", np.zeros(n)),
                              np.float32).ravel()
            cols.append(np.clip((0.1 - duty) / 0.1, 0.0, 1.0).astype(np.float32))
        return np.stack(cols, axis=1).astype(np.float32)

    def _demo_sequence(self):
        """演示模式：脚本化视觉动画，精确复现时序。

        完全不走 FAST.Farm（它无法保证转速在恰好 N 秒到达 8rpm，且 spawn 要 1min），
        直接驱动 measure 与 3D 可视化。每 120ms 一帧（与 StudioWindow._tick 同步），
        逐帧插值 yaw/pitch/rpm/power，严格按秒数执行用户给定的序列。

        序列：
        - 循环 1：起始静止 → 变桨 90°→0° → 转速 0→8rpm（rpm=4 时错峰偏航 60°）
          → 保持 8rpm → 顺桨 0°→90°（同时停转）
        - 循环 2+：不重置机位（偏航保持），停机后直接再变桨 90°→0° 起转 → 顺桨停机

        变桨/偏航速率、整体时长可用 TS 时间因子统一放缩（当前 2.0 = 慢一倍）。
        """
        torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        n = self.scene.n
        # 纯动画，不建 runtime、不 spawn FAST.Farm；u_inf 直接取场景来流
        self.runtime = None
        u_inf = float(self.scene.wind_speed)
        ctx = {"u_inf": u_inf, "t": 0.0}
        self._publish(phase="warmup")

        # ---- 时间参数（TS = 时间放缩因子；2.0 = 变桨/偏航速率慢一倍、总时长翻倍）----
        TS = 2.0
        static_dur = 1.0 * TS          # 起始静止
        pitch_dur = 5.0 * TS           # 90° 变桨用时（启动/顺桨同速率）
        yaw_dur = 4.0 * TS             # 60° 偏航用时
        spin_dur = 10.0 * TS           # 转速 0→8rpm 用时
        hold_after_8 = 3.0 * TS        # 到 8rpm 后保持多久再顺桨
        stop_dur = 3.0 * TS            # 停转用时（顺桨时同步降速）
        yaw_start = spin_dur / 2.0     # rpm=4（中点）时开始偏航
        lag_t2 = 2.0 * TS              # T2 落后 T1
        lag_t3 = 1.0 * TS              # T3 再落后 T2
        # 转速+偏航合并阶段的总时长：覆盖转速爬升+保持、以及最晚一台偏航到位
        spin_phase_dur = max(spin_dur + hold_after_8,
                             yaw_start + lag_t2 + lag_t3 + yaw_dur)

        # 动画状态：逐机组 yaw/pitch/rpm/power
        state_yaw = np.zeros(n, dtype=float)
        state_pitch = np.full(n, 90.0, dtype=float)
        state_rpm = np.zeros(n, dtype=float)
        step_count = 0
        # 当前阶段名（"开始"/"启动"/"运行"/"停机"/"结束"），进 banner 前缀
        phase_h = {"name": "开始"}

        # 基准功率（演示示意，非物理）：额定 11.4 m/s → 5MW，当前 u_inf 下 P∝u³
        p_base = 5.0 * (u_inf / 11.4) ** 3          # ≈ 2.0 MW @ 8 m/s
        p_up, p_dn = p_base, p_base * 0.75          # 下游尾流打折

        def _powers():
            active = (state_rpm > 0.5) & (state_pitch < 45.0)
            base = np.array(([p_up] + [p_dn] * (n - 1))[:n], dtype=float)
            pw = np.where(active, base, 0.0)
            if n >= 2 and state_yaw[0] > 10.0:      # T1 偏航 → 下游尾流偏走、受益
                pw[0] *= 0.95
                pw[1] *= 1.10
                if n >= 3:
                    pw[2] *= 1.08
            return pw

        def _ev(rule, control, turbine, requested, applied, detail,
                severity="info"):
            """往 SafetyLimiter 写一条演示事件，面板的规则计数/最近事件即时刷新。"""
            from wfrl.safety import Event
            self.limiter._record([Event(
                step_count, turbine, control, rule,
                float(requested), float(applied), severity, detail)])

        def _live_label():
            """banner 文本：阶段名 + 实时数值（转速/桨距/偏航），不写"0→8"这类假值。"""
            rpm = float(state_rpm.max()) if n else 0.0
            pit = float(state_pitch.mean()) if n else 0.0
            yaws = "/".join(f"{state_yaw[i]:.0f}" for i in range(min(n, 3)))
            return (f"{phase_h['name']} · 转速 {rpm:.1f} rpm · "
                    f"桨距 {pit:.0f}° · 偏航 {yaws}°")

        def _publish_frame(record=True):
            """发一帧合成快照。record=False 时只更新画面、不进 history（降采样）。"""
            nonlocal step_count
            step_count += 1
            pw = _powers()
            measure = {
                "yaw": state_yaw.copy(), "pitch": state_pitch.copy(),
                "pitch_meas": state_pitch.copy(), "rotor_speed": state_rpm.copy(),
                "power": pw.copy(), "wind_speed": np.full(n, u_inf),
                "wind_direction": np.full(n, 270.0), "duty": {},
                "reward": 0.0, "done": False, "step": step_count,
            }
            if record:
                self.history.append({
                    "iter": step_count, "step": step_count,
                    "yaw": state_yaw.copy(), "pitch": state_pitch.copy(),
                    "rpm": state_rpm.copy(), "power": pw.copy(),
                    "mean_power": float(pw.sum()),
                    "mean_reward": 0.0, "vloss": 0.0, "explained_var": 0.0,
                })
            self._publish(step=step_count, iters_done=0,
                          frame={"_meta": {"farm_power": float(pw.sum())}},
                          measure=measure, ctx=ctx, farm_power=float(pw.sum()),
                          reward=0.0, events=[], phase="sampling",
                          demo_label=_live_label())

        # history 每 3 帧记一条（曲线够用，避免表格 O(n²) 重建拖慢长演示）
        frame_i = {"k": 0}

        def _step():
            frame_i["k"] += 1
            _publish_frame(record=(frame_i["k"] % 3 == 0))
            time.sleep(0.12)

        def _wait_frames(sec, phase):
            phase_h["name"] = phase
            for _ in range(max(1, int(sec / 0.12))):
                if self._stop.is_set():
                    return False
                _step()
            return True

        def _lerp_frames(sec, phase, update_fn):
            phase_h["name"] = phase
            frames = max(1, int(sec / 0.12))
            for i in range(frames + 1):
                if self._stop.is_set():
                    return False
                update_fn(min(i / max(1.0, float(frames)), 1.0))
                _step()
            return True

        def _make_spin_yaw():
            """转速+错峰偏航，闭包里记录每台的"起偏/到位"事件。"""
            started, reached = set(), set()

            def _fn(frac):
                elapsed = frac * spin_phase_dur
                state_rpm[:] = 8.0 * min(elapsed / spin_dur, 1.0)
                starts = [yaw_start, yaw_start + lag_t2,
                          yaw_start + lag_t2 + lag_t3]
                for i in range(min(n, 3)):
                    state_yaw[i] = 60.0 * min(max((elapsed - starts[i])
                                                  / yaw_dur, 0.0), 1.0)
                    tid = f"T{i+1}"
                    if i not in started and state_yaw[i] > 0.1:
                        started.add(i)
                        _ev("偏航启动", "yaw", tid, 0.0, 60.0,
                            f"{tid} 开始偏航，目标 60° 做尾流偏转", "warn")
                    if i not in reached and state_yaw[i] >= 59.5:
                        reached.add(i)
                        _ev("偏航到位", "yaw", tid, 60.0, 60.0,
                            f"{tid} 偏航达 60°，尾流偏离下游机组", "info")
            return _fn

        def _feather(frac):
            """顺桨 0°→90°，同时在 stop_dur 内把转速降到 0。"""
            state_pitch[:] = 90.0 * frac
            elapsed = frac * pitch_dur
            state_rpm[:] = 8.0 * max(0.0, 1.0 - elapsed / stop_dur)

        def _startup_pitch(frac):
            """启动变桨 90°→0°（速率与顺桨一致）。"""
            state_pitch[:] = 90.0 * (1.0 - frac)

        def _spin_only(frac):
            elapsed = frac * spin_phase_dur
            state_rpm[:] = 8.0 * min(elapsed / spin_dur, 1.0)

        # ---- 执行序列 ----
        for cyc in range(self.demo_cycles):
            first = (cyc == 0)

            if first:
                # 循环 1：起始静止（yaw=0, pitch=90, rpm=0）
                state_yaw[:] = 0.0
                state_pitch[:] = 90.0
                state_rpm[:] = 0.0
                _ev("开始", "-", "全部", 0.0, 0.0,
                    "初始静止：桨距 90° 顺桨、转子停转", "info")
                if not _wait_frames(static_dur, "开始"):
                    return
            # 循环 2+：不重置机位（偏航保持上一循环末值），停机后直接再变桨

            # 阶段：启动变桨 90°→0°
            _ev("变桨启动", "pitch", "全部", 90.0, 0.0,
                "桨距 90°→0°，叶片转到运行角、开始吃风", "info")
            if not _lerp_frames(pitch_dur, "启动", _startup_pitch):
                return

            # 阶段：转速 0→8rpm（循环 1 期间错峰偏航；循环 2+ 偏航保持不动）
            _ev("转子并网", "rotor", "全部", 0.0, 8.0,
                "转子由静止加速至 8 rpm 并网运行", "info")
            if first:
                if not _lerp_frames(spin_phase_dur, "运行", _make_spin_yaw()):
                    return
                state_yaw[:min(n, 3)] = 60.0  # 保证全部到位
            else:
                if not _lerp_frames(spin_phase_dur, "运行", _spin_only):
                    return

            state_rpm[:] = 8.0
            _ev("稳定运行", "-", "全部", 8.0, 8.0,
                "8 rpm 稳定运行，观察偏航后的尾流偏转", "info")
            if not _wait_frames(hold_after_8, "运行"):
                return

            # 阶段：顺桨 0°→90° + 同步停转
            _ev("顺桨制动", "pitch", "全部", 0.0, 90.0,
                "桨距 0°→90° 顺桨，叶片气动制动、转子停转", "warn")
            if not _lerp_frames(pitch_dur, "停机", _feather):
                return
            state_rpm[:] = 0.0
            _ev("停机完成", "-", "全部", 0.0, 0.0,
                "转子停转、桨距 90° 顺桨，本循环结束", "info")

        # 结束（保持末态）
        phase_h["name"] = "结束"
        self._publish(step=step_count, iters_done=1,
                      frame={"_meta": {"farm_power": 0.0}},
                      measure={"yaw": state_yaw, "pitch": state_pitch,
                               "pitch_meas": state_pitch,
                               "rotor_speed": state_rpm,
                               "power": np.zeros(n)},
                      ctx=ctx, farm_power=0.0, reward=0.0,
                      events=[], phase="done", demo_label=_live_label())

    def _train(self):
        from wfrl.scene.runtime import SceneRuntime
        torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        n = self.scene.n
        total = self.iters * self.n_steps
        # driver 的预算要一次给够：FAST.Farm 在 t=0 领到固定迭代数，中途加不了。
        # +8 留给 close() 排空与热身。
        budget = total + self.warmup_steps + 8
        self.runtime = SceneRuntime(self.scene, max_steps=budget,
                                    warmup_steps=self.warmup_steps)
        # warmup 交给 trainer 逐步发快照：runtime.reset 里默认会把 warmup 步全部
        # 空转完再返回，那段几十秒里 GUI 拿不到 measure、叶片静止不动。这里把
        # warmup 设 0、先 reset（driver 一 reset 就有 rotor_speed），立刻发一帧让
        # 叶片开始转，再自己逐步跑 warmup、每步发快照 —— 转子在 spawn 一结束就动。
        want_warmup = self.warmup_steps
        self.runtime.warmup_steps = 0
        self._publish(phase="warmup")
        frame = self.runtime.reset()
        m = self.runtime.last_measure
        self._publish(step=0, iters_done=0, frame=frame, measure=m,
                      ctx=self.runtime.last_ctx,
                      farm_power=float(frame["_meta"].get("farm_power") or 0.0),
                      reward=0.0, events=[], phase="warmup")
        for _ in range(want_warmup):
            if self._stop.is_set():
                break
            frame = self.runtime.step(yaw_delta=np.zeros(n))
            m = self.runtime.last_measure
            self._publish(step=0, iters_done=0, frame=frame, measure=m,
                          ctx=self.runtime.last_ctx,
                          farm_power=float(frame["_meta"].get("farm_power") or 0.0),
                          reward=0.0, events=[], phase="warmup")

        # 观测 = (各被控量当前值, wind_speed, wind_direction)，动作 = 各被控量增量。
        # 维度从 scene.controls 派生（obs_keys 在 __init__ 里已建好）：controls=[yaw]
        # 时回到 3/1，与旧的单控路径逐位一致；[yaw,pitch,torque] 时是 5/3。被控量在
        # m 里存的是 driver 回读值（yaw/pitch 是累加指令、torque 是实测转矩），都是
        # 策略该看的状态。act_high 逐通道取各自动作边界，tanh 后各乘各的上界。
        act_high = np.array([self.runtime.driver.act_bounds[c][1]
                             for c in self.controls], np.float32)
        obs_dim, act_dim = len(self.obs_keys), len(self.controls)
        state_dim = n * obs_dim
        self.actor = Actor(obs_dim, act_dim).to(DEVICE)
        self.critic = Critic(state_dim).to(DEVICE)
        opt = torch.optim.Adam(
            list(self.actor.parameters()) + list(self.critic.parameters()),
            lr=self.hp["lr"])
        rms = RunningMeanStd(obs_dim)
        ret_rms_var, ret_acc = 1.0, 0.0     # 回报归一化（等价 VecNormalize）

        gstep = 0
        for it in range(self.iters):
            if self._stop.is_set():
                break
            obs_buf = np.zeros((self.n_steps, n, obs_dim), np.float32)
            act_buf = np.zeros((self.n_steps, n, act_dim), np.float32)
            logp_buf = np.zeros((self.n_steps, n, act_dim), np.float32)
            rew_buf = np.zeros(self.n_steps, np.float32)
            pow_buf = np.full(self.n_steps, np.nan, np.float32)
            val_buf = np.zeros(self.n_steps + 1, np.float32)
            # 实际采到的步数。回合提前结束（预算耗尽 / 用户停）时它 < n_steps，
            # 后面所有切片都必须用它 —— 拿满长度去算 GAE 等于把尾部一串零当成
            # 真实的零回报学进去，价值函数会被拽平。
            filled = 0

            for k in range(self.n_steps):
                while self._paused.is_set() and not self._stop.is_set():
                    time.sleep(0.05)
                if self._stop.is_set():
                    break
                o = self._pack(m)
                rms.update(o)
                on = rms.norm(o).astype(np.float32)
                ot = torch.as_tensor(on, device=DEVICE)
                with torch.no_grad():
                    dist = self.actor.dist(ot)
                    a = dist.sample()
                    logp = dist.log_prob(a)
                    st = torch.as_tensor(on.reshape(-1), device=DEVICE)
                    v = self.critic.v(st).squeeze(-1)

                # a: (n, act_dim)。tanh 后逐通道乘各自动作上界（act_high 按
                # controls 顺序），再过约束层。约束层要一份 {控制量: (n,)} 的字典。
                a_scaled = np.tanh(a.cpu().numpy()) * act_high            # (n, act_dim)
                cmd, evs = self.limiter.apply(
                    {c: a_scaled[:, j] for j, c in enumerate(self.controls)},
                    m, enforce_duty=self.enforce_duty)
                frame = self.runtime.step(
                    yaw_delta=cmd.get("yaw"), pitch_delta=cmd.get("pitch"),
                    torque_delta=cmd.get("torque"))
                m = self.runtime.last_measure
                evs = evs + self.limiter.monitor(m)

                r = float(frame["_meta"].get("reward") or 0.0)
                obs_buf[k], act_buf[k] = on, a.cpu().numpy()
                logp_buf[k], rew_buf[k], val_buf[k] = logp.cpu().numpy(), r, v
                pow_buf[k] = float(frame["_meta"]["farm_power"])
                filled = k + 1
                gstep += 1
                self._publish(step=gstep, iters_done=it, frame=frame,
                              measure=m, ctx=self.runtime.last_ctx,
                              farm_power=float(frame["_meta"]["farm_power"]),
                              reward=r, events=evs, phase="sampling")
                if self.runtime.done:
                    break

            if self._stop.is_set() or filled == 0:
                break
            # 回报归一化：奖励量级随风速三次方变，不归一 vloss 会压过策略梯度
            for r in rew_buf[:filled]:
                ret_acc = ret_acc * self.hp["gamma"] + float(r)
                ret_rms_var = 0.99 * ret_rms_var + 0.01 * ret_acc ** 2
            rew_n = rew_buf[:filled] / (np.sqrt(ret_rms_var) + 1e-8)

            with torch.no_grad():
                st = torch.as_tensor(self._pack_norm(m, rms).reshape(-1),
                                     device=DEVICE)
                # 回合是被**预算截断**的，不是真终止 —— 尾值必须自举，置 0
                # 等于告诉 critic"这里之后再无回报"，价值函数会在每个回合末尾
                # 被拽出一个假的塌陷。
                val_buf[filled] = self.critic.v(st).squeeze(-1)
            adv, ret = self._gae(rew_n, val_buf[:filled + 1])
            stats = self._update(obs_buf[:filled], act_buf[:filled],
                                 logp_buf[:filled], adv, ret, opt)
            # 全场功率取**本轮整段**的均值而不是最后一帧：一帧受尾流蜿蜒的
            # 瞬时起伏影响能差 10%，画在曲线上看不出策略有没有在涨。
            stats.update(iter=it, step=gstep,
                         mean_power=float(np.nanmean(pow_buf[:filled])),
                         mean_reward=float(rew_buf[:filled].mean()))
            self.history.append(stats)
            self._publish(step=gstep, iters_done=it + 1, frame=frame,
                          measure=m, ctx=self.runtime.last_ctx,
                          farm_power=float(frame["_meta"]["farm_power"]),
                          reward=float(rew_buf.mean()), events=[],
                          phase="updating")

        if self.ckpt_path:
            self._save(rms)

    # ---- 算法细节（与 mappo_central.py 同一套口径）-----------------------
    def _pack(self, m):
        """观测矩阵 (n, obs_dim)：列 = 各被控量当前值 + 风速 + 风向。

        列顺序由 self.obs_keys 定（controls + wind_speed + wind_direction），
        在 _train 里按 scene.controls 建好。被控量取 driver 回读：yaw/pitch 是
        累加指令、torque 是实测转矩 —— 都是策略下一步该看的状态。
        """
        cols = [np.asarray(m[k], float).ravel() for k in self.obs_keys]
        return np.stack(cols, axis=1).astype(np.float32)

    def _pack_norm(self, m, rms):
        return rms.norm(self._pack(m)).astype(np.float32)

    def _gae(self, rew, val):
        gamma, lam = self.hp["gamma"], self.hp["lam"]
        T = len(rew)
        adv = np.zeros(T, np.float32)
        last = 0.0
        for t in reversed(range(T)):
            delta = rew[t] + gamma * val[t + 1] - val[t]
            last = delta + gamma * lam * last
            adv[t] = last
        return adv, adv + val[:T]

    def _update(self, obs, act, logp, adv, ret, opt):
        T = len(adv)
        ot = torch.as_tensor(obs[:T], device=DEVICE)
        at = torch.as_tensor(act[:T], device=DEVICE)
        lt = torch.as_tensor(logp[:T], device=DEVICE)
        advt = torch.as_tensor(adv, device=DEVICE)
        rett = torch.as_tensor(ret, device=DEVICE)
        stt = ot.reshape(T, -1)
        clip, mb = self.hp["clip"], max(1, self.hp["mb_steps"])
        last = {}
        for _ in range(self.hp["n_epochs"]):
            idx = torch.randperm(T)
            for s in range(0, T, mb):
                b = idx[s:s + mb]
                dist = self.actor.dist(ot[b])
                new_logp = dist.log_prob(at[b])
                ratio = (new_logp - lt[b]).exp()
                # 团队奖励 ⇒ 标量优势广播给 n 台。这是 MAPPO 的参数共享口径，
                # 不是 bug：每台机组的信用分配靠集中式 critic，不靠拆奖励。
                A = advt[b].unsqueeze(-1).unsqueeze(-1).expand_as(ratio)
                A = (A - A.mean()) / (A.std() + 1e-8)
                pg = -torch.min(ratio * A,
                                torch.clamp(ratio, 1 - clip, 1 + clip) * A).mean()
                ent = dist.entropy().mean()
                v = self.critic.v(stt[b]).squeeze(-1)
                vloss = ((v - rett[b]) ** 2).mean()
                loss = (pg - self.hp["ent_coef"] * ent
                        + self.hp["vf_coef"] * vloss)
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    list(self.actor.parameters()) + list(self.critic.parameters()),
                    self.hp["max_grad"])
                opt.step()
                last = {"pg": pg.item(), "vloss": vloss.item(),
                        "ent": ent.item()}
        with torch.no_grad():
            v_all = self.critic.v(stt).squeeze(-1).cpu().numpy()
        var = float(np.var(ret))
        last["explained_var"] = (float(1.0 - np.var(ret - v_all) / var)
                                 if var > 1e-12 else 0.0)
        return last

    def _save(self, rms):
        os.makedirs(os.path.dirname(self.ckpt_path), exist_ok=True)
        obs_dim = len(self.obs_keys)
        torch.save({"actor": self.actor.state_dict(),
                    "critic": self.critic.state_dict(),
                    "obs_mean": rms.mean, "obs_var": rms.var,
                    "obs_keys": list(self.obs_keys),
                    "obs_dim": obs_dim, "act_dim": len(self.controls),
                    "controls": list(self.controls),
                    "state_dim": self.scene.n * obs_dim,
                    "scene": self.scene.to_dict()}, self.ckpt_path)
        print(f"[trainer] checkpoint → {self.ckpt_path}", flush=True)
