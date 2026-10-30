"""Validated offline blade-response results, independent of the radar result layer."""
from bisect import bisect_right
import hashlib
import json
import math
from pathlib import Path


METRICS = {
    'tip_x': ('叶尖根系 x', 'm', 1.),
    'tip_y': ('叶尖根系 y', 'm', 1.),
    'tip_z': ('叶尖根系 z', 'm', 1.),
    'root_Mx': ('叶根弯矩 Mx', 'kN·m', .001),
    'root_My': ('叶根弯矩 My', 'kN·m', .001),
    'root_Mz': ('叶根弯矩 Mz', 'kN·m', .001),
}
SOURCE_FILES = {'manifest.json', 'data.json', 'geometry.npz', 'telemetry.json', 'deflection-t1.json'}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _require(condition, message):
    if not condition:
        raise ValueError('Blade response: ' + message)


def _vector(value, size):
    return isinstance(value, list) and len(value) == size and all(_finite(x) for x in value)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _physical(value, kind):
    _require(isinstance(value, dict) and value.get('status') in {'available', 'unavailable'}, 'physical status')
    if value['status'] == 'unavailable':
        _require(set(value) == {'status'}, 'unavailable physical result must not contain numbers')
        return
    _require(value.get('source_kind') in {'simulation_reference', 'estimate'}
             and isinstance(value.get('provenance'), str) and value['provenance'].strip(), 'physical provenance')
    if kind == 'mass':
        _require(_finite(value.get('magnitude_kg_m')) and value['magnitude_kg_m'] >= 0, 'mass unit/value')
        phase = value.get('phase_deg')
        _require(phase is None or (_finite(phase) and 0 <= phase < 360
                 and isinstance(value.get('phase_reference'), str) and value['phase_reference'].strip()), 'mass direction')
        blades = value.get('blades')
        _require(isinstance(blades, list) and len(blades) == len(set(blades))
                 and all(type(b) is int and b in (1, 2, 3) for b in blades), 'mass blade identity')
    else:
        _require(value.get('parameter') in {'aerodynamic_twist_offset', 'pitch_zero_offset'}, 'aerodynamic parameter')
        _require(_vector(value.get('blade_offsets_deg'), 3), 'aerodynamic offsets')


def _stats(value):
    _require(isinstance(value, dict) and _finite(value.get('rms_spread')) and value['rms_spread'] >= 0
             and _vector(value.get('blade_means'), 3) and _vector(value.get('blade_offsets'), 3), 'response statistics')
    mean = sum(value['blade_means']) / 3
    _require(all(math.isclose(a - mean, b, rel_tol=1e-8, abs_tol=1e-8)
                 for a, b in zip(value['blade_means'], value['blade_offsets'])), 'inconsistent blade offsets')


def validate(data, source_manifest, telemetry):
    _require(data.get('schema') == 'wfrl.blade-response.v1'
             and data.get('method') == 'common-turn-phase-mean.v1', 'schema/method')
    segment = data.get('segment_s')
    expected = [source_manifest['segment']['start_s'], source_manifest['segment']['end_s']]
    _require(_vector(segment, 2) and segment == expected and segment[0] < segment[1], 'segment differs')
    grid = data.get('phase_grid_deg')
    _require(grid == [2.5 + 5 * i for i in range(72)], 'phase grid')
    turbines = data.get('turbines')
    _require(isinstance(turbines, dict) and set(turbines) == set(source_manifest['turbine_ids']), 'turbine identities')
    for tid, row in turbines.items():
        _require(row['source']['sha256'] == telemetry['sources'][tid]['sha256'], 'raw output source: ' + tid)
        _require(set(row['physical']) == {'mass', 'aerodynamic'}, 'physical result types')
        for kind, physical in row['physical'].items():
            _physical(physical, kind)
        _require(set(row['metrics']) == set(METRICS), 'metric coverage')
        for key, metric in row['metrics'].items():
            _stats(metric)
            _require(metric.get('unit') == ('m' if key.startswith('tip_') else 'N m'), 'metric unit: ' + key)
            curves = metric.get('phase_mean')
            _require(isinstance(curves, list) and len(curves) == 3
                     and all(_vector(curve, 72) for curve in curves), 'phase curves')
            mean = [sum(samples) / 3 for samples in zip(*curves)]
            spread = math.sqrt(sum((x - y) ** 2 for curve in curves for x, y in zip(curve, mean)) / 216)
            _require(math.isclose(spread, metric['rms_spread'], rel_tol=1e-8, abs_tol=1e-8)
                     and all(math.isclose(sum(curve) / 72, value, rel_tol=1e-8, abs_tol=1e-8)
                             for curve, value in zip(curves, metric['blade_means'])), 'curves and statistics differ')
        turns = row.get('turns')
        _require(isinstance(turns, list) and bool(turns), 'complete turns missing')
        previous = None
        for turn in turns:
            _require(type(turn.get('cycle_index')) is int
                     and _finite(turn.get('start_s')) and _finite(turn.get('end_s'))
                     and segment[0] <= turn['start_s'] < turn['end_s'] <= segment[1], 'turn coverage')
            if previous:
                _require(turn['cycle_index'] == previous['cycle_index'] + 1
                         and turn['start_s'] > previous['start_s'] and turn['end_s'] > previous['end_s'], 'turn order')
            _require(set(turn['metrics']) == set(METRICS), 'turn metrics')
            for metric in turn['metrics'].values():
                _stats(metric)
            previous = turn
    return data


