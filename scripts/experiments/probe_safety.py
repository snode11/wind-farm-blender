"""约束层的离线验收（不起 FAST.Farm）。

要证的不是"clip 会 clip"，而是每条规则都对得上仿真侧的真实行为：

  1. 占空比清零：事件触发的时刻与 multiagent_env.py:196-207 的判据逐位一致
  2. 可持续量：dt=3 s 下 yaw 只有 0.09 °/步，与动作空间 ±5° 差 55 倍
  3. 变桨下界：指令低于基线 PitComT 时报"被顶替"，且**不**改指令
  4. 转矩顶替：低于额定 60% 会被托起来，因为 GenTrq 完全替换基线
  5. 累加值边界：yaw 到 40° 后余量为 0
  6. nan 归零：策略吐 nan 不能一路进 MPI
  7. 超速：monitor 分得清"真超速"和"转矩趋零导致的反解发散"

跑：
    "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" scripts/experiments/probe_safety.py
"""
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfrl.safety import (ACTUATOR_RATE, DUTY_LIMIT, RATED_RPM,   # noqa: E402
                         RATED_TORQUE, RPM_ALARM, TORQUE_ENV_HI,
                         TORQUE_ENV_LO, baseline_gen_torque, SafetyLimiter)
from wfrl.scene.schema import Scene, Turbine                     # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def scene(controls=("yaw",), dt=3.0):
    return Scene(name="probe_safety", backend="fastfarm", dt=dt,
                 turbines=[Turbine("W1", 0.0, 0.0), Turbine("W2", 630.0, 0.0),
                           Turbine("W3", 1260.0, 0.0)],
                 wind_speed=8.0, controls=list(controls)).validate()


def measure(n=3, duty=None, pitch_meas=None, torque=None, rpm=None):
    m = {"yaw": np.zeros(n), "power": np.full(n, 2.0),
         "rotor_speed": np.full(n, 9.0) if rpm is None else np.asarray(rpm, float),
         "pitch_meas": (np.zeros(n) if pitch_meas is None
                        else np.asarray(pitch_meas, float)),
         "torque": (np.full(n, RATED_TORQUE) if torque is None
                    else np.asarray(torque, float)),
         "duty": {}}
    if duty is not None:
        m["duty"] = {k: np.asarray(v, float) for k, v in duty.items()}
    return m


class FakeEnv:
    """复刻 multiagent_env.py:196-207 的占空比判据，用来逐步对拍。

    只抄那一段：累计 |Δ| / rate / k / dt ≥ 0.1 就把动作清零；累计在**清零之后**
    才更新（清零的动作贡献 0），k 在动作之前 +1。这三处顺序错一个，对拍就会
    在某一步分岔 —— 这正是这个测试要抓的东西。
    """

    def __init__(self, n, dt, rate):
        self.n, self.dt, self.rate = n, float(dt), float(rate)
        self.acc = np.zeros(n)
        self.k = np.zeros(n, dtype=int)
        self.executed = []

    def step(self, a):
        a = np.asarray(a, float).copy()
        self.k += 1
        frac = self.acc / self.rate / self.k / self.dt
        a[frac >= DUTY_LIMIT] = 0.0
        self.acc += np.abs(a)
        self.executed.append(a.copy())
        return a

    def duty(self):
        """driver._duty 的口径：用 k+1 预测**下一步**会不会被清零。"""
        return self.acc / self.rate / (self.k + 1) / self.dt


