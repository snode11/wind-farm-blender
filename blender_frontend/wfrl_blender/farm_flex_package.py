"""Verified farm packages, source parsing and bounded parsed-data reuse."""
import hashlib
import json
import math
import pickle
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np

from . import farm_flex as _farm


@lru_cache(maxsize=1)
def dual_beam_api():
    try:
        from ._vendor.lidar import dual_beam_replay
    except ImportError:
        from wfrl.lidar import dual_beam_replay
    return _DualBeamAPI(dual_beam_replay)


def default_package():
    return Path(__file__).resolve().parent / 'assets' / 'mappo'


def default_dual_package():
    return Path(__file__).resolve().parent / 'assets' / 'dual_beam'


def default_dual_tls_package():
    """The optional TLS candidate shares the original package's saved geometry."""
    return Path(__file__).resolve().parent / 'assets' / 'dual_beam_tls'


class _SavedPackagePath(type(Path())):
    """A restored path also carries the manifest identity saved in its scene."""


def saved_package(scene):
    """Relocate an installed built-in only when its saved manifest is identical."""
    saved = _SavedPackagePath(scene['wfrl_farm_flex_path'])
    expected = scene.get('wfrl_farm_manifest_sha256')
    saved.expected_manifest_sha256 = expected
    if saved.is_dir():
        return saved
    if expected:
        for candidate in (_farm.default_package(), _farm.default_dual_package(), _farm.default_dual_tls_package()):
            manifest = candidate / 'manifest.json'
            if manifest.is_file() and hashlib.sha256(manifest.read_bytes()).hexdigest() == expected:
                relocated = _SavedPackagePath(candidate)
                relocated.expected_manifest_sha256 = expected
                return relocated
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
    result_manifest_sha256: str | None = None

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
    reader_data: dict
    validated_json: frozenset = frozenset()


class _ReaderPackage(SimpleNamespace):
    """Each reader materializes its own mutable fields on their first access."""

    def __getattr__(self, name):
        payloads = self.__dict__.get('_payloads', {})
        if name not in payloads:
            raise AttributeError(name)
        value = pickle.loads(payloads[name])
        setattr(self, name, value)
        return value


def clear_package_cache():
    """Release parsed source data without changing any scene's runtime."""
    _farm._PACKAGE_CACHE.clear()


def _json(raw, name):
    """Reject ambiguous keys and nonfinite numbers before any consumer parses JSON."""
    def pairs(items):
        result = dict(items)
        if len(result) != len(items):
            raise ValueError('Duplicate JSON key in ' + name)
        return result

    def number(value, convert=float, finite=math.isfinite):
        result = convert(value)
        if not finite(result):
            raise ValueError('Nonfinite JSON number in ' + name)
        return result

    return json.loads(raw, object_pairs_hook=pairs, parse_float=number, parse_constant=number)


@dataclass(frozen=True)
class _VerifiedFile:
    path: Path
    sha256: str
    raw: bytes | None = None
    data: object = None

    def __fspath__(self):
        return str(self.path)


@dataclass
class _PackageSnapshot:
    """Transaction-local files; cache hits retain only per-load sidecar data."""
    path: Path
    manifest: dict
    files: dict
    source: object = None
    parse_path: Path | None = None

    def __truediv__(self, name):
        return self.files[name]

    def exists(self):
        return self.parse_path is not None and self.parse_path.exists()

    @property
    def hashes(self):
        return {name: file.sha256 for name, file in self.files.items()}


