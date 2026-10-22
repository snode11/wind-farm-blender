"""Render nine seconds with progressive orange/blue/red physical tip trails."""
from pathlib import Path
import json
import runpy
import bpy
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'evidence/down-gust-preview/v4-trails'
OUT.mkdir(parents=True,exist_ok=True)
loaded=runpy.run_path(str(ROOT/'scripts/blender/open_down_gust_preview.py'))
scene=bpy.context.scene
from wfrl_blender import tip_tracking, clearance_replay

scene.render.engine='BLENDER_EEVEE'
scene.eevee.taa_render_samples=8
scene.render.resolution_x=1280
scene.render.resolution_y=720
scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
scene.frame_set(271)
scene.wfrl_flex_show_tip_trails=True
trail=tip_tracking.active(scene)
assert trail and trail.enabled
assert all(len(points)==1 for points in trail.points.values())
frames=OUT/'frames';frames.mkdir(exist_ok=True)
records=[]
for i in range(540):
    # Evaluate every frame, including cached renders, to reconstruct the same
    # progressive history if an interrupted export is resumed.
    scene.frame_set(271+i)
    bpy.context.view_layer.update()
    for bid in (1,2,3):
        obj=scene.objects[f'WFRL.Turbine.T1.Blade{bid}']
        point=obj.matrix_world@obj.data.vertices[-1].co
        assert np.linalg.norm(np.array(trail.points[bid][-1])-np.array(point))<1e-5
        assert len(trail.points[bid])==min(i+1,540)
        assert not trail.objects[bid].hide_render
    if i in (0,120,240,400,539):
        records.append(dict(frame=i,source_time_s=clearance_replay.sample(scene)['time_s'],
                            points_per_blade=[len(p) for p in trail.points.values()]))
    target=frames/f'{i:04d}.png'
    if not target.exists():
        scene.render.filepath=str(target)
        bpy.ops.render.render(write_still=True)
(OUT/'verification.json').write_text(json.dumps(dict(records=records,frames=540,fps=60,duration_s=9,
    source_run='down-random-gust-v3',colors=['orange','blue','red'],
    trails='progressive from video start; bounded at 540 points per blade',
    endpoint_matches_flexible_mesh=True),indent=2))
print('DOWN_GUST_TRAILS_VIDEO_PASS',flush=True)
