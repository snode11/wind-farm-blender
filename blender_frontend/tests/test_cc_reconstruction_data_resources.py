"""Saved reconstruction validation and import-resource ownership contracts."""
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from blender_frontend.tests.test_cc_reconstruction_lifecycle import runtime, Scene
from blender_frontend.tests.test_blade_recon_data import sample_data
from wfrl_blender.blade_recon_data import MODEL_SHA256, ReconSequence


@pytest.mark.parametrize('companion', ['truth', 'cameras'])
@pytest.mark.parametrize('explicit', [False, True])
def test_present_null_companion_is_not_missing_data(tmp_path, companion, explicit):
    path = tmp_path / 'recon.json'
    path.write_text(json.dumps(sample_data()), encoding='utf-8')
    sidecar = path.with_name(companion + '.json')
    sidecar.write_text('null', encoding='utf-8')
    with pytest.raises(ValueError, match=companion):
        ReconSequence(path, **({companion + '_path': sidecar} if explicit else {}))


class Block(dict):
    def __init__(self, **attrs):
        super().__init__()
        self.use_fake_user = False
        self.users = 0
        self.__dict__.update(attrs)

    def as_pointer(self):
        return id(self)

    __hash__ = object.__hash__


@pytest.fixture
def import_scene(runtime):
    scene = Scene('blade_recon')
    scene.name = 'Review'
    scene.collection = NS(children_recursive=[], as_pointer=lambda: id(scene) + 1)
    scene.camera = scene.world = None
    original = NS(name='Original')
    window = NS(scene=original)
    context = NS(scene=original, window=window, screen=None)
    texts, removed_scenes = [], []
    failures = set()

    def new_text(name):
        def write(raw):
            raise RuntimeError('text write failed')
        text = Block(name=name, write=write)
        texts.append(text)
        return text

    def remove_text(text):
        if 'text_remove' in failures:
            raise RuntimeError('text removal failed')
        texts.remove(text)

    def remove_scene(target):
        if 'scene_remove' in failures:
            raise RuntimeError('scene removal failed')
        removed_scenes.append(target)

    class Scenes(list):
        def new(self, name):
            return scene

        def remove(self, target):
            remove_scene(target)

    runtime.bpy.data.scenes = Scenes([scene])
    runtime.bpy.data.texts = NS(new=new_text, remove=remove_text,
        get=lambda name: next((text for text in texts if text.name == name), None))
    runtime.bpy.data.materials = []
    runtime.bpy.data.images = []
    return NS(scene=scene, context=context, texts=texts, removed=removed_scenes, failures=failures)


def test_review_failed_import_reclaims_unlinked_text(runtime, import_scene, tmp_path):
    review = runtime.load('blade_recon_review')
    path = tmp_path / 'recon.json'
    path.write_text(json.dumps(sample_data()), encoding='utf-8')
    with pytest.raises(RuntimeError, match='text write failed'):
        review.load(import_scene.context, path)
    assert not import_scene.texts
    assert import_scene.removed == [import_scene.scene]
    assert import_scene.context.window.scene is import_scene.context.scene


@pytest.mark.parametrize('failure', ['text_remove', 'scene_remove'])
def test_review_import_cleanup_failure_keeps_original_error_and_retries(runtime, import_scene, tmp_path, failure):
    review = runtime.load('blade_recon_review')
    path = tmp_path / 'recon.json'
    path.write_text(json.dumps(sample_data()), encoding='utf-8')
    import_scene.failures.add(failure)
    with pytest.raises(RuntimeError, match='text write failed') as caught:
        review.load(import_scene.context, path)
    assert caught.value.__notes__
    assert review._PENDING_IMPORTS
    import_scene.failures.clear()
    review._retry_pending()
    assert not review._PENDING_IMPORTS and not import_scene.texts
    assert import_scene.removed == [import_scene.scene]


def test_review_remove_preserves_external_scene_object_and_unrelated_material(runtime, import_scene):
    review = runtime.load('blade_recon_review')
    scene = import_scene.scene
    mesh = Block(users=1, shape_keys=None, materials=[])
    obj = Block(data=mesh, users_scene=[scene, object()])
    obj['blade_recon_owned'] = True
    scene.objects.append(obj)
    removed = []
    runtime.bpy.data.objects = NS(remove=lambda obj, **kwargs: removed.append(obj))
    runtime.bpy.types.Mesh = type(mesh)
    unrelated = Block()
    unrelated['blade_recon_owned'] = True
    runtime.bpy.data.materials = [unrelated]
    review.remove(scene)
    assert removed == []
    assert runtime.bpy.data.materials == [unrelated]


