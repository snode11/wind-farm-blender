"""Same-source preparation binds identities to consumed bytes and publishes complete files."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest
from PIL import Image
from unittest.mock import patch
from tests.radar_x100.test_cy_script_io_integrity import inject_text_write_failure


try:
    import cv2
except ImportError:
    def decode(raw, flags):
        with Image.open(io.BytesIO(bytes(raw))) as image:
            if flags == 1:
                image = image.convert('RGB')
            elif flags == 0:
                image = image.convert('L')
            return np.asarray(image).copy()
    # These I/O fixtures contain constant grayscale pixels; no fusion or
    # OpenCV numerical behavior is evaluated by this adapter.
    cv2 = NS(IMREAD_UNCHANGED=-1, IMREAD_COLOR=1, IMREAD_GRAYSCALE=0, imdecode=decode,
             imread=lambda path, flags: decode(Path(path).read_bytes(), flags) if Path(path).exists() else None,
             boxFilter=lambda image, depth, size: image.copy())
path = Path(__file__).resolve().parents[1] / 'scripts/reconstruction/prepare_mappo_same_source_texture.py'
spec = importlib.util.spec_from_file_location('_cy_texture_io', path)
texture = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, cv2=cv2):
    spec.loader.exec_module(texture)


@pytest.mark.parametrize('raw', [b'{', b'{"x": NaN}', b'{"x": 1e999}'])
def test_texture_json_rejects_invalid_data_with_path(tmp_path, raw):
    path = tmp_path / 'calibration.json'
    path.write_bytes(raw)
    with pytest.raises(ValueError, match=str(path)):
        texture.read_json(path)


def test_source_identity_size_and_hash_use_the_same_read(tmp_path, monkeypatch):
    path = tmp_path / 'source.json'
    path.write_bytes(b'original')
    read = Path.read_bytes
    def changing_read(self):
        raw = read(self)
        if self == path:
            self.write_bytes(b'changed source with different length')
        return raw
    monkeypatch.setattr(Path, 'read_bytes', changing_read)
    identity = texture.record(path)
    assert identity['sha256'] == hashlib.sha256(b'original').hexdigest()
    assert identity['size_bytes'] == len(b'original')


def test_failed_texture_copy_removes_partial_target(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.png', tmp_path / 'target.png'
    source.write_bytes(b'complete image')
    def fail_copy(source, target):
        Path(target).write_bytes(b'partial')
        raise OSError('disk full')
    monkeypatch.setattr(texture.shutil, 'copyfile', fail_copy)
    with pytest.raises(OSError, match='disk full'):
        texture.preserve_copy(source, target)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == [source]


def test_texture_copy_verifies_bytes_that_were_copied(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.png', tmp_path / 'target.png'
    source.write_bytes(b'validated source')
    def changed_copy(source, target):
        Path(target).write_bytes(b'different bytes')
    monkeypatch.setattr(texture.shutil, 'copyfile', changed_copy)
    with pytest.raises(ValueError, match='hash|identity|changed'):
        texture.preserve_copy(source, target)
    assert not target.exists()


def test_failed_texture_json_write_preserves_existing_result(tmp_path, monkeypatch):
    path = tmp_path / 'report.json'
    path.write_bytes(b'prior report')
    inject_text_write_failure(monkeypatch)
    with pytest.raises(OSError, match='short write'):
        texture.write_json(path, {'value': 'new report'})
    assert path.read_bytes() == b'prior report'
    assert list(tmp_path.iterdir()) == [path]


def test_failed_runner_write_preserves_existing_script(tmp_path, monkeypatch):
    runner = tmp_path / 'texture_diagnostic_runner.py'
    runner.write_bytes(b'prior complete script')
    inject_text_write_failure(monkeypatch)
    with pytest.raises(OSError, match='short write'):
        texture.write_diagnostic_runner(tmp_path, tmp_path / 'engine')
    assert runner.read_bytes() == b'prior complete script'
    assert list(tmp_path.iterdir()) == [runner]


def test_failed_diagnostic_write_preserves_existing_report(tmp_path, monkeypatch):
    runner = texture.write_diagnostic_runner(tmp_path, tmp_path / 'engine')
    code = runner.read_text()
    output = tmp_path / 'sampling_diagnostics.json'
    output.write_bytes(b'prior complete diagnostic')
    fake = NS(validate_saved_cameras=lambda *a: None, sample_view=lambda *a: None,
              frame_views=lambda *a, **kw: [], main=lambda: None)
    monkeypatch.setitem(sys.modules, 'texture', fake)
    inject_text_write_failure(monkeypatch)
    with pytest.raises(OSError, match='short write'):
        exec(compile(code, str(runner), 'exec'), {'__file__': str(runner)})
    assert output.read_bytes() == b'prior complete diagnostic'


@pytest.fixture
def package_data(tmp_path):
    out = tmp_path / 'output'
    identities = [dict(frame=k, t=k/10, cameras=texture.CAMERA_NAMES, saved_calibration_checked=True) for k in range(601)]
    frames = [dict(frame=k, t=k/10, cameras=[{'name': name} for name in texture.CAMERA_NAMES]) for k in range(601)]
    texture.write_json(out / 'texture/texture.json', {'atlas': {'H': 4, 'W': 8, 'dr': 1., 'r0': 0},
        'params': {}, 'input_contract': {'frames': identities,
                                      'reconstruction_input_provenance_checked': True}})
    for name in ('recon.json', 'input/input_bundle.json'):
        texture.write_json(out / name, {'frames': frames})
    texture.write_json(out / 'model_compatibility.json', {})
    texture.write_json(out / 'source_integrity.json', {'source_files': {}, 'model_compatibility': {
        'vendor_model': {'sha256': 'a'*64}, 'source_model': {}, 'texture_model': {}},
        'source_capture': {'source_hashes': {'assets/mappo/manifest.json': 'b'*64}}})
    for name in ('model.py', 'atlas.py'):
        path = out / 'engine/blade_recon0.2' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# fixture\n')
    data = dict(has=np.ones((3, 4, 8), bool), nview=np.ones((3, 4, 8), int),
                val=np.ones((3, 4, 8)), raw=np.ones((3, 4, 8)), wmax=np.ones((3, 4, 8)))
    np.savez(out / 'texture/texture_data.npz', **data)
    for blade in (1, 2, 3):
        Image.fromarray(np.full((4, 8, 4), 255, np.uint8)).save(out / f'texture/blade{blade}_tex.png')
    return out, tmp_path / 'asset'


def test_texture_package_collision_leaves_old_asset_and_no_new_files(package_data):
    out, asset = package_data
    asset.mkdir()
    (asset / 'manifest.json').write_text('{"owner":"existing"}')
    with pytest.raises(ValueError, match='differs'):
        texture.package(NS(asset=asset), out)
    assert list(asset.iterdir()) == [asset / 'manifest.json']
    assert not (out / 'texture_quality.json').exists()


def test_missing_texture_image_reports_path(package_data):
    out, asset = package_data
    path = out / 'texture/blade1_tex.png'
    path.unlink()
    with pytest.raises((ValueError, OSError), match=str(path)):
        texture.package(NS(asset=asset), out)
    assert not asset.exists()


def test_texture_package_rejects_repeated_frame_identity(package_data):
    out, asset = package_data
    path = out / 'texture/texture.json'
    value = texture.read_json(path)
    value['input_contract']['frames'][1] = value['input_contract']['frames'][0]
    texture.write_json(path, value)
    with pytest.raises(ValueError, match='frame|clock'):
        texture.package(NS(asset=asset), out)
    assert not asset.exists()


def test_texture_package_rejects_wrong_array_shape(package_data):
    out, asset = package_data
    path = out / 'texture/texture_data.npz'
    with np.load(path) as archive:
        data = {name: archive[name] for name in archive.files}
    data['val'] = np.zeros((3, 1, 1))
    np.savez(path, **data)
    with pytest.raises(ValueError, match=str(path)):
        texture.package(NS(asset=asset), out)
    assert not asset.exists()


def test_package_publication_failure_rolls_back_created_files(package_data, monkeypatch):
    from wfrl.camera_video import publication
    out, asset = package_data
    publish = publication.publish
    count = 0
    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('publish failed')
        return publish(*args, **kwargs)
    monkeypatch.setattr(publication, 'publish', fail_second)
    with pytest.raises(OSError, match='publish failed'):
        texture.package(NS(asset=asset), out)
    assert not asset.exists()
    assert not (out / 'texture_quality.json').exists()


def test_texture_package_closes_npz_on_validation_failure(package_data, monkeypatch):
    out, asset = package_data
    load = np.load
    opened = []
    def track(*args, **kwargs):
        result = load(*args, **kwargs)
        opened.append(result)
        return result
    monkeypatch.setattr(np, 'load', track)
    (out / 'texture/blade1_tex.png').unlink()
    with pytest.raises(Exception):
        texture.package(NS(asset=asset), out)
    assert opened and all(archive.zip is None for archive in opened)


@pytest.mark.parametrize('flag,value', [('--atlas-dr', 'nan'), ('--atlas-dr', '0'), ('--atlas-cols', '0')])
def test_texture_cli_rejects_invalid_atlas_before_preparing(flag, value, monkeypatch):
    called = []
    monkeypatch.setattr(sys, 'argv', ['prepare', '--prepare-only', flag, value])
    monkeypatch.setattr(texture, 'prepare', lambda args: (called.append(args) or (None, None)))
    with pytest.raises((SystemExit, ValueError)):
        texture.main()
    assert not called


@pytest.fixture
def prepared_source(tmp_path, monkeypatch):
    root, source, out = tmp_path / 'repo', tmp_path / 'source', tmp_path / 'prepared'
    for directory, names in (('blade_recon', ('model.py', 'camera.py', 'input_bundle.py')),
            ('blade_recon0.2', ('model.py', 'atlas.py', 'texture.py')),
            ('blender_frontend/wfrl_blender/_vendor/blade_recon', ('model.py',))):
        for name in names:
            path = root / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# model fixture\n')
    monkeypatch.setattr(texture, 'ROOT', root)
    monkeypatch.setattr(texture, 'compatibility', lambda original, source_model, texture_model, vendor_model, *args:
        {'source_model': texture.record(source_model), 'texture_model': texture.record(texture_model),
         'vendor_model': texture.record(vendor_model)})
    rgb = tmp_path / 'pixel.png'
    Image.fromarray(np.zeros((4, 8, 3), np.uint8)).save(rgb)
    mask = tmp_path / 'mask.png'
    Image.fromarray(np.zeros((4, 8), np.uint8)).save(mask)
    original, calibration = [], []
    for k in range(601):
        fields = dict(frame=k, t=k/10, sim_t=117+k/10, blender_frame=1+6*k,
                      T_world_from_model=np.eye(4).tolist())
        old_cameras, cameras = [], []
        for name in texture.CAMERA_NAMES:
            old_cameras.append(dict(camera=name, rgb_sha256=texture.digest(rgb), mask_sha256=texture.digest(mask)))
            cameras.append(dict(name=name, W=8, H=4, K=np.eye(3).tolist(), T_cv_from_world=np.eye(4).tolist()))
            for prefix, image in ((name, rgb), ('masks/' + name, mask)):
                path = source / 'input' / prefix / f'{k:06d}.png'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(image.read_bytes())
        original.append(dict(fields, state=[0.], observed_sections=[], cameras=old_cameras))
        calibration.append(dict(fields, cameras=cameras))
    texture.write_json(source / 'reconstruction/recon.json', dict(frames=original, turbine={}, source={}))
    texture.write_json(source / 'input/calibration.json', dict(frames=calibration, turbine={}))
    texture.write_json(source / 'reconstruction/solver_manifest.json', dict(
        calibration_sha256=texture.digest(source / 'input/calibration.json'),
        algorithm_sources={'model.py': {'sha256': texture.digest(root / 'blade_recon/model.py')}}))
    texture.write_json(source / 'input/capture_report.json', {'model_config': {}})
    (source / 'REPORT.md').write_text('source evidence\n')
    return NS(source=source, output=out, atlas_dr=.05, atlas_cols=8)


def test_preparation_hashes_the_json_snapshot_actually_used(prepared_source, monkeypatch):
    args = prepared_source
    path = args.source / 'input/calibration.json'
    original_hash = texture.digest(path)
    read = texture.read_json
    def replace_after_parse(path, **kwargs):
        value = read(path, **kwargs)
        if path == args.source / 'input/calibration.json':
            path.write_bytes(b'{"different":"calibration"}')
        return value
    monkeypatch.setattr(texture, 'read_json', replace_after_parse)
    texture.prepare(args)
    report = read(args.output / 'source_integrity.json')
    bundle = read(args.output / 'input/input_bundle.json')
    assert report['source_files']['input/calibration.json']['sha256'] == original_hash
    assert bundle['provenance']['calibration']['sha256'] == original_hash


def test_preparation_keeps_validated_images_when_source_changes_later(prepared_source, monkeypatch):
    args = prepared_source
    first = args.source / 'input/T1Down/000000.png'
    original = first.read_bytes()
    copy_file = texture.preserve_copy
    def change_after_last_image(source, target, *a, **kw):
        result = copy_file(source, target, *a, **kw)
        if str(source).endswith('masks/NacelleT1/000600.png'):
            first.write_bytes(b'changed after validation')
        return result
    monkeypatch.setattr(texture, 'preserve_copy', change_after_last_image)
    texture.prepare(args)
    assert (args.output / 'input/T1Down/000000.png').read_bytes() == original
    assert texture.read_json(args.output / 'source_integrity.json')['input_copy_modes'] == ['byte_copy']


def test_preparation_publishes_completion_after_input_files(prepared_source, monkeypatch):
    from wfrl.camera_video import publication
    args = prepared_source
    publish = publication.publish
    def check(source, target, **kwargs):
        if target == args.output / 'source_integrity.json':
            assert len(list((args.output / 'input').rglob('*.png'))) == 2404
            assert (args.output / 'input/input_bundle.json').exists()
        return publish(source, target, **kwargs)
    monkeypatch.setattr(publication, 'publish', check)
    texture.prepare(args)


def test_preparation_rejects_corrupt_image_even_when_hash_matches(prepared_source):
    args = prepared_source
    path = args.source / 'input/T1Down/000000.png'
    path.write_bytes(b'truncated PNG')
    original_path = args.source / 'reconstruction/recon.json'
    original = texture.read_json(original_path)
    original['frames'][0]['cameras'][0]['rgb_sha256'] = texture.digest(path)
    texture.write_json(original_path, original)
    with pytest.raises((ValueError, OSError), match=str(path)):
        texture.prepare(args)
    assert not args.output.exists()


def test_failed_preparation_does_not_publish_partial_bundle(prepared_source, monkeypatch):
    args = prepared_source
    copy_file = texture.preserve_copy
    def fail_last_image(source, target, *a, **kw):
        if str(source).endswith('NacelleT1/000600.png'):
            raise OSError('injected last-image failure')
        return copy_file(source, target, *a, **kw)
    monkeypatch.setattr(texture, 'preserve_copy', fail_last_image)
    with pytest.raises(OSError, match='last-image failure'):
        texture.prepare(args)
    assert not args.output.exists()


def test_model_identity_matches_executed_source_bytes(tmp_path):
    code = '''import numpy as np
STATE_NAMES = ['x']
class TurbineConfig:
    @classmethod
    def from_dict(cls, value): return cls()
    def to_dict(self): return {}
class Template:
    r = foils = chord = twist = np.ones(3)
    def faces(self): return np.array([[0, 1, 2]])
class Rotor:
    def __init__(self, cfg): self.cfg, self.tpl = cfg, Template()
    def forward(self, state): return np.zeros((1, 3, 3)), np.zeros((1, 3))
'''
    source, target, vendor, atlas = [tmp_path / name for name in ('source.py', 'texture.py', 'vendor.py', 'atlas.py')]
    target.write_text(code)
    vendor.write_text(code)
    source.write_text(code + "\nfrom pathlib import Path\nPath(__file__).write_text('# changed after loading\\n')\n")
    original_hash = texture.digest(source)
    atlas.write_text('''import numpy as np
class Atlas:
    def __init__(self, *args, **kwargs): pass
    def corner_uv(self, faces): return np.zeros((1, 3, 2))
    def meta(self): return {}
''')
    result = texture.compatibility({'turbine': {}, 'state_names': ['x'], 'frames': [{'state': [0.]}]},
                                   source, target, vendor, atlas, .05, 8)
    assert result['source_model']['sha256'] == original_hash


def test_failed_texture_engine_leaves_no_partial_texture(tmp_path, monkeypatch):
    out, engine = tmp_path / 'out', tmp_path / 'engine'
    engine.mkdir()
    runner = tmp_path / 'runner.py'
    monkeypatch.setattr(sys, 'argv', ['texture', '--output', str(out)])
    monkeypatch.setattr(texture, 'prepare', lambda args: (out, engine))
    monkeypatch.setattr(texture, 'write_diagnostic_runner', lambda *args: runner)
    def fail(command, **kwargs):
        destination = Path(command[command.index('--out') + 1])
        destination.mkdir(parents=True)
        (destination / 'blade1_tex.png').write_bytes(b'partial texture')
        raise texture.subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(texture.subprocess, 'run', fail)
    with pytest.raises(texture.subprocess.CalledProcessError):
        texture.main()
    assert not (out / 'texture').exists()
