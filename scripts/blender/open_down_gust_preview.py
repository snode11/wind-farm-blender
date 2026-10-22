"""Down-first real-gust prototype; new geometry and measurements share one run."""
from pathlib import Path
import runpy, json, os
import bpy
ROOT=Path(__file__).resolve().parents[2]
runpy.run_path(str(ROOT/'scripts/blender/open_blade_flex_preview.py'))
from wfrl.lidar.flex_provisional import load
from wfrl_blender import clearance_replay,blade_flex_preview
from wfrl_blender.cameras import ensure_gimbal,down_gimbal
scene=bpy.context.scene
folder=Path(os.environ.get('WFRL_GUST_PREVIEW_DIR', ROOT/'evidence/down-gust-preview/v3-random'))
reader=load(folder/'data')
clearance_replay._READERS[scene.as_pointer()]=reader
scene.frame_end=1+round((reader.end_s-reader.start_s)*60)
scene.frame_set(1)  # Restore the old flexible binding before attaching new data.
preview=blade_flex_preview.attach(scene,folder/'gust-flex.npz',folder/'data/manifest.json')
wind=reader.package.manifest['wind_profile']
scene['wfrl_flex_title']=f"FAST.Farm 单机 · {reader.end_s-reader.start_s:g} 秒 · 基线 {wind['mean_wind_mps']:g} m/s 阵风"
scene['wfrl_flex_provenance']='单机随机湍流 + 阵风；固定 9 rpm、零变桨；未接入 MAPPO'
scene['wfrl_tip_reference_note']=scene['wfrl_flex_provenance']
scene['wfrl_flex_tip_trails']=False
scene['wfrl_tip_trail_max_points']=540  # Preserve all three blade passes in a nine-second comparison.
scene.wfrl_flex_show_tip_trails=False
scene['wfrl_flex_preview']='阵风形变实验预览；新仿真、新净空读数；数值细化尚未验证'
scene['wfrl_flex_wind_note']=f"背景 TI {wind['target_ti']*100:g}% · 随机空间风场 + 短阵风"
scene['wfrl_flex_provisional']=True
scene['wfrl_clearance_status']='REVIEW_ONLY'
scene.camera=ensure_gimbal(scene,'T1')
down_gimbal(scene.camera)
scene['wfrl_camera']=scene.camera.name
scene['wfrl_flex_view']='Down gimbal'
for screen in bpy.data.screens:
 for area in screen.areas:
  if area.type=='VIEW_3D':
   area.spaces.active.camera=scene.camera
   area.spaces.active.region_3d.view_perspective='CAMERA'
scene.frame_set(1)
scene.wfrl_flex_show_tip_trails = os.environ.get('WFRL_GUST_TRAILS', '1') == '1'
print('DOWN_GUST_PREVIEW_READY',flush=True)

def start_preview():
 for window in bpy.context.window_manager.windows:
  if window.scene == scene and not window.screen.is_animation_playing:
   with bpy.context.temp_override(window=window, screen=window.screen):
    bpy.ops.screen.animation_play()
   break

if not bpy.app.background and os.environ.get('WFRL_AUTOPLAY', '1') == '1':
 bpy.app.timers.register(start_preview, first_interval=3.0)
