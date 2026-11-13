"""Recorded-tip boundary contracts using explicit synthetic file fixtures."""
from copy import deepcopy
import hashlib
import json
import threading
from types import SimpleNamespace as NS

import numpy as np
import pytest

from wfrl.blender_bridge import tip_replay as replay


COORDINATES = 'FAST inertial: x downwind, y lateral, z up; tower center origin'


def snapshot():
    def channel(value, unit):
        return dict(value=value, unit=unit, validity='valid', error=None, fidelity='EXPORTED',
                    provenance={'file': 'fixture', 'channel': 'fixture'},
                    source_age_seconds=0., stale_after_seconds=2.)
    geometry = dict(time_s=3., source='FAST.Farm', coordinate_system=COORDINATES,
                    blades=[dict(blade_id=bid, sections_m=[[bid, 0., 0.], [bid, 0., 63.]],
                                 tip_m=[bid, 0., 63.]) for bid in (1, 2, 3)])
    return dict(timestamp=dict(value=3., timebase='simulation_seconds'), turbines=[dict(
        turbine_id='T1', channels=dict(blade_geometry=channel(geometry, 'm'),
                                      rotor_speed=channel(12., 'rpm'), yaw=channel(180., 'deg')))])


@pytest.mark.parametrize('name,unit', [('blade_geometry', 'cm'), ('rotor_speed', 'rad/s'), ('yaw', 'rad')])
def test_snapshot_rejects_wrong_units(name, unit):
    payload = snapshot()
    payload['turbines'][0]['channels'][name]['unit'] = unit
    with pytest.raises(ValueError, match='unit'):
        replay.sample_from_snapshot(payload)


@pytest.mark.parametrize('name', ['blade_geometry', 'rotor_speed', 'yaw'])
@pytest.mark.parametrize('field,value', [('source_age_seconds', float('nan')),
                                       ('source_age_seconds', -1.), ('source_age_seconds', 2.),
                                       ('stale_after_seconds', float('nan')),
                                       ('stale_after_seconds', float('inf')),
                                       ('source_age_seconds', True), ('fidelity', 'SYNTH')])
def test_snapshot_rejects_invalid_or_stale_channel_metadata(name, field, value):
    payload = snapshot()
    payload['turbines'][0]['channels'][name][field] = value
    with pytest.raises(ValueError):
        replay.sample_from_snapshot(payload)


@pytest.mark.parametrize('value', [float('inf'), -float('inf'), True, None, '3'])
def test_snapshot_requires_finite_numeric_simulation_time(value):
    payload = snapshot()
    payload['timestamp']['value'] = value
    payload['turbines'][0]['channels']['blade_geometry']['value']['time_s'] = value
    with pytest.raises(ValueError, match='time'):
        replay.sample_from_snapshot(payload)


@pytest.mark.parametrize('name', ['blade_geometry', 'rotor_speed', 'yaw'])
def test_missing_channels_are_reported_without_fabricating_values(name):
    payload = snapshot()
    del payload['turbines'][0]['channels'][name]
    with pytest.raises(ValueError, match=name):
        replay.sample_from_snapshot(payload)


@pytest.mark.parametrize('name', ['rotor_speed', 'yaw'])
@pytest.mark.parametrize('value', [None, True, '12', float('nan')])
def test_motion_channels_require_numeric_finite_values(name, value):
    payload = snapshot()
    payload['turbines'][0]['channels'][name]['value'] = value
    with pytest.raises(ValueError, match=name):
        replay.sample_from_snapshot(payload)


def test_tip_must_match_last_section_with_absolute_tolerance():
    payload = snapshot()
    blade = payload['turbines'][0]['channels']['blade_geometry']['value']['blades'][0]
    blade['sections_m'][-1][0] = 1_000_000.
    blade['tip_m'][0] = 1_000_001.
    with pytest.raises(ValueError, match='sections/tip'):
        replay.sample_from_snapshot(payload)


@pytest.mark.parametrize('value', [True, '63'])
def test_blade_coordinates_require_numbers(value):
    payload = snapshot()
    blade = payload['turbines'][0]['channels']['blade_geometry']['value']['blades'][0]
    blade['sections_m'][-1][2] = blade['tip_m'][2] = value
    with pytest.raises(ValueError, match='sections/tip'):
        replay.sample_from_snapshot(payload)


