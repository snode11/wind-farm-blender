"""Verified farm packages, source parsing and bounded parsed-data reuse."""
import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from . import farm_flex as _farm


def dual_beam_api():
    try:
        from ._vendor.lidar import dual_beam_replay
    except ImportError:
        from wfrl.lidar import dual_beam_replay
    return dual_beam_replay


def default_package():
    return Path(__file__).resolve().parent / 'assets' / 'mappo'


def default_dual_package():
    return Path(__file__).resolve().parent / 'assets' / 'dual_beam'


def default_dual_tls_package():
    """The optional TLS candidate shares the original package's saved geometry."""
    return Path(__file__).resolve().parent / 'assets' / 'dual_beam_tls'


def saved_package(scene):
    """Relocate an installed built-in only when its saved manifest is identical."""
    saved = Path(scene['wfrl_farm_flex_path'])
    if saved.is_dir():
        return saved
    expected = scene.get('wfrl_farm_manifest_sha256')
    if expected:
        for candidate in (_farm.default_package(), _farm.default_dual_package(), _farm.default_dual_tls_package()):
            manifest = candidate / 'manifest.json'
            if manifest.is_file() and hashlib.sha256(manifest.read_bytes()).hexdigest() == expected:
                return candidate
    # The caller retains its normal missing/integrity error and clears old data.
    return saved


@dataclass(frozen=True)
class _VerifiedSource:
    """Source identity and telemetry belonging to one complete load transaction."""
    path: Path
    manifest: dict
    hashes: dict
    telemetry: dict | None


@dataclass(frozen=True)
class _LoadedPackage:
    manifest: dict
    times: np.ndarray
    transforms: np.ndarray
    poses: np.ndarray
    readers: dict
    source_path: Path
    result_path: Path
    overlay: dict | None
    tower_motion: dict | None
    reference: dict | None
    verified_source: _VerifiedSource | None = None
    comparison: dict | None = None

    @property
    def playback(self):
        return self.manifest, self.times, self.transforms, self.poses, self.readers


@dataclass(frozen=True)
class _ParsedPackage:
    times: np.ndarray
    transforms: np.ndarray
    poses: np.ndarray
    contents: dict
    tower_motion: dict | None
    reference: dict | None


def clear_package_cache():
    """Release parsed source data without changing any scene's runtime."""
    _farm._PACKAGE_CACHE.clear()


