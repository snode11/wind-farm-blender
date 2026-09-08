"""稳态检验：FAST.Farm 的 100 控制步够不够让尾流发展完全？

这一步不能省。若尾流尚未传到最下游，下游机组会测到偏高的风速 → 功率偏高，
而这**正好是**对照实验里观察到的差异方向。不排掉它，"FAST.Farm 下游功率更高"
就可能只是没跑够时间的假象。

u=8 m/s 下尾流从 T1 到 T3 的对流时间 ≈ 1008/8 = 126 s = 42 个控制步(dt=3)。
这里跑 180 步(540 s)，看 T2/T3 的功率是否在 100 步之后已经平掉。

    "/c/Program Files/Microsoft MPI/Bin/mpiexec.exe" -n 1 \
        "C:/Users/s1155/.conda/envs/wfcrl/python.exe" exp_settle_check.py
"""
import os
import json

import numpy as np

from wfrl.fastfarm_driver import FastFarmDriver

from wfrl import paths

U, STEPS = 8.0, 180


def main():
    drv = FastFarmDriver("Dec_Turb3_Row1_Fastfarm", max_steps=STEPS + 2)
    drv.reset(wind_speed=U, wind_direction=270.0)
    hist = []
    for k in range(STEPS):
        m = drv.step(np.zeros(drv.n))
        hist.append(m["power"].copy())
        if (k + 1) % 20 == 0:
            w = np.asarray(hist[-10:]).mean(axis=0)
            print(f"  步 {k+1:3d} (t={3*(k+1):4d}s)  末10步均值 "
                  f"T1={w[0]:.4f} T2={w[1]:.4f} T3={w[2]:.4f} "
                  f"合计={w.sum():.4f} MW", flush=True)
        if m["done"]:
            break
    drv.close()

    H = np.asarray(hist)
    w85 = H[85:100].mean(axis=0)          # 对照实验取的窗口(末15步 of 100)
    wend = H[-15:].mean(axis=0)
    drift = 100 * (wend.sum() / w85.sum() - 1)
    print(f"\n  步 86-100 窗口 合计 {w85.sum():.4f} MW  (对照实验用的就是这个)")
    print(f"  步 {len(H)-14}-{len(H)} 窗口 合计 {wend.sum():.4f} MW")
    print(f"  相对漂移 {drift:+.2f}%  -> "
          f"{'已收敛，对照实验的取值有效' if abs(drift) < 2 else '未收敛，需延长'}")
    json.dump({"u_inf": U, "power_series": H.tolist(),
               "win_86_100_MW": float(w85.sum()),
               "win_end_MW": float(wend.sum()), "drift_pct": float(drift)},
              open(os.path.join(paths.RUNS, "settle_check.json"), "w"), indent=2)
    print("-> runs/settle_check.json")


if __name__ == "__main__":
    main()
