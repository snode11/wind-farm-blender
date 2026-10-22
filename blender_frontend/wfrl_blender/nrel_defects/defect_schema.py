"""Strict, JSON-safe visual defect contract bound to the NREL display loft."""
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import uuid

from .reference_surface import DIMENSION_TOLERANCE_M
from .defect_shapes import PATCH_KINDS, lightning_crater

SCHEMA = 'wfrl.blade_defects.v1'
PRESETS = {
    'fine_crack': dict(representation='appearance', generator_version='fine-crack-v1',
                      preset_id='fine-crack-dark', length_m=.6, max_width_m=.002, depth_m=0.),
    'open_crack': dict(representation='geometry', generator_version='open-crack-v1',
                      preset_id='rough-interior', length_m=.6, max_width_m=.08, depth_m=.015),
    'pit': dict(representation='geometry', generator_version='pit-v1',
                preset_id='pit-interior', length_m=.30, max_width_m=.24, depth_m=.015,
                edge_roughness=0.),
    'erosion': dict(representation='geometry', generator_version='erosion-v1',
                    preset_id='eroded-substrate', length_m=.65, max_width_m=.22, depth_m=.004,
                    edge_roughness=.35),
    'coating_loss': dict(representation='appearance', generator_version='coating-loss-v1',
                         preset_id='exposed-substrate', length_m=.45, max_width_m=.28, depth_m=0.,
                         edge_roughness=.4),
    'lightning': dict(representation='hybrid', generator_version='lightning-v1',
                     preset_id='scorch-and-pit', length_m=.60, max_width_m=.36, depth_m=.02,
                     edge_roughness=.3, crater_fraction=.4),
}
KIND_LABELS = dict(fine_crack='材质细裂纹', open_crack='几何开口裂缝', pit='凹坑',
                   erosion='局部表面侵蚀', coating_loss='涂层剥落', lightning='雷击损伤（烧蚀与凹坑）')
KIND_DESCRIPTIONS = dict(fine_crack='表面材质裂纹，无真实深度', open_crack='实体开口与内壁',
    pit='圆钝凹陷；长度、横向宽度和最大深度', erosion='不规则浅层损耗；暂不跨前后缘',
    coating_loss='不规则露底材质区域；不模拟涂层厚度或翘起',
    lightning='同一事件的烧蚀环与实体凹坑；合成外观，不模拟放电或内部损伤')
DEFAULT_SPANS = dict(fine_crack=44., open_crack=45., pit=46., erosion=47., coating_loss=48., lightning=49.)


def new_document(surface):
    return dict(schema=SCHEMA, reference_geometry_id=surface.reference_geometry_id,
                reference_geometry_sha256=surface.reference_geometry_sha256,
                parameterization_version=surface.parameterization_version, defects=[])


def new_defect(surface, kind, **overrides):
    if kind not in PRESETS: raise ValueError(f'Unsupported defect kind: {kind}')
    preset = PRESETS[kind]
    result = dict(id='D'+uuid.uuid4().hex, revision=1, enabled=True,
        turbine_id=surface.turbine_ids[0], blade_id=1, cause='synthetic_lightning' if kind == 'lightning' else 'unspecified', morphology=kind,
        representation=preset['representation'],
        anchor=dict(s_m=DEFAULT_SPANS[kind], u=.4,
                    surface_side='suction', theta_deg=0., region_hint='surface'),
        shape={key: preset[key] for key in ('length_m', 'max_width_m', 'depth_m', 'generator_version')},
        appearance=dict(preset_id=preset['preset_id'], preset_version='v1'),
        effects=dict(visual_geometry=preset['representation'] != 'appearance', physics_coupled=False),
        provenance=dict(reference_asset_id=None, dimension_basis='synthetic_example',
            assumptions=['Synthetic dimensions on the packaged NREL 5MW display loft; not measured damage.',
                         'Visual surface changes follow saved replay deformation; physics_coupled remains false.']))
    result['shape']['seed'] = 17
    for key in ('edge_roughness', 'crater_fraction'):
        if key in preset:
            result['shape'][key] = preset[key]
    for key, value in overrides.items():
        if key in ('anchor', 'shape', 'appearance', 'effects', 'provenance') and isinstance(value, dict):
            result[key].update(deepcopy(value))
        else: result[key] = deepcopy(value)
    doc = new_document(surface); doc['defects'] = [result]
    return validate_document(doc, surface)['defects'][0]


