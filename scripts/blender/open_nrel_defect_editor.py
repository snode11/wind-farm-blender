"""Workspace-source NREL editor launcher. No rendering or solver execution."""
from pathlib import Path
import os
import sys
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import wfrl_blender
from wfrl_blender.nrel_defects import editor
wfrl_blender.register()
wfrl_blender.load_demo_scene()
s=editor.ensure(bpy.context.scene)
if os.environ.get('WFRL_NREL_DEFECT_CONFIG'):
    s.load(os.environ['WFRL_NREL_DEFECT_CONFIG'])
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=='VIEW_3D':
            area.spaces.active.show_region_ui=True
            area.spaces.active.overlay.show_overlays=True
            area.spaces.active.shading.type='MATERIAL'
print('NREL_DEFECT_EDITOR_READY',wfrl_blender.__file__,flush=True)
