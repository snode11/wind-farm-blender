"""对照实验 A：同一来流下的两后端零偏航功率对照（纯物理，不含 RL）。

动机：日报 §1.2 的"静态/动态 2.43×"是拿两次**独立的风况抽签**在比。
`wfcrl/mdp.py:233-258` 每次 reset 都抽 `wind_speed = 8*rng.weibull(8)`，而功率 ~ u^3，
实测 12 次 FLORIS reset 的零偏航全场功率跨度 1.305–8.329 MW（6.4×）。
也就是说 2.43× 完全可能只是抽签噪声，与物理后端无关。

本脚本把风速**显式钉死**在同一组值上（`reset(options={"wind_speed": U})`，
FAST.Farm 侧会被写进 InflowWind 的 HWindSpeed，见 interface.py:437-456），
零偏航跑到稳态，比较两后端的全场功率与归一化功率 P/u_inf^3。
唯一变量真正只剩物理后端。

零偏航是刻意的：不引入策略，任何差异都只能来自尾流模型本身。

运行（FAST.Farm 需要 MPI 进程管理器）：
    "/c/Program Files/Microsoft MPI/Bin/mpiexec.exe" -n 1 \
        "C:/Users/s1155/.conda/envs/wfcrl/python.exe" exp_matched_inflow.py
"""
import os
import json
import time

import numpy as np

from wfrl.fastfarm_driver import FastFarmDriver

from wfrl import paths

SPEEDS = [6.0, 7.0, 8.0, 9.0]
WD = 270.0                    # FLORIS 约定：270° → 来流沿 +x，与机列同向（FAST.Farm 固定值）

# FLORIS 稳态：一步即到平衡，多跑几步只为确认数值不漂
FLORIS_STEPS, FLORIS_AVG = 6, 3
# FAST.Farm 要等尾流真正传到最下游：1008 m / 6 m/s = 168 s = 56 个 dt(3s)。
# 给 100 步（300 s）留足余量，取末 15 步均值。
FF_STEPS, FF_AVG = 100, 15


def run_case(env_id, speeds, n_steps, n_avg, tag):
    rows = []
    for U in speeds:
        t0 = time.time()
        drv = FastFarmDriver(env_id, max_steps=n_steps + 2)
        m = drv.reset(wind_speed=U, wind_direction=WD)
        hist = []
        for _ in range(n_steps):
            m = drv.step(np.zeros(drv.n))        # 零偏航，全程不动
            hist.append(m["power"].copy())
            if m["done"]:
                break
        drv.close()

        H = np.asarray(hist)                     # (T, n) MW
        k = min(n_avg, len(H))
        per = H[-k:].mean(axis=0)
        tot = float(per.sum())
        row = {
            "backend": tag, "u_inf": U, "steps": len(H),
            "power_per_turbine": [round(float(v), 4) for v in per],
            "power_total_MW": round(tot, 4),
            "P_over_u3": round(tot * 1e3 / U ** 3, 4),   # kW/(m/s)^3
            "rotor_ws": [round(float(v), 3) for v in m["wind_speed"]],
            "wall_s": round(time.time() - t0, 1),
        }
        rows.append(row)
        print(f"[{tag}] u_inf={U:.1f}  P={tot:.4f} MW  P/u^3={row['P_over_u3']:.4f}"
              f"  每台={np.round(per,4).tolist()}  ({row['wall_s']}s, {len(H)} 步)",
              flush=True)
    return rows


def main():
    out = {"speeds": SPEEDS, "wind_direction": WD, "yaw": "zero", "rows": []}

    print("=== FLORIS 稳态 ===", flush=True)
    out["rows"] += run_case("Dec_Turb3_Row1_Floris", SPEEDS,
                            FLORIS_STEPS, FLORIS_AVG, "floris")
    with open(os.path.join(paths.RUNS, "matched_inflow.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n=== FAST.Farm 气弹 ===", flush=True)
    out["rows"] += run_case("Dec_Turb3_Row1_Fastfarm", SPEEDS,
                            FF_STEPS, FF_AVG, "fastfarm")
    with open(os.path.join(paths.RUNS, "matched_inflow.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n=== 同来流下的后端比值 ===", flush=True)
    fl = {r["u_inf"]: r for r in out["rows"] if r["backend"] == "floris"}
    ff = {r["u_inf"]: r for r in out["rows"] if r["backend"] == "fastfarm"}
    for U in SPEEDS:
        if U in fl and U in ff:
            a, b = fl[U]["power_total_MW"], ff[U]["power_total_MW"]
            print(f"  u={U:.1f}  FLORIS {a:.4f} / FAST.Farm {b:.4f} = "
                  f"{a/max(b,1e-9):.3f}×", flush=True)
    print("\n-> runs/matched_inflow.json", flush=True)


if __name__ == "__main__":
    main()