def test_valid_snapshot_preserves_world_coordinates_units_and_zero_rpm():
    payload = snapshot()
    payload['turbines'][0]['channels']['rotor_speed']['value'] = 0.
    before = deepcopy(payload)
    sample = replay.sample_from_snapshot(payload)
    assert (sample['time_s'], sample['rpm'], sample['yaw_deg']) == (3., 0., 180.)
    for bid in range(3):
        np.testing.assert_array_equal(sample['blades'][bid]['P'], [bid + 1, 0., 63.])
    assert payload == before


def surface_bytes(offset=0.):
    points = [(offset + x, y, z) for z in range(19) for x, y in ((0, 0), (1, 0), (0, 1))]
    coordinates = ' '.join(str(value) for point in points for value in point)
    connectivity = ' '.join(map(str, range(57)))
    offsets = ' '.join(map(str, range(3, 58, 3)))
    return (f'<VTKFile><PolyData><Piece NumberOfPoints="57" NumberOfPolys="19">'
            f'<Points><DataArray>{coordinates}</DataArray></Points><Polys>'
            f'<DataArray Name="connectivity">{connectivity}</DataArray>'
            f'<DataArray Name="offsets">{offsets}</DataArray>'
            '</Polys></Piece></PolyData></VTKFile>').encode('utf-8')


@pytest.fixture
def raw_package(tmp_path, monkeypatch):
    def create(times=(0., .1, .2), surface=None):
        package = tmp_path / 'package'
        package.mkdir(exist_ok=True)
        (package / 'manifest.json').write_text('{}', encoding='utf-8')
        run = tmp_path / 'fixture-run'
        run.mkdir(exist_ok=True)
        def write(relative, data):
            path = run / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest())
        ed = 'FarmInputs/ElastoDyn.dat'
        geometry = write(ed, b'-5 ShftTilt\n-5.019 OverHang\n87.6 TowerHt\n1.96256 Twr2Shft\n63 TipRad\n')
        config = dict(fps=10., span_refined=False,
                      inputs=[dict(path=ed, sha256=geometry['sha256'])])
        sources = [write('run_config.json', json.dumps(config).encode('utf-8')),
                   write('exit_status.json', b'{"exit_code": 0}'),
                   write('solver.log', b'FAST.Farm terminated normally.')]
        hashes = []
        for frame in {round(t * 10) for t in times}:
            for bid in (1, 2, 3):
                relative = f'FarmInputs/vtk/Case.T1.Blade{bid}Surface.{frame:05d}.vtp'
                entry = write(relative, surface if surface is not None else surface_bytes(bid))
                hashes.append(dict(path=relative, sha256=entry['sha256']))
        sources.append(write('surface_hashes.json', json.dumps(hashes).encode('utf-8')))
        manifest = dict(source='FAST.Farm', model={'id': 'nrel5mw'}, run_id=run.name,
                        turbine_id='T1', segment=dict(start_s=times[0], end_s=times[-1]), raw_sources=sources)
        motion = [dict(time_s=t, rotor_speed_rpm=12., yaw_deg=180., azimuth_deg=t * 72.,
                       pitch_deg=[1., 2., 3.]) for t in times]
        monkeypatch.setattr(replay.ReplayPackage, 'load', lambda path: NS(manifest=manifest, motion=motion))
        return package, run
    return create


@pytest.mark.parametrize('times', [(.05, .15, .25), (0., .1000001, .2000002),
                                   (0., .1, .1, .2), (0., .2, .1, .3)])
def test_motion_clock_must_match_integer_surface_frames(raw_package, times):
    package, _ = raw_package(times)
    with pytest.raises(ValueError, match='clock|frame'):
        replay.FastFarmTipReplay(package)


def test_surface_parser_uses_the_bytes_whose_digest_was_verified(raw_package, monkeypatch):
    package, run = raw_package()
    reader = replay.FastFarmTipReplay(package)
    target = run / 'FarmInputs/vtk/Case.T1.Blade1Surface.00000.vtp'
    original_parser = replay.read_surface
    def replace_before_parse(source):
        target.write_bytes(surface_bytes(90.))
        return original_parser(source)
    monkeypatch.setattr(replay, 'read_surface', replace_before_parse)
    sample = reader.at_index(0)
    np.testing.assert_array_equal(sample['blades'][0]['P'], [4. / 3., 1. / 3., 18.])


