"""Persist display choices per texture source and commit switches atomically.

The Scene RNA properties remain the UI's working values.  Each source keeps a
saved copy in ordinary Scene metadata, so it survives file save and reopening.
"""
from __future__ import annotations

import json
from contextlib import contextmanager


CURRENT = 'split_texture_current_source'
FIELDS = (
    'wfrl_recon_show_texture', 'wfrl_recon_texture_mode',
    'wfrl_recon_texture_gain', 'wfrl_recon_texture_threshold',
    'wfrl_recon_inspect_blade', 'wfrl_recon_inspect_radius',
    'wfrl_recon_inspect_tau', 'wfrl_recon_inspect_size', 'wfrl_recon_inspect_box',
)
_RNA_DEFAULTS = (True, 'ORIGINAL', 6., .06, '1', 59.3, .6, 2., False)


def capture(scene):
    return {name: getattr(scene, name, default) for name, default in zip(FIELDS, _RNA_DEFAULTS)}


def _key(source):
    return 'split_texture_display_' + source.lower()


def _source(scene):
    source = scene.get(CURRENT)
    if source in {'MAPPO', 'SYNTHETIC'}:
        return source
    # Pre-selector saved synthetic scenes retain their current RNA choices.
    if scene.get('split_texture_review'):
        return 'SYNTHETIC'
    if scene.get('split_mappo_texture_review'):
        return 'MAPPO'
    return None


def _write(scene, values):
    for name, value in values.items():
        if getattr(scene, name, None) != value:
            setattr(scene, name, value)


def _encode(values):
    return json.dumps(dict(version=1, values=values), sort_keys=True)


def _store(scene, key, value):
    if scene.get(key) != value:
        scene[key] = value


@contextmanager
def rollback_on_error(scene):
    """Extend source/settings rollback through an entry's layout setup."""
    before = capture(scene)
    keys = (CURRENT, _key('MAPPO'), _key('SYNTHETIC'), 'split_review_enabled', 'split_review_status',
            'split_mappo_texture_detail')
    saved = {key: (key in scene, scene.get(key)) for key in keys}
    try:
        yield
    except Exception:
        _write(scene, before)
        for key, (present, value) in saved.items():
            if present:
                _store(scene, key, value)
            else:
                scene.pop(key, None)
        raise


def activate(scene, source, defaults, commit, *, new=False):
    """Apply target display settings, then commit its source after success.

    Existing files without per-source metadata preserve the currently selected
    source's settings; an inactive source starts from its own defaults.  MAPPO
    never inherits synthetic false colour, including from older saved files.
    A failed target application restores the source selector and all UI values.
    """
    previous = _source(scene)
    before = capture(scene)
    keys = (CURRENT, _key(source)) + ((_key(previous),) if previous else ())
    saved = {key: (key in scene, scene.get(key)) for key in keys}
    switching = new or previous != source
    target = before.copy()
    if switching:
        target = defaults.copy()
        raw = scene.get(_key(source))
        if raw:
            try:
                record = json.loads(raw)
                if record.get('version') == 1 and set(record.get('values', {})) == set(FIELDS):
                    target = record['values']
            except (ValueError, TypeError, AttributeError):
                pass
    # Same-source UI only offers original or contrast; migrate an old shared
    # FALSE_COLOR value without discarding the user's other current choices.
    if source == 'MAPPO' and target.get('wfrl_recon_texture_mode') not in {'ORIGINAL', 'CONTRAST'}:
        target['wfrl_recon_texture_mode'] = defaults['wfrl_recon_texture_mode']
    try:
        _write(scene, target)
        result = commit()
        if previous and previous != source:
            _store(scene, _key(previous), _encode(before))
        _store(scene, _key(source), _encode(capture(scene)))
        _store(scene, CURRENT, source)
        return result
    except Exception:
        _write(scene, before)
        for key, (present, value) in saved.items():
            if present:
                scene[key] = value
            else:
                scene.pop(key, None)
        raise
