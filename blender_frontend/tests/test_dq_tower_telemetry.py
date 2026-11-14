"""Native tower-top displacement survives the wire, chart and export layers."""
from types import SimpleNamespace as NS

from wfrl.blender_bridge.messages import ALL_SOLVER_OUTPUT_CHANNELS, encode_message
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl_blender.protocol import FrameDecoder
from wfrl_blender import charts
from wfrl_blender.history import HistoryWindow


def test_launcher_preserves_unspecified_and_explicit_physics_flags():
    import argparse
    from scripts.blender import wfrl_launcher
    from wfrl.physics_options import physics_cli_arguments
    parser = argparse.ArgumentParser()
    wfrl_launcher.common(parser)
    assert physics_cli_arguments(parser.parse_args([])) == []
    args = parser.parse_args(['--tower-flex', '--no-dynamic-stall', '--tower-shadow'])
    assert physics_cli_arguments(args) == ['--tower-flex', '--no-dynamic-stall', '--tower-shadow']


def test_tower_top_wire_chart_and_export_preserve_two_axes(monkeypatch):
    scene = NS(turbines=[NS(id='T1')], dt=3., controls=['yaw'], backend='fastfarm')
    records = {key: dict(value=.125 if key == 'tower_top_fa' else -.025,
                        unit=spec['unit'], validity='valid', error=None, fidelity='DIRECT',
                        provenance=dict(channel=spec['native'], physics_identity='test-case'),
                        timestamp=dict(value=29.75, timebase='simulation_seconds', source='openfast_output'),
                        source_age_seconds=0., stale_after_seconds=2.)
               for key, spec in ALL_SOLVER_OUTPUT_CHANNELS.items()}
    sample = NS(step=10, episode=0, phase='sampling', events=[], reward=0., farm_power=None,
                ctx={'t': 30.}, measure={'output_channels': [records]})
    payload = SnapshotAdapter(scene, 's').encode(sample)
    message = dict(protocol_version=1, type='snapshot', session_id='s', sequence=1, payload=payload)
    decoded = FrameDecoder().feed(encode_message(message))[0]
    history, exported = charts.ChartHistory(), HistoryWindow()
    history.ingest(decoded)
    exported.ingest(decoded)
    monkeypatch.setattr(charts, 'history', history)
    charts.invalidate_render_cache()
    assert charts.selected_channels('tower_top_displacement') == ('tower_top_fa', 'tower_top_ss')
    plot = charts._plot_data('tower_top_displacement', 'T1')
    assert len(plot.series) == 2 and len(plot.units) == 1
    for key, value in (('tower_top_fa', .125), ('tower_top_ss', -.025)):
        point = history.points('T1', key)[0]
        assert point.value == value and point.time == 29.75
        assert point.provenance['physics_identity'] == 'test-case'
        record = next(s['records'][0] for s in exported.snapshot()['series'] if s['key'][-1] == key)
        assert record['data']['value'] == value and record['data']['unit'] == 'm'