def main():
    print("== 1. 常数口径 ==", flush=True)
    check("额定转子转速 ≈ 11.98 rpm", abs(RATED_RPM - 11.979) < 0.01,
          f"{RATED_RPM:.3f}")
    check("额定发电机转矩 ≈ 43529 N·m", abs(RATED_TORQUE - 43528.8) < 1.0,
          f"{RATED_TORQUE:.1f}")
    check("动作空间 ±2e4 不足额定的一半",
          2e4 / RATED_TORQUE < 0.5, f"{2e4 / RATED_TORQUE * 100:.1f}%")

    print("\n== 2. 可持续量 vs 动作空间 ==", flush=True)
    lim = SafetyLimiter(scene(dt=3.0))
    s = lim.sustainable("yaw")
    check("yaw 可持续 0.09 °/步", abs(s - 0.09) < 1e-9, f"{s:.4g}")
    check("与动作空间 ±5° 差 55 倍", abs(5.0 / s - 55.6) < 0.5, f"{5.0 / s:.1f}×")
    check("torque 不受占空比约束（mdp.py:52 无条目）",
          np.isinf(SafetyLimiter(scene(controls=("torque",))).sustainable("torque")))
    # 可达量：占空比是累计预算不是限幅 ⇒ K 步可达 ≈ 0.09K + 首步免费的 5°
    r128 = lim.reachable("yaw", 128)
    check("128 步回合内 yaw 最多累积 ~16.5°",
          abs(r128 - (0.09 * 128 + 5.0)) < 1e-6, f"{r128:.2f}°")
    check("回合内到不了尾流偏航最优点 20°", r128 < 20.0,
          f"{r128:.2f}° < 20°，差 {20.0 / r128:.2f} 倍")
    for line in lim.notes(n_steps=128):
        print(f"    {line}", flush=True)

    print("\n== 3. 占空比：与环境判据逐步对拍 ==", flush=True)
    n, dt = 3, 3.0
    env = FakeEnv(n, dt, ACTUATOR_RATE["yaw"])
    lim = SafetyLimiter(scene(dt=dt))
    mismatch, first_zero, first_evt = [], None, None
    for k in range(40):
        m = measure(n, duty={"yaw": env.duty()})
        # 每步满幅 +4°：4 / 0.09 = 44 倍可持续量，必然很快被清零
        cmd, evs = lim.apply({"yaw": np.full(n, 4.0)}, m, enforce_duty=True)
        got = env.step(np.full(n, 4.0))
        if not np.allclose(cmd["yaw"], got):
            mismatch.append((k, cmd["yaw"].copy(), got.copy()))
        if first_zero is None and np.any(got == 0.0):
            first_zero = k
        if first_evt is None and any(e.rule == "执行机构占空比清零" for e in evs):
            first_evt = k
    check("40 步内约束层与环境执行结果逐位一致", not mismatch,
          f"{len(mismatch)} 步分岔" if mismatch else "0 步分岔")
    check("清零时刻与事件时刻同一步", first_zero == first_evt,
          f"环境第 {first_zero} 步清零，事件第 {first_evt} 步")
    check("确实发生了清零（否则上面两条是空断言）", first_zero is not None,
          f"第 {first_zero} 步")

    print("\n== 4. 不加 enforce 时只报不改 ==", flush=True)
    lim = SafetyLimiter(scene(dt=dt))
    m = measure(n, duty={"yaw": np.full(n, 0.5)})     # 已远超 0.1
    cmd, evs = lim.apply({"yaw": np.full(n, 3.0)}, m)
    check("指令未被改动", np.allclose(cmd["yaw"], 3.0), f"{cmd['yaw']}")
    check("但发了 block 事件", sum(e.blocked for e in evs) == n,
          f"{sum(e.blocked for e in evs)} 条")
    print(f"    {evs[0]}", flush=True)

    print("\n== 5. 累加值边界 ==", flush=True)
    lim = SafetyLimiter(scene(dt=dt))
    for _ in range(9):                                 # 9×5 = 45 > 40
        cmd, evs = lim.apply({"yaw": np.full(n, 5.0)}, measure(n))
    check("偏航停在 +40°", np.allclose(lim.state["yaw"], 40.0),
          f"{lim.state['yaw']}")
    check("最后一步余量 < 5°", np.all(cmd["yaw"] < 5.0), f"{cmd['yaw']}")
    check("报了累加值越界", any(e.rule == "累加值越界" for e in evs))

    print("\n== 6. 单步增量与 nan ==", flush=True)
    lim = SafetyLimiter(scene(dt=dt))
    cmd, evs = lim.apply({"yaw": np.array([12.0, np.nan, -30.0])}, measure(n))
    check("超幅裁到 ±5", abs(cmd["yaw"][0] - 5.0) < 1e-9
          and abs(cmd["yaw"][2] + 5.0) < 1e-9, f"{cmd['yaw']}")
    check("nan 归零而不是继续传播", cmd["yaw"][1] == 0.0
          and np.all(np.isfinite(cmd["yaw"])), f"{cmd['yaw']}")
    check("nan 是 block 级", any(e.rule == "非有限值" and e.blocked for e in evs))

    print("\n== 7. 变桨是下界不是指令 ==", flush=True)
    lim = SafetyLimiter(scene(controls=("yaw", "pitch"), dt=dt))
    # 基线在 6.5°（region 3 已顺桨），外部只发 0.8° ⇒ 被 MAX 顶替
    m = measure(n, pitch_meas=np.full(n, 6.5))
    cmd, evs = lim.apply({"yaw": np.zeros(n), "pitch": np.full(n, 0.8)}, m)
    top = [e for e in evs if e.rule == "指令被基线顶替"]
    check("报了被顶替", len(top) == n, f"{len(top)} 条")
    check("指令**未**被改（MAX 在控制器那头，改也没用）",
          np.allclose(cmd["pitch"], 0.8), f"{cmd['pitch']}")
    check("严重级是 info 而非 block", all(e.severity == "info" for e in top))
    # 基线在 0（region 2），外部 0.8° 高于它 ⇒ 生效，不该报
    m2 = measure(n, pitch_meas=np.zeros(n))
    lim2 = SafetyLimiter(scene(controls=("yaw", "pitch"), dt=dt))
    _, evs2 = lim2.apply({"yaw": np.zeros(n), "pitch": np.full(n, 0.8)}, m2)
    check("基线为 0 时不误报", not any(e.rule == "指令被基线顶替" for e in evs2))

    print("\n== 8. 转矩双向包线（锚定基线 VS 曲线）==", flush=True)
    # 基线 VS 曲线在 9~10 rpm 落在 region 2（T ∝ ω²）
    tvs9 = float(baseline_gen_torque(np.array([9.0]))[0])
    check("baseline_gen_torque(9 rpm) 落在 region 2 (~19.5 kN·m)",
          18000 < tvs9 < 21000, f"{tvs9:.0f} N·m")
    t_lo, t_hi = (1 - TORQUE_ENV_LO) * tvs9, (1 + TORQUE_ENV_HI) * tvs9

    # 8a 转矩偏高 → 转子会减速失速 → 被托回上限（这是修复 T2/T3 停转的那一条）
    lim = SafetyLimiter(scene(controls=("yaw", "torque"), dt=dt))
    base_hi = t_hi - 200.0                 # 已接近上限，再 +1e3 必越界
    m = measure(n, torque=np.full(n, base_hi), rpm=np.full(n, 9.0))
    cmd, evs = lim.apply({"yaw": np.zeros(n), "torque": np.full(n, 1e3)}, m)
    check("偏高转矩被限到包线上限（防失速）",
          np.allclose(base_hi + cmd["torque"], t_hi, rtol=1e-4),
          f"{base_hi + cmd['torque'][0]:.0f} vs 上限 {t_hi:.0f}")
    check("防失速事件是 block 级",
          any(e.rule == "转矩超运行上限（防失速）" and e.blocked for e in evs))

    # 8b 转矩偏低 → 转子会加速超速 → 被托到下限（保留的那一条）
    lim = SafetyLimiter(scene(controls=("yaw", "torque"), dt=dt))
    base_lo = t_lo + 200.0
    m = measure(n, torque=np.full(n, base_lo), rpm=np.full(n, 9.0))
    cmd, evs = lim.apply({"yaw": np.zeros(n), "torque": np.full(n, -1e3)}, m)
    check("偏低转矩被托到包线下限（防超速）",
          np.allclose(base_lo + cmd["torque"], t_lo, rtol=1e-4),
          f"{base_lo + cmd['torque'][0]:.0f} vs 下限 {t_lo:.0f}")
    check("防超速事件是 block 级",
          any(e.rule == "转矩低于运行下限（防超速）" and e.blocked for e in evs))

    # 8c 包线内的小幅调整不干预
    lim = SafetyLimiter(scene(controls=("yaw", "torque"), dt=dt))
    m = measure(n, torque=np.full(n, tvs9), rpm=np.full(n, 9.0))
    cmd, evs = lim.apply({"yaw": np.zeros(n), "torque": np.full(n, 300.0)}, m)
    check("包线内小幅调整不干预", np.allclose(cmd["torque"], 300.0),
          f"{cmd['torque'][0]:.0f}")
    check("包线内不报事件",
          not any("转矩" in e.rule for e in evs))

    print("\n== 9. 超速 vs 反解发散 ==", flush=True)
    lim = SafetyLimiter(scene(dt=dt))
    # 分辨依据是**转矩**不是转速：W2 转矩正常 ⇒ 真超速；W3 转矩趋零 ⇒ ω=P/(ηT)
    # 的商无意义。两台转速都高，只有转矩能把它们分开。
    evs = lim.monitor(measure(n, rpm=[9.0, 13.5, 19.9],
                              torque=[RATED_TORQUE, RATED_TORQUE, 300.0]))
    kinds = {e.turbine: e.rule for e in evs}
    check("9.0 rpm 正常，不报", "W1" not in kinds, f"{kinds}")
    check(f"13.5 rpm + 额定转矩 ⇒ 超速", kinds.get("W2") == "超速")
    check("19.9 rpm + 转矩 300 N·m ⇒ 反解发散",
          kinds.get("W3") == "转速反解发散",
          "转矩趋零时 ω=P/(ηT) 发散，不该当成物理超速")
    check("超速是 block、发散是 warn",
          all(e.blocked for e in evs if e.rule == "超速")
          and all(not e.blocked for e in evs if e.rule == "转速反解发散"))
    # 反过来：同样 19.9 rpm 但转矩正常 ⇒ 必须判超速，否则"发散"成了万能借口
    lim2 = SafetyLimiter(scene(dt=dt))
    evs2 = lim2.monitor(measure(n, rpm=[9.0, 9.0, 19.9],
                                torque=np.full(n, RATED_TORQUE)))
    check("同一转速、转矩正常时判超速（不是靠转速大小猜）",
          {e.turbine: e.rule for e in evs2}.get("W3") == "超速")

    print("\n== 10. 汇总给 UI ==", flush=True)
    lim = SafetyLimiter(scene(dt=dt))
    for _ in range(30):
        lim.apply({"yaw": np.full(n, 4.0)},
                  measure(n, duty={"yaw": np.full(n, 0.3)}), enforce_duty=True)
    smry = lim.summary()
    check("汇总非空且按次数降序", smry and all(
        smry[i][1] >= smry[i + 1][1] for i in range(len(smry) - 1)), f"{smry}")
    check("最近事件可取", len(lim.recent(5)) == 5)
    for k, v in smry:
        print(f"    {k}: {v} 次", flush=True)

    print()
    if FAILS:
        print(f"SAFETY_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("SAFETY_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("SAFETY_FAILED — 未捕获异常")
        sys.exit(1)
