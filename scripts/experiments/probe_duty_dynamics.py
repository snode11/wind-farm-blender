"""量占空比这条约束的真实形状：它是**累计预算**，不是限幅。

环境侧的确切顺序（multiagent_env.py:179-207 + mdp.py:299-318）：

    k        = _num_steps += 1                 先自增
    frac     = acc[c] / rate[c] / k / dt       用**上一步结束**的 acc 判定
    frac >= 0.1  ⇒  action[c][:] = 0           整个控制量清零
    Δ        = clip(Δ, ±5°)                    动作空间（清零后才裁）
    state    = clip(state + Δ, ±40°)           状态边界
    acc[c]  += |Δ|                             只累加**真正执行**的量

三个后果，都是纯递推能算出来的，不用起仿真：

  1 k=1 时 acc=0 ⇒ frac=0，**第一步永远免费**，不管发多大。
  2 acc 只增不减，分母的 k 单调增 ⇒ frac 是"历史平均动作速率"。一次满幅
    5° 之后要等 acc/(rate·dt) / 0.1 = 55.6 步才解锁，静默 168 s。
  3 于是 K 步内的**最大可达偏航**约为 0.1·rate·dt·K = 0.09K，与怎么排都
    几乎无关 —— 满幅猛冲和每步发可持续量，总量落在同一条线上。

第 3 条决定了回合长度够不够：尾流偏航的最优失配在 20°上下，按 0.09 °/步要
220 多个控制步。n_steps=64 一轮只能走到 6°，策略在原理上到不了最优点。
"""
import numpy as np

DUTY_LIMIT = 0.1
ACTUATOR_RATE = {"yaw": 0.3, "pitch": 8.0}
STEP_BOUND = 5.0            # data_cases.py:20-23 DefaultControl yaw
STATE_BOUND = 40.0          # mdp.py:48 DEFAULT_BOUNDS yaw


def run(deltas, rate=0.3, dt=3.0, step_bound=STEP_BOUND,
        state_bound=STATE_BOUND):
    """按环境的确切顺序走一遍。返回逐步 (请求, 执行, 判定用 frac, 累计偏航)。"""
    acc, yaw, out = 0.0, 0.0, []
    for k0, d in enumerate(deltas):
        k = k0 + 1
        frac = acc / rate / k / dt
        a = 0.0 if frac >= DUTY_LIMIT else float(np.clip(d, -step_bound,
                                                        step_bound))
        new_yaw = float(np.clip(yaw + a, -state_bound, state_bound))
        a = new_yaw - yaw                        # 状态边界吃掉的部分也不计入 acc
        yaw = new_yaw
        acc += abs(a)
        out.append((d, a, frac, yaw))
    return out


def steps_to(trace, target):
    for i, (_d, _a, _f, y) in enumerate(trace):
        if y >= target - 1e-9:
            return i + 1
    return None


def main():
    dt, rate = 3.0, ACTUATOR_RATE["yaw"]
    sustain = DUTY_LIMIT * rate * dt
    print(f"dt={dt}s  rate={rate}°/s")
    print(f"稳态可持续量 = 0.1×{rate}×{dt} = {sustain:.3g} °/步；"
          f"动作空间 ±{STEP_BOUND:g}° 是它的 {STEP_BOUND / sustain:.1f} 倍\n")

    K = 200
    print(f"[{K} 步，三种动作序列]")
    rows = []
    for name, seq in [("每步满幅 5°", [STEP_BOUND] * K),
                      ("每步可持续 0.09°", [sustain] * K),
                      ("首步 5° 其余 0", [STEP_BOUND] + [0.0] * (K - 1))]:
        tr = run(seq, rate, dt)
        nz = sum(1 for _d, a, _f, _y in tr if a == 0.0)
        fired = [i + 1 for i, (_d, a, _f, _y) in enumerate(tr) if a != 0.0]
        rows.append((name, nz, tr[-1][3], fired[:6]))
        print(f"  {name:<18} 清零 {nz:>3}/{K} ({nz / K:>4.0%})  "
              f"末端偏航 {tr[-1][3]:>6.2f}°  执行于步 {fired[:6]}"
              + (" ..." if len(fired) > 6 else ""))

    print("\n  注意两点：")
    print("  · 满幅序列被清零 98%，可持续序列**一次都没被清零** —— "
          f"frac = 0.1(k−1)/k 恒 < 0.1")
    print(f"  · 两者末端偏航 {rows[0][2]:.1f}° vs {rows[1][2]:.1f}°，"
          f"同一量级。猛冲换不来更多偏航，只换来 98% 的动作被吞")

    print("\n[一次满幅之后的静默期]")
    lock = STEP_BOUND / rate / dt / DUTY_LIMIT
    print(f"  acc={STEP_BOUND:g} ⇒ 需 k > {lock:.1f}，即第 {int(lock) + 1} 步才解锁")
    print(f"  {int(lock) + 1} 步 × {dt}s = {(int(lock) + 1) * dt:.0f}s 静默；"
          f"n_steps=64 的一轮里吃掉 {(int(lock) + 1) / 64:.0%}")

    print("\n[K 步内的最大可达偏航 —— 决定回合长度够不够]")
    print(f"  {'K':>5} {'0.09/步':>9} {'满幅':>8} {'理论 0.09K':>11}")
    for K2 in (16, 32, 64, 128, 256, 512):
        a = run([sustain] * K2, rate, dt)[-1][3]
        b = run([STEP_BOUND] * K2, rate, dt)[-1][3]
        print(f"  {K2:>5} {a:>9.2f} {b:>8.2f} {sustain * K2:>11.2f}")

    print("\n[要走到 20° 失配（尾流偏航最优点附近）]")
    for name, seq in [("每步可持续", [sustain] * 900),
                      ("每步满幅", [STEP_BOUND] * 900)]:
        tr = run(seq, rate, dt)
        s = steps_to(tr, 20.0)
        print(f"  {name:<12} " + (f"第 {s} 步到 20°，即 {s * dt:.0f}s "
                                  f"= {s * dt / 60:.1f} min 仿真时间"
                                  if s else "900 步内未达到"))

    n64 = run([sustain] * 64, rate, dt)[-1][3]
    print(f"\n  ⇒ n_steps=64 一轮最多走到 {n64:.2f}°，"
          f"距 20° 差 {20 / n64:.1f} 倍。回合内到不了最优点，"
          f"策略拿不到\"偏航有增益\"的证据")

    print("\nDUTY_PROBE_OK")


if __name__ == "__main__":
    main()
