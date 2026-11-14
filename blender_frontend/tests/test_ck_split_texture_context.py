"""Host contracts for saved atlases, scene bindings and local inspection."""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from wfrl_blender import split_mappo_texture as same
from wfrl_blender import split_surface_texture as synthetic
from wfrl_blender import split_turbine_context as turbine


class ID(dict):
    __hash__ = object.__hash__

    def __init__(self, **attributes):
        super().__init__()
        self.__dict__.update(attributes)

    def as_pointer(self):
        return id(self)

    def __bool__(self):
        return True

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


class Pixels:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=np.float32)

    def __len__(self):
        return self.values.size

    def foreach_get(self, output):
        output[:] = self.values.ravel()


@pytest.fixture(params=[same, synthetic], ids=['mappo', 'synthetic'])
def saved_texture(request, monkeypatch, stub_module):
    adapter = request.param
    stem = 'split_mappo_texture' if adapter is same else 'split_texture'
    scene = ID(name='Saved texture', session_uid=17, frame_current=1, frame_subframe=0.)
    scene.objects = {}
    scene[adapter.MARKER] = True
    scene[stem + '_model_sha256'] = 'model'
    scene[stem + '_camera_name'] = adapter.CAMERA
    scene.objects[adapter.CAMERA] = ID(type='CAMERA', name=adapter.CAMERA)
    sequence = NS(model_sha256='model', states=[0, 1], times=np.array([0., .1]),
                  source_frames=np.array([0, 1]), fps=10., source_label='test source',
                  rotor=NS(cfg=NS(n_sections=40, n_ring=32)))
    raw_recon = '{}'
    records, blades = [], []
    for blade in range(3):
        content = b'saved RGBA atlas'
        image = ID(size=(2, 1), channels=4, packed_file=NS(data=content),
                   pixels=Pixels([[.5, .5, .5, 1.], [.5, .5, .5, 0.]]))
        texture = ID(image=image)
        material = ID(use_nodes=True, node_tree=ID(nodes={'SurfaceTexture': texture}))
        mesh = ID(vertices=[None] * 1280, materials=[material],
                  uv_layers={'atlas': ID()}, color_attributes={'evidence': ID(data=[None] * 1280)},
                  shape_keys=ID(use_relative=False, key_blocks=[None] * 601, animation_data=NS()))
        obj = ID(name=adapter.PREFIX + f'B{blade + 1}', type='MESH', data=mesh)
        obj[stem + '_blade'] = blade
        scene.objects[obj.name] = obj
        blades.append(obj)
        records.append(dict(blade=blade, name=f'blade{blade + 1}_tex.png',
                            sha256=hashlib.sha256(content).hexdigest()))
    scene[stem + '_blade_names'] = [obj.name for obj in blades]
    manifest = dict(atlas=dict(W=2, H=1), files=records)
    source = json.dumps(dict(atlas=manifest['atlas']))
    provenance = dict(source_label='test source', files={
        'recon.json': dict(sha256=hashlib.sha256(raw_recon.encode()).hexdigest()),
        'texture/texture.json': dict(sha256=hashlib.sha256(source.encode()).hexdigest()),
        **{'texture/' + row['name']: dict(sha256=row['sha256']) for row in records},
    })
    texts = {}

    def embed(key, raw):
        block = ID(raw=raw)
        block.as_string = lambda: block.raw
        texts[key] = block
        scene[stem + '_' + key + '_text'] = key
        scene[stem + '_' + key + '_sha256'] = hashlib.sha256(raw.encode()).hexdigest()

    embed('recon', raw_recon)
    embed('texture', json.dumps(manifest))
    embed('texture_source', source)
    embed('provenance', json.dumps(provenance))
    stub_module('bpy', data=NS(texts=texts))
    stub_module('wfrl_blender.blade_recon_data',
                ReconSequence=NS(from_data=lambda *_a, **_kw: sequence),
                TexturePackage=object, detail_recon_path=lambda: None, detail_texture_path=lambda: None)
    stub_module('wfrl_blender.blade_recon_review')
    monkeypatch.setattr(adapter, '_SESSIONS', {})
    monkeypatch.setattr(adapter, '_PENDING_IMPORTS', {})
    if adapter is same:
        monkeypatch.setattr(same, '_validate_source', lambda *_a, **_kw: None)
        monkeypatch.setattr(same, '_validate_clock', lambda *_a: None)
        monkeypatch.setattr(same, '_source_signature', lambda *_a: ())
        monkeypatch.setattr(same, '_ensure_structure', lambda *_a, **_kw: [])
    return NS(adapter=adapter, stem=stem, scene=scene, blades=blades,
              manifest=manifest, embed=embed, sequence=sequence, texts=texts)


