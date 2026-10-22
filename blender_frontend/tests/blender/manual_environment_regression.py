"""Manual daylight presentation, private assets, and return shading checks."""
import importlib
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(addon.__file__).resolve().parent == Path(expected_root).resolve()


def tree_snapshot(tree):
    return [dict(name=n.name, type=n.bl_idname,
                 image=n.image.as_pointer() if hasattr(n, 'image') and n.image else None,
                 inputs={s.name: list(s.default_value) if hasattr(s.default_value, '__len__')
                         else s.default_value for s in n.inputs if hasattr(s, 'default_value')})
            for n in tree.nodes]


def source_snapshot(source):
    return dict(objects=[o.as_pointer() for o in source.objects],
                world=source.world.as_pointer(), world_nodes=tree_snapshot(source.world.node_tree),
                materials={m.as_pointer(): tree_snapshot(m.node_tree) for m in bpy.data.materials
                           if m.use_nodes and not m.name.startswith('WFRLManual.')},
                images={i.as_pointer(): (i.name, i.colorspace_settings.name, i.filepath)
                        for i in bpy.data.images if i.source == 'FILE'
                        and not i.name.startswith('WFRLManual.')})


addon.register()
addon.load_demo_scene(camera_rig=False)
source = bpy.context.scene
before = source_snapshot(source)
space = next(a.spaces.active for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
space.shading.type = 'MATERIAL'
space.shading.use_scene_world = False
space.shading.use_scene_lights = False
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
manual = bpy.context.scene
assert control.validate_scene(manual)[0]
assert space.shading.type == 'MATERIAL', 'Manual view must show surface materials and sky'
assert space.shading.use_scene_world and space.shading.use_scene_lights
prefix = manual['wfrl_manual_owner']
assert manual.world != source.world and manual.world.name.startswith(prefix)
sky = manual.world.node_tree.nodes.get('WFRL.Atmosphere.HDRI')
assert sky and sky.image, 'Cloudy sky must use the bundled environment image'
ground = manual.objects.get(prefix + '.Ground')
assert ground is not None and len(ground.data.polygons) <= 6000
terrain = ground.data.materials[0]
assert terrain.name.startswith(prefix) and terrain.node_tree.nodes.get('WFRL.Terrain.Habitat')
images = {n.image for tree in (manual.world.node_tree, terrain.node_tree)
          for n in tree.nodes if hasattr(n, 'image') and n.image}
assert len(images) == 4
assert all(i.name.startswith(prefix) and i.packed_file for i in images)
assert not any(i.as_pointer() in before['images'] for i in images)
assert source_snapshot(source) == before, 'Manual environment changed source assets'
assert bpy.ops.wfrl.manual_focus() == {'FINISHED'}
assert space.shading.type == 'MATERIAL' and space.shading.use_scene_world
assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
assert bpy.context.scene == source
assert space.shading.type == 'MATERIAL'
assert not space.shading.use_scene_world and not space.shading.use_scene_lights
assert source_snapshot(source) == before
# Saved manual scenes can contain the simple flat environment.
manual['wfrl_manual_environment_revision'] = 0
manual.world.node_tree.nodes.remove(sky)
terrain.node_tree.nodes.clear()
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
assert bpy.context.scene == manual and control.validate_scene(manual)[0]
assert manual.world.node_tree.nodes.get('WFRL.Atmosphere.HDRI')
assert manual.objects.get(prefix + '.Ground').data.materials[0].node_tree.nodes.get('WFRL.Terrain.Habitat')
assert source_snapshot(source) == before
assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
result = dict(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
              private_world=True, private_packed_images=len(images),
              ground_faces=len(ground.data.polygons), source_assets_unchanged=True,
              source_material_preview_restored=True, existing_environment_recovered=True)
(OUT / 'checks.json').write_text(json.dumps(result, indent=2))
print('MANUAL_ENVIRONMENT_PASS ' + json.dumps(result), flush=True)
addon.unregister()
