"""FAST.Farm 后端的增益可达性直接测量：占空比真实约束下，偏航到底能带来多少
功率变化。

FLORIS 版（probe_gain_reachable.py）用 calculate_wake 直接设绝对偏航角，测的是
"理想可达"。FAST.Farm 没有这个口子，只能靠 step 增量累积，而占空比会把大增量
清零 —— 所以这里测的是**真实约束下**的可达增益，正是训练面对的情形。

做法：对每个"上游偏航增量强度" d（每步给上游机组发 d°），起一次 FAST.Farm，
warmup 后持续发 d 若干步让偏航尽量累积（占空比自然限制），再跑到尾流稳定，
读【实际偏航角】与【末段平均全场功率】。与 d=0（零偏航）比即增益。

判据：若某个 d 下全场功率显著高于 d=0，则 turb3 正对一列有正增益可拿，
训练值得投入；若都不高于 d=0，则该构型无增益，应换构型（turb6/斜风）。

需 mpiexec。约 10~15 min（每个 d 起一次 FAST.Farm）。
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfrl.fastfarm_driver import FastFarmDriver              # noqa: E402

WARMUP = 10          # 丢弃启动瞬态
PUSH = 40            # 持续发增量、累积偏航的步数
SETTLE = 30          # 之后跑到尾流稳定（下游影响滞后 ~46 步，这里读末段均值）
READ = 12            # 末段平均的步数


def valid_measurement(m, key):
    values = np.asarray(m[key], float).ravel()
    valid = np.isfinite(values)
    if f"{key}_valid" in m:
        declared = np.asarray(m[f"{key}_valid"])
        if declared.dtype.kind != "b" or declared.size != values.size:
            raise ValueError(f"{key}_valid must contain one boolean per turbine")
        valid &= declared.ravel()
    return values, valid


def summarize_readings(measurements, up):
    """Yaw counts turbine samples; farm power counts complete control steps."""
    yaws, powers = [], []
    for m in measurements:
        yaw, yaw_valid = valid_measurement(m, "yaw")
        yaws.extend(yaw[up][yaw_valid[up]])
        power, power_valid = valid_measurement(m, "power")
        if power.size and power_valid.all():
            total = float(power.sum())
            if np.isfinite(total):
                powers.append(total)
    return (float(np.mean(yaws)) if yaws else None,
            float(np.mean(powers)) if powers else None,
            {"yaw": len(yaws), "power": len(powers)})


def describe(value, count, unit):
    result = "无有效数据" if value is None else f"{value:.4f} {unit}"
    return f"{result}（有效样本数={count}）"


def run_one(d_deg, scene=None):
    """返回上游平均偏航、平均全场功率MW、上游idx及有效样本数；空均值为None。

    scene=None 走预注册 turb3 env；给了场景 YAML 则走 SceneRuntime（turb6 等
    自定义布局）。上游机组 = x 最小的那些（一列/一行的最前，可能多台）。
    """
    from wfrl.scene.schema import load_scene
    from wfrl.scene.runtime import SceneRuntime
    budget = WARMUP + PUSH + SETTLE + READ + 4
    if scene is None:
        drv = FastFarmDriver("Dec_Turb3_Row1_Fastfarm", max_steps=budget)
        drv.reset(wind_speed=8.0, wind_direction=270.0)
        n = drv.n
        xs = drv.xcoords
        step = lambda push: drv.step(push)                       # noqa: E731
        read_m = lambda m: m                                    # noqa: E731
    else:
        sc = load_scene(scene)
        rt = SceneRuntime(sc, max_steps=budget, warmup_steps=WARMUP)
        rt.reset()                        # 内部已做 warmup
        n = sc.n
        xs = np.asarray(sc.xcoords)
        step = lambda push: rt.step(yaw_delta=push)              # noqa: E731
        read_m = lambda fr: rt.last_measure                     # noqa: E731
        drv = rt
    up = np.where(xs <= xs.min() + 1.0)[0]      # 上游机组（x 最小）
    measurements = []
    try:
        if scene is None:
            for _ in range(WARMUP):
                step(np.zeros(n))
        push = np.zeros(n)
        push[up] = d_deg
        for _ in range(PUSH):
            step(push)
        for _ in range(SETTLE):
            step(np.zeros(n))
        for _ in range(READ):
            m = read_m(step(np.zeros(n)))
            measurements.append({key: np.asarray(value).copy() for key, value in m.items()
                                 if key in ("yaw", "yaw_valid", "power", "power_valid")})
    finally:
        drv.close() if scene else drv.close(purge=True)
    yaw, power, counts = summarize_readings(measurements, up)
    return yaw, power, list(up), counts


def main():
    scene = sys.argv[1] if len(sys.argv) > 1 else None
    label = scene if scene else "Dec_Turb3_Row1_Fastfarm（预注册）"
    print(f"FAST.Farm 增益可达性扫描  场景={label}  "
          f"(u=8, 270°, warmup={WARMUP} push={PUSH} settle={SETTLE} "
          f"read={READ})", flush=True)
    rows = []
    for d in (0.0, 1.0, 2.0, 3.0, 5.0):
        yaw, p, up, counts = run_one(d, scene=scene)
        rows.append((d, yaw, p, counts))
        print(f"  每步偏 {d:.0f}° → 上游({len(up)}台)平均偏航 "
              f"{describe(yaw, counts['yaw'], '°')} → "
              f"全场功率 {describe(p, counts['power'], 'MW')}", flush=True)
    p0 = rows[0][2]
    print(f"\n零偏航基线功率: {describe(p0, rows[0][3]['power'], 'MW')}")
    if p0 is None:
        print("GAIN_FF_UNAVAILABLE — 基线无有效数据，不能计算增益")
        return 0
    if p0 == 0:
        print("GAIN_FF_UNAVAILABLE — 基线为零，不能计算相对增益")
        return 0
    print("相对基线增益：")
    best = rows[0]
    for d, yaw, p, counts in rows:
        if p is None:
            print(f"  每步 {d:.0f}° → 无有效数据（有效样本数=0）")
            continue
        g = (p - p0) / p0 * 100
        print(f"  每步 {d:.0f}° (偏到 {describe(yaw, counts['yaw'], '°')}) → "
              f"{g:+.2f}%（功率有效样本数={counts['power']}，基线={rows[0][3]['power']}）")
        if p > best[2]:
            best = (d, yaw, p, counts)
    bg = (best[2] - p0) / p0 * 100
    print(f"\n有效功率数据中的最优：每步 {best[0]:.0f}° "
          f"偏到 {describe(best[1], best[3]['yaw'], '°')} → 增益 {bg:+.2f}% "
          f"（功率有效样本数={best[3]['power']}，基线={rows[0][3]['power']}）")
    if any(row[1] is None or row[2] is None for row in rows):
        print("GAIN_FF_UNAVAILABLE — 部分扫描无有效数据，不能给出完整可达性结论")
        return 0
    if bg > 1.0:
        print(f"GAIN_FF_OK — FAST.Farm 上有 {bg:.1f}% 正增益可拿，值得训练")
    else:
        print(f"GAIN_FF_NONE — FAST.Farm 上偏航最大增益仅 {bg:+.2f}%，"
              f"turb3 正对一列无可观增益，应换构型")
    return 0


if __name__ == "__main__":
    sys.exit(main())
