import importlib,runpy
from wfrl_blender import farm_flex,deflection,tower_motion,charts
farm_flex.detach()
for module in (deflection,tower_motion,charts,farm_flex):importlib.reload(module)
runpy.run_path('/Users/eason/Desktop/wfcrl/wind farm RL/evidence/prebend-20260918/short_window.py')
