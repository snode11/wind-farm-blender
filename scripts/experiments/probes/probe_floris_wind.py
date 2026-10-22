"""FLORIS 后端每次 reset 抽到的来流，以及零偏航基线功率。"""
import numpy as np
from wfrl.fastfarm_driver import FastFarmDriver

ws_all, pw_all = [], []
for k in range(12):
    drv = FastFarmDriver("Dec_Turb3_Row1_Floris", max_steps=12)
    m = drv.reset()
    for _ in range(4):
        m = drv.step(np.zeros(len(m["yaw"])))
    ws = float(np.nanmean(m["wind_speed"])); pw = float(np.sum(m["power"]))
    ws_all.append(ws); pw_all.append(pw)
    print(f"  reset {k:2d}:  u={ws:6.3f} m/s   P={pw:6.3f} MW   P/u^3={pw/ws**3*1e3:7.4f}")
    drv.close()
ws_all, pw_all = np.asarray(ws_all), np.asarray(pw_all)
print(f"\nFLORIS  u:  mean={ws_all.mean():.3f}  min={ws_all.min():.3f}  max={ws_all.max():.3f}")
print(f"FLORIS  P:  mean={pw_all.mean():.3f}  min={pw_all.min():.3f}  max={pw_all.max():.3f} MW")
print(f"  -> 单次 reset 决定整个训练回合的风速，P~u^3 使功率极差达 "
      f"{pw_all.max()/max(pw_all.min(),1e-9):.1f}x")
