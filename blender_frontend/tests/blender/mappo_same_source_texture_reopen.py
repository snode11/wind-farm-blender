"""Independent-process installed .blend restoration without texture asset reload.

Start a NEW Blender process using the isolated enabled-extension profile and the
saved same-source .blend as a CLI file argument, then --python this_file. Required
environment: WFRL_ADDON_MODULE (bl_ext.*), WFRL_EXPECTED_ADDON_ROOT,
WFRL_SPLIT_INPUT (the exact CLI .blend), WFRL_EXPECTED_WINDOW_REPORT (the saving
window's PASS JSON), and WFRL_TEST_OUTPUT (a new output directory).

Example, after setting those variables and the isolated BLENDER_USER_* profile:
Blender --background --python-exit-code 1 /absolute/saved.blend --python this_file

The saved file is never written. Runtime texture state is cleared deliberately;
external texture package paths and constructors are guarded against use. Saved
embedded JSON, packed images and existing native keys must restore in place.
This is a background new-process check, separate from visible-window evidence.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import traceback

import bpy

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('same_source_reopen_helpers',
                                             HERE / 'mappo_same_source_texture_regression.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def inventory():
    """Runtime pointer identity is meaningful before/after this same-process call."""
    groups = ('objects', 'meshes', 'shape_keys', 'actions', 'materials', 'images',
              'cameras', 'texts', 'collections', 'scenes', 'worlds', 'node_groups')
    return {name: sorted((item.name, item.as_pointer()) for item in getattr(bpy.data, name))
            for name in groups}


def saved_texture_identity(validation):
    scene = validation.scene
    result = []
    for obj in validation.same.blades(scene):
        keys = obj.data.shape_keys
        assert keys and not keys.use_relative and len(keys.key_blocks) == 601
        digest = hashlib.sha256()
        for key in keys.key_blocks:
            digest.update(helpers.array_data(key.data, 'co', 3).tobytes())
        result.append(dict(name=obj.name, object_pointer=obj.as_pointer(), mesh_pointer=obj.data.as_pointer(),
                           key_pointer=keys.as_pointer(), action_pointer=keys.animation_data.action.as_pointer(),
                           shape_coordinates_sha256=digest.hexdigest(), keys=601))
    camera = validation.same.camera(scene)
    assert camera is not None
    embedded = {}
    for key in ('recon', 'texture', 'texture_source', 'provenance'):
        block = bpy.data.texts[scene['split_mappo_texture_' + key + '_text']]
        raw = validation.same._read(scene, key)
        embedded[key] = dict(name=block.name, pointer=block.as_pointer(),
                             sha256=hashlib.sha256(raw.encode()).hexdigest())
    return dict(blades=result, camera_pointer=camera.as_pointer(), camera_data_pointer=camera.data.as_pointer(),
                embedded=embedded, texture=validation.immutable_texture())


def main():
    output = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(status='RUNNING', scope='INSTALLED_BACKGROUND_INDEPENDENT_PROCESS_EMBEDDED_PACKED_REOPEN',
                  visible_window_evidence=False, solver_started=False, scene_saved=False)
    originals = None
    try:
        assert bpy.app.background
        module = os.environ['WFRL_ADDON_MODULE']
        assert module.startswith('bl_ext.'), 'This probe requires an installed extension module'
        expected_root = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT']).expanduser().resolve()
        opened = Path(bpy.data.filepath).resolve()
        expected_input = Path(os.environ['WFRL_SPLIT_INPUT']).expanduser().resolve()
        assert opened == expected_input and opened.is_file(), (opened, expected_input)
        baseline_path = Path(os.environ['WFRL_EXPECTED_WINDOW_REPORT']).expanduser().resolve()
        baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
        assert baseline['status'] == 'PASS'
        assert Path(baseline['saved']).resolve() == opened
        original_file_sha256 = helpers.sha256(opened)
        # Registration must have happened in this new process before opening
        # the CLI scene, through the enabled installed-extension profile.
        assert hasattr(bpy.types.Scene, 'wfrl_recon_show_texture'), 'Installed extension was not enabled on new-process startup'
        addon = importlib.import_module(module)
        assert Path(addon.__file__).resolve().parent == expected_root
        validation = helpers.Validation()
        scene = validation.scene
        assert validation.same.active(scene) and not validation.synthetic.active(scene)
        assert scene.get(validation.same.MARKER) and scene.get('split_texture_current_source') == 'MAPPO'
        assert scene.frame_current == 52
        original_keys = validation.geometry_identity()
        assert original_keys == baseline['original_geometry_identity']
        before_ids = inventory()
        before_saved = saved_texture_identity(validation)
        assert before_saved['texture'] == baseline['immutable_texture']
        data = importlib.import_module(module + '.blade_recon_data')
        originals = (validation.same.package_path, data.ReconSequence.__init__, data.TexturePackage.__init__)

        def forbid_external_texture_reload(*_args, **_kwargs):
            raise AssertionError('Saved same-source restoration attempted an external texture/reconstruction package reload')

        validation.same.package_path = forbid_external_texture_reload
        data.ReconSequence.__init__ = forbid_external_texture_reload
        data.TexturePackage.__init__ = forbid_external_texture_reload
        validation.same.clear_runtime()
        assert validation.same.information(scene) is None
        validation.activate()
        assert validation.value['sequence'].path is None, 'Restoration did not use the embedded reconstruction JSON'
        assert inventory() == before_ids, 'Restoration allocated or replaced saved Blender datablocks'
        assert saved_texture_identity(validation) == before_saved, 'Restoration changed saved keys, meshes, images, embedded JSON or cameras'
        assert validation.geometry_identity() == original_keys
        report.update(addon_module=module, addon_file=addon.__file__, actual_module_root=str(expected_root),
                      blender=bpy.app.version_string, opened_blend=str(opened), opened_blend_sha256=original_file_sha256,
                      baseline_window_report=str(baseline_path), baseline_module_root=baseline['actual_module_root'],
                      native_startup_source_binding=True, embedded_and_packed_identity=before_saved,
                      entire_datablock_inventory_preserved=True, external_texture_package_reload_forbidden=True,
                      restored_from_empty_runtime_cache=True, reconstruction_restored_from_embedded_json=True,
                      saved_geometry=validation.all_saved_geometry(),
                      provenance=validation.provenance())
        report['frames'] = [validation.frame(frame) for frame in (52, 55, 1, 901, 1801, 2701, 3601, 52)]
        assert inventory() == before_ids
        assert saved_texture_identity(validation) == before_saved
        assert validation.geometry_identity() == original_keys
        assert helpers.sha256(opened) == original_file_sha256
        report.update(status='PASS', frame_and_source_mapping_preserved=True,
                      saved_file_bytes_preserved=True, original_keys_not_recreated=True,
                      original_and_new_evaluated_geometry_bit_identical=True,
                      software_sync_acceptance_only=True, reconstruction_accuracy_accepted=False)
    except Exception:
        report.update(status='FAIL', error=traceback.format_exc())
        (output / 'independent-reopen-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        raise
    finally:
        if originals is not None:
            validation.same.package_path, data.ReconSequence.__init__, data.TexturePackage.__init__ = originals
    (output / 'independent-reopen-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('MAPPO_SAME_SOURCE_TEXTURE_INDEPENDENT_REOPEN_PASS', flush=True)


if __name__ == '__main__':
    main()
