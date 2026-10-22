import numpy as np
from wfcrl import environments as envs
from wfrl.viz.field import get_floris

print("--- 同一个 env 反复 reset ---")
e = envs.make("Dec_Turb3_Row1_Floris", controls=["yaw"], max_num_steps=8)
fi = get_floris(e)
for k in range(6):
    e.reset()
    print(f"  reset {k}: free-stream u={np.ravel(fi.floris.flow_field.wind_speeds)[0]:.4f}"
          f"  dir={np.ravel(fi.floris.flow_field.wind_directions)[0]:.3f}")

print("--- 每次新建 env ---")
for k in range(6):
    e2 = envs.make("Dec_Turb3_Row1_Floris", controls=["yaw"], max_num_steps=8)
    e2.reset()
    f2 = get_floris(e2)
    print(f"  make {k}: free-stream u={np.ravel(f2.floris.flow_field.wind_speeds)[0]:.4f}"
          f"  dir={np.ravel(f2.floris.flow_field.wind_directions)[0]:.3f}")
