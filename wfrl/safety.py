"""控制指令的物理与安全约束层。

策略网络输出的是一组无量纲实数。它和"能下发给风机的指令"之间隔着一串硬约束，
这一层把它们写成代码，并且**记录每一次修改**。

存在的理由不是"再裁一次"。仿真侧已经在三处静默地改动作：

    DEFAULT ACTION SPACE   每步增量裁到 ±5°/±1°/±1e3   (mdp.py:300-305)
    状态边界               累加值裁到 yaw[-40,40] 等    (mdp.py:311-315)
    执行机构占空比          超 10% 把该控制量**整个清零** (multiagent_env.py:196-207)

第三条最要命：它不是限幅，是清零，而且是**累计预算**式的清零 —— accumulator
回合内永不衰减，判据 acc/(rate·k·dt) 约束的是"历史平均动作速率"。策略以为
自己发了 5° 偏航，环境执行 0°，回报照常算 —— 训练曲线上什么都看不出来，只有
奖励莫名其妙不涨。dt=3 s 时 yaw 的稳态可持续量只有 0.09 °/步（0.1 ×
ACTUATORS_RATE 0.3 °/s × 3 s），于是 K 步回合内**可达偏航被封在 0.09K 附近**
（见 `reachable`）：128 步只能走到 16.5°，而尾流偏航的增益要到 20° 才显现。
`probe_duty_env.py` 在真环境上逐位零偏差地复现了这套时序。

还有两条仿真侧**不检查**、必须我们自己判的：

    变桨是下界不是指令   PitchRef = MAX(PitchRef, PitComT)   (DISCON.F90:523)
        外部桨距低于基线控制器的 PitComT 时完全无效。region 2 里 PitComT≈0，
        所以下发 0° 等于没发；反过来在 region 3 里基线已经在顺桨，外部指令要
        高于它才起作用。UI 要能说出"这条指令被基线顶替了"。

    转矩是顶替不是叠加   GenTrq = TorqueRef                  (DISCON.F90:440)
        外部转矩**完全替换**基线 VS 控制器的输出，而指令带 ±2e4 N·m 只有额定
        43529 N·m（VS_RtPwr/VS_RtGnSp）的 46%。直接把策略输出当转矩下发，气动
        转矩大于发电转矩，转子加速直到超速 —— 仿真器不会拦，它只会给出越来越
        大的转速然后发散。

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
GB_RATIO = 97.0            # 齿轮箱速比，与 fastfarm_driver.py:38 同源
VS_RtGnSp = 121.6805       # 额定发电机转速 (rad/s)，DISCON.F90:85
VS_RtPwr = 5296610.0       # 额定发电功率 (W)，DISCON.F90:86
VS_MaxTq = 47402.91        # 发电机转矩上限 (N·m)，DISCON.F90:81
PC_MaxPit = 90.0           # 桨距上限 (deg)，DISCON.F90:63 的 1.570796 rad

# 基线变速控制器（VS）的转矩-转速曲线常数，DISCON.F90:78-90。这条曲线就是
# **维持最优叶尖速比**的发电机转矩：GenTrq = f(ω_gen)。外部转矩完全顶替它
# （DISCON.F90:440），所以合法的运行包线应锚定在这条曲线上 —— 偏低转子加速
# 超速、偏高转子减速失速（TSR 掉到 ~2 时 BEM 关闭，气动没了，转子停）。
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

    逐位复刻 DISCON.F90:426-435 的五段式曲线（换算到发电机高速轴）。这是维持
    最优叶尖速比的转矩 —— 外部转矩的合法包线就锚定在它上下。rpm 给 nan 时返回
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


#: 外部转矩相对基线 VS 曲线的合法带：低于 (1−LO)·T_vs 会超速，高于 (1+HI)·T_vs
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
#: driver 把反解结果裁在这里（fastfarm_driver.py:41 的 RPM_MAX），所以读到这个
#: 数只说明"被裁过"，不说明转子真转到了 20 rpm。
RPM_CLIPPED = 20.0
#: 转速是 ω=P/(ηT) 反解的，T 趋零时商发散 —— 判"是不是数值问题"要看**转矩**，
#: 不能看转速大小：driver 已经裁过，靠转速阈值等于在裁剪天花板附近猜。
#: 低于额定的 30% 时 region 2 都到不了，此时的大转速一律当反解发散。
DIVERGE_TORQUE_FRAC = 0.3

#: 执行机构占空比上限，multiagent_env.py:205 的 `>= 0.1`
DUTY_LIMIT = 0.1
#: 各控制量的执行机构速率 (单位/s)，mdp.py:52。torque 没有条目 ⇒ 不受占空比约束
ACTUATOR_RATE = {"yaw": 0.3, "pitch": 8.0}

#: 每步增量边界与累加值边界，data_cases.py:20-23 的 DefaultControl
STEP_BOUND = {"yaw": 5.0, "pitch": 1.0, "torque": 1e3}
STATE_BOUND = {"yaw": (-40.0, 40.0), "pitch": (0.0, 45.0),
               "torque": (-2e4, 2e4)}

SEVERITY = ("info", "warn", "block")


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
        self.events: List[Event] = []
        self.counts: Dict[str, int] = {}
        self._step = 0
        # 累加的指令值，用来预测状态边界裁剪。仿真侧的 next_state 我们看不到
        # 全部（torque 在 obs 里不是被控量时读不回来），所以自己跟一份。
        self.state = {c: np.zeros(self.n) for c in self.controls}

    # ------------------------------------------------------------------
    def sustainable(self, control: str) -> float:
        """该控制量每步可持续的最大 |Δ|，超过它迟早触发占空比清零。

        占空比 = Σ|Δ| / rate / (k·dt)，稳态下每步都发 d 则 = d/(rate·dt)。
        令其 < 0.1 得 d < 0.1·rate·dt。yaw 在 dt=3 s 下只有 0.09 °/步 ——
        动作空间给的 ±5° 是它的 55 倍，这个落差是 UI 上必须写出来的一句话。
        """
        r = ACTUATOR_RATE.get(control)
        return float("inf") if r is None else DUTY_LIMIT * r * self.dt

    def budget(self, measure: Dict[str, Any], control: str) -> np.ndarray:
        """本步在被清零之前还能发多大的 |Δ| (n,)。没有占空比约束的返回 inf。

        环境判据用的是**上一步结束时**的累计除以 k+1 步（driver._duty 已按 k+1
        算，与 multiagent_env.py:205 逐位对齐），本步的 |Δ| 不参与判定 —— 也就
        是说超限那一步是"事后清零"，不是"提前限幅"。所以这里返回的是"还没超"
        时的余量：一旦 duty 已经 ≥ 0.1，余量是 0，发多少都会被吞。
        """
        if control not in ACTUATOR_RATE:
            return np.full(self.n, np.inf)
        duty = (measure or {}).get("duty") or {}
        d = duty.get(control)
        if d is None:
            return np.full(self.n, np.inf)
        d = np.asarray(d, float).ravel()
        return np.where(d >= DUTY_LIMIT, 0.0, np.inf)

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
            v = self._nan_guard(c, v, evs)
            v = self._step_bound(c, v, evs)
            v = self._state_bound(c, v, evs)
            v = self._duty(c, v, measure, evs, enforce_duty)
            if c == "pitch":
                self._pitch_semantics(v, measure, evs)
            if c == "torque":
                v = self._torque_semantics(v, measure, evs)
            out[c] = v
            self.state[c] = self.state[c] + v
        self._record(evs)
        return out, evs

    def monitor(self, measure: Dict[str, Any]) -> List[Event]:
        """看仿真回读判超速。不改指令 —— 这一步已经执行完了。

        "超速"与"反解发散"要靠**转矩**分辨，不是转速大小：转速本身就是
        ω=P/(ηT) 反解来的，而 driver 已经把它裁在 20 rpm，拿转速阈值去分
        等于在裁剪天花板附近猜。转矩低于额定的 30% 时机组连 region 2 都没
        进，这时的大转速一律是数值问题。
        """
        evs: List[Event] = []
        m = measure or {}
        rpm = np.asarray(m.get("rotor_speed", np.full(self.n, np.nan)), float)
        tq = np.asarray(m.get("torque", np.full(self.n, np.nan)), float)
        tq_min = DIVERGE_TORQUE_FRAC * RATED_TORQUE
        for i in range(self.n):
            r = float(rpm[i]) if i < rpm.size else float("nan")
            if not np.isfinite(r) or r <= RPM_ALARM:
                continue
            t = float(tq[i]) if i < tq.size else float("nan")
            if np.isfinite(t) and abs(t) < tq_min:
                evs.append(Event(self._step, self.ids[i], "rotor_speed",
                                 "转速反解发散", r, r, "warn",
                                 f"转矩仅 {t:.0f} N·m（< 额定的 "
                                 f"{DIVERGE_TORQUE_FRAC:.0%}），ω=P/(ηT) 的商"
                                 f"无意义；driver 已裁在 {RPM_CLIPPED} rpm"))
            else:
                evs.append(Event(self._step, self.ids[i], "rotor_speed",
                                 "超速", r, r, "block",
                                 f"额定 {RATED_RPM:.2f} rpm，报警线 "
                                 f"{RPM_ALARM:.2f} rpm"))
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
        for i in np.where(over)[0]:
            evs.append(Event(
                self._step, self.ids[i], c, "执行机构占空比清零",
                float(v[i]), 0.0 if enforce else float(v[i]), "block",
                f"占空比 {duty[i]:.3f} ≥ {DUTY_LIMIT}，环境会把本步指令整个"
                f"归零（multiagent_env.py:196-207）；可持续量仅 "
                f"{self.sustainable(c):.3g} °/步"))
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

    def _torque_semantics(self, v, measure, evs):
        """转矩**完全顶替**基线（DISCON.F90:440），合法值锚定在基线 VS 曲线上。

        双向包线，替换旧的单向托底。旧逻辑只防"转矩偏低→超速"，漏了另一头：
        转矩偏高→转子减速→叶尖速比掉到 ~2、BEM 关闭、气动没了→转子失速停转
        （下游机组 T2/T3 在尾流里正是这样被刹停的）。而且旧的把绝对转矩托到
        `floor-base`（可能上千），会被 driver 每步 ±1e3 的裁剪挡回去，形同虚设。

        新逻辑：从回读转速反算基线 VS 会下发的转矩 `t_vs`（维持最优 TSR 的值），
        把绝对转矩限制在 `[t_vs·(1−LO), t_vs·(1+HI)]`。返回的是**增量**，且因为
        策略侧动作已限在 ±1e3，本方法输出的增量也在 ±1e3 内 —— 不会再被 driver
        的裁剪覆盖。每步都执行 ⇒ 绝对转矩不会跑出包线，也就不会失速/超速。
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
        adj = target - base                                    # 对应的合法增量
        changed = np.abs(adj - v) > 1e-6
        for i in np.where(changed)[0]:
            over = (base[i] + v[i]) > t_hi[i]
            evs.append(Event(
                self._step, self.ids[i], "torque",
                "转矩超运行上限（防失速）" if over else "转矩低于运行下限（防超速）",
                float(base[i] + v[i]), float(target[i]), "block",
                f"基线 VS 在 {rpm[i]:.2f} rpm 下给 {t_vs[i]:.0f} N·m，"
                f"合法带 [{t_lo[i]:.0f}, {t_hi[i]:.0f}]；"
                + ("偏高转子减速会失速（TSR→2 时 BEM 关闭）"
                   if over else "偏低转子加速会超速")))
        return adj

    # ---- 汇总 ----------------------------------------------------------
    def _record(self, evs):
        for e in evs:
            self.counts[e.rule] = self.counts.get(e.rule, 0) + 1
        self.events.extend(evs)
        if len(self.events) > self.max_events:
            # 只丢最老的：训练跑几千步时事件量会很大，但最近的那些才是要看的
            self.events = self.events[-self.max_events:]

    def summary(self) -> List[tuple]:
        """(规则, 次数) 按次数降序，训练面板直接渲染。"""
        return sorted(self.counts.items(), key=lambda kv: -kv[1])

    def recent(self, k: int = 20) -> List[Event]:
        return self.events[-k:]

    def reachable(self, control: str, n_steps: int) -> float:
        """n_steps 步之内该控制量能累积到的最大变化量。

        占空比不是限幅，是**累计预算**：`_actuation_accumulator` 逐步累加
        |Δ| 且回合内永不衰减（mdp.py:317-318），判据 acc/(rate·k·dt) 的分母
        随 k 增长 —— 也就是说它约束的是"历史平均动作速率"。

            reachable ≈ 0.1·rate·dt·K + step_bound

        末项是因为 k=1 时 acc=0 ⇒ frac=0，**第一步永远免费**。稳态下清零占比
        为 1 − sustainable/step_bound，生效间隔趋近 step_bound/sustainable 步；
        `probe_duty_env.py` 在 FLORIS 算例（dt=60 s）上与环境逐位零偏差地
        复现了这三条。

        这个数决定回合长度够不够：yaw 在 dt=3 s 下 K=128 只能走到 16.5°，而
        尾流偏航的增益要到 20° 上下才显现 —— 回合内到不了最优点，策略也就
        拿不到"偏航有增益"的证据。

        两处实测才发现、朴素推导会算错的地方：

        · **acc 累加的是动作空间裁剪后的量，不含状态边界。** mdp.py:299-318
          先 clip(Δ, ±5°) 累加进 accumulator，再 clip(state+Δ, ±40°) 写状态。
          偏航贴到 ±40° 之后指令不再改变状态，acc 却照涨（实测末端偏航 40°
          时 acc 已累到 115°）—— 饱和后每一步都在为未发生的动作付占空比。
        · **accumulator 对非末位 agent 是先读后写，逐机组不同步。**
          multiagent_env.py:246-249 的回抄在 is_last() 分支之后，而 mdp 的
          累计值只在那一支里更新，于是遍历序靠前的 agent 抄到的值滞后一步、
          约束更松。占空比在机组之间**不等价**，而 obs 里又没有 duty ——
          策略在学一个自己看不见、且逐机组不一致的门控。
        """
        s = self.sustainable(control)
        if math.isinf(s):
            return float("inf")
        return s * int(n_steps) + STEP_BOUND.get(control, 0.0)

    def notes(self, n_steps: Optional[int] = None) -> List[str]:
        """这份场景下各控制量的可持续量，UI 启动时显示一次。

        给了 n_steps 就同时报回合内可达量 —— 那是判断"这个回合长度下策略有
        没有机会达到最优点"的唯一依据，比可持续量本身更该出现在面板上。
        """
        out = []
        for c in self.controls:
            s = self.sustainable(c)
            if math.isinf(s):
                out.append(f"{c}: 不受执行机构占空比约束（mdp.py:52 无条目）")
                continue
            line = (f"{c}: 动作空间 ±{STEP_BOUND.get(c, float('nan')):g}/步，"
                    f"但占空比可持续量仅 {s:.3g}/步（差 "
                    f"{STEP_BOUND.get(c, float('nan')) / s:.0f} 倍），"
                    f"持续满幅动作必被清零")
            if n_steps:
                line += (f"；{n_steps} 步回合内最多累积 "
                         f"{self.reachable(c, n_steps):.3g}")
            out.append(line)
        return out
