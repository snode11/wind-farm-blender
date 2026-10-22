import json
from wfrl_blender.history import HistoryWindow, export_document, ExportJob


def sample(seq, kind='progress', phase='sampling', run_id='run'):
    record = dict(value=2., unit='MW', fidelity='EXPORTED', provenance={'file': 'run.jsonl', 'channel': 'mean_power'},
                  validity='valid', error=None, source_age_seconds=4., stale_after_seconds=30.)
    return dict(type='training_stats', session_id='s', sequence=seq, payload=dict(
        run_id=run_id, mode='formal_training', step=10, agent_step=30, iteration=1,
        phase=phase, record_kind=kind, timestamp={'value': 100., 'timebase': 'unix_seconds'},
        source_age_seconds=4., stale_after_seconds=30., stats={} if kind == 'progress' else {'mean_power': record}))


def test_601_records_are_bounded_and_same_step_phases_survive():
    window = HistoryWindow(clock=lambda: 10.)
    for seq in range(601):
        window.ingest(sample(seq, phase='sampling' if seq % 2 else 'updating'))
    doc = export_document(window.snapshot(), now_monotonic=40., now_unix=1000.)
    series = doc['series'][0]
    assert doc['truncated'] and series['truncated']
    assert series['dropped_count'] == 1 and series['retained_count'] == 600
    assert series['range']['first']['sequence'] == 1
    assert series['range']['last']['sequence'] == 600
    assert series['records'][0]['phase'] != series['records'][1]['phase']
    assert series['records'][0]['source_age_at_receive_seconds'] == 4.
    assert series['records'][0]['age_at_export_seconds'] == 34.


def test_stats_keep_raw_metadata_and_run_isolation(tmp_path):
    window = HistoryWindow(clock=lambda: 10.)
    window.ingest(sample(1, 'iteration_stats'))
    window.ingest(sample(2))
    snapshot = window.snapshot()
    window.clear('new-run')
    doc = export_document(snapshot, now_monotonic=40.)
    assert len(doc['series']) == 2
    record = doc['series'][0]['records'][0]
    assert record['data']['value'] == 2.
    assert record['data']['validity'] == 'valid'
    assert record['data']['error'] is None
    assert record['validity_at_export'] == 'stale'
    assert window.snapshot()['series'] == []
    job = ExportJob()
    job.start(tmp_path / 'export.json', snapshot)
    job.thread.join(2)
    assert job.poll() == 'COMPLETE'
    assert json.loads((tmp_path / 'export.json').read_text())['run_id'] == 'run'
    job.start(tmp_path / 'missing' / 'fail.json', snapshot)
    job.thread.join(2)
    assert job.poll() == 'FAILED' and job.error


def test_export_serialization_runs_off_calling_thread(monkeypatch, tmp_path):
    import threading
    import wfrl_blender.history as module
    entered, release = threading.Event(), threading.Event()
    original = module.export_document
    def slow(snapshot):
        assert threading.current_thread() is not threading.main_thread()
        entered.set()
        release.wait(2)
        return original(snapshot)
    monkeypatch.setattr(module, 'export_document', slow)
    window = HistoryWindow()
    window.ingest(sample(1))
    job = ExportJob()
    job.start(tmp_path / 'export.json', window.snapshot())
    assert entered.wait(1)
    window.ingest(sample(2))
    assert job.poll() == 'WRITING'
    release.set()
    job.thread.join(2)
    assert job.poll() == 'COMPLETE'
