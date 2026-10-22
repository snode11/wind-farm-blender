from copy import deepcopy
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import numpy as np
import pytest
from wfrl.blender_bridge.tip_replay import FastFarmTipReplay,resolve_raw_source_path,sample_from_snapshot
from wfrl.lidar.replay import ReplayPackage
from wfrl.blender_bridge.messages import encode_message,FrameDecoder
from wfrl.blender_bridge.workflow import validate_run_options

ROOT=Path(__file__).resolve().parents[2]
PACKAGE=ROOT/'results/lidar/packages/normal-v1.1'

@pytest.fixture
def reader():
    if not (PACKAGE/'manifest.json').is_file():pytest.skip('Archived physical results not installed')
    package=ReplayPackage.load(PACKAGE)
    configs=[resolve_raw_source_path(PACKAGE,s['path']) for s in package.manifest['raw_sources']
             if Path(s['path']).name=='run_config.json'
             and Path(s['path']).parent.name==package.manifest['run_id']]
    if len(configs)==1 and not configs[0].is_file():
        pytest.skip('Original FAST.Farm raw run is not installed; the offline replay package does not contain native VTP geometry')
    result=FastFarmTipReplay(PACKAGE)
    tid=result.package.manifest['turbine_id']
    missing=sum(not (result.run/f'FarmInputs/vtk/Case.{tid}.Blade{bid}Surface.{round(t*result.fps):05d}.vtp').is_file()
                for t in result.times for bid in (1,2,3))
    if missing:
        pytest.skip(f'Original FAST.Farm native VTP geometry is not installed ({missing} required files missing); offline package assertions remain separate')
    return result


@pytest.mark.parametrize('absolute_record', [False, True])
def test_raw_source_relocation_uses_the_package_repository_without_old_path_fallback(tmp_path, monkeypatch, absolute_record):
    package=tmp_path/'relocated/results/lidar/packages/normal-v1.1'
    source=Path('results/lidar/raw/normal-v1/run_config.json')
    recorded=tmp_path/'original'/source if absolute_record else source
    if absolute_record:
        recorded.parent.mkdir(parents=True)
        recorded.write_text('original source metadata')
    elsewhere=tmp_path/'unrelated'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    expected=tmp_path/'relocated'/source
    assert resolve_raw_source_path(package,recorded)==expected.resolve()
    assert not expected.exists()


def test_raw_source_relocation_rejects_parent_traversal(tmp_path):
    package=tmp_path/'results/lidar/packages/normal-v1.1'
    with pytest.raises(ValueError,match='inside results/lidar/raw'):
        resolve_raw_source_path(package,'results/lidar/raw/../other/run_config.json')


def test_standalone_package_keeps_explicit_raw_source_reference(tmp_path):
    recorded=tmp_path/'archive/normal-v1/run_config.json'
    assert resolve_raw_source_path(tmp_path/'standalone-package',recorded)==recorded.resolve()


@pytest.mark.parametrize('corrupt_layer', ['package', 'raw_metadata'])
def test_missing_vtp_does_not_turn_present_data_corruption_into_a_skip(tmp_path, monkeypatch, corrupt_layer):
    package=tmp_path/'results/lidar/packages/normal-v1.1'
    package.mkdir(parents=True)
    (package/'manifest.json').write_text('{}')
    config=tmp_path/'results/lidar/raw/normal-v1/run_config.json'
    config.parent.mkdir(parents=True)
    config.write_text('{}')
    monkeypatch.setitem(reader.__wrapped__.__globals__,'PACKAGE',package)

    def corrupt(*args):
        raise ValueError('Present data integrity mismatch')

    if corrupt_layer=='package':
        monkeypatch.setattr(ReplayPackage,'load',corrupt)
    else:
        manifest={'run_id':'normal-v1','raw_sources':[{'path':str(config)}]}
        monkeypatch.setattr(ReplayPackage,'load',lambda path: SimpleNamespace(manifest=manifest))
        monkeypatch.setitem(reader.__wrapped__.__globals__,'FastFarmTipReplay',corrupt)
    with pytest.raises(ValueError,match='integrity mismatch'):
        reader.__wrapped__()


def test_recorded_geometry_matches_saved_truth_and_wire(reader):
    rows=reader.package.measurements
    row=next(r for r in rows if r['time_s']>=reader.times[0])
    index=round((row['time_s']-reader.times[0])*reader.fps)
    sample=reader.at_index(index)
    bid=row['blade_id']-1
    assert np.allclose(sample['blades'][bid]['P'],row['truth_tip_point_m'],atol=1e-9)
    payload=reader.wire_payload(index)
    wire=encode_message(dict(protocol_version=1,type='snapshot',session_id='test',sequence=1,payload=payload))
    decoded=sample_from_snapshot(FrameDecoder().feed(wire)[0]['payload'])
    assert np.allclose(decoded['blades'][bid]['P'],sample['blades'][bid]['P'])
    assert decoded['time_s']==row['time_s']
    for i in (0,1,2,3):reader.at_index(i)
    assert len(reader._cache)==2


def test_bad_geometry_rejected(reader):
    payload=reader.wire_payload(0)
    bad=deepcopy(payload);bad['timestamp']['value']+=1
    with pytest.raises(ValueError,match='timestamp'):sample_from_snapshot(bad)
    bad=deepcopy(payload);bad['turbines'][0]['channels']['blade_geometry']['value']['blades'][0]['tip_m'][0]=float('nan')
    with pytest.raises(ValueError,match='Invalid blade'):sample_from_snapshot(bad)
    key=next(k for k in reader.hashes if k.endswith('Blade1Surface.01441.vtp'))
    reader.hashes[key]='0'*64
    with pytest.raises(ValueError,match='integrity'):reader.at_index(1)


def test_recorded_options_are_separate_from_policy():
    with pytest.raises(ValueError,match='only replay'):validate_run_options('demo',{'tip_package':str(PACKAGE)})
    with pytest.raises(ValueError,match='no policy'):validate_run_options('replay',{'tip_package':str(PACKAGE),'ckpt_path':'x'})


def test_real_bridge_pause_step_and_stop(reader):
    from wfrl.blender_bridge.backend_session import BackendSession
    from wfrl.blender_bridge.server import BridgeServer
    from wfrl.blender_bridge.tip_client import TipBridgeClient
    from wfrl.scene.schema import load_scene
    session=BackendSession(load_scene(str(ROOT/'scenes/turb3_row.yaml')))
    server=BridgeServer(port=0,session=session)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=None
    try:
        client=TipBridgeClient(f'127.0.0.1:{server.port}',PACKAGE,timeout=5)
        a=client.next_sample();time.sleep(.08)
        assert session.status=='PAUSED'
        b=client.next_sample()
        assert b['time_s']==pytest.approx(a['time_s']+1/reader.fps)
        assert np.linalg.norm(b['blades'][0]['P']-a['blades'][0]['P'])>0
        client.close();client=None
        assert session.status=='STOPPED' and not session.alive()
    finally:
        if client is not None:client.close()
        server.close();thread.join(3)
        assert not thread.is_alive()