def _file_bytes(path, name):
    relative = Path(name)
    if (not name or relative.is_absolute() or PureWindowsPath(name).drive
            or '\\' in name or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Unsafe farm package file path: ' + name)
    target = (path / relative).resolve()
    if not target.is_file():
        raise ValueError('Farm package file is missing or not a regular file: ' + name)
    return target.read_bytes()


def _capture_package(path):
    """Hash all files; retain only bytes or JSON needed by this load's consumers."""
    if not (path / 'manifest.json').exists():
        raise FileNotFoundError('Farm package manifest not found: ' + str(path / 'manifest.json'))
    raw = _file_bytes(path, 'manifest.json')
    manifest = _json(raw, 'manifest.json')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), dict):
        raise ValueError('Missing farm package file inventory')
    label = ('Dual-beam result' if manifest.get('schema') == 'wfrl.dual-beam-review.v1'
             else 'Farm package')
    digest = hashlib.sha256(raw).hexdigest()
    cold = (path, digest) not in _farm._PACKAGE_CACHE
    needed = {'telemetry.json', 'deflection-t1.json'}
    if cold:
        needed.update(('geometry.npz', 'tower-motion.npz', 'data.json', 'blade-reference.json'))
    if manifest.get('schema') == 'wfrl.dual-beam-review.v1':
        cold = True
        needed = {'calibration.json', 'results.json'}
    files = {'manifest.json': _VerifiedFile(path / 'manifest.json', digest, data=manifest)}
    validated_json = frozenset().union(*(entry.validated_json for entry in _farm._PACKAGE_CACHE.values()))
    for name, expected in manifest['files'].items():
        if (name == 'manifest.json' or not isinstance(expected, str) or len(expected) != 64
                or any(c not in '0123456789abcdef' for c in expected)):
            raise ValueError('Invalid farm package file digest: ' + name)
        content = _file_bytes(path, name)
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected:
            raise ValueError(label + ' integrity mismatch: ' + name)
        data = None
        if name.endswith('.json'):
            if cold and (name, digest) not in validated_json:
                data = _json(content, name)
            elif name in needed:
                # Cached identities are recorded only after complete validation;
                # the digest above proves these JSON bytes identical.
                data = json.loads(content)
        files[name] = _VerifiedFile(path / name, digest,
            raw=content if cold and name in ('geometry.npz', 'tower-motion.npz', 'blade-reference.json') else None,
            data=data if name in needed else None)
    return _PackageSnapshot(path, manifest, files)


@contextmanager
def _snapshot(result_path):
    """Retain verified inputs, staging only cold path-based geometry consumers."""
    result = _capture_package(result_path)
    source = result
    try:
        if result.manifest.get('schema') == 'wfrl.dual-beam-review.v1':
            link = result.manifest.get('source_package')
            if not isinstance(link, str) or not link:
                raise ValueError('Missing dual-beam source package path')
            source_path = (result_path / link).resolve()
            if source_path == result_path:
                raise ValueError('Dual-beam source cannot refer to itself')
            source = _capture_package(source_path)
            if result.manifest.get('turbine_ids') != source.manifest.get('turbine_ids'):
                raise ValueError('Dual-beam manifest turbines differ from source')
            result.source = source
        if source.files.get('geometry.npz') is not None and source.files['geometry.npz'].raw is not None:
            with TemporaryDirectory(prefix='wfrl-farm-verified-') as directory:
                source.parse_path = Path(directory)
                for name in ('geometry.npz', 'tower-motion.npz', 'blade-reference.json'):
                    file = source.files.get(name)
                    if file is not None:
                        (source.parse_path / name).write_bytes(file.raw)
                yield result
        else:
            yield result
    finally:
        source.files.clear()
        result.files.clear()