def read_package(path, source_path, *, _source=None):
    """Check source identity and result integrity before exposing any metric."""
    path, source_path = Path(path), Path(source_path)
    try:
        if _source is not None:
            _require(_source.path == source_path.resolve(), 'verified source belongs to another package')
        manifest = json.loads((path / 'manifest.json').read_bytes())
        _require(manifest.get('schema') == 'wfrl.blade-response-package.v1'
                 and set(manifest['files']) == {'results.json'}
                 and set(manifest['source_hashes']) == SOURCE_FILES, 'analysis manifest')
        for name, expected in manifest['source_hashes'].items():
            actual = _source.hashes.get(name) if _source is not None else _digest(source_path / name)
            _require(actual == expected, 'source identity mismatch: ' + name)
        _require(_digest(path / 'results.json') == manifest['files']['results.json'], 'result integrity mismatch')
        source = (_source.manifest if _source is not None else
                  json.loads((source_path / 'manifest.json').read_bytes()))
        _require(all(source['files'][name] == manifest['source_hashes'][name]
                     for name in SOURCE_FILES - {'manifest.json'}), 'source inventory differs')
        telemetry = (_source.telemetry if _source is not None else
                     json.loads((source_path / 'telemetry.json').read_bytes()))
        return validate(json.loads((path / 'results.json').read_bytes()), source, telemetry)
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError('Blade response: malformed analysis package') from error


def load_for_source(source_path, *, _source=None):
    """Prefer a source-local analysis; use the built-in only for its exact source."""
    source_path = Path(source_path)
    if _source is not None:
        _require(_source.path == source_path.resolve(), 'verified source belongs to another package')
    local = source_path / 'blade-response'
    if local.exists():
        return read_package(local, source_path, _source=_source)
    builtin = Path(__file__).resolve().parent / 'assets' / 'blade_response'
    if not builtin.exists():
        return None
    manifest = json.loads((builtin / 'manifest.json').read_bytes())
    _require(isinstance(manifest, dict) and manifest.get('schema') == 'wfrl.blade-response-package.v1'
             and isinstance(manifest.get('source_hashes'), dict)
             and set(manifest['source_hashes']) == SOURCE_FILES, 'built-in analysis manifest')
    source_digest = (_source.hashes.get('manifest.json') if _source is not None else
                     _digest(source_path / 'manifest.json'))
    if manifest['source_hashes']['manifest.json'] != source_digest:
        return None
    return read_package(builtin, source_path, _source=_source)


def latest_turn(row, time_s):
    """Last fully observed three-blade turn, with no accumulation across seeks."""
    if not _finite(time_s):
        return None
    turns = row['turns']
    index = bisect_right([turn['end_s'] for turn in turns], time_s) - 1
    return turns[index] if index >= 0 else None


def plot_enabled(scene):
    return (scene.get('wfrl_scene_kind') != 'manual'
            and getattr(scene, 'wfrl_farm_panel_page', None) == 'IMBALANCE'
            and getattr(scene, 'wfrl_imbalance_curve', False))


def plot_data(scene):
    from . import farm_flex, charts
    active = farm_flex.active_for(scene)
    data = getattr(active, 'imbalance', None)
    tid, metric = scene.wfrl_imbalance_turbine, scene.wfrl_imbalance_metric
    if data is None or tid not in data['turbines']:
        return None
    cache = active.imbalance_plots
    if (tid, metric) not in cache:
        label, unit, factor = METRICS[metric]
        curves = data['turbines'][tid]['metrics'][metric]['phase_mean']
        values = [x * factor for curve in curves for x in curve]
        start, end = data['segment_s']
        cache[tid, metric] = charts._PlotData(
            tuple((f'B{b+1}', (tuple(zip(data['phase_grid_deg'], [v * factor for v in curve])),), '离线均值')
                  for b, curve in enumerate(curves)),
            ('B1', 'B2', 'B3'), (unit,), (0., 360., min(values), max(values)),
            '叶片方位角 / ° · 0 朝上，180 朝下', metric, 'ALL',
            title=f'{tid} · {label} / {unit} · {start:g}–{end:g} s 全片段')
    return cache[tid, metric]
