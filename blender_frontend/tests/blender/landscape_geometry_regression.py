"""Verify static landscape detail, contact, vegetation and saved geometry."""

from pathlib import Path
import hashlib
import json
import math
import os
import random
import struct
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'blender_frontend'))
from wfrl_blender import landscape


def mesh_digest(data):
    digest = hashlib.sha256()
    for vertex in data.vertices:
        digest.update(struct.pack('<3f', *vertex.co))
    for polygon in data.polygons:
        digest.update(struct.pack('<2I?', len(polygon.vertices), polygon.material_index, polygon.use_smooth))
        digest.update(struct.pack('<' + 'I' * len(polygon.vertices), *polygon.vertices))
    for attribute in data.attributes:
        if attribute.name.startswith('.'):
            continue
        digest.update(attribute.name.encode())
        for item in attribute.data:
            value = (item.vector if hasattr(item, 'vector') else
                     item.color if hasattr(item, 'color') else (item.value,))
            digest.update(struct.pack('<' + 'd' * len(value), *value))
    return digest.hexdigest()


def build(detail):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.data.collections.new('Landscape')
    bpy.context.scene.collection.children.link(collection)
    terrain = landscape.build_landscape(collection, detail=detail)
    bpy.context.view_layer.update()
    return collection, terrain


def triangles(objects):
    total = 0
    for obj in objects:
        obj.data.calc_loop_triangles()
        total += len(obj.data.loop_triangles)
    return total


def instances():
    return sum(item.is_instance for item in bpy.context.evaluated_depsgraph_get().object_instances)


collection, terrain = build('full')
full_height = landscape.terrain_sampler(terrain)
full_triangles = triangles(collection.objects)
full_instances = instances()
unchanged = {obj.name: mesh_digest(obj.data) for obj in collection.objects
             if obj.name.startswith('WFRL.Landscape.') and
             'DistantRidge' not in obj.name and not obj.name.endswith('GrassTufts')}
grass = collection.objects['WFRL.Landscape.GrassTufts']
full_grass = [tuple(vertex.co) for vertex in grass.data.vertices]
full_materials = [polygon.material_index for polygon in grass.data.polygons]
tuft_count = grass['tuft_count']

collection, terrain = build('balanced')
balanced_height = landscape.terrain_sampler(terrain)
balanced_triangles = triangles(collection.objects)
assert balanced_triangles < full_triangles * .5
assert full_instances == instances()
for name, expected in unchanged.items():
    assert mesh_digest(collection.objects[name].data) == expected, name
    assert not collection.objects[name].hide_render
    assert not collection.objects[name].hide_viewport

rng = random.Random(4301)
worst_contact_error = 0.0
for _ in range(3000):
    x, y = rng.uniform(-980, 2050), rng.uniform(-940, 1140)
    error = abs(full_height(x, y) - balanced_height(x, y))
    worst_contact_error = max(worst_contact_error, error)
    assert error < .0002, (x, y, error)

grass = collection.objects['WFRL.Landscape.GrassTufts']
assert grass['tuft_count'] == tuft_count
vertices = [tuple(vertex.co) for vertex in grass.data.vertices]
vertex_index = polygon_index = near_count = distant_count = 0
for tuft in range(tuft_count):
    original = full_grass[tuft * 15:(tuft + 1) * 15]
    near = len(grass.data.polygons[polygon_index].vertices) == 4
    if near:
        assert vertices[vertex_index:vertex_index + 15] == original
        assert [p.material_index for p in grass.data.polygons[polygon_index:polygon_index + 6]] == full_materials[tuft * 6:(tuft + 1) * 6]
        vertex_index += 15
        polygon_index += 6
        near_count += 1
    else:
        endpoints = [original[blade * 5 + index] for blade in range(3) for index in (0, 1, 4)]
        assert vertices[vertex_index:vertex_index + 9] == endpoints
        assert [p.material_index for p in grass.data.polygons[polygon_index:polygon_index + 3]] == full_materials[tuft * 6:(tuft + 1) * 6:2]
        vertex_index += 9
        polygon_index += 3
        distant_count += 1
    x, y, _ = original[0]
    distance = min(math.hypot(x - tx, y) for tx in landscape.TURBINE_X)
    assert near or distance > landscape.GRASS_DETAIL_RADIUS - .2
    assert not near or distance < landscape.GRASS_DETAIL_RADIUS + .2
assert vertex_index == len(vertices)
assert polygon_index == len(grass.data.polygons)
assert near_count and distant_count

saved = {obj.name: mesh_digest(obj.data) for obj in collection.objects}
with tempfile.TemporaryDirectory(prefix='wfrl-landscape-reopen-') as directory:
    path = Path(directory) / 'landscape.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    assert bpy.data.objects['WFRL.Terrain']['wfrl_landscape_detail'] == 'balanced'
    assert instances() == full_instances
    for name, expected in saved.items():
        assert mesh_digest(bpy.data.objects[name].data) == expected, name

result = dict(status='PASS', full_triangles=full_triangles, balanced_triangles=balanced_triangles,
              instances=full_instances, tufts=tuft_count, near_tufts=near_count,
              distant_tufts=distant_count, worst_contact_error_m=worst_contact_error,
              unchanged_meshes=sorted(unchanged), saved_reopen=True)
if os.environ.get('WFRL_TEST_OUTPUT'):
    output = Path(os.environ['WFRL_TEST_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    (output / 'checks.json').write_text(json.dumps(result, indent=2))
print('LANDSCAPE_GEOMETRY_REGRESSION_PASS', json.dumps(result), flush=True)
