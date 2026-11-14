"""Three elastic tip twists retain units, geometry definition and source time."""
from types import SimpleNamespace as NS

from wfrl.blender_bridge.messages import SOLVER_OUTPUT_CHANNELS, TORSION_OUTPUT_CHANNELS, encode_message
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl_blender.protocol import FrameDecoder
from wfrl_blender import charts
from wfrl_blender.history import HistoryWindow


def test_tip_torsion_wire_chart_and_export(monkeypatch):
    scene = NS(turbines=[NS(id='T1')], dt=3., controls=['yaw'], backend='fastfarm')
    records = {key: dict(value=-.12 if key in TORSION_OUTPUT_CHANNELS else 1.,
                        unit=spec['unit'], validity='valid', error=None, fidelity='DIRECT',
                        provenance=dict(channel=spec['native'], physics_identity='beam-case',
                                        derivation='beamdyn-relative-rotation-swing-twist-z.v1'),
                        timestamp=dict(value=29.75, timebase='simulation_seconds', source='openfast_output'),
                        source_age_seconds=0., stale_after_seconds=2.)
               for key, spec in {**SOLVER_OUTPUT_CHANNELS, **TORSION_OUTPUT_CHANNELS}.items()}
    sample = NS(step=10, episode=0, phase='sampling', events=[], reward=0., farm_power=None,
                ctx={'t': 30.}, measure={'output_channels': [records]})
    payload = SnapshotAdapter(scene, 'beam-session').encode(sample)
    message = dict(protocol_version=1, type='snapshot', session_id='beam-session', sequence=1, payload=payload)
    decoded = FrameDecoder().feed(encode_message(message))[0]
    history, exported = charts.ChartHistory(), HistoryWindow()
    history.ingest(decoded)
    exported.ingest(decoded)
    monkeypatch.setattr(charts, 'history', history)
    charts.invalidate_render_cache()
    assert charts.selected_channels('tip_torsion') == tuple(TORSION_OUTPUT_CHANNELS)
    plot = charts._plot_data('tip_torsion', 'T1')
    assert len(plot.series) == 3 and len(plot.units) == 1
    assert 'torsion' in charts.LABELS['tip_torsion']
    for key in TORSION_OUTPUT_CHANNELS:
        point = history.points('T1', key)[0]
        assert point.value == -.12 and point.time == 29.75
        assert point.provenance['physics_identity'] == 'beam-case'
        record = next(s['records'][0] for s in exported.snapshot()['series'] if s['key'][-1] == key)
        assert record['data']['value'] == -.12 and record['data']['unit'] == 'deg'


def test_launcher_serializes_both_torsion_boolean_overrides():
    import argparse
    from scripts.blender import wfrl_launcher
    from wfrl.physics_options import physics_cli_arguments
    parser = argparse.ArgumentParser()
    wfrl_launcher.common(parser)
    assert physics_cli_arguments(parser.parse_args(['--blade-torsion'])) == ['--blade-torsion']
    assert physics_cli_arguments(parser.parse_args(['--no-blade-torsion'])) == ['--no-blade-torsion']
