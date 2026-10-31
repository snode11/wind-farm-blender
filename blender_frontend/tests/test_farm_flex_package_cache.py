"""Verified parsed sources can be reused without sharing scene readers."""
from collections import OrderedDict
import hashlib
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from wfrl.lidar.replay import precompute

from wfrl_blender import clearance_replay, farm_flex, tip_tracking


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(farm_flex, '_PACKAGE_CACHE', OrderedDict())


@pytest.fixture
def packages(tmp_path):
    def make(name='source'):
        path = tmp_path / name
        path.mkdir()
        times = np.array([0., 1., 2.])
        transforms = np.zeros((3, 3, 3, 19, 3, 4), dtype=np.float32)
        transforms[..., :3] = np.eye(3)
        np.savez(path / 'geometry.npz', times=times, transforms=transforms,
                 poses=np.zeros((3, 3, 6), dtype=np.float32))
        nacelle = np.zeros((3, 3, 3, 4))
        nacelle[..., :3] = np.eye(3)
        tower = np.zeros((3, 3, 2, 3, 4))
        tower[..., :3] = np.eye(3)
        np.savez(path / 'tower-motion.npz', times=times, heights=[0., 87.6],
                 transforms=tower, nacelle=nacelle)
        contents = {}
        replay = dict(threshold_m=7., hysteresis_m=.1, max_hold_s=5., passage_margin=1.25)
        layout = [[0., 0., 0.], [504., 0., 0.], [1008., 0., 0.]]
        for k, tid in enumerate(('T1', 'T2', 'T3')):
            motion = [dict(time_s=t, yaw_deg=0., azimuth_deg=0., rotor_speed_rpm=0.,
                           pitch_deg=[0., 0., 0.], nacelle_transform=nacelle[i, k].tolist(),
                           nacelle_position_m=(np.array(layout[k]) + [0., 0., 87.6]).tolist())
                      for i, t in enumerate(times)]
            cumulative, statistics = precompute([], motion, replay)
            contents[tid] = dict(motion=motion, measurements=[],
                                 cumulative=cumulative, statistics=statistics)
        (path / 'data.json').write_text(json.dumps(contents))
        (path / 'source-surfaces.json').write_text('{}')
        manifest = dict(schema='wfrl.farm-flex-review.v2', status='REVIEW_ONLY',
                        turbine_ids=list(contents), layout_m=layout,
                        replay=replay,
                        tower_model='elastodyn-flexible', segment=dict(start_s=0., end_s=2.),
                        files={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in path.iterdir()})
        (path / 'manifest.json').write_text(json.dumps(manifest))
        return path
    return make


@pytest.fixture
def parses(monkeypatch):
    parse = Mock(wraps=farm_flex._parse_package)
    monkeypatch.setattr(farm_flex, '_parse_package', parse)
    return parse


def test_second_load_reuses_parsed_arrays_and_resolves_again(packages, parses, monkeypatch):
    path = packages()
    api = farm_flex.dual_beam_api()
    resolve = Mock(wraps=api.resolve_package)
    monkeypatch.setattr(api, 'resolve_package', resolve)
    first, second = farm_flex._load_package(path), farm_flex._load_package(path)
    assert parses.call_count == 1 and resolve.call_count == 2
    for name in ('times', 'transforms', 'poses'):
        assert getattr(first, name) is getattr(second, name)
    assert first.tower_motion is not second.tower_motion
    assert first.tower_motion['nacelle'] is second.tower_motion['nacelle']


@pytest.mark.parametrize('name', ['geometry.npz', 'data.json', 'source-surfaces.json',
                                'tower-motion.npz'])
def test_warm_load_checks_every_file_even_with_same_size_and_mtime(packages, parses, name):
    path = packages()
    farm_flex._load_package(path)
    target = path / name
    stat = target.stat()
    raw = target.read_bytes()
    target.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match='Farm package integrity mismatch: ' + name):
        farm_flex._load_package(path)
    assert parses.call_count == 1


