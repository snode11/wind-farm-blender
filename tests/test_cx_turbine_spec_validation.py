"""Turbine dimension validation and unchanged NREL rendering inputs."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from wfrl.turbines import TurbineSpec, TurbineSpecError, load_turbine_spec


SPEC = dict(name='fixture', reference=dict(hub_height=90., rotor_diameter=126., tower_top=87.6),
            shell=dict(nacelle=dict(length=8.03, width=3.3, height=3.3),
                       spinner_radius=2.4, nacelle_x_bias=.15),
            mounts=dict(camera=dict(dx_down=1.506, dy_lat=0., dz_vert=-1.89)))
DIMENSIONS = ('shell.nacelle.length', 'shell.nacelle.width', 'shell.nacelle.height',
              'shell.spinner_radius', 'reference.hub_height', 'reference.rotor_diameter',
              'reference.tower_top')
OFFSETS = ('shell.nacelle_x_bias', 'mounts.camera.dx_down', 'mounts.camera.dy_lat',
           'mounts.camera.dz_vert')


def load_changed(tmp_path, field, value):
    data = deepcopy(SPEC)
    keys = field.split('.')
    target = data
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    path = tmp_path / 'turbine.spec'
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    return load_turbine_spec(str(path))


@pytest.mark.parametrize('field', DIMENSIONS)
@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), 0., -1., True, [], 'bad'])
def test_dimensions_reject_nonfinite_nonpositive_and_invalid_types(tmp_path, field, value):
    with pytest.raises(TurbineSpecError, match=field):
        load_changed(tmp_path, field, value)


@pytest.mark.parametrize('field', OFFSETS)
@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), True, None, [], 'bad'])
def test_signed_offsets_require_finite_numbers(tmp_path, field, value):
    with pytest.raises(TurbineSpecError, match=field):
        load_changed(tmp_path, field, value)


@pytest.mark.parametrize('field', ['shell', 'shell.nacelle', 'reference', 'mounts', 'mounts.camera'])
@pytest.mark.parametrize('value', [[], False, 5, 'bad'])
def test_optional_sections_must_be_mappings(tmp_path, field, value):
    with pytest.raises(TurbineSpecError, match=field):
        load_changed(tmp_path, field, value)


@pytest.mark.parametrize('field', DIMENSIONS + OFFSETS)
def test_unrepresentable_numeric_values_have_field_errors(tmp_path, field):
    with pytest.raises(TurbineSpecError, match=field):
        load_changed(tmp_path, field, 10 ** 400)


@pytest.mark.parametrize('hub_rad,overhang', [(float('nan'), -5.), (float('inf'), -5.),
                                           (-1., -5.), (0., -5.), (1., float('nan')),
                                           (1., float('inf')), (1., 0.)])
def test_nacelle_fallback_rejects_invalid_geometry(hub_rad, overhang):
    with pytest.raises(TurbineSpecError, match='shell.nacelle'):
        TurbineSpec('fallback').nacelle_dims(SimpleNamespace(hub_rad=hub_rad, overhang=overhang))


@pytest.mark.parametrize('hub_rad', [float('nan'), float('inf'), -1., 0.])
def test_spinner_fallback_rejects_invalid_geometry(hub_rad):
    with pytest.raises(TurbineSpecError, match='shell.spinner_radius'):
        TurbineSpec('fallback').spinner_radius(SimpleNamespace(hub_rad=hub_rad))


def test_missing_dimensions_keep_documented_fallback_and_require_geometry():
    spec = TurbineSpec('fallback', shell={'nacelle': {'width': 4.}})
    geo = SimpleNamespace(overhang=-5.019, hub_rad=1.5)
    assert spec.nacelle_dims(geo) == (abs(geo.overhang) * 1.6, 4., geo.hub_rad * 2.2)
    assert spec.spinner_radius(geo) == geo.hub_rad * 1.6
    assert spec.nacelle_x_bias() == .15
    assert spec.mount_offset('absent') is None
    with pytest.raises(TurbineSpecError, match='geo'):
        spec.nacelle_dims()
    with pytest.raises(TurbineSpecError, match='geo'):
        spec.spinner_radius()


def test_nrel_source_hash_and_every_rendering_value_are_unchanged():
    spec = load_turbine_spec('nrel5mw')
    assert hashlib.sha256(Path(spec.source_path).read_bytes()).hexdigest() == (
        '2224977994b6392c44fcdad6cd9b72323b9f41ceef9c4fbb3cea8230c7919460')
    assert spec.reference == dict(hub_height=90., rotor_diameter=126., tower_top=87.6)
    assert spec.nacelle_dims() == (8.03, 3.3, 3.3)
    assert spec.spinner_radius() == 2.4
    assert spec.nacelle_x_bias() == .15
    assert spec.mount_names() == ['clearance_cam', 'hub_bottom', 'rotor_front']
    assert spec.mount_offset('clearance_cam') == (1.506, 0., -1.89)
    assert spec.mount_offset('rotor_front') == (-5.019, 0., -1.89)
    assert spec.mount_offset('hub_bottom') == (0., 0., -1.89)