class _DualBeamAPI:
    """Keep the path API and validate captured sidecars without reopening files."""

    def __init__(self, core):
        self.core = core

    def __getattr__(self, name):
        return getattr(self.core, name)

    def digest(self, file):
        return file.sha256 if isinstance(file, _VerifiedFile) else self.core.digest(file)

    def resolve_package(self, path):
        if not isinstance(path, _PackageSnapshot):
            return self.core.resolve_package(path)
        manifest = path.manifest
        if manifest.get('schema') != self.core.SCHEMA:
            return path, None
        if manifest.get('status') == 'REJECTED':
            raise ValueError('Rejected installation: ' + manifest.get('rejection_reason', 'inadmissible mount'))
        if manifest.get('status') != 'REVIEW_ONLY' or manifest.get('sample_kind') != 'source':
            raise ValueError('Unsupported dual-beam evidence status')
        source = path.source
        source_manifest = source.manifest
        if source_manifest.get('schema') != 'wfrl.farm-flex-review.v3':
            raise ValueError('Dual-beam source must be v3 geometry')
        for name, expected in manifest['source_hashes'].items():
            if Path(name).name != name or self.digest(source / name) != expected:
                raise ValueError('Dual-beam source integrity mismatch: ' + name)
        if manifest['source_hashes'].get('manifest.json') != self.digest(source / 'manifest.json'):
            raise ValueError('Dual-beam source manifest missing or mismatched')
        if manifest['source_hashes'] != source.hashes:
            raise ValueError('Dual-beam source inventory is incomplete or differs from source')
        if not {'results.json', 'calibration.json'}.issubset(manifest['files']):
            raise ValueError('Dual-beam required result files are missing from inventory')
        for name, expected in manifest['files'].items():
            if Path(name).name != name or self.digest(path / name) != expected:
                raise ValueError('Dual-beam result integrity mismatch: ' + name)
        config = path.files['calibration.json'].data
        if str(config.get('status', '')).startswith('REJECTED'):
            raise ValueError('Rejected installation calibration')
        if config.get('schema') != 'wfrl.dual-beam-calibration.v1':
            raise ValueError('Unsupported dual-beam calibration schema')
        for key in ('alarm_hold_s', 'measurement_hold_s', 'effective_length_m'):
            if not isinstance(config.get(key), (int, float)) or not math.isfinite(config[key]) or config[key] <= 0:
                raise ValueError('Invalid dual-beam calibration: ' + key)
        for key, count in (('origins_m', 3), ('angles_deg', 3)):
            values = config[key]
            if not isinstance(values, list) or len(values) != count:
                raise ValueError('Invalid dual-beam mounting: ' + key)
        if any(not isinstance(o, list) or len(o) != 3 or any(not math.isfinite(x) for x in o)
               for o in config['origins_m']):
            raise ValueError('Invalid dual-beam origins')
        angles = config['angles_deg']
        if not all(math.isfinite(a) and 0 < a < 90 for a in angles) or not angles[0] < angles[1] < angles[2]:
            raise ValueError('Invalid dual-beam directions')
        results = path.files['results.json'].data
        if set(results) != set(source_manifest['turbine_ids']):
            raise ValueError('Dual-beam turbines differ from source')
        if manifest['segment'] != source_manifest['segment']:
            raise ValueError('Dual-beam segment differs from source')
        self.core._validate_algorithm_contract(config, manifest, results)
        return source, dict(manifest=manifest, config=config, results=results)


def _load_package(path):
    """Verify every file on every load; parse only the same captured bytes."""
    expected = getattr(path, 'expected_manifest_sha256', None)
    result_path = Path(path).resolve()
    try:
        with _snapshot(result_path) as captured:
            return _load_snapshot(captured, expected_manifest_sha256=expected)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError('Invalid farm package: ' + str(exc)) from exc