def test_valid_file_and_manifest_replacement_misses_cache(packages, parses):
    path = packages()
    first = farm_flex._load_package(path)
    contents = json.loads((path / 'data.json').read_bytes())
    contents['T1']['motion'][0]['yaw_deg'] = .0005
    (path / 'data.json').write_text(json.dumps(contents))
    manifest = json.loads((path / 'manifest.json').read_bytes())
    manifest['files']['data.json'] = hashlib.sha256((path / 'data.json').read_bytes()).hexdigest()
    (path / 'manifest.json').write_text(json.dumps(manifest))
    second = farm_flex._load_package(path)
    assert parses.call_count == 2
    assert first.poses[0, 0, 0] == 0.
    assert second.poses[0, 0, 0] == .0005
    assert second.readers['T1'].at(0.)['motion']['yaw_deg'] == .0005


def test_resolved_path_is_part_of_cache_identity(packages, parses):
    a, b = packages('a'), packages('b')
    first, other = farm_flex._load_package(a), farm_flex._load_package(b)
    alias = a.parent / 'alias'
    alias.symlink_to(a, target_is_directory=True)
    aliased = farm_flex._load_package(alias)
    assert parses.call_count == 2
    assert first.times is aliased.times and first.times is not other.times
    assert aliased.result_path == a.resolve()


def test_cache_is_bounded_and_uses_recently_loaded_entry(packages, parses):
    a, b, c = (packages(name) for name in ('a', 'b', 'c'))
    for path in (a, b, a, c):
        farm_flex._load_package(path)
    assert parses.call_count == 3 and len(farm_flex._PACKAGE_CACHE) == 2
    farm_flex._load_package(a)
    assert parses.call_count == 3
    farm_flex._load_package(b)
    assert parses.call_count == 4 and len(farm_flex._PACKAGE_CACHE) == 2


def test_loads_have_distinct_readers_and_independent_sample_outputs(packages):
    path = packages()
    first, second = farm_flex._load_package(path), farm_flex._load_package(path)
    assert first.readers is not second.readers
    for tid in first.readers:
        a, b = first.readers[tid], second.readers[tid]
        assert a is not b and a.package is not b.package
        assert a not in second.readers.values()
        assert a._motion_times is not b._motion_times
        sample = a.at(0.)
        sample['motion']['pitch_deg'][0] = 99.
        assert b.at(0.)['motion']['pitch_deg'][0] == 0.
        assert a.at(0.)['motion']['pitch_deg'][0] == 0.


def assert_readonly(package):
    arrays = [package.times, package.transforms, package.poses,
              *package.tower_motion.values()]
    for array in arrays:
        assert not array.flags.writeable
        with pytest.raises(ValueError, match='read-only'):
            array.flat[0] = 99.


def test_all_cached_numeric_arrays_are_readonly_on_cold_and_warm_load(packages):
    path = packages()
    assert_readonly(farm_flex._load_package(path))
    assert_readonly(farm_flex._load_package(path))


def test_builtin_reference_and_comparison_accept_readonly_sources():
    from wfrl_blender.deflection import read_comparison, rigid_frame
    from wfrl_blender.prebend import bend_vertices
    from wfrl_blender.tower_motion import interpolate_transform

    package = farm_flex._load_package(farm_flex.default_package())
    assert_readonly(package)
    comparison = read_comparison(package.source_path, package.manifest,
        package.times, package.poses, rigid_poses=package.poses,
        tower_motion=package.tower_motion, blade_reference=package.reference)
    support = package.tower_motion['nacelle']
    interpolated = interpolate_transform(support[0], support[1], .5)
    rigid_frame(comparison['scalars'], package.poses[0, 0], 1, interpolated[0])
    bend_vertices([[0., 0., 63.]], package.reference)
    assert not np.shares_memory(interpolated, support)
    assert_readonly(package)


@pytest.mark.parametrize('variant', ['default_dual_package', 'default_dual_tls_package'])
def test_overlay_variants_and_readers_are_distinct(parses, variant):
    base = farm_flex._load_package(farm_flex.default_package())
    path = getattr(farm_flex, variant)()
    first, second = farm_flex._load_package(path), farm_flex._load_package(path)
    assert parses.call_count == 1
    assert first.source_path == base.source_path and first.result_path != base.result_path
    assert first.times is second.times and first.times is base.times
    assert first.overlay is not None and base.overlay is None
    for tid in first.readers:
        a, b = first.readers[tid], second.readers[tid]
        assert a is not b and a.motion_reader is not b.motion_reader
        assert a.at(a.start_s) == b.at(b.start_s)


