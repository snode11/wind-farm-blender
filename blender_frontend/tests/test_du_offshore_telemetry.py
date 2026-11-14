"""Host-only offshore telemetry, physics status, charts and export contracts."""
from types import SimpleNamespace as NS

from wfrl.blender_bridge.messages import (
    SOLVER_OUTPUT_CHANNELS, TOWER_OUTPUT_CHANNELS, OFFSHORE_OUTPUT_CHANNELS, encode_message,
)
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl.scene.schema import from_dict
from wfrl_blender.protocol import FrameDecoder
from wfrl_blender import charts
from wfrl_blender.history import HistoryWindow

SEA = dict(model='oc3_monopile', hs_m=2., tp_s=8., direction_deg=0., seed=123456789, water_depth_m=20.)


def payload_fixture():
    scene = from_dict(dict(layout=[dict(id='T1', x=0., y=0.)], physics=dict(tower_flex=True), substructure=SEA))
    catalog = {**SOLVER_OUTPUT_CHANNELS, **TOWER_OUTPUT_CHANNELS, **OFFSHORE_OUTPUT_CHANNELS}
    records = {key: dict(value=-12.5 if key.startswith('mudline') else .125,
                        unit=spec['unit'], validity='valid', error=None, fidelity='DIRECT',
                        provenance=dict(channel=spec['native'], physics_identity='oc3-case'),
                        timestamp=dict(value=119.75, timebase='simulation_seconds', source='openfast_output'),
                        source_age_seconds=0., stale_after_seconds=2.) for key, spec in catalog.items()}
    sample = NS(step=40, episode=0, phase='sampling', events=[], reward=0., farm_power=None,
                ctx={'t': 120.}, measure={'output_channels': [records], 'physics': dict(identity='oc3-case',
                    substructure=dict(requested=SEA, wave_field='phase_adjusted_shared_realization'))})
    return scene, sample


def test_offshore_wire_metadata_and_scalar_units_survive_chart_export(monkeypatch):
    scene, sample = payload_fixture()
    payload = SnapshotAdapter(scene, 'session').encode(sample)
    assert payload['hydrodynamics']['status'] == 'computed'
    assert payload['hydrodynamics']['physics_identity'] == 'oc3-case'
    assert payload['scene']['substructure'] == SEA
    message = dict(protocol_version=1, type='snapshot', session_id='session', sequence=1, payload=payload)
    decoded = FrameDecoder().feed(encode_message(message))[0]
    history, exported = charts.ChartHistory(), HistoryWindow()
    history.ingest(decoded); exported.ingest(decoded)
    monkeypatch.setattr(charts, 'history', history)
    charts.invalidate_render_cache()
    assert charts.selected_channels('mudline_moment') == ('mudline_moment_x', 'mudline_moment_y')
    assert len(charts._plot_data('mudline_moment', 'T1').series) == 2
    for key, spec in OFFSHORE_OUTPUT_CHANNELS.items():
        point = history.points('T1', key)[0]
        assert point.time == 119.75 and point.provenance['physics_identity'] == 'oc3-case'
        record = next(s['records'][0] for s in exported.snapshot()['series'] if s['key'][-1] == key)
        assert record['data']['unit'] == spec['unit']
        assert record['data']['value'] == sample.measure['output_channels'][0][key]['value']


def test_requested_visual_only_demo_stale_and_wrong_identity_never_claim_computed():
    scene, sample = payload_fixture()
    adapter = SnapshotAdapter(scene, 'session')
    sample.measure['output_channels'][0]['wave_elevation'].update(value=None, validity='waiting')
    assert adapter.encode(sample)['hydrodynamics']['status'] == 'configured'
    scene, sample = payload_fixture()
    sample.measure['output_channels'][0]['platform_pitch']['provenance']['physics_identity'] = 'another-case'
    assert adapter.encode(sample)['hydrodynamics']['status'] == 'configured'
    scene, sample = payload_fixture()
    assert SnapshotAdapter(scene, 'session', mode='demo').encode(sample)['hydrodynamics']['status'] == 'configured'
    sample.measure.pop('physics')
    assert adapter.encode(sample)['hydrodynamics']['status'] == 'requested'
    visual = from_dict(dict(layout=[dict(id='T1', x=0., y=0.)]))
    assert SnapshotAdapter(visual, 'session').encode(sample)['hydrodynamics']['status'] == 'not_computed'
