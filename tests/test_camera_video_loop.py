"""Actual codec repetition and semantic source-cycle corruption checks.

Base rendering verification is mocked here; base-package tests cover it, while
these tests exercise real FFmpeg and the new derived delivery contract.
"""
import csv
import json
import numpy as np
import pytest
from test_camera_video_media import encoded_package
from wfrl.camera_video import loop, package
from wfrl.camera_video.media import sha256, read_frames


@pytest.fixture
def base(encoded_package, monkeypatch):
    root = encoded_package
    np.savez_compressed(root / 'frame_geometry.npz', frame_id=np.arange(40),
                        sim_time_s=117 + (np.arange(40) + .5) / 20,
                        clearance_valid=np.zeros((40, 3), dtype=bool),
                        nearest_tower_world_m=np.full((40, 3, 3), np.nan))
    (root / 'camera.json').write_text(json.dumps({'turbine_id': 'T1', 'resolution': [128, 72]}))
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest.update(schema='wfrl.camera-video.v1', status='REVIEW_ONLY', fps=20,
                    width=128, height=72, frame_count=40)
    manifest['files'].update({name: sha256(root / name) for name in ('frame_geometry.npz','camera.json')})
    package.write_json(root / 'manifest.json', manifest)
    monkeypatch.setattr(package, 'verify_package', lambda *a, **kw: {'valid': True})
    return root


def rehash(root, name):
    m=package.read_json(root/'manifest.json')
    m['files'][name]=sha256(root/name)
    m['dataset_id']=loop._identity(m)
    package.write_json(root/'manifest.json',m)


def test_five_cycles_preserve_packets_and_reset_source_time(base):
    out=base.parent/(base.name+'-loop')
    report=loop.build_loop(base,out,repeats=5)
    assert report['frame_count']==200 and report['duration_s']==10
    assert report['exact_encoded_repetition']
    rows=read_frames(out/'frames.csv')
    assert [int(r['video_pts']) for r in rows]==list(range(200))
    assert rows[40]['source_frame_id']=='0' and rows[40]['source_cycle_id']=='1'
    assert rows[40]['sim_time_s']==rows[0]['sim_time_s']
    assert float(rows[40]['video_time_s'])==2
    assert rows[-1]['source_cycle_id']=='4'
    with np.load(out/'frame_geometry.npz') as geometry:
        assert np.isnan(geometry['nearest_tower_world_m']).all()
        assert not geometry['clearance_valid'].any()
    assert loop.verify_loop(out)['valid']
    with pytest.raises(FileExistsError):loop.build_loop(base,out)


@pytest.mark.parametrize('field,value',[('source_cycle_id','9'),('source_frame_id','1'),('sim_time_s','999'),('source_dataset_id','wrong')])
def test_rehashed_label_corruption_is_rejected(base,field,value):
    out=base.parent/(base.name+'-loop')
    loop.build_loop(base,out,repeats=2)
    rows=read_frames(out/'frames.csv');rows[40][field]=value
    with (out/'frames.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    rehash(out,'frames.csv')
    with pytest.raises(ValueError,match='Derived frame labels'):loop.verify_loop(out)


def test_rehashed_geometry_corruption_is_rejected(base):
    out=base.parent/(base.name+'-loop');loop.build_loop(base,out,repeats=2)
    with np.load(out/'frame_geometry.npz') as z: arrays=dict(z)
    arrays['source_cycle_id'][40]=0
    np.savez_compressed(out/'frame_geometry.npz',**arrays);rehash(out,'frame_geometry.npz')
    with pytest.raises(ValueError,match='geometry source mapping'):loop.verify_loop(out)