def test_overlay_manifest_refreshes_identity_without_reparsing_source(tmp_path, parses):
    source = farm_flex.default_dual_package()
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest['source_package'] = str(farm_flex.default_package())
    for name in manifest['files']:
        (tmp_path / name).write_bytes((source / name).read_bytes())
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    first = farm_flex._load_package(tmp_path)
    assert farm_flex._load_package(tmp_path).times is first.times
    assert parses.call_count == 1
    manifest['provenance_label'] = 'Verified dual-beam review'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    second = farm_flex._load_package(tmp_path)
    assert second.times is first.times
    assert second.manifest['dual_beam']['provenance_label'] == 'Verified dual-beam review'
    assert 'provenance_label' not in first.manifest['dual_beam']
    assert parses.call_count == 1
    target = tmp_path / 'calibration.json'
    stat, raw = target.stat(), target.read_bytes()
    target.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match='Dual-beam result integrity mismatch: calibration.json'):
        farm_flex._load_package(tmp_path)
    assert parses.call_count == 1


def test_base_dual_and_tls_share_source_but_keep_results_independent(parses):
    paths = (farm_flex.default_package(), farm_flex.default_dual_package(),
             farm_flex.default_dual_tls_package())
    loaded = [farm_flex._load_package(path) for path in paths * 2]
    assert parses.call_count == 1
    for package in loaded:
        assert package.times is loaded[0].times
        assert package.transforms is loaded[0].transforms
        assert_readonly(package)
    assert loaded[0].overlay is None
    assert loaded[1].overlay['config']['reconstruction_method'] == 'hub-axis.v1'
    assert loaded[2].overlay['config']['reconstruction_method'] == 'hub-tls.v1'
    for index in (1, 2):
        reader = loaded[index].readers['T1']
        sample = reader.at(reader.start_s)
        original = sample['motion']['pitch_deg'][0]
        sample['motion']['pitch_deg'][0] = 99.
        assert reader.at(reader.start_s)['motion']['pitch_deg'][0] == original
        assert loaded[index + 3].readers['T1'] is not reader


@pytest.mark.parametrize('callback', ['replay_load_pre', 'farm_load_pre', 'unregister', 'history_pre'])
def test_cache_lifetime_matches_file_and_addon_lifetime(packages, monkeypatch, callback):
    import wfrl_blender
    from wfrl_blender import playback, radar_feedback

    farm_flex._load_package(packages())
    monkeypatch.setattr(farm_flex, 'detach', lambda **_kw: None)
    monkeypatch.setattr(tip_tracking, 'on_load_pre', lambda _: None)
    monkeypatch.setattr(tip_tracking, 'unregister', lambda: None)
    monkeypatch.setattr(radar_feedback, 'unregister', lambda: None)
    monkeypatch.setattr(wfrl_blender, 'native_camera_views',
                        SimpleNamespace(on_history_pre=lambda _: None), raising=False)
    monkeypatch.setattr(playback, 'owner_scene', lambda: None)
    monkeypatch.setattr(clearance_replay, '_READERS', {})
    monkeypatch.setattr(clearance_replay, '_END_REQUEST', None)
    handlers = SimpleNamespace(**{name: [] for name, _ in clearance_replay._HANDLERS})
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(app=SimpleNamespace(
        handlers=handlers, timers=SimpleNamespace(is_registered=lambda _: False))))
    callbacks = dict(replay_load_pre=lambda: clearance_replay.on_load_pre(None),
                     farm_load_pre=lambda: farm_flex.on_load_pre(None),
                     unregister=clearance_replay.unregister,
                     history_pre=lambda: clearance_replay.on_history_pre(None))
    callbacks[callback]()
    assert bool(farm_flex._PACKAGE_CACHE) == (callback == 'history_pre')
