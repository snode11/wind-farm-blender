"""控制指令的物理与安全约束层。

策略网络输出的是一组无量纲实数。它和"能下发给风机的指令"之间隔着一串硬约束，
这一层把它们写成代码，并且**记录每一次修改**。

存在的理由不是"再裁一次"。仿真侧已经在三处静默地改动作：

    DEFAULT ACTION SPACE   每步增量裁到 ±5°/±1°/±1e3   (mdp.py:300-305)
    状态边界               累加值裁到 yaw[-40,40] 等    (mdp.py:311-315)
    执行机构占空比          达到逐控制量 duty_limit 时把该控制量整个清零

占空比是清零门控，不是增量限幅。FastFarmDriver 的 yaw 默认走滑动窗口，
上限由其运行配置给出；pitch 及关闭滑动窗口的 yaw 走 env 累计版 0.1。
本层按测量中的 duty_limit 判定；缺少该字段或控制量时回退 DUTY_LIMIT。
sustainable/reachable/notes 不接收测量，只描述 0.1 的累计模型估算，不能
据此判断滑动窗口的运行预算；运行预算由 budget/apply 的测量决定。

还有两条仿真侧**不检查**、必须我们自己判的：

    变桨是下界不是指令   PitchRef = MAX(PitchRef, PitComT)   (DISCON.F90:523)
        外部桨距低于基线控制器的 PitComT 时完全无效。region 2 里 PitComT≈0，
        所以下发 0° 等于没发；反过来在 region 3 里基线已经在顺桨，外部指令要
        高于它才起作用。UI 要能说出"这条指令被基线顶替了"。

    转矩是顶替不是叠加   GenTrq = TorqueRef                  (DISCON.F90:440)
        外部转矩**完全替换**基线 VS 控制器的输出，而指令带 ±2e4 N·m 只有额定
        43529 N·m（VS_RtPwr/VS_RtGnSp）的 46%。直接把策略输出当转矩下发，气动
        转矩大于发电转矩，转子加速。基线变桨在发电机转速超过 PC_RefSpd 后顺桨
        限速：无外部变桨指令时按 8 °/s 速率限制执行，有外部指令时作为下界；
        转矩缺口变成功率损失。仿真器没有硬停机，超速仍由本层按转速回读报告。

所以这一层的输出有两部分：改过的指令，和一串 `Event`。Event 才是重点 —— 训练
面板上要看得见"第 k 步 T2 的偏航被占空比清零了"，而不是只看到奖励不涨。

用法：

    lim = SafetyLimiter(scene)
    cmd, events = lim.apply({"yaw": a}, measure)     # measure 来自 driver.step
    m = rt.step(yaw_delta=cmd["yaw"])
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

# --- NREL 5MW 基线控制器常数，全部来自 DISCON.F90 的 PARAMETER 段 -----------
GB_RATIO = 97.0            # NREL 5MW 齿轮箱速比
VS_RtGnSp = 121.6805       # 额定发电机转速 (rad/s)，DISCON.F90:85
VS_RtPwr = 5296610.0       # 额定发电功率 (W)，DISCON.F90:86
VS_MaxTq = 47402.91        # 发电机转矩上限 (N·m)，DISCON.F90:81
PC_MaxPit = 90.0           # 桨距上限 (deg)，DISCON.F90:63 的 1.570796 rad

# 基线变速控制器（VS）的转矩-转速曲线常数，DISCON.F90:78-90。这条曲线就是
# **维持最优叶尖速比**的发电机转矩：GenTrq = f(ω_gen)。外部转矩完全顶替它
# （DISCON.F90:440），所以合法的运行包线应锚定在这条曲线上 —— 偏低转子加速
# 至基线变桨限速、偏高转子减速失速（TSR 掉到 ~2 时 BEM 关闭，气动没了，转子停）。
VS_CtInSp = 70.16224       # region 1→1½ 切入转速 (rad/s)，DISCON.F90:78
VS_Rgn2Sp = 91.21091       # region 1½→2 转速 (rad/s)，DISCON.F90:83
VS_Rgn2K = 2.332287        # region 2 转矩系数 N·m/(rad/s)²，DISCON.F90:82
VS_SlPc = 10.0             # region 2½ 滑差百分比，DISCON.F90:89
VS_SySp = VS_RtGnSp / (1.0 + 0.01 * VS_SlPc)             # DISCON.F90:167
VS_Slope15 = (VS_Rgn2K * VS_Rgn2Sp ** 2) / (VS_Rgn2Sp - VS_CtInSp)  # :168
VS_Slope25 = (VS_RtPwr / VS_RtGnSp) / (VS_RtGnSp - VS_SySp)          # :169
VS_TrGnSp = ((VS_Slope25 - math.sqrt(
    VS_Slope25 * (VS_Slope25 - 4.0 * VS_Rgn2K * VS_SySp)))
    / (2.0 * VS_Rgn2K))                                  # DISCON.F90:173


def baseline_gen_torque(rpm):
    """基线 VS 控制器在给定**转子转速** rpm 下会下发的发电机转矩 (N·m)。

    按转速复刻 DISCON.F90:426-435 的五段式曲线（换算到发电机高速轴）。DISCON
    在无外部变桨且桨距 ≥ VS_Rgn3MP 时也取恒功率段，这里只按转速判定；外部
    转矩下发时 DISCON 不经过该曲线。这是维持最优叶尖速比的转矩 —— 外部转矩的
    合法包线就锚定在它上下。rpm 给 nan 时返回
    额定转矩的一半作为兜底（既不超速也不至于失速的粗略中点）。
    """
    rpm = np.asarray(rpm, float)
    w = rpm * GB_RATIO * 2.0 * math.pi / 60.0            # 转子 rpm → 发电机 rad/s
    t = np.empty_like(w)
    t[:] = 0.5 * RATED_TORQUE                            # nan 兜底
    ok = np.isfinite(w)
    ww = np.where(ok, w, VS_Rgn2Sp)
    reg3 = ok & (ww >= VS_RtGnSp)
    reg1 = ok & (ww <= VS_CtInSp)
    reg15 = ok & (ww > VS_CtInSp) & (ww < VS_Rgn2Sp)
    reg2 = ok & (ww >= VS_Rgn2Sp) & (ww < VS_TrGnSp)
    reg25 = ok & (ww >= VS_TrGnSp) & (ww < VS_RtGnSp)
    t[reg3] = VS_RtPwr / ww[reg3]
    t[reg1] = 0.0
    t[reg15] = VS_Slope15 * (ww[reg15] - VS_CtInSp)
    t[reg2] = VS_Rgn2K * ww[reg2] ** 2
    t[reg25] = VS_Slope25 * (ww[reg25] - VS_SySp)
    return t


#: 外部转矩相对基线 VS 曲线的合法带：低于 (1−LO)·T_vs 转子加速至基线变桨限速，高于 (1+HI)·T_vs
#: 会失速。取 ±0.25 —— region 2 里 T∝ω²，留 25% 让策略能调节又不至把 TSR 拽到
#: 失速。这两个数经 probe_torque_env.py 在真 FAST.Farm 上验证过不失速。
TORQUE_ENV_LO = 0.25
TORQUE_ENV_HI = 0.25

#: 额定转子转速 (rpm) = VS_RtGnSp 换算到低速轴 = 11.98
RATED_RPM = VS_RtGnSp * 60.0 / (2.0 * math.pi) / GB_RATIO
#: 额定发电机转矩 (N·m) = 43529；动作边界 ±2e4 只有它的 46%
RATED_TORQUE = VS_RtPwr / VS_RtGnSp
#: 超速判据。ElastoDyn 没有硬停机逻辑，这条是我们自己设的运行包线。
#: 取额定的 1.1 倍 —— 与 DISCON 用 VS_MaxTq = 1.1 × VS_RtTq 同一个裕度口径。
RPM_ALARM = 1.1 * RATED_RPM

#: env 累计版上限及缺少 duty_limit 测量时的回退，multiagent_env.py 的 `>= 0.1`
DUTY_LIMIT = 0.1
#: 各控制量的执行机构速率 (单位/s)，mdp.py:52。torque 没有条目 ⇒ 不受占空比约束
ACTUATOR_RATE = {"yaw": 0.3, "pitch": 8.0}

#: 每步增量边界与累加值边界，data_cases.py:20-23 的 DefaultControl
STEP_BOUND = {"yaw": 5.0, "pitch": 1.0, "torque": 1e3}
STATE_BOUND = {"yaw": (-40.0, 40.0), "pitch": (0.0, 45.0),
               "torque": (-2e4, 2e4)}

SEVERITY = ("info", "warn", "block")


def control_duty_limit(measure, control):
    """Read a control's effective limit, falling back to the cumulative env limit."""
    return float(((measure or {}).get("duty_limit") or {}).get(control, DUTY_LIMIT))


