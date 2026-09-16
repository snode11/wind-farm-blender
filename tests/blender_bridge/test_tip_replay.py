from copy import deepcopy
from pathlib import Path
import threading
import time
import numpy as np
import pytest
from wfrl.blender_bridge.tip_replay import FastFarmTipReplay,sample_from_snapshot
from wfrl.blender_bridge.messages import encode_message,FrameDecoder
from wfrl.blender_bridge.workflow import validate_run_options

ROOT=Path(__file__).resolve().parents[2]
PACKAGE=ROOT/'results/lidar/packages/normal-v1.1'

@pytest.fixture
def reader():
    if not (PACKAGE/'manifest.json').is_file():pytest.skip('Archived physical results not installed')
    return FastFarmTipReplay(PACKAGE)


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
