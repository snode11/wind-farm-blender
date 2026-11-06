"""Layout import validates mounting geometry before any camera is staged."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_cameras as core, stacked_camera_rig as rig


@pytest.fixture
def surface_layout(monkeypatch):
    scene = NS(objects={})
    params = core.CameraParameters(location=(0., 0., .1))
    anchor = core.SurfaceAnchor((0., 0., 0.), (0., 0., 1.), .1)
    payload = dict(schema_version=core.SCHEMA_VERSION, coordinate_frame=core.COORDINATE_VERSION,
                   model_signature='shell', cameras=[dict(
                       slot_id=1, installed=True, enabled=True, label='C1', parameters=asdict(params),
                       mount_mode='SURFACE', mount_validation_status='surface_validated',
                       research_confirmed=False, nearest_distance_m=None,
                       anchor=asdict(anchor), image_width_px=1920, image_height_px=1080)])
    monkeypatch.setattr(core, 'model_signature', lambda _scene: 'shell')
    monkeypatch.setattr(core, 'validate_position', lambda *_: [anchor])
    return scene, payload


@pytest.mark.parametrize('changes', [
    {'normal': (0., 0., 2.)}, {'normal': (0., 0., 1., 0.)},
    {'point': (.0002, 0., 0.)}, {'normal': (.001, 0., 1.)},
])
def test_import_rejects_anchor_that_cannot_reproduce_optical_centre(surface_layout, changes):
    scene, payload = surface_layout
    payload['cameras'][0]['anchor'].update(changes)
    before = deepcopy(payload)
    with pytest.raises(ValueError, match='锚点'):
        core.validate_layout(scene, payload)
    assert payload == before


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
def test_import_rejects_nonfinite_image_metadata(surface_layout, value):
    scene, payload = surface_layout
    payload['cameras'][0]['image_width_px'] = value
    with pytest.raises(ValueError):
        core.validate_layout(scene, payload)


def test_layout_export_rejects_nonfinite_mount_metadata(surface_layout, monkeypatch):
    scene, payload = surface_layout
    camera = dict(custom_nearest_distance=math.nan, custom_mount_mode='RESEARCH')
    monkeypatch.setattr(core, 'migrate_scene', lambda _scene: None)
    monkeypatch.setattr(core, 'get_camera', lambda _scene, slot: camera if slot == 1 else None)
    monkeypatch.setattr(core, 'parameters', lambda _: core.CameraParameters((0., 0., .1)))
    monkeypatch.setattr(core, 'anchor', lambda _: None)
    with pytest.raises(ValueError):
        core.layout_dict(scene)


def test_valid_layout_and_preset_hash_keep_exact_serialized_values(surface_layout):
    scene, payload = surface_layout
    before = deepcopy(payload)
    assert len(core.validate_layout(scene, payload)) == 1
    assert payload == before
    preset = rig._default_layout()
    expected = '538c874543ff19932e3f228a59a7a311e6363d57cc0569b0da238384e8a5a7cf'
    assert core.layout_hash(preset) == expected
    assert core.layout_hash(json.loads(json.dumps(preset, allow_nan=False))) == expected
    changed = deepcopy(preset)
    changed['cameras'][0]['parameters']['location'][0] += .000001
    assert core.layout_hash(changed) != expected
