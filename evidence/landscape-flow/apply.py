import bpy,sys,importlib,json,time
from pathlib import Path
out=Path('/Users/eason/Desktop/wfcrl/wind farm RL/evidence/landscape-flow')
mods={n.rsplit('.',1)[-1]:m for n,m in list(sys.modules.items()) if n.endswith(('wfrl_blender.landscape','wfrl_blender.cinematic','wfrl_blender.live_scene'))}
for m in mods.values():importlib.reload(m)
s=bpy.context.scene
report=mods['landscape'].optimize_shrubs(bpy.data.collections['WFRL_Scene'])
mods['cinematic'].update(s,s.frame_current/s.render.fps*.9)
(out/'live-result.json').write_text(json.dumps(report))
print('Landscape and cinematic updated',report)
