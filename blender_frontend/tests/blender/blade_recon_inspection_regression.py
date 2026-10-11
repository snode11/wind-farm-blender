"""Native appearance inspection regression; no solver, truth, or GUI required.

WFRL_BLADE_RECON_MODE=create|reopen|legacy-reopen. Create saves independent
enhanced and pre-upgrade .blend files, then deletes the disposable external
reconstruction inputs. Reopen modes must run in fresh Blender processes with
the corresponding saved file. WFRL_ADDON_MODULE and WFRL_EXPECTED_ADDON_ROOT
record and optionally constrain the actual source or installed addon.

An independent RGBA atlas is rendered through the actual TexMix output with
EEVEE. Emission isolates shader colors from lighting. These swatches represent
known appearance inputs, not defect labels or reconstructed truth.
"""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import traceback
import zlib

import bpy
from mathutils import Vector
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/blade-recon-inspection-regression')).resolve()
OUT.mkdir(parents=True, exist_ok=True)
MODE = os.environ.get('WFRL_BLADE_RECON_MODE', 'create')
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
report = {'status': 'RUNNING', 'mode': MODE, 'blender': bpy.app.version_string,
          'render_engine': 'BLENDER_EEVEE',
          'scope': 'native appearance shader, saved estimate focus, fresh-process persistence; no solver or truth',
          'checks': {}, 'rendered_samples': {}}
checks = report['checks']
PROP_VALUES = {
    'wfrl_recon_show_texture': True,
    'wfrl_recon_texture_mode': 'FALSE_COLOR',
    'wfrl_recon_texture_gain': 8.,
    'wfrl_recon_texture_threshold': .08,
    'wfrl_recon_inspect_blade': '2',
    'wfrl_recon_inspect_radius': 47.,
    'wfrl_recon_inspect_tau': .2,
    'wfrl_recon_inspect_size': 3.,
    'wfrl_recon_inspect_box': True,
}
LEGACY_DEFAULTS = {
    'wfrl_recon_texture_mode': 'ORIGINAL',
    'wfrl_recon_texture_gain': 6.,
    'wfrl_recon_texture_threshold': .06,
    'wfrl_recon_inspect_blade': '1',
    'wfrl_recon_inspect_tau': .6,
    'wfrl_recon_inspect_size': 2.,
    'wfrl_recon_inspect_box': False,
}


def output():
    (OUT / (MODE + '.json')).write_text(json.dumps(report, indent=2, ensure_ascii=False))


def vertices(obj):
    result = np.empty(len(obj.data.vertices) * 3, np.float32)
    obj.data.vertices.foreach_get('co', result)
    return result


def evidence(obj):
    result = np.empty(len(obj.data.color_attributes['evidence'].data) * 4, np.float32)
    obj.data.color_attributes['evidence'].data.foreach_get('color', result)
    return result


def image_fingerprint(image):
    return (image.as_pointer(), hashlib.sha256(image.packed_file.data).hexdigest(),
            hashlib.sha256(np.asarray(image.pixels[:], np.float32).tobytes()).hexdigest())


def material_contract(mat):
    nodes = mat.node_tree.nodes
    return {
        'image': image_fingerprint(nodes['SurfaceTexture'].image),
        'texture_vector': tuple(link.from_socket.as_pointer() for link in nodes['SurfaceTexture'].inputs['Vector'].links),
        'mix_alpha': tuple(link.from_socket.as_pointer() for link in nodes['TexMix'].inputs[0].links),
        'mix_evidence': tuple(link.from_socket.as_pointer() for link in nodes['TexMix'].inputs[1].links),
    }


def png_chunk(kind, body):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))


