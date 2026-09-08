"""核对两个后端在训练时实际看到的来流是否可比。

power ~ u^3，若 FLORIS 与 FAST.Farm 的平均来流风速不同，静/动功率差里
就混进了纯风速效应，2.43x 这个结论会被污染。
"""
import numpy as np
from wfrl.fastfarm_driver import FastFarmDriver

for env_id in ["Dec_Turb3_Row1_Floris", "Dec_Turb3_Row1_Fastfarm"]:
    drv = FastFarmDriver(env_id, max_steps=40)
    m = drv.reset()
    ws, pw = [], []
    for _ in range(24):
        m = drv.step(np.zeros(len(m["yaw"])))      # 零增量：只看来流与基线功率
        ws.append(np.nanmean(m["wind_speed"]))
        pw.append(np.sum(m["power"]))
    drv.close()
    ws, pw = np.asarray(ws), np.asarray(pw)
    print(f"\n[{env_id}]")
    print(f"  wind_speed  mean={np.nanmean(ws):6.3f}  min={np.nanmin(ws):6.3f}  max={np.nanmax(ws):6.3f}")
    print(f"  farm_power  mean={pw.mean():6.3f} MW  min={pw.min():6.3f}  max={pw.max():6.3f}")
    print(f"  P/u^3       {pw.mean()/np.nanmean(ws)**3*1e3:8.4f}  kW/(m/s)^3")