def test_surface_scene_cleanup_failure_keeps_original_error_and_retries(runtime, import_scene, monkeypatch):
    import sys
    surface = runtime.load('blade_recon_surface')
    package = dict(recon_data=sample_data(), metadata=dict(model_sha256=MODEL_SHA256,
        recon_sha256='source hash', appearance_scope='captured RGB'))
    monkeypatch.setitem(sys.modules, 'blade_recon.surface_io',
                        NS(load_surface_package=lambda *args, **kwargs: package))
    import_scene.failures.add('scene_remove')
    with pytest.raises(RuntimeError, match='text write failed') as caught:
        surface.load_surface(import_scene.context, '/tmp/cc-surface-fixture.json')
    assert caught.value.__notes__ and surface._PENDING_IMPORTS
    import_scene.failures.clear()
    surface._retry_pending()
    assert not surface._PENDING_IMPORTS and not import_scene.texts
    assert import_scene.removed == [import_scene.scene]


@pytest.fixture
def saved_surface(runtime, monkeypatch):
    surface = runtime.load('blade_recon_surface')
    scene = Scene('blade_recon_surface')
    scene['surface_model_sha256'] = MODEL_SHA256
    scene['surface_recon_sha256'] = 'saved original source hash'
    contents = b'saved packed image bytes'
    metadata = dict(model_sha256=MODEL_SHA256, recon_sha256=scene['surface_recon_sha256'],
        atlas_shape=[3, 4], textures=[dict(sha256=hashlib.sha256(contents).hexdigest()) for _ in range(3)])
    monkeypatch.setattr(surface, '_embedded', lambda scene, key: metadata if key == 'sidecar' else sample_data())
    for blade in range(3):
        image = NS(packed_file=NS(data=contents), size=(4, 3))
        obj = Block(data=NS(vertices=[None] * 32, uv_layers={'template_material_q': object()},
            materials=[NS(node_tree=NS(nodes={'Image Texture': NS(image=image)}))]))
        obj.update(surface_role='estimate', surface_blade_id=blade)
        scene.objects.append(obj)
    return NS(module=surface, scene=scene, metadata=metadata)


@pytest.mark.parametrize('fault', ['duplicate_blade', 'wrong_image', 'wrong_size', 'wrong_source_hash'])
def test_saved_surface_rejects_inconsistent_binding(saved_surface, fault):
    surface, scene = saved_surface.module, saved_surface.scene
    image = scene.objects[1].data.materials[0].node_tree.nodes['Image Texture'].image
    if fault == 'duplicate_blade':
        scene.objects[1]['surface_blade_id'] = 0
    elif fault == 'wrong_image':
        image.packed_file.data = b'other texture'
    elif fault == 'wrong_size':
        image.size = (3, 4)
    else:
        scene['surface_recon_sha256'] = 'different source'
    with pytest.raises(ValueError):
        surface.restore(scene)
    assert not surface.SESSIONS


def test_saved_surface_valid_binding_restores(saved_surface):
    saved_surface.module.restore(saved_surface.scene)
    assert saved_surface.scene.as_pointer() in saved_surface.module.SESSIONS


def test_review_restore_rejects_duplicate_blade_identity(runtime, monkeypatch):
    review = runtime.load('blade_recon_review')
    scene = Scene('blade_recon')
    raw = json.dumps(sample_data())
    scene['blade_recon_sha256'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    scene['blade_recon_model_sha256'] = MODEL_SHA256
    monkeypatch.setattr(review, '_read_embedded', lambda scene, key: raw if key == 'recon' else None)
    for blade in (0, 0, 2):
        obj = Block(data=NS(vertices=[None] * 32))
        obj.update(blade_recon_role='estimate', blade_recon_blade=blade)
        scene.objects.append(obj)
    with pytest.raises(ValueError, match='叶片'):
        review._restore(scene)
    assert not review.SESSIONS