def swatch_fixture(review):
    # Four uniform stripes avoid interpolation ambiguities. The last two have
    # transparent appearance and distinct evidence colors on the actual mesh.
    row = bytes((230, 230, 230, 255)) * 2 + bytes((10, 10, 10, 255)) * 2
    row += bytes((230, 230, 230, 0)) * 2 + bytes((10, 10, 10, 0)) * 2
    path = OUT / 'appearance-rgba-input.png'
    path.write_bytes(b'\x89PNG\r\n\x1a\n' +
        png_chunk(b'IHDR', struct.pack('>IIBBBBB', 8, 2, 8, 6, 0, 0, 0)) +
        png_chunk(b'IDAT', zlib.compress((b'\x00' + row) * 2)) + png_chunk(b'IEND', b''))
    scene = bpy.data.scenes.new('WFRL.Inspection.MaterialSwatches')
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y = 128, 32
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'OPEN_EXR'
    scene.render.image_settings.color_depth = '32'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.view_settings.view_transform = 'Standard'
    scene.world = bpy.data.worlds.new('WFRL.Inspection.SwatchWorld')
    scene.world.color = (0., 0., 0.)
    mat = review._texture_material('WFRL.Inspection.SwatchMaterial', path, {'W': 8, 'H': 2})
    tree = mat.node_tree
    emission = tree.nodes.new('ShaderNodeEmission')
    tree.links.new(tree.nodes['TexMix'].outputs[0], emission.inputs['Color'])
    tree.links.new(emission.outputs[0], tree.nodes['Material Output'].inputs['Surface'])
    mesh = bpy.data.meshes.new('WFRL.Inspection.SwatchMesh')
    coords, faces, uvs, colors = [], [], [], []
    for index in range(4):
        lower = -4 + 2 * index
        coords.extend(((lower, -1, 0), (lower + 2, -1, 0), (lower + 2, 1, 0), (lower, 1, 0)))
        faces.append(tuple(range(index * 4, index * 4 + 4)))
        uvs.extend(((index + .5) / 4., .5) for _ in range(4))
        colors.extend(review.ORANGE if index == 3 else review.GREEN for _ in range(4))
    mesh.from_pydata(coords, [], faces)
    mesh.materials.append(mat)
    mesh.uv_layers.new(name='atlas').data.foreach_set('uv', np.asarray(uvs, np.float32).reshape(-1))
    mesh.color_attributes.new(name='evidence', type='FLOAT_COLOR', domain='POINT').data.foreach_set(
        'color', np.asarray(colors, np.float32).reshape(-1))
    obj = bpy.data.objects.new('WFRL.Inspection.Swatches', mesh)
    scene.collection.objects.link(obj)
    cam_data = bpy.data.cameras.new('WFRL.Inspection.SwatchCamera')
    cam_data.type, cam_data.ortho_scale = 'ORTHO', 8
    cam = bpy.data.objects.new(cam_data.name, cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0, 0, 4)
    scene.camera = cam
    return scene, obj


def render_swatches(scene, label):
    previous = bpy.context.window.scene
    bpy.context.window.scene = scene
    scene.render.filepath = str(OUT / (MODE + '-' + label + '.exr'))
    try:
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(scene.render.filepath, check_existing=False)
        pixels = np.asarray(image.pixels[:], np.float32).reshape(32, 128, 4)
        samples = np.stack([pixels[12:20, center - 4:center + 4, :3].mean(axis=(0, 1))
                            for center in (16, 48, 80, 112)])
        bpy.data.images.remove(image)
        report['rendered_samples'][label] = samples.tolist()
        return samples
    finally:
        bpy.context.window.scene = previous


def check_swatches(review, inspection, rotor):
    if MODE == 'create':
        scene, obj = swatch_fixture(review)
        base = render_swatches(scene, 'untouched-original')
    else:
        scene = bpy.data.scenes['WFRL.Inspection.MaterialSwatches']
        obj = scene.objects['WFRL.Inspection.Swatches']
        base = np.asarray(json.loads(scene['inspection_baseline_samples']), np.float32)
    before = material_contract(obj.data.materials[0])
    geometry, colors = vertices(obj).copy(), evidence(obj).copy()
    scene.wfrl_recon_inspect_box = False
    scene.wfrl_recon_texture_mode = 'ORIGINAL'
    inspection.update(scene, [obj], rotor)
    actual = render_swatches(scene, 'original')
    assert np.max(np.abs(base - actual)) < .002, (base.tolist(), actual.tolist())
    scene['inspection_baseline_samples'] = json.dumps(base.tolist())
    checks['original_mode_actual_shader_matches_untouched_material'] = True
    scene.wfrl_recon_texture_mode = 'CONTRAST'
    inspection.update(scene, [obj], rotor)
    contrast = render_swatches(scene, 'contrast')
    assert contrast[0].mean() > actual[0].mean() + .1, (actual.tolist(), contrast.tolist())
    assert contrast[1].mean() < actual[1].mean() - .001, (actual.tolist(), contrast.tolist())
    checks['contrast_mode_actual_shader_increases_bright_dark_separation'] = True
    scene.wfrl_recon_texture_mode = 'FALSE_COLOR'
    inspection.update(scene, [obj], rotor)
    actual = render_swatches(scene, 'false-color')
    expected = np.asarray(((.015, 1., 1.), (1., .015, .5), review.GREEN[:3], review.ORANGE[:3]), np.float32)
    assert np.max(np.abs(actual - expected)) < .003, (actual.tolist(), expected.tolist())
    assert np.max(np.abs(contrast[2:] - expected[2:])) < .003, contrast.tolist()
    assert material_contract(obj.data.materials[0]) == before
    assert np.array_equal(vertices(obj), geometry) and np.array_equal(evidence(obj), colors)
    checks['false_color_actual_shader_bright_cyan_dark_magenta_and_alpha_zero_green_orange'] = True
    checks['all_appearance_modes_preserve_source_image_pixels_alpha_uv_evidence_and_geometry'] = True


