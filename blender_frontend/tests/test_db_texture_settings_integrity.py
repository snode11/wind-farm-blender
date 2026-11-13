"""Corrupt saved display choices cannot reach texture rendering callbacks."""
import json
import math
import struct

import pytest

from wfrl_blender import split_texture_settings as settings


DEFAULTS = dict(wfrl_recon_show_texture=True, wfrl_recon_texture_mode='ORIGINAL',
                wfrl_recon_texture_gain=6., wfrl_recon_texture_threshold=.06,
                wfrl_recon_inspect_blade='1', wfrl_recon_inspect_radius=59.3,
                wfrl_recon_inspect_tau=.6, wfrl_recon_inspect_size=2., wfrl_recon_inspect_box=False)


class Scene(dict):
    def __init__(self):
        super().__init__(split_texture_current_source='SYNTHETIC')
        self.__dict__.update(DEFAULTS)
        self.wfrl_recon_texture_mode = 'CONTRAST'


@pytest.mark.parametrize('field,value', [
    ('wfrl_recon_texture_gain', math.nan), ('wfrl_recon_texture_gain', math.inf),
    ('wfrl_recon_texture_gain', -math.inf), ('wfrl_recon_texture_gain', '6'),
    ('wfrl_recon_texture_gain', True), ('wfrl_recon_texture_gain', 0.),
    ('wfrl_recon_texture_threshold', .4), ('wfrl_recon_texture_threshold', 0.),
    ('wfrl_recon_inspect_radius', -1.), ('wfrl_recon_inspect_radius', 1001.),
    ('wfrl_recon_inspect_tau', 1.01), ('wfrl_recon_inspect_size', 0.),
    ('wfrl_recon_texture_mode', []), ('wfrl_recon_inspect_blade', '4'),
    ('wfrl_recon_show_texture', 'false'), ('wfrl_recon_inspect_box', None),
])
def test_invalid_saved_texture_record_uses_complete_defaults_before_commit(field, value):
    scene = Scene()
    saved = dict(DEFAULTS, wfrl_recon_texture_gain=11.)
    saved[field] = value
    scene['split_texture_display_mappo'] = json.dumps({'version': 1, 'values': saved})
    seen = []
    settings.activate(scene, 'MAPPO', DEFAULTS,
                      lambda: seen.append(settings.capture(scene)))
    assert seen == [DEFAULTS]
    assert settings.capture(scene) == DEFAULTS
    assert json.loads(scene['split_texture_display_mappo'])['values'] == DEFAULTS


def test_saved_texture_values_must_be_an_object_even_when_keys_match():
    scene = Scene()
    scene['split_texture_display_mappo'] = json.dumps({'version': 1, 'values': list(settings.FIELDS)})
    seen = []
    settings.activate(scene, 'MAPPO', DEFAULTS, lambda: seen.append(settings.capture(scene)))
    assert seen == [DEFAULTS]


@pytest.mark.parametrize('threshold', [.005, .3])
def test_saved_texture_rna_float_boundary_keeps_user_choices(threshold):
    scene = Scene()
    saved = dict(DEFAULTS, wfrl_recon_texture_gain=11.,
                 wfrl_recon_texture_threshold=struct.unpack('f', struct.pack('f', threshold))[0])
    scene['split_texture_display_mappo'] = json.dumps({'version': 1, 'values': saved})
    seen = []
    settings.activate(scene, 'MAPPO', DEFAULTS, lambda: seen.append(settings.capture(scene)))
    assert seen == [saved]
