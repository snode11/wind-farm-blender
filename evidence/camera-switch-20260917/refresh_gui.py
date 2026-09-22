import ast
from pathlib import Path
from wfrl_blender.panels import farm_replay
source = Path('/Users/eason/Desktop/wfcrl/wind farm RL/blender_frontend/wfrl_blender/panels/farm_replay.py')
node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'draw_views')
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), vars(farm_replay))