def test_truncated_surface_error_identifies_file_and_does_not_cache(raw_package):
    package, _ = raw_package(surface=b'<VTKFile><PolyData>')
    reader = replay.FastFarmTipReplay(package)
    with pytest.raises(ValueError, match=r'Blade1Surface\.00000\.vtp'):
        reader.at_index(0)
    assert not reader._cache


def test_missing_surface_error_identifies_file_and_does_not_cache(raw_package):
    package, run = raw_package()
    reader = replay.FastFarmTipReplay(package)
    (run / 'FarmInputs/vtk/Case.T1.Blade1Surface.00000.vtp').unlink()
    with pytest.raises((OSError, ValueError), match=r'Blade1Surface\.00000\.vtp'):
        reader.at_index(0)
    assert not reader._cache


def test_invalid_metadata_error_identifies_source_file(raw_package):
    package, run = raw_package()
    raw = b'{"fps":'
    (run / 'run_config.json').write_bytes(raw)
    manifest = replay.ReplayPackage.load(package).manifest
    for source in manifest['raw_sources']:
        if source['path'].endswith('run_config.json'):
            source['sha256'] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match='run_config.json'):
        replay.FastFarmTipReplay(package)


def test_overflowing_section_centroids_are_not_valid_geometry(raw_package):
    package, _ = raw_package(surface=surface_bytes(1e308))
    reader = replay.FastFarmTipReplay(package)
    with np.errstate(over='ignore'), pytest.raises(ValueError, match='section'):
        reader.at_index(0)
    assert not reader._cache


def test_reentrant_trainer_start_does_not_create_another_worker(raw_package):
    package, _ = raw_package()
    entered, release = threading.Event(), threading.Event()
    callbacks = []
    def on_snapshot(sample):
        callbacks.append(sample)
        entered.set()
        assert release.wait(3.)
    scene = NS(backend='fastfarm', turbines=[NS(id='T1')])
    trainer = replay.RecordedTipTrainer(scene, tip_package=package, on_snapshot=on_snapshot)
    trainer.start()
    original_thread = trainer._thread
    try:
        assert entered.wait(3.)
        with pytest.raises(RuntimeError, match='running'):
            trainer.start()
        assert trainer._thread is original_thread
    finally:
        trainer.stop(timeout=0)
        release.set()
        trainer.stop()
        original_thread.join(3.)
    assert not trainer.running
    assert len(callbacks) == 1


def test_failed_thread_start_releases_running_state(raw_package, monkeypatch):
    package, _ = raw_package()
    scene = NS(backend='fastfarm', turbines=[NS(id='T1')])
    trainer = replay.RecordedTipTrainer(scene, tip_package=package, on_snapshot=lambda _: None)
    def fail_start(_thread):
        raise RuntimeError('thread unavailable')
    monkeypatch.setattr(threading.Thread, 'start', fail_start)
    with pytest.raises(RuntimeError, match='thread unavailable'):
        trainer.start()
    assert not trainer.running
    trainer.stop()


def test_recorded_normal_geometry_and_wire_values_are_unchanged(raw_package):
    package, _ = raw_package()
    reader = replay.FastFarmTipReplay(package)
    sample = reader.at_index(1)
    assert (sample['time_s'], sample['rpm'], sample['yaw_deg'], sample['pitch_deg']) == (.1, 12., 180., [1., 2., 3.])
    for bid in range(3):
        expected = np.array([[bid + 1 + 1. / 3., 1. / 3., z] for z in range(19)])
        np.testing.assert_array_equal(sample['blades'][bid]['sections'], expected)
    decoded = replay.sample_from_snapshot(reader.wire_payload(1))
    np.testing.assert_array_equal(decoded['blades'][0]['P'], sample['blades'][0]['P'])
    assert reader.at_index(1) is sample
    reader.at_index(0)
    reader.at_index(2)
    assert len(reader._cache) == 2