@pytest.mark.parametrize('damage', ['order', 'duplicate', 'tag', 'record'])
def test_saved_blade_identity_is_validated_before_restore(saved_texture, damage):
    f = saved_texture
    if damage == 'order':
        f.scene[f.stem + '_blade_names'][:2] = f.scene[f.stem + '_blade_names'][1::-1]
        f.manifest['files'][:2] = f.manifest['files'][1::-1]
    elif damage == 'duplicate':
        f.scene[f.stem + '_blade_names'][1] = f.scene[f.stem + '_blade_names'][0]
    elif damage == 'tag':
        f.blades[0][f.stem + '_blade'] = 2
    else:
        f.manifest['files'][0]['blade'] = True
    f.embed('texture', json.dumps(f.manifest))
    with pytest.raises(ValueError, match='叶片'):
        f.adapter._restore(f.scene)
    assert not f.adapter._SESSIONS


@pytest.mark.parametrize('damage', ['channels', 'empty', 'nan', 'inf', 'negative_inf'])
def test_saved_atlas_rejects_invalid_decoded_pixels(saved_texture, damage):
    f = saved_texture
    image = f.blades[0].data.materials[0].node_tree.nodes['SurfaceTexture'].image
    if damage == 'channels':
        image.channels = 3
    elif damage == 'empty':
        image.pixels = Pixels([])
    else:
        image.pixels.values[0, 0] = {'nan': np.nan, 'inf': np.inf, 'negative_inf': -np.inf}[damage]
    with pytest.raises(ValueError, match='纹理|图像'):
        f.adapter._restore(f.scene)
    assert not f.adapter._SESSIONS


@pytest.mark.parametrize('alpha', [0., 1.])
def test_constant_atlas_and_empty_observation_mask_are_preserved(saved_texture, alpha):
    f = saved_texture
    images = [obj.data.materials[0].node_tree.nodes['SurfaceTexture'].image for obj in f.blades]
    for image in images:
        image.pixels.values[:, 3] = alpha
    before = [image.pixels.values.copy() for image in images]
    value = f.adapter._restore(f.scene)
    assert value['blades'] == f.blades
    for image, expected in zip(images, before):
        np.testing.assert_array_equal(image.pixels.values, expected)


@pytest.mark.parametrize('change', ['object', 'mesh', 'text', 'scene_uid'])
@pytest.mark.parametrize('saved_texture', [pytest.param(synthetic, id='synthetic')], indirect=True)
def test_synthetic_ensure_revalidates_replaced_bindings(saved_texture, monkeypatch, change):
    f = saved_texture
    original = synthetic._restore(f.scene)
    original['index'] = 1
    monkeypatch.setattr(synthetic, '_activate', lambda _scene, value, **_kw: value)
    if change == 'object':
        obj = copy.copy(f.blades[0])
        obj.__dict__ = f.blades[0].__dict__.copy()
        f.scene.objects[obj.name] = obj
    elif change == 'mesh':
        f.blades[0].data = copy.copy(f.blades[0].data)
    elif change == 'text':
        f.embed('recon', '{}')
    else:
        f.scene.session_uid += 1
    refreshed = synthetic.ensure(f.scene)
    assert refreshed is not original
    assert refreshed['index'] is None
    assert refreshed['blades'][0] is f.scene.objects[f.scene[f.stem + '_blade_names'][0]]