def _load_snapshot(result, *, expected_manifest_sha256=None):
    result_path = result.path
    result_digest = result.files['manifest.json'].sha256
    if expected_manifest_sha256 and result_digest != expected_manifest_sha256:
        raise ValueError('Saved scene and farm package manifest differ; reload the result explicitly')
    snapshot, overlay = _farm.dual_beam_api().resolve_package(result)
    path, manifest = snapshot.path, snapshot.manifest
    if (manifest.get('schema') not in ('wfrl.farm-flex-review.v1','wfrl.farm-flex-review.v2','wfrl.farm-flex-review.v3')
            or manifest.get('status')!='REVIEW_ONLY'
            or manifest.get('turbine_ids') not in (['T1','T2','T3'], ['T1'])
            or (manifest.get('turbine_ids') == ['T1'] and (manifest.get('schema') != 'wfrl.farm-flex-review.v3' or manifest.get('interface_only') is not True))):
        raise ValueError('Expected a complete three-turbine flexible review package')
    required = {'geometry.npz', 'data.json', 'source-surfaces.json'}
    if manifest['schema'] != 'wfrl.farm-flex-review.v1':
        required.add('tower-motion.npz')
    if manifest['schema'] == 'wfrl.farm-flex-review.v3':
        required.update(('blade-reference.json', 'deflection-t1.json'))
    if not required <= manifest['files'].keys():
        raise ValueError('Missing farm package files: ' + ', '.join(sorted(required - manifest['files'].keys())))
    segment = manifest.get('segment', {})
    if (any(type(segment.get(key)) not in (int, float) or not math.isfinite(segment[key])
            for key in ('start_s', 'end_s')) or segment['start_s'] >= segment['end_s']):
        raise ValueError('Invalid farm package segment')
    if 'layout_m' in manifest:
        layout = np.asarray(manifest['layout_m'])
        if (layout.shape != (len(manifest['turbine_ids']), 3) or layout.dtype.kind not in 'fiu'
                or not np.isfinite(layout).all()):
            raise ValueError('Invalid farm turbine layout_m')
    source_hashes = snapshot.hashes
    telemetry = snapshot.files.get('telemetry.json')
    verified_source = _farm._VerifiedSource(path, manifest, source_hashes,
        telemetry.data if telemetry is not None else None)
    # The verified source owns parsed geometry; each load owns its result layer.
    # resolve_package independently validates sidecars and their source inventory.
    key = (path, source_hashes['manifest.json'])
    parsed = _farm._PACKAGE_CACHE.get(key)
    if parsed is None:
        parsed = _farm._parse_package(snapshot, manifest)
    try:
        from ._vendor.lidar.replay import ReplayReader
    except ImportError:
        from wfrl.lidar.replay import ReplayReader
    readers = {}
    for tid in manifest['turbine_ids']:
        # Public readers own their mutable rows; cached source rows must not
        # be reachable through a scene's reader or its returned samples.
        reader = ReplayReader(SimpleNamespace(
            manifest=deepcopy({**manifest, 'turbine_id': tid}), **parsed.contents[tid]))
        reader.package = _ReaderPackage(manifest=reader.package.manifest,
                                        _payloads=parsed.reader_data[tid])
        readers[tid] = reader
        if overlay is not None:
            # The resolver checked all turbines together. Each reader validates
            # its own rows without rescanning the other turbines' algorithms.
            turbine_overlay = {**overlay, 'results': {tid: overlay['results'][tid]}}
            readers[tid] = _farm.dual_beam_api().DualBeamReader(reader, turbine_overlay, tid)
    if overlay is not None:
        manifest = {**manifest, 'measurement_mode': 'dual_beam',
                    'dual_beam_calibration': overlay['config'],
                    'dual_beam': overlay['manifest']}
    tower_data = None if parsed.tower_motion is None else dict(parsed.tower_motion)
    _farm._read_telemetry(path, manifest, parsed.times, readers, _source=verified_source)
    from .deflection import read_comparison
    comparison = read_comparison(snapshot, manifest, parsed.times, parsed.poses,
        rigid_poses=parsed.poses, tower_motion=tower_data, blade_reference=parsed.reference,
        _verified=snapshot)
    validated_json = frozenset((name, file.sha256) for package in (snapshot, result)
                               for name, file in package.files.items() if name.endswith('.json'))
    _farm._PACKAGE_CACHE[key] = replace(parsed, validated_json=validated_json)
    _farm._PACKAGE_CACHE.move_to_end(key)
    while len(_farm._PACKAGE_CACHE) > _farm._PACKAGE_CACHE_SIZE:
        _farm._PACKAGE_CACHE.popitem(last=False)
    return _farm._LoadedPackage(manifest, parsed.times, parsed.transforms, parsed.poses,
                          readers, path, result_path, overlay, tower_data,
                          deepcopy(parsed.reference), verified_source, comparison,
                          result_digest)


