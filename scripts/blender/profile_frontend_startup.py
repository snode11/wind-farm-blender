"""Profile native scene construction separately from solver initialization.

Run inside Blender with --python. Input scene JSON is produced by the backend's
Scene.to_dict(). This measures synchronous scene/geometry/material work; it does
not claim GPU shader compilation or time to a fully converged rendered frame.
Registration is released on failure; the JSON report and completion marker are
published only after scene construction and resource cleanup succeed.
"""
import cProfile
import json
from pathlib import Path
import pstats
import sys
import time
import bpy

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'evidence'/'frontend_completion'
sys.path.insert(0, str(ROOT/'blender_frontend'))
import wfrl_blender
from wfrl_blender import atomic_json
from wfrl_blender.live_scene import build_live_scene
from wfrl_blender.scene_model import SceneDTO
scene = SceneDTO.from_mapping(json.loads((OUT/'profile-scene.json').read_text(encoding='utf-8')))
try:
    wfrl_blender.register()
    profile = cProfile.Profile()
    start = time.monotonic()
    profile.runcall(build_live_scene, scene)
    elapsed = time.monotonic()-start
    profile.dump_stats(str(OUT/'scene-build.pstats'))
    rows=[]
    for (filename,line,name),(primitive,total,own,cumulative,callers) in pstats.Stats(profile).stats.items():
        if 'wfrl_blender' in filename:
            rows.append(dict(file=filename.split('wfrl_blender/',1)[-1],line=line,function=name,
                             calls=total,own_seconds=own,cumulative_seconds=cumulative))
    report=dict(background=bpy.app.background, total_seconds=elapsed,objects=len(bpy.data.objects),
                scope='synchronous scene construction; excludes first rendered frame and solver',
                functions=sorted(rows,key=lambda row:row['cumulative_seconds'],reverse=True)[:40])
finally:
    wfrl_blender.unregister()
atomic_json.write_json(OUT/'startup-profile.json', report)
print('STARTUP_PROFILE',elapsed,flush=True)