@pytest.mark.parametrize('saved_texture', [pytest.param(synthetic, id='synthetic')], indirect=True)
def test_stable_synthetic_bindings_do_not_reparse_or_reallocate(saved_texture, monkeypatch):
    f = saved_texture
    value = synthetic._restore(f.scene)
    monkeypatch.setattr(synthetic, '_activate', lambda _scene, current, **_kw: current)

    def unexpected_restore(_scene):
        raise AssertionError('Stable bindings must reuse the validated session')

    monkeypatch.setattr(synthetic, '_restore', unexpected_restore)
    assert synthetic.ensure(f.scene) is value
    assert synthetic.ensure(f.scene) is value


@pytest.mark.parametrize('saved_texture', [pytest.param(synthetic, id='synthetic')], indirect=True)
def test_synthetic_ensure_rejects_removed_blade(saved_texture, monkeypatch):
    f = saved_texture
    synthetic._restore(f.scene)
    monkeypatch.setattr(synthetic, '_activate', lambda _scene, value, **_kw: value)
    del f.scene.objects[f.blades[0].name]
    with pytest.raises(ValueError, match='网格或相机缺失'):
        synthetic.ensure(f.scene)


class Direction:
    x, y, z = 0., 0., 1.

    def cross(self, _other):
        return self

    def normalized(self):
        return self

    def __iter__(self):
        return iter((self.x, self.y, self.z))


def area():
    return NS(type='VIEW_3D', spaces=NS(active=NS(
        region_3d=NS(view_perspective='PERSP', view_distance=100.), region_quadviews=[],
        show_region_ui=False, lens=35., clip_start=.1, clip_end=1000.,
        shading=NS(type='SOLID'), overlay=NS(show_floor=True, show_axis_x=True, show_axis_y=True))),
        tag_redraw=lambda: None)


@pytest.mark.parametrize('caller_type', ['VIEW_3D', 'PROPERTIES', None])
def test_inspection_focus_only_changes_calling_3d_area(frontend_module, monkeypatch, stub_module, caller_type):
    inspection = frontend_module('wfrl_blender.blade_recon_inspection')
    first, second = area(), area()
    caller = first if caller_type == 'VIEW_3D' else NS(type=caller_type) if caller_type else None
    scene = NS(frame_current=1)
    context = NS(scene=scene, area=caller, screen=NS(areas=[first, second]))
    sequence = NS(rotor=NS(), states=[0])
    stub_module('wfrl_blender.playback', cancel=lambda _scene: None)
    monkeypatch.setattr(inspection, '_coordinates', lambda *_a: (0, 3., .5, 2.))
    monkeypatch.setattr(inspection, '_surface_sample', lambda *_a: (Direction(), Direction(), Direction()))
    monkeypatch.setattr(inspection, 'Matrix', lambda *_a: NS(to_quaternion=lambda: (1., 0., 0., 0.)))
    monkeypatch.setattr(inspection, 'update', lambda *_a: None)
    result = inspection.focus(context, sequence, [object()])
    assert result['views'] == int(caller_type == 'VIEW_3D')
    assert second.spaces.active.region_3d.view_perspective == 'PERSP'
    assert second.spaces.active.lens == 35.
    if caller_type == 'VIEW_3D':
        assert first.spaces.active.region_3d.view_perspective == 'ORTHO'
        assert first.spaces.active.region_3d.view_distance == 2.7
    else:
        assert first.spaces.active.region_3d.view_perspective == 'PERSP'


class Objects(dict):
    def __iter__(self):
        return iter(self.values())


