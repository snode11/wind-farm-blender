import numpy as np
from wfcrl import environments as envs

for eid in ["Dec_Turb3_Row1_Floris", "Dec_Turb3_Row1_Fastfarm"]:
    e = envs.make(eid, controls=["yaw"], max_num_steps=8)
    fc = e.mdp.farm_case
    print("="*70); print(eid)
    print("  farm_case:", type(fc).__name__)
    for k in sorted(vars(fc)):
        v = getattr(fc, k)
        if isinstance(v, (int, float, str, bool)) or v is None:
            print(f"    {k:22s} = {v}")
    print("  mdp attrs:", [a for a in vars(e.mdp) if not a.startswith('__')])
    itf = e.mdp.interface
    print("  interface:", type(itf).__name__,
          [a for a in vars(itf) if not a.startswith('_')][:20])