def _parse_package(path, manifest):
    """Parse and validate geometry, motion and independent support/reference data."""
    with np.load(path.parse_path / 'geometry.npz', allow_pickle=False) as data:
        if len(data.files) != len(set(data.files)):
            raise ValueError('Duplicate farm geometry arrays')
        times=data['times']
        transforms=data['transforms']
        poses=data['poses']
    if any(array.dtype.kind not in 'fiu' for array in (times, transforms, poses)) or times.ndim != 1:
        raise ValueError('Invalid farm geometry numeric arrays')
    n=len(times)
    turbines=len(manifest['turbine_ids'])
    if (n<2 or times.ndim!=1 or not np.isfinite(times).all() or np.any(np.diff(times)<=0)
            or transforms.shape!=(n,turbines,3,19,3,4) or poses.shape!=(n,turbines,6)
            or not np.isfinite(transforms).all() or not np.isfinite(poses).all()
            or times[0]!=manifest['segment']['start_s'] or times[-1]!=manifest['segment']['end_s']):
        raise ValueError('Incomplete farm geometry or time axis')
    rotations = transforms[..., :3]
    # R.T @ R is symmetric: validate its six distinct entries without forming
    # the full matrix for every blade station. The scalar triple product is det(R).
    for i in range(3):
        for j in range(i, 3):
            dot = np.einsum('...k,...k->...', rotations[..., i], rotations[..., j])
            if np.any(np.abs(dot - (i == j)) > 2e-5):
                raise ValueError('Invalid farm blade rotation')
    if np.any(np.sum(np.cross(rotations[..., 0, :], rotations[..., 1, :])
                     * rotations[..., 2, :], axis=-1) < .9999):
        raise ValueError('Invalid farm blade rotation')
    from .prebend import read_reference
    reference = read_reference(path.parse_path, manifest)
    if reference is not None and 'deflection-t1.json' not in manifest['files']:
        raise ValueError('BeamDyn package requires mapped deflection data')
    from .tower_motion import read_tower
    tower_data = read_tower(path.parse_path, manifest, times)
    if tower_data is not None and any(value.dtype.kind not in 'fiu' for value in tower_data.values()):
        raise ValueError('Invalid farm tower numeric arrays')
    contents = path.files['data.json'].data
    _farm._validate_replay_contents(manifest, contents, _json_validated=True)
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
    # Only internally serialized, validated JSON is unpickled for reader-owned
    # mutable rows. Package bytes are never interpreted as pickle input.
    reader_data = {tid: {name: pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
                        for name, value in payload.items()}
                   for tid, payload in contents.items()}
    return _farm._ParsedPackage(times, transforms, poses, contents, tower_data, reference, reader_data)


def _validate_replay_contents(manifest, contents, *, _json_validated=False):
    """Validate review observations and derive B2 state without READY evidence rules."""
    try:
        from ._vendor.lidar import replay
    except ImportError:
        from wfrl.lidar import replay
    require, finite, vector = replay._require, replay._finite, replay._vector
    if _json_validated:
        def finite(value):
            # Strict decoding already checked every float, including metadata.
            # Integers still require representability; booleans are not numbers.
            return type(value) is float or (type(value) is int and math.isfinite(value))

        def vector(value):
            return isinstance(value, list) and len(value) == 3 and all(finite(x) for x in value)
    try:
        if not _json_validated:
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
    from .farm_flex_scene import _validate_scene_manifest
    package = _farm._load_package(path)
    _validate_scene_manifest(package.manifest, overlay=package.overlay)
    return package