@pytest.mark.parametrize('legacy,saved', [(False, True), (True, False), (True, True)])
def test_shared_turbine_display_objects_are_rejected_before_mutation(legacy, saved, stub_module):
    scene, other = ID(), ID()
    scene.objects = Objects()
    other.objects = Objects()
    stub_module('bpy', data=NS(scenes=[scene, other]))
    prefix, names_key = 'SplitRecon.Reference.', 'split_reconstruction_structure_names'
    sources = {part: ID(name=turbine.SOURCE_PREFIX + part) for part in turbine._CORE}
    names = []
    for part in turbine._LEGACY if legacy else turbine._CORE:
        obj = ID(name=prefix + part, type='MESH', users_scene=[scene, other])
        if legacy:
            obj.update(blade_recon_owned=True, split_display_offset_y=10000.)
        else:
            obj.update(split_turbine_context_classification=turbine.CLASSIFICATION,
                       split_turbine_context_component=part,
                       split_turbine_context_source=sources[part].name)
        scene.objects[obj.name] = obj
        other.objects[obj.name] = obj
        names.append(obj.name)
    if saved:
        scene[names_key] = names
    with pytest.raises(ValueError, match='共享|其他场景'):
        turbine._existing(scene, prefix, names_key, sources)


def test_image_rollback_preserves_shared_and_fake_user_images(stub_module):
    scene = ID(session_uid=9, collection=ID(children_recursive=[]))
    resources = same._ImportResources(scene)
    images = [ID(users=0, use_fake_user=False), ID(users=1, use_fake_user=False),
              ID(users=1, use_fake_user=True)]
    for image in images:
        resources.register('images', image)
    removed = []
    stub_module('bpy', data=NS(images=NS(remove=removed.append)))
    assert resources.rollback() == []
    assert removed == images[:1]
    assert len(resources.records) == 2


@pytest.fixture(params=turbine._SAVED_GROUPS, ids=['reconstruction', 'mappo'])
def cached_turbine_context(request, monkeypatch, stub_module):
    scene, other = ID(objects=Objects()), ID(objects=Objects())
    names_key, prefix, offset = request.param
    pairs = []
    for part in turbine._CORE:
        source = ID(name=turbine.SOURCE_PREFIX + part, type='MESH', data=ID())
        obj = ID(name=prefix + part, type='MESH', data=source.data, parent=None,
                 hide_viewport=False, hide_render=False, users_scene=[scene])
        turbine._tag(obj, source, part, prefix)
        scene.objects[source.name] = source
        scene.objects[obj.name] = obj
        pairs.append((source, obj))
    yaw = ID(name=turbine.SOURCE_PREFIX + 'YawRoot', type='EMPTY')
    scene.objects[yaw.name] = yaw
    objects = [obj for _, obj in pairs]
    scene[names_key] = [obj.name for obj in objects]
    scene[names_key + '_offset'] = offset
    group = dict(scene=scene, prefix=prefix, names_key=names_key, offset=offset,
                 names=tuple(scene[names_key]), object_count=len(scene.objects), yaw=yaw, pairs=pairs,
                 bindings=[(source, obj, source.name, obj.name, source.data) for source, obj in pairs])
    data = NS(scenes=[scene, other])
    stub_module('bpy', data=data)
    monkeypatch.setattr(turbine, '_SESSIONS', {scene.as_pointer(): {names_key: group}})
    return NS(scene=scene, other=other, objects=objects, group=group, data=data,
              spec=dict(prefix=prefix, offset=offset, names_key=names_key))


def test_stable_context_refresh_does_not_scan_scene_ownership(cached_turbine_context):
    f = cached_turbine_context

    class Unscannable:
        def __iter__(self):
            raise AssertionError('Stable refresh must not scan scene ownership')

    f.data.scenes = Unscannable()
    for obj in f.objects:
        obj.users_scene = Unscannable()
    for _ in range(20):
        assert turbine.refresh(f.scene, **f.spec) == f.objects
    assert not any(obj.hide_viewport or obj.hide_render for obj in f.objects)


