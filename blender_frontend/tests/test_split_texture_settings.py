"""Source display isolation and failure rollback without a Blender window."""
import copy
import json
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import split_texture_settings as settings
from wfrl_blender import split_surface_texture as synthetic
from wfrl_blender import split_mappo_texture as same


class Scene(dict):
    def __init__(self, source='MAPPO'):
        super().__init__(split_texture_current_source=source)
        for name, value in same._display_defaults({}).items():
            setattr(self, name, value)

    def as_pointer(self):
        return 9901


def defaults():
    return same._display_defaults(dict(inspection_default=dict(blade=2, radius_m=47.225, tau=.8216, size_m=2.)))


def change(scene, source, *, new=False, commit=lambda: True):
    return settings.activate(scene, source, defaults() if source == 'MAPPO' else synthetic.DISPLAY_DEFAULTS,
                             commit, new=new)


def test_roundtrip_restores_all_each_source_user_display_choices():
    scene = Scene()
    scene.wfrl_recon_texture_mode = 'CONTRAST'
    scene.wfrl_recon_inspect_blade = '3'
    scene.wfrl_recon_inspect_radius = 48.
    scene.wfrl_recon_inspect_tau = .74
    scene.wfrl_recon_inspect_size = 4.
    scene.wfrl_recon_texture_gain = 12.
    scene.wfrl_recon_texture_threshold = .12
    scene.wfrl_recon_show_texture = False
    scene.wfrl_recon_inspect_box = False
    expected_mappo = settings.capture(scene)
    change(scene, 'SYNTHETIC', new=True)
    assert settings.capture(scene) == synthetic.DISPLAY_DEFAULTS
    scene.wfrl_recon_texture_mode = 'ORIGINAL'
    scene.wfrl_recon_inspect_blade = '2'
    scene.wfrl_recon_inspect_radius = 51.
    scene.wfrl_recon_texture_gain = 8.
    expected_synthetic = settings.capture(scene)
    for _ in range(4):
        change(scene, 'MAPPO')
        assert settings.capture(scene) == expected_mappo
        change(scene, 'SYNTHETIC')
        assert settings.capture(scene) == expected_synthetic


def test_current_and_exit_return_preserve_unsaved_latest_choices():
    scene = Scene()
    change(scene, 'MAPPO')
    scene.wfrl_recon_inspect_tau = .29
    scene.wfrl_recon_texture_mode = 'CONTRAST'
    scene['split_review_enabled'] = False
    expected = settings.capture(scene)
    change(scene, 'MAPPO')
    assert settings.capture(scene) == expected
    assert not scene['split_review_enabled']


def test_old_saved_file_preserves_active_values_and_defaults_inactive_source():
    scene = Scene()
    scene.wfrl_recon_inspect_radius = 44.
    scene.wfrl_recon_texture_mode = 'CONTRAST'
    expected = settings.capture(scene)
    change(scene, 'MAPPO')
    assert settings.capture(scene) == expected
    change(scene, 'SYNTHETIC')
    assert settings.capture(scene) == synthetic.DISPLAY_DEFAULTS
    change(scene, 'MAPPO')
    assert settings.capture(scene) == expected
    # Older shared-property contamination cannot display unlabelled false colour.
    scene.wfrl_recon_texture_mode = 'FALSE_COLOR'
    change(scene, 'MAPPO')
    assert scene.wfrl_recon_texture_mode == 'ORIGINAL'
    assert scene.wfrl_recon_inspect_radius == 44.


def test_old_synthetic_marker_without_selector_migrates_active_choices():
    scene = Scene('SYNTHETIC')
    del scene[settings.CURRENT]
    scene[synthetic.MARKER] = True
    scene.wfrl_recon_texture_mode = 'FALSE_COLOR'
    scene.wfrl_recon_inspect_tau = .22
    before = settings.capture(scene)
    change(scene, 'SYNTHETIC')
    assert settings.capture(scene) == before
    change(scene, 'MAPPO')
    assert settings.capture(scene) == defaults()
    change(scene, 'SYNTHETIC')
    assert settings.capture(scene) == before


