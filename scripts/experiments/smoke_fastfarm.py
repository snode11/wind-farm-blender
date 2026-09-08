"""FAST.Farm 冒烟测试（AEC 规范用法，参照 example_fastfarm.py）。
跑一个短回合，打印每步回传的真实测量，回合自然结束 → FAST.Farm 干净退出。"""
import os, time

from wfrl import paths

os.chdir(paths.ROOT)                # wfcrl 按 CWD 找 simulators/ 与 __simul__/
print("cwd:", os.getcwd(), flush=True)

import numpy as np
from mpi4py import MPI
print("mpi4py:", MPI.Get_library_version().strip(), flush=True)

from wfcrl import environments as envs
from wfcrl.rewards import StepPercentage

ENV = "Dec_Turb3_Row1_Fastfarm"
MAX_STEPS = 4                       # 短回合：够证明链路即可
t0 = time.time()
env = envs.make(ENV, max_num_steps=MAX_STEPS, controls=["yaw"],
                reward_shaper=StepPercentage(), load_coef=1)
print(f"built {ENV} in {time.time()-t0:.1f}s; agents={env.possible_agents}", flush=True)

env.reset()
print(f"reset OK in {time.time()-t0:.1f}s", flush=True)

r = {a: 0.0 for a in env.possible_agents}
done = {a: False for a in env.possible_agents}
nstep = {a: 0 for a in env.possible_agents}

for agent in env.agent_iter():
    obs, reward, term, trunc, info = env.last()
    done[agent] = done[agent] or term or trunc
    r[agent] += float(np.asarray(reward).squeeze())
    if done[agent]:
        env.step(None)
        continue
    # 打印 turbine_1 每一步回传的真实测量
    if agent == "turbine_1":
        pw = float(info["power"]) if "power" in info else float(np.ravel(obs.get("power", [0]))[0])
        ws = float(np.ravel(obs["wind_speed"])[0]) if "wind_speed" in obs else float("nan")
        print(f"  [T1 step {nstep[agent]}] power={pw:.1f}  wind_speed={ws:.2f}  "
              f"reward={float(np.asarray(reward).squeeze()):.3f}  t={time.time()-t0:.1f}s",
              flush=True)
    env.step({"yaw": np.array([1.0])})   # 每步 +1° 偏航
    nstep[agent] += 1

print(f"\nEPISODE DONE. total_reward={ {k: round(v,3) for k,v in r.items()} }", flush=True)
print("SMOKE_OK", flush=True)