@dataclass
class Event:
    """一次约束动作。UI 逐条显示，训练结束后统计。"""
    step: int
    turbine: str
    control: str
    rule: str
    requested: float
    applied: float
    severity: str = "warn"
    detail: str = ""

    @property
    def blocked(self) -> bool:
        return self.severity == "block"

    def __str__(self):
        return (f"[{self.step:>4}] {self.turbine} {self.control} "
                f"{self.rule}: {self.requested:+.3g} → {self.applied:+.3g}"
                + (f"  ({self.detail})" if self.detail else ""))


class SafetyLimiter:
    """把策略输出改造成可下发指令，并记录每一次改动。

    `apply` 只做**能在下发前判定**的事。转速超限这类要看仿真回读的，走
    `monitor`：它不改指令（这一步已经发出去了），只发事件，由调用方决定是
    降转矩还是终止回合。
    """

    def __init__(self, scene, max_events: int = 2000):
        self.scene = scene
        self.n = scene.n
        self.dt = float(scene.dt)
        self.controls = list(scene.controls)
        self.ids = [t.id for t in scene.turbines]
        self.max_events = int(max_events)
        if self.max_events < 0:
            raise ValueError("max_events 必须为非负整数")
        self.events: List[Event] = []
        self.counts: Dict[str, int] = {}
        self._step = 0
        # 累加的指令值，用来预测状态边界裁剪。仿真侧的 next_state 我们看不到
        # 全部（torque 在 obs 里不是被控量时读不回来），所以自己跟一份。
        self.state = {c: np.zeros(self.n) for c in self.controls}

    # ------------------------------------------------------------------
    def sustainable(self, control: str) -> float:
        """以 DUTY_LIMIT=0.1 估算 env 累计版的每步可持续 |Δ|。

        本方法不读取运行测量，也不表示滑动窗口的实际配置。
        占空比 = Σ|Δ| / rate / (k·dt)，稳态临界量为 limit·rate·dt。
        """
        r = ACTUATOR_RATE.get(control)
        return float("inf") if r is None else DUTY_LIMIT * r * self.dt

    def budget(self, measure: Dict[str, Any], control: str) -> np.ndarray:
        """本步在被清零之前还能发多大的 |Δ| (n,)。没有占空比约束的返回 inf。

        driver 的 duty 表示下一步判据：yaw 滑动窗口取当前窗口，env 累计版
        取累计除以 k+1 步。本步的 |Δ| 不参与判定；duty 达到该控制量的
        duty_limit 就返回 0，否则返回 inf。缺少上限时使用 DUTY_LIMIT。
        """
        if control not in ACTUATOR_RATE:
            return np.full(self.n, np.inf)
        duty = (measure or {}).get("duty") or {}
        d = duty.get(control)
        if d is None:
            return np.full(self.n, np.inf)
        d = np.asarray(d, float).ravel()
        return np.where(d >= control_duty_limit(measure, control), 0.0, np.inf)

    # ------------------------------------------------------------------
    def apply(self, cmd: Dict[str, Any], measure: Optional[Dict[str, Any]] = None,
              enforce_duty: bool = False):
        """把一组指令增量过一遍约束。返回 (安全指令, 本步事件)。

        `enforce_duty=False`（默认）时**不**改动作，只在占空比已超时发一条
        block 事件说明"这一步环境会把它清零"。改成 True 会主动把指令压到 0 ——
        效果一样但训练数据里的动作与实际执行一致，做行为克隆时需要。
        """
        self._step += 1
        out: Dict[str, np.ndarray] = {}
        evs: List[Event] = []
        for c in self.controls:
            v = cmd.get(c)
            if v is None:
                out[c] = np.zeros(self.n)
                continue
            v = np.asarray(v, dtype=float).ravel()
            if v.size == 1:
                v = np.repeat(v, self.n)
            if v.size != self.n:
                raise ValueError(f"控制量 {c} 长度 {v.size} != 机组数 {self.n}")
            finite = np.isfinite(v)
            v = self._nan_guard(c, v, evs)
            v = self._step_bound(c, v, evs)
            v = self._state_bound(c, v, evs)
            v = self._duty(c, v, measure, evs, enforce_duty)
            if c == "pitch":
                self._pitch_semantics(v, measure, evs)
            if c == "torque":
                v = self._torque_semantics(v, measure, evs, finite)
            out[c] = v
            self.state[c] = self.state[c] + v
        self._record(evs)
        return out, evs

    def monitor(self, measure: Dict[str, Any]) -> List[Event]:
        """按直接转速回读报告超速；缺测不推断，低转矩不撤销超速报警。"""
        evs: List[Event] = []
        m = measure or {}
        nan = np.full(self.n, np.nan)
        latest = np.asarray(m.get("rotor_speed", nan), float).ravel()
        # 区间内全部有效原生样本的峰值及其仿真时刻（solver_outputs.SolverOutputs.sample）；
        # 没有原生输出读取器的后端不提供这三项。
        peak = np.asarray(m.get("rotor_speed_interval_max", nan), float).ravel()
        peak_time = np.asarray(m.get("rotor_speed_interval_max_time", nan), float).ravel()
        samples = np.asarray(m.get("rotor_speed_interval_samples", np.zeros(self.n)), float).ravel()
        for i in range(self.n):
            r = float(latest[i]) if i < latest.size else float("nan")
            when = None
            if (i < samples.size and samples[i] > 0
                    and i < peak.size and np.isfinite(peak[i])):
                r = float(peak[i])
                if i < peak_time.size and np.isfinite(peak_time[i]):
                    when = float(peak_time[i])
            if not np.isfinite(r) or r <= RPM_ALARM:
                continue
            at = "" if when is None else f"，峰值时刻 t={when:.2f} s"
            evs.append(Event(self._step, self.ids[i], "rotor_speed",
                             "超速", r, r, "block",
                             f"额定 {RATED_RPM:.2f} rpm，报警线 "
                             f"{RPM_ALARM:.2f} rpm{at}"))
        self._record(evs)
        return evs

    # ---- 各条规则 ------------------------------------------------------
    def _nan_guard(self, c, v, evs):
        """策略输出 nan/inf 时归零。

        不是防御性编程的客套：Gaussian 策略在 log_std 发散时会吐 nan，而
        `np.clip(nan, lo, hi)` 仍是 nan，一路传进 FAST.Farm 会让整个算例在
        MPI 那头静默挂死 —— 表现是等 20 分钟没有任何输出。
        """
        bad = ~np.isfinite(v)
        if bad.any():
            for i in np.where(bad)[0]:
                evs.append(Event(self._step, self.ids[i], c, "非有限值",
                                 float(v[i]), 0.0, "block",
                                 "策略输出 nan/inf，已归零"))
            v = np.where(bad, 0.0, v)
        return v

    def _step_bound(self, c, v, evs):
        b = STEP_BOUND.get(c)
        if b is None:
            return v
        clipped = np.clip(v, -b, b)
        for i in np.where(np.abs(clipped - v) > 1e-9)[0]:
            evs.append(Event(self._step, self.ids[i], c, "单步增量越界",
                             float(v[i]), float(clipped[i]), "warn",
                             f"动作空间 ±{b:g}（data_cases.py:20-23）"))
        return clipped

    def _state_bound(self, c, v, evs):
        lo, hi = STATE_BOUND.get(c, (-np.inf, np.inf))
        nxt = self.state[c] + v
        room = np.clip(nxt, lo, hi) - self.state[c]
        for i in np.where(np.abs(room - v) > 1e-9)[0]:
            evs.append(Event(self._step, self.ids[i], c, "累加值越界",
                             float(v[i]), float(room[i]), "warn",
                             f"{c} 已在 {self.state[c][i]:+.2f}，边界 "
                             f"[{lo:g}, {hi:g}]（mdp.py:311-315 会静默裁）"))
        return room

    def _duty(self, c, v, measure, evs, enforce):
        if c not in ACTUATOR_RATE:
            return v
        room = self.budget(measure, c)
        over = (room == 0.0) & (np.abs(v) > 1e-9)
        if not over.any():
            return v
        duty = np.asarray(((measure or {}).get("duty") or {}).get(
            c, np.zeros(self.n)), float).ravel()
        limit = control_duty_limit(measure, c)
        sustainable = limit * ACTUATOR_RATE[c] * self.dt
        effect = "本步指令已归零" if enforce else "后端会把本步指令整个归零"
        for i in np.where(over)[0]:
            evs.append(Event(
                self._step, self.ids[i], c, "执行机构占空比清零",
                float(v[i]), 0.0 if enforce else float(v[i]), "block",
                f"占空比 {duty[i]:.3f} ≥ {limit:g}，{effect}；"
                f"按该上限的可持续量 {sustainable:.3g} °/步"))
        return np.where(over, 0.0, v) if enforce else v

    def _pitch_semantics(self, v, measure, evs):
        """外部桨距只是**下界**，低于基线 PitComT 的部分不会生效。

        不改指令 —— 改也没用，MAX 在控制器那头。只发事件，让 UI 能说出
        "这一步的变桨没起作用"，否则策略学的是一个从未被执行过的动作。
        """
        pm = np.asarray((measure or {}).get("pitch_meas",
                                            np.full(self.n, np.nan)), float)
        cmd_abs = self.state["pitch"] + v
        for i in range(self.n):
            if i >= pm.size or not np.isfinite(pm[i]):
                continue
            # 指令低于实测 ⇒ 实测是基线给的，外部这一路被 MAX 顶替
            if cmd_abs[i] < pm[i] - 0.05:
                evs.append(Event(
                    self._step, self.ids[i], "pitch", "指令被基线顶替",
                    float(cmd_abs[i]), float(pm[i]), "info",
                    f"PitchRef = MAX(PitchRef, PitComT)（DISCON.F90:523），"
                    f"基线在 {pm[i]:.2f}°，外部 {cmd_abs[i]:.2f}° 无效"))

    def _torque_semantics(self, v, measure, evs, finite):
        """向基线 VS 包线修正转矩，受单步及累加指令边界约束。

        当前值距包线过远时只能逐步接近，不能保证一步进入包线。
        事件和内部状态使用 Driver 可提交的 float32 增量；实际转矩仍取决于后端。
        非有限原始指令对应的增量保持为零，不参与包线修正或生成修正事件。
        """
        tq = np.asarray((measure or {}).get("torque",
                                            np.full(self.n, np.nan)), float)
        rpm = np.asarray((measure or {}).get("rotor_speed",
                                             np.full(self.n, np.nan)), float)
        base = np.where(np.isfinite(tq), tq, RATED_TORQUE)      # 当前绝对转矩
        t_vs = baseline_gen_torque(rpm)                        # 最优 TSR 的转矩
        t_lo = (1.0 - TORQUE_ENV_LO) * t_vs
        t_hi = (1.0 + TORQUE_ENV_HI) * t_vs
        target = np.clip(base + v, t_lo, t_hi)                 # 限进包线的绝对值
        lo, hi = STATE_BOUND["torque"]
        bound = STEP_BOUND["torque"]
        lower = np.maximum(-bound, lo - self.state["torque"])
        upper = np.minimum(bound, hi - self.state["torque"])
        adj = np.clip(target - base, lower, upper).astype(np.float32).astype(float)
        adj = np.where(finite, adj, 0.0)
        changed = finite & ((np.abs(target - (base + v)) > 1e-6) | (np.abs(adj - v) > 1e-6))
        for i in np.where(changed)[0]:
            over = (base[i] + v[i]) > t_hi[i]
            incomplete = not t_lo[i] - 1e-6 <= base[i] + adj[i] <= t_hi[i] + 1e-6
            evs.append(Event(
                self._step, self.ids[i], "torque",
                "转矩超运行上限（防失速）" if over else "转矩低于运行下限（防超速）",
                float(v[i]), float(adj[i]), "warn" if incomplete else "block",
                f"基线 VS 在 {rpm[i]:.2f} rpm 下给 {t_vs[i]:.0f} N·m，"
                f"包线 [{t_lo[i]:.0f}, {t_hi[i]:.0f}]；"
                f"目标 {target[i]:.0f}，本步可达 {base[i] + adj[i]:.0f} N·m"
                + ("；受单步或累加边界限制，尚未进入包线" if incomplete else "")))
        return adj

    # ---- 汇总 ----------------------------------------------------------
    def _record(self, evs):
        for e in evs:
            self.counts[e.rule] = self.counts.get(e.rule, 0) + 1
        self.events.extend(evs)
        if len(self.events) > self.max_events:
            # 只丢最老的：训练跑几千步时事件量会很大，但最近的那些才是要看的
            del self.events[:len(self.events) - self.max_events]

    def summary(self) -> List[tuple]:
        """(规则, 次数) 按次数降序，训练面板直接渲染。"""
        return sorted(self.counts.items(), key=lambda kv: -kv[1])

    def recent(self, k: int = 20) -> List[Event]:
        if k == 0:
            return []
        return self.events[-k:]

    def reachable(self, control: str, n_steps: int) -> float:
        """以 DUTY_LIMIT=0.1 估算 env 累计版在 n_steps 步内的累计变化量。

        不读取运行测量，不考虑状态边界，也不作为滑动窗口的可达上界。

        估算为 DUTY_LIMIT·rate·dt·K + step_bound；末项表示首步累计为零时
        允许的一次满幅动作。累计器计动作空间裁剪后的量，状态边界截去的
        部分仍计入占空比；非末位 agent 的累计回抄滞后一个控制步。
        完整逐机组时序由 probe_duty_env.EnvModel 描述。
        """
        s = self.sustainable(control)
        if math.isinf(s):
            return float("inf")
        return s * int(n_steps) + STEP_BOUND.get(control, 0.0)

    def notes(self, n_steps: Optional[int] = None) -> List[str]:
        """报告 DUTY_LIMIT 的累计模型估算；运行限制须读取测量 duty_limit。"""
        out = []
        for c in self.controls:
            s = self.sustainable(c)
            if math.isinf(s):
                out.append(f"{c}: 不受执行机构占空比约束（mdp.py:52 无条目）")
                continue
            bound = STEP_BOUND.get(c, float("nan"))
            line = f"{c}: 动作空间 ±{bound:g}/步，"
            if bound > s:
                line += (f"但占空比可持续量仅 {s:.3g}/步（差 {bound / s:.0f} 倍），"
                         f"持续满幅动作必被清零")
            else:
                line += f"占空比可持续量 {s:.3g}/步，满幅动作不超过占空比可持续量"
            if n_steps:
                line += (f"；{n_steps} 步回合内最多累积 "
                         f"{self.reachable(c, n_steps):.3g}")
            line += (f"（累计模型上限 {DUTY_LIMIT:g} 的估算，未使用运行测量；"
                     "不含状态边界，不代表 yaw 滑动窗口，实际限制以 duty_limit 为准）")
            out.append(line)
        return out