def test_saved_records_restore_inactive_choices_after_session_cache_reset():
    scene = Scene()
    scene.wfrl_recon_inspect_radius = 45.
    expected = settings.capture(scene)
    change(scene, 'SYNTHETIC', new=True)
    reopened = copy.deepcopy(scene)
    assert json.loads(reopened['split_texture_display_mappo'])['version'] == 1
    change(reopened, 'MAPPO')
    assert settings.capture(reopened) == expected


def test_failed_apply_restores_selector_display_and_persistent_records():
    scene = Scene()
    change(scene, 'MAPPO')
    before_display, before_metadata = settings.capture(scene), dict(scene)
    seen = []
    def fail():
        seen.append(settings.capture(scene))
        raise ValueError('target shader failed')
    with pytest.raises(ValueError, match='shader'):
        change(scene, 'SYNTHETIC', new=True, commit=fail)
    assert seen == [synthetic.DISPLAY_DEFAULTS]
    assert settings.capture(scene) == before_display
    assert dict(scene) == before_metadata


@pytest.mark.parametrize('detail', [None, False, True])
def test_failed_layout_rolls_back_already_committed_source_and_display(detail):
    scene = Scene()
    scene['split_review_enabled'] = True
    scene['split_review_status'] = 'READY'
    if detail is not None:
        scene[same.DETAIL] = detail
    before_display, before_metadata = settings.capture(scene), dict(scene)
    with pytest.raises(RuntimeError, match='layout'):
        with settings.rollback_on_error(scene):
            change(scene, 'SYNTHETIC', new=True)
            scene[same.DETAIL] = False
            scene['split_review_status'] = 'PREPARING'
            raise RuntimeError('layout failed')
    assert settings.capture(scene) == before_display
    assert dict(scene) == before_metadata


def test_existing_synthetic_restore_failure_does_not_switch_current_source(monkeypatch):
    scene = Scene()
    scene[synthetic.MARKER] = True
    monkeypatch.setitem(sys.modules, 'bpy', NS())
    monkeypatch.setitem(sys.modules, 'wfrl_blender.blade_recon_review', NS())
    monkeypatch.setattr(synthetic, '_SESSIONS', {})
    def fail(_scene):
        raise ValueError('missing embedded reconstruction')
    monkeypatch.setattr(synthetic, '_restore', fail)
    before_display, before_metadata = settings.capture(scene), dict(scene)
    with pytest.raises(ValueError, match='embedded'):
        synthetic.ensure(scene)
    assert settings.capture(scene) == before_display
    assert dict(scene) == before_metadata


def test_existing_adapter_failed_apply_preserves_outgoing_settings(monkeypatch):
    scene = Scene()
    monkeypatch.setitem(sys.modules, 'bpy', NS())
    def fail(*args, **kwargs):
        raise ValueError('apply failed')
    monkeypatch.setattr(synthetic, 'apply', fail)
    before_display, before_metadata = settings.capture(scene), dict(scene)
    with pytest.raises(ValueError, match='apply'):
        synthetic._activate(scene, {})
    assert settings.capture(scene) == before_display
    assert dict(scene) == before_metadata


@pytest.mark.parametrize('source,adapter', [('MAPPO', same), ('SYNTHETIC', synthetic)])
@pytest.mark.parametrize('busy', ['split', 'camera'])
def test_busy_fit_cancels_before_changing_detail_or_scene(monkeypatch, source, adapter, busy):
    from wfrl_blender import cameras, split_reconstruction as split
    scene = Scene(source)
    scene[adapter.MARKER] = True
    scene[adapter.DETAIL] = True
    scene['split_review_enabled'] = True
    window = NS(as_pointer=lambda: 771, screen=NS(as_pointer=lambda: 772), scene=scene)
    monkeypatch.setattr(split, '_JOBS', {split._key(window): {}} if busy == 'split' else {})
    monkeypatch.setattr(cameras, '_LAYOUT_JOB', {} if busy == 'camera' else None)
    before = dict(scene)
    with pytest.raises(ValueError, match='布局调整'):
        split.fit(NS(scene=scene, window=window))
    assert dict(scene) == before