def _finite_tree(value, path='document'):
    if isinstance(value, float) and not math.isfinite(value): raise ValueError(f'{path}: non-finite number')
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value): raise ValueError(f'{path}: object keys must be strings')
        for key, item in value.items(): _finite_tree(item, f'{path}.{key}')
    elif isinstance(value, list):
        for i, item in enumerate(value): _finite_tree(item, f'{path}[{i}]')
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError(f'{path}: not a JSON value')


def _number(value, name, low, high=None, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    if value < low or (not allow_zero and value == low) or (high is not None and value > high):
        raise ValueError(f'{name} is out of supported range')


def _required_keys(value, required, label):
    if not isinstance(value, dict): raise ValueError(f'{label} must be an object')
    missing = set(required)-set(value)
    if missing: raise ValueError(f'{label} missing {sorted(missing)}')


def _orient(a, b, c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _segments_touch(a, b, c, d):
    eps = 1e-12
    if (max(a[0], b[0])+eps < min(c[0], d[0]) or max(c[0], d[0])+eps < min(a[0], b[0]) or
        max(a[1], b[1])+eps < min(c[1], d[1]) or max(c[1], d[1])+eps < min(a[1], b[1])): return False
    return _orient(a, b, c)*_orient(a, b, d) <= eps and _orient(c, d, a)*_orient(c, d, b) <= eps


def _inside(point, polygon):
    x, y = point; inside = False
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]: inside = not inside
    return inside


def _polygon(shape):
    return [(a['s_m'], a['u']) for a in shape['left_anchors']+list(reversed(shape['right_anchors']))]


def _overlap(first, second):
    for a, b in zip(first, first[1:]+first[:1]):
        for c, d in zip(second, second[1:]+second[:1]):
            if _segments_touch(a, b, c, d): return True
    return _inside(first[0], second) or _inside(second[0], first)


def validate_document(document, surface):
    """Return an independent validated document or raise a useful ValueError.

    Geometry identity mismatch always fails, including an otherwise empty file.
    Disabled defects are still validated, but do not collide or generate support.
    """
    doc = deepcopy(document); _finite_tree(doc)
    _required_keys(doc, ('schema', 'reference_geometry_id', 'reference_geometry_sha256',
                        'parameterization_version', 'defects'), 'document')
    if doc['schema'] != SCHEMA: raise ValueError('Unsupported defect schema')
    if not isinstance(doc['reference_geometry_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', doc['reference_geometry_sha256']):
        raise ValueError('reference_geometry_sha256 must be an actual SHA-256')
    for key in ('reference_geometry_id', 'reference_geometry_sha256', 'parameterization_version'):
        if doc[key] != getattr(surface, key): raise ValueError(f'STALE_GEOMETRY: {key} mismatch; explicit rebind required')
    if not isinstance(doc['defects'], list): raise ValueError('defects must be an array')
    seen = set(); active = []
    for item in doc['defects']:
        _required_keys(item, ('id', 'revision', 'enabled', 'turbine_id', 'blade_id', 'cause',
            'morphology', 'representation', 'anchor', 'shape', 'appearance', 'effects', 'provenance'), 'defect')
        if not isinstance(item['id'], str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', item['id']):
            raise ValueError('Invalid defect id')
        if item['id'] in seen: raise ValueError(f'Duplicate defect id: {item["id"]}')
        seen.add(item['id'])
        if type(item['revision']) is not int or item['revision'] < 1: raise ValueError('revision must be a positive integer')
        if type(item['enabled']) is not bool: raise ValueError('enabled must be boolean')
        if item['turbine_id'] not in surface.turbine_ids or type(item['blade_id']) is not int or item['blade_id'] not in (1, 2, 3):
            raise ValueError('Unknown turbine_id or blade_id in NREL scene')
        kind = item['morphology']
        if kind not in PRESETS: raise ValueError(f'Unsupported morphology: {kind}')
        preset = PRESETS[kind]
        if item['representation'] != preset['representation']: raise ValueError('Morphology/representation mismatch')
        if not isinstance(item['cause'], str) or not item['cause']: raise ValueError('cause must be a nonempty string')
        _required_keys(item['anchor'], ('s_m', 'u', 'surface_side', 'theta_deg'), 'anchor')
        surface.frame(item['anchor'])
        shape = item['shape']
        _required_keys(shape, ('length_m', 'max_width_m', 'depth_m', 'seed', 'generator_version'), 'shape')
        _number(shape['length_m'], 'length_m', 0, 5.)
        _number(shape['max_width_m'], 'max_width_m', 0, min(1., shape['length_m']))
        _number(shape['depth_m'], 'depth_m', 0, .1, allow_zero=True)
        if type(shape['seed']) is not int or not 0 <= shape['seed'] <= 2**32-1: raise ValueError('seed must be a uint32')
        if shape['generator_version'] != preset['generator_version']: raise ValueError('Unknown generator_version')
        if preset['representation'] == 'appearance' and shape['depth_m'] != 0:
            raise ValueError('Appearance-only defect has no geometric depth')
        if preset['representation'] != 'appearance' and shape['depth_m'] <= 0:
            raise ValueError('Geometric defect needs positive depth')
        if kind in PATCH_KINDS:
            _required_keys(shape, ('edge_roughness',), 'patch shape')
            _number(shape['edge_roughness'], 'edge_roughness', 0., .5, allow_zero=True)
        if kind == 'lightning':
            _required_keys(shape, ('crater_fraction',), 'lightning shape')
            _number(shape['crater_fraction'], 'crater_fraction', .15, .6, allow_zero=True)
        _required_keys(item['appearance'], ('preset_id', 'preset_version'), 'appearance')
        if item['appearance'] != dict(preset_id=preset['preset_id'], preset_version='v1'):
            raise ValueError('Unknown appearance resource or version')
        expected_effects = dict(visual_geometry=preset['representation'] != 'appearance', physics_coupled=False)
        if item['effects'] != expected_effects or any(type(v) is not bool for v in item['effects'].values()):
            raise ValueError('Unsupported effects; only declared visual effect is supported')
        provenance = item['provenance']
        _required_keys(provenance, ('reference_asset_id', 'dimension_basis', 'assumptions'), 'provenance')
        if provenance['reference_asset_id'] is not None: raise ValueError('Missing registered reference asset')
        if not isinstance(provenance['dimension_basis'], str) or not provenance['dimension_basis']:
            raise ValueError('dimension_basis must be declared')
        if not isinstance(provenance['assumptions'], list) or any(not isinstance(x, str) for x in provenance['assumptions']):
            raise ValueError('assumptions must be an array of strings')
        sampled = surface.sample_shape(item)
        if kind == 'lightning':
            outer = _polygon(sampled)
            inner = _polygon(surface.sample_shape(lightning_crater(item)))
            if not all(_inside(point, outer) for point in inner) or any(
                    _segments_touch(a, b, c, d)
                    for a, b in zip(outer, outer[1:]+outer[:1])
                    for c, d in zip(inner, inner[1:]+inner[:1])):
                raise ValueError('Lightning crater must stay strictly within its scorch footprint')
        if item['enabled']:
            polygon = _polygon(sampled)
            for other, other_polygon in active:
                if (item['turbine_id'], item['blade_id'], item['anchor']['surface_side']) == (
                        other['turbine_id'], other['blade_id'], other['anchor']['surface_side']) and _overlap(polygon, other_polygon):
                    raise ValueError(f'OVERLAPPING_DEFECTS: {other["id"]} and {item["id"]}')
            active.append((item, polygon))
    return doc


def load_document(path, surface):
    return validate_document(json.loads(Path(path).read_text()), surface)


def save_document(path, document, surface):
    """Atomic replacement after full validation; does not initiate rendering."""
    import os
    import tempfile
    data = validate_document(document, surface)
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return path