def _load_package(path):
    """Verify every file on every load; reuse only matching parsed source data."""
    result_path = Path(path).resolve()
    path, overlay = _farm.dual_beam_api().resolve_package(result_path)
    path = Path(path).resolve()
    manifest_raw = (path / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    if (manifest.get('schema') not in ('wfrl.farm-flex-review.v1','wfrl.farm-flex-review.v2','wfrl.farm-flex-review.v3')
            or manifest.get('status')!='REVIEW_ONLY'
            or manifest.get('turbine_ids') not in (['T1','T2','T3'], ['T1'])
            or (manifest.get('turbine_ids') == ['T1'] and (manifest.get('schema') != 'wfrl.farm-flex-review.v3' or not manifest.get('interface_only')))):
        raise ValueError('Expected a complete three-turbine flexible review package')
    source_hashes = {'manifest.json': hashlib.sha256(manifest_raw).hexdigest()}
    telemetry_raw = None
    for name in set(manifest['files']) | {'geometry.npz','data.json','source-surfaces.json'}:
        raw = (path/name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != manifest['files'][name]:
            raise ValueError('Farm package integrity mismatch: '+name)
        source_hashes[name] = digest
        if name == 'telemetry.json':
            telemetry_raw = raw
    del raw
    verified_source = _farm._VerifiedSource(path, manifest, source_hashes,
        json.loads(telemetry_raw) if telemetry_raw is not None else None)
    # The verified source owns parsed geometry; each load owns its result layer.
    # resolve_package independently validates sidecars and their source inventory.
    key = (path, hashlib.sha256(manifest_raw).hexdigest())
    parsed = _farm._PACKAGE_CACHE.get(key)
    if parsed is None:
        parsed = _farm._parse_package(path, manifest)
    try:
        from ._vendor.lidar.replay import ReplayReader
    except ImportError:
        from wfrl.lidar.replay import ReplayReader
    readers = {}
    for tid in manifest['turbine_ids']:
        # Cached rows are read-only inputs; ReplayReader.at copies its outputs.
        # Reader identity and per-reader indexes belong to each scene binding.
        readers[tid] = ReplayReader(SimpleNamespace(
            manifest=deepcopy({**manifest, 'turbine_id': tid}), **parsed.contents[tid]))
        if overlay is not None:
            readers[tid] = _farm.dual_beam_api().DualBeamReader(readers[tid], overlay, tid)
    if overlay is not None:
        manifest = {**manifest, 'measurement_mode': 'dual_beam',
                    'dual_beam_calibration': overlay['config'],
                    'dual_beam': overlay['manifest']}
    _farm._PACKAGE_CACHE[key] = parsed
    _farm._PACKAGE_CACHE.move_to_end(key)
    while len(_farm._PACKAGE_CACHE) > _farm._PACKAGE_CACHE_SIZE:
        _farm._PACKAGE_CACHE.popitem(last=False)
    tower_data = None if parsed.tower_motion is None else dict(parsed.tower_motion)
    _farm._read_telemetry(path, manifest, parsed.times, readers, _source=verified_source)
    from .deflection import read_comparison
    comparison = read_comparison(path, manifest, parsed.times, parsed.poses,
        rigid_poses=parsed.poses, tower_motion=tower_data, blade_reference=parsed.reference)
    return _farm._LoadedPackage(manifest, parsed.times, parsed.transforms, parsed.poses,
                          readers, path, result_path, overlay, tower_data,
                          deepcopy(parsed.reference), verified_source, comparison)


def _parse_package(path, manifest):
    """Parse and validate geometry, motion and independent support/reference data."""
    with np.load(path/'geometry.npz',allow_pickle=False) as data:
        times=data['times'].copy()
        transforms=data['transforms'].copy()
        poses=data['poses'].copy()
    n=len(times)
    turbines=len(manifest['turbine_ids'])
    if (n<2 or times.ndim!=1 or not np.isfinite(times).all() or np.any(np.diff(times)<=0)
            or transforms.shape!=(n,turbines,3,19,3,4) or poses.shape!=(n,turbines,6)
            or not np.isfinite(transforms).all() or not np.isfinite(poses).all()
            or times[0]!=manifest['segment']['start_s'] or times[-1]!=manifest['segment']['end_s']):
        raise ValueError('Incomplete farm geometry or time axis')
    from .prebend import read_reference
    reference = read_reference(path, manifest)
    if reference is not None and 'deflection-t1.json' not in manifest['files']:
        raise ValueError('BeamDyn package requires mapped deflection data')
    from .tower_motion import read_tower
    tower_data=read_tower(path,manifest,times)
    contents=json.loads((path/'data.json').read_text(encoding='utf-8'))
    _farm._validate_replay_contents(manifest, contents)
    motion_poses=[]
    for index,tid in enumerate(manifest['turbine_ids']):
        payload=contents[tid]
        if not np.array_equal([r['time_s'] for r in payload['motion']],times):
            raise ValueError('Motion and geometry time axes differ: '+tid)
        expected=np.array([[r['yaw_deg'],r['azimuth_deg'],r['rotor_speed_rpm'],*r['pitch_deg']]
                           for r in payload['motion']])
        if not np.allclose(expected,poses[:,index],atol=.001,rtol=0):
            raise ValueError('Motion and geometry poses differ: '+tid)
        # Keep the source motion precision. Unwrapped azimuth reaches thousands
        # of degrees; the float32 NPZ poses lose sub-millidegree rigid phase.
        motion_poses.append(expected)
        if tower_data is not None:
            actual=np.asarray([r['nacelle_transform'] for r in payload['motion']])
            if not np.allclose(actual,tower_data['nacelle'][:,index],atol=1e-8,rtol=0):
                raise ValueError('Nacelle motion and geometry differ: '+tid)
            top=np.einsum('nij,j->ni',actual[:,:,:3],[0,0,87.6])+actual[:,:,3]+manifest['layout_m'][index]
            if not np.allclose(top,[r['nacelle_position_m'] for r in payload['motion']],atol=1e-8,rtol=0):
                raise ValueError('Nacelle position and geometry differ: '+tid)
    poses = np.stack(motion_poses, axis=1)
    _farm._readonly_arrays((times, transforms, poses, tower_data, reference))
    return _farm._ParsedPackage(times, transforms, poses, contents, tower_data, reference)


def _validate_replay_contents(manifest, contents):
    """Validate review observations and derive B2 state without READY evidence rules."""
    try:
        from ._vendor.lidar import replay
    except ImportError:
        from wfrl.lidar import replay
    require, finite, vector = replay._require, replay._finite, replay._vector
    try:
        replay._scan([manifest, contents])
        require(isinstance(contents, dict) and set(contents) == set(manifest['turbine_ids']),
                'Farm replay turbines differ from geometry')
        settings = manifest.get('replay', {})
        require(all(finite(settings.get(key)) for key in
                    ('threshold_m', 'hysteresis_m', 'max_hold_s', 'passage_margin')),
                'Invalid farm replay settings')
        require(settings['hysteresis_m'] >= 0 and settings['max_hold_s'] > 0
                and settings['passage_margin'] >= 1, 'Invalid farm replay timing/hysteresis')
        start, end = manifest['segment']['start_s'], manifest['segment']['end_s']
        for tid, payload in contents.items():
            motion, measurements = payload['motion'], payload['measurements']
            require(isinstance(motion, list) and bool(motion)
                    and isinstance(measurements, list), 'Invalid farm replay records: ' + tid)
            motion_times = replay._ordered(motion, 'farm motion')
            require(motion_times[0] == start and motion_times[-1] == end
                    and all(a < b for a, b in zip(motion_times, motion_times[1:])),
                    'Farm motion must cover segment with unique times: ' + tid)
            for row in motion:
                require(all(finite(row.get(key)) for key in
                            ('azimuth_deg', 'yaw_deg', 'rotor_speed_rpm'))
                        and isinstance(row.get('pitch_deg'), list) and len(row['pitch_deg']) == 3
                        and all(finite(p) for p in row['pitch_deg']), 'Invalid farm motion pose: ' + tid)
                for key in ('nacelle_position_m', 'nacelle_orientation_deg'):
                    require(key not in row or vector(row[key]), 'Invalid farm nacelle pose: ' + tid)
            replay._ordered(measurements, 'farm measurements')
            identities = set()
            for row in measurements:
                require(start <= row['time_s'] <= end and type(row.get('blade_id')) is int
                        and row['blade_id'] in (1, 2, 3), 'Invalid farm measurement time/blade: ' + tid)
                identity = (row['time_s'], row['blade_id'])
                require(identity not in identities, 'Duplicate farm measurement: ' + tid)
                identities.add(identity)
                require(type(row.get('expected')) is bool
                        and isinstance(row.get('passage_id'), str) and bool(row['passage_id']),
                        'Missing farm evaluation grid/passage: ' + tid)
                for name in ('B1', 'B2', 'B3'):
                    beam = row['beams'][name]
                    require(type(beam.get('valid')) is bool, 'Invalid farm beam validity: ' + tid)
                    if beam['valid']:
                        require(row['expected'] and finite(row.get('truth_m'))
                                and all(finite(beam.get(key)) for key in
                                        ('slant_range_m', 'estimate_m', 'error_m'))
                                and beam['slant_range_m'] >= 0,
                                'Invalid valid farm measurement: ' + tid)
                        require(math.isclose(beam['error_m'], beam['estimate_m'] - row['truth_m'],
                                             rel_tol=0., abs_tol=1e-9),
                                'Inconsistent farm signed error: ' + tid)
                        require(vector(beam.get('hit_point_m'))
                                and vector(row.get('truth_tip_point_m'))
                                and vector(row.get('truth_wall_point_m')),
                                'Missing farm geometry audit endpoints: ' + tid)
                        tip, wall = row['truth_tip_point_m'], row['truth_wall_point_m']
                        require(math.isclose(tip[2], wall[2], rel_tol=0., abs_tol=1e-7)
                                and math.isclose(math.dist(tip, wall), abs(row['truth_m']),
                                                 rel_tol=0., abs_tol=1e-6),
                                'Inconsistent farm truth endpoints: ' + tid)
                    else:
                        require(bool(beam.get('reason')) and beam.get('estimate_m') is None
                                and beam.get('error_m') is None, 'Invalid farm beam null contract: ' + tid)
            cumulative, statistics = replay.precompute(measurements, motion, settings)
            equal = _farm.dual_beam_api()._prefix_states_equal
            require(equal(cumulative, payload['cumulative'])
                    and equal([dict(statistics=statistics)], [dict(statistics=payload['statistics'])]),
                    'Inconsistent farm precomputed statistics/state: ' + tid)
            payload['cumulative'], payload['statistics'] = cumulative, statistics
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError('Invalid farm replay data: ' + str(exc)) from exc


def read_package(path):
    """Return validated manifest, clock, transforms, poses and replay readers."""
    return _farm._load_package(path).playback


def validate_candidate(path, package):
    if (not isinstance(package, _farm._LoadedPackage)
            or package.result_path != Path(path).resolve()):
        raise ValueError('Validated package does not match the requested result path')


def preflight(path):
    """Fully validate a candidate without changing any scene or reader."""
    return _farm._load_package(path)