@pytest.mark.parametrize('operation', ['ensure', 'refresh', 'rebind_meshes'])
def test_rebuilt_context_rejects_new_cross_scene_link(cached_turbine_context, operation):
    f = cached_turbine_context
    obj = f.objects[0]
    f.other.objects[obj.name] = obj
    obj.users_scene.append(f.other)
    obj.hide_viewport = obj.hide_render = True
    f.group['dirty'] = True
    del f.data.scenes
    replacements = []
    with pytest.raises(ValueError, match='其他场景'):
        if operation == 'rebind_meshes':
            turbine.rebind_meshes(f.scene, NS(replace=lambda *args: replacements.append(args)))
        else:
            getattr(turbine, operation)(f.scene, **f.spec)
    assert not replacements
    assert obj.hide_viewport and obj.hide_render
    assert f.scene[f.spec['names_key']] == [current.name for current in f.objects]
    assert turbine._SESSIONS[f.scene.as_pointer()][f.spec['names_key']] is f.group


@pytest.mark.parametrize('retention', ['fake_user', 'external_id', None])
def test_legacy_context_removal_protects_user_retained_objects(retention, stub_module):
    collection = ID(children_recursive=[])
    scene = ID(objects=Objects(), collection=collection)
    prefix, names_key = 'SplitRecon.Reference.', 'split_reconstruction_structure_names'
    sources = {part: ID(name=turbine.SOURCE_PREFIX + part) for part in turbine._CORE}
    for part in turbine._LEGACY:
        obj = ID(name=prefix + part, type='MESH', use_fake_user=False)
        obj.update(blade_recon_owned=True, split_display_offset_y=10000.)
        scene.objects[obj.name] = obj
    held = scene.objects[prefix + 'Tower']
    held.use_fake_user = retention == 'fake_user'
    foreign = ID()

    def user_map(*, subset):
        return {obj: {collection, foreign} if obj is held and retention == 'external_id' else {collection}
                for obj in subset}

    stub_module('bpy', data=NS(scenes=[scene], user_map=user_map))
    if retention:
        with pytest.raises(ValueError, match='保留|引用'):
            turbine._existing(scene, prefix, names_key, sources)
    else:
        existing, legacy = turbine._existing(scene, prefix, names_key, sources)
        assert not existing and legacy == list(scene.objects.values())


@pytest.mark.parametrize('field', ['radius', 'tau', 'size'])
@pytest.mark.parametrize('invalid', [np.nan, np.inf, -np.inf])
def test_nonfinite_inspection_coordinates_are_rejected(frontend_module, field, invalid):
    inspection = frontend_module('wfrl_blender.blade_recon_inspection')
    scene = NS(**{'wfrl_recon_inspect_' + field: invalid})
    rotor = NS(tpl=NS(r=np.array([1.5, 63.])))
    with pytest.raises(ValueError, match='有限'):
        inspection._coordinates(scene, rotor)


@pytest.mark.parametrize('field', ['gain', 'threshold'])
@pytest.mark.parametrize('invalid', [np.nan, np.inf, -np.inf])
def test_nonfinite_inspection_color_settings_do_not_commit(frontend_module, field, invalid):
    inspection = frontend_module('wfrl_blender.blade_recon_inspection')
    scene = ID(**{'wfrl_recon_texture_' + field: invalid})
    rotor = NS(tpl=NS(r=np.array([1.5, 63.]), chord=np.ones(2),
                      foils=np.ones((2, 4, 2))))
    with pytest.raises(ValueError, match='有限'):
        inspection.update(scene, [], rotor)
    assert 'blade_recon_inspection_scope' not in scene


def test_valid_inspection_coordinate_clamps_and_periodicity_are_unchanged(frontend_module):
    inspection = frontend_module('wfrl_blender.blade_recon_inspection')
    scene = NS(wfrl_recon_inspect_blade='3', wfrl_recon_inspect_radius=80.,
               wfrl_recon_inspect_tau=-.25, wfrl_recon_inspect_size=.1)
    rotor = NS(tpl=NS(r=np.array([1.5, 63.])))
    assert inspection._coordinates(scene, rotor) == (2, 63., .75, .3)
