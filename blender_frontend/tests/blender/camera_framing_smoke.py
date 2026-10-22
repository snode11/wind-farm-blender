"""Full rotor envelopes must fit each overview; catch cropped downstream turbines."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
import wfrl_blender

from wfrl_blender.scene_model import SceneDTO
from wfrl_blender.scene_builder import build_scene
from wfrl_blender.cameras import build_cameras
dto = SceneDTO.from_mapping({'layout': [{'id': 'T1', 'x': 0, 'y': 0}, {'id': 'T2', 'x': 504, 'y': 31.5}, {'id': 'T3', 'x': 1008, 'y': 63}]})
build_scene(dto)
build_cameras(dto)
scene = bpy.context.scene
scene.frame_set(451)
bpy.context.view_layer.update()
for name in ('World', 'Top', 'Side'):
    camera = bpy.data.objects['WFRL.Camera.' + name]
    for tid in ('T1', 'T2', 'T3'):
        root = bpy.data.objects['WFRL.Turbine.' + tid]
        for dx in (-70, 70):
            for dy in (-70, 70):
                for z in (0, 155):
                    p = world_to_camera_view(scene, camera, root.location + Vector((dx, dy, z)))
                    assert .02 < p.x < .98 and .02 < p.y < .98 and p.z > 0, (name, tid, tuple(p))
print('WFRL_CAMERA_FRAMING=PASS')