def check_estimate(scene, review, inspection):
    value = review.session(scene)
    sequence, blades = value['sequence'], value['blades']
    assert sequence.truth_states is None and not value['ghosts']
    assert not any(obj.get('blade_recon_role') == 'truth' for obj in scene.objects)
    assert not scene.get('truth_text')
    checks['inspection_uses_saved_estimates_without_truth_companion_or_truth_objects'] = True
    scene.frame_set(1)
    review.apply(scene)
    images = [material_contract(obj.data.materials[0]) for obj in blades]
    geometry = [vertices(obj).copy() for obj in blades]
    colors = [evidence(obj).copy() for obj in blades]
    for mode in ('ORIGINAL', 'CONTRAST', 'FALSE_COLOR'):
        scene.wfrl_recon_texture_mode = mode
        review.apply(scene)
        assert [material_contract(obj.data.materials[0]) for obj in blades] == images
        assert all(np.array_equal(vertices(obj), old) for obj, old in zip(blades, geometry))
        assert all(np.array_equal(evidence(obj), old) for obj, old in zip(blades, colors))
    checks['enhanced_real_blades_preserve_packed_atlases_geometry_and_evidence'] = True
    for name, expected in (('wfrl_recon_inspect_blade', '1'), ('wfrl_recon_inspect_radius', 59.),
                           ('wfrl_recon_inspect_tau', .6), ('wfrl_recon_inspect_size', 2.)):
        setattr(scene, name, expected)
    first = inspection.focus(bpy.context, sequence, blades)
    again = inspection.focus(bpy.context, sequence, blades)
    assert first['target'] == again['target'] and first['normal'] == again['normal']
    assert abs(Vector(first['normal']).length - 1.) < 1e-6
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bpy.context.view_layer.update()
    obj = blades[0].evaluated_get(depsgraph)
    target, normal = Vector(first['target']), Vector(first['normal'])
    inverse = obj.matrix_world.inverted()
    hit, point, _, _ = obj.ray_cast(inverse @ (target + normal * .03),
                                   inverse.to_3x3() @ (-normal), distance=.1)
    assert hit and ((obj.matrix_world @ point) - target).length < 2e-4, (hit, tuple(point), first)
    scene.frame_set(len(sequence.states))
    review.apply(scene)
    moved = inspection.focus(bpy.context, sequence, blades)
    assert np.linalg.norm(np.asarray(moved['target']) - np.asarray(first['target'])) > .1, (first, moved)
    report['focus'] = {'first': first, 'later': moved}
    checks['repeat_focus_stable_target_on_actual_estimated_mesh_and_saved_frame_motion'] = True
    return sequence, blades


try:
    assert MODE in ('create', 'reopen', 'legacy-reopen'), MODE
    if MODULE == 'wfrl_blender':
        sys.path.insert(0, str(ROOT / 'blender_frontend'))
    addon = importlib.import_module(MODULE)
    report['addon_file'] = str(Path(addon.__file__).resolve())
    expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
    if expected_root:
        assert Path(addon.__file__).resolve().is_relative_to(Path(expected_root).resolve())
    review = importlib.import_module(MODULE + '.blade_recon_review')
    inspection = importlib.import_module(MODULE + '.blade_recon_inspection')
    if MODE != 'create':
        scene = next(s for s in bpy.data.scenes if review.active(s))
        blades = [obj for obj in scene.objects if obj.get('blade_recon_role') == 'estimate']
        if MODE == 'legacy-reopen':
            assert all(inspection.NODE_NAME not in obj.data.materials[0].node_tree.nodes for obj in blades)
            checks['pre_upgrade_file_started_without_inspection_nodes'] = True
        else:
            for obj in blades:
                node = obj.data.materials[0].node_tree.nodes.get(inspection.NODE_NAME)
                assert node is not None and node.node_tree.get('blade_recon_inspection_version') == inspection.VERSION
                assert node.inputs['FalseColor'].default_value == 1
                assert node.inputs['Gain'].default_value == PROP_VALUES['wfrl_recon_texture_gain']
                assert node.inputs['ShowBox'].default_value == float(obj['blade_recon_blade'] == 1)
            checks['fresh_process_saved_enhancement_nodes_present_before_any_restore'] = True
    if not hasattr(bpy.types.Scene, 'wfrl_recon_texture_mode'):
        addon.register()
    if MODE == 'create':
        fixture = OUT / 'disposable-external-inputs'
        fixture.mkdir(exist_ok=True)
        data = json.loads((ROOT / 'blade_recon0.2/out/synth_tex/recon.json').read_text())
        data['frames'] = [copy.deepcopy(data['frames'][index]) for index in (0, 40, 80)]
        (fixture / 'recon.json').write_text(json.dumps(data))
        texture = fixture / 'texture'
        texture.mkdir(exist_ok=True)
        for name in ('texture.json', 'blade1_tex.png', 'blade2_tex.png', 'blade3_tex.png'):
            shutil.copy2(ROOT / 'blade_recon0.2/out/synth_tex/texture' / name, texture / name)
        scene = review.load(bpy.context, fixture / 'recon.json', texture_path=texture)
    else:
        bpy.context.window.scene = scene
        assert not Path(scene['blade_recon_source_path']).exists()
        assert not Path(scene['blade_recon_texture_source_path']).exists()
        expected_values = LEGACY_DEFAULTS if MODE == 'legacy-reopen' else PROP_VALUES
        for name, expected in expected_values.items():
            actual = getattr(scene, name)
            assert abs(actual - expected) < 1e-6 if isinstance(expected, float) else actual == expected, (name, actual, expected)
        if MODE == 'legacy-reopen':
            assert all(not scene.is_property_set(name) for name in LEGACY_DEFAULTS)
            checks['old_blend_without_new_saved_properties_gets_original_appearance_defaults'] = True
        else:
            checks['fresh_process_saved_enhancement_and_manual_region_properties_restored'] = True
        review._tick()
        assert not scene.get('blade_recon_error'), scene.get('blade_recon_error')
        checks['fresh_process_restores_embedded_estimates_without_external_inputs'] = True
    sequence, blades = check_estimate(scene, review, inspection)
    for obj in blades:
        node = obj.data.materials[0].node_tree.nodes.get(inspection.NODE_NAME)
        assert node is not None and node.node_tree.get('blade_recon_inspection_version') == inspection.VERSION
    checks['loaded_or_legacy_materials_automatically_have_current_inspection_nodes'] = True
    if MODE == 'legacy-reopen':
        checks['old_blend_missing_nodes_automatically_upgraded_on_restore'] = True
    check_swatches(review, inspection, sequence.rotor)
    bpy.context.window.scene = scene
    for name, expected in PROP_VALUES.items():
        setattr(scene, name, expected)
    review.apply(scene)
    for index, obj in enumerate(blades):
        node = obj.data.materials[0].node_tree.nodes[inspection.NODE_NAME]
        assert node.inputs['FalseColor'].default_value == 1
        assert node.inputs['Gain'].default_value == PROP_VALUES['wfrl_recon_texture_gain']
        assert node.inputs['ShowBox'].default_value == float(index == 1)
    checks['saved_mode_gain_and_selected_blade_box_reach_actual_material_inputs'] = True
    if MODE == 'create':
        # A genuine old v0.2 file has neither these new properties nor their
        # shader node. Reset RNA storage first, since update callbacks can
        # create nodes; remove the nodes only after resetting the properties.
        for name in PROP_VALUES:
            if name != 'wfrl_recon_show_texture':
                scene.property_unset(name)
        for obj in blades:
            tree = obj.data.materials[0].node_tree
            tree.nodes.remove(tree.nodes[inspection.NODE_NAME])
            tree.links.new(tree.nodes['SurfaceTexture'].outputs['Color'], tree.nodes['TexMix'].inputs[2])
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'legacy-without-inspection.blend'))
        for name, expected in PROP_VALUES.items():
            setattr(scene, name, expected)
        inspection.update(scene, blades, sequence.rotor)
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'enhanced-inspection.blend'))
        shutil.rmtree(fixture)
        checks['enhanced_and_pre_upgrade_blends_saved_external_inputs_removed'] = True
    report['status'] = 'PASS'
    output()
    print('BLADE_RECON_INSPECTION_' + MODE.upper().replace('-', '_') + '_PASS')
except Exception:
    report['status'] = 'FAIL'
    report['error'] = traceback.format_exc()
    output()
    raise
