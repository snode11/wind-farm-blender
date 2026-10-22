"""Bounded raw observations and asynchronous JSON export; never imports bpy."""
from collections import deque
from copy import deepcopy
from pathlib import Path
import json
import threading
import time


class HistoryWindow:
    def __init__(self, capacity=600, clock=time.monotonic):
        if type(capacity) is not int or not 1 <= capacity <= 600:
            raise ValueError('History capacity must be between 1 and 600')
        self.capacity, self.clock = capacity, clock
        self.clear()

    def clear(self, run_id=None):
        self.run_id = run_id
        self.series = {}
        self.dropped = {}
        self.last_sequence = None
        self.session_id = None

    def ingest(self, message):
        p = message['payload']
        run_id = p.get('run_id') or message.get('session_id') or 'local-demo'
        if self.run_id != run_id:
            self.clear(run_id)
        sequence = message.get('sequence')
        if sequence is not None and self.last_sequence is not None and sequence <= self.last_sequence:
            return False
        self.last_sequence = sequence
        self.session_id = message.get('session_id')
        now = self.clock()
        context = {key: deepcopy(p[key]) for key in
                   ('mode', 'step', 'agent_step', 'iteration', 'phase', 'episode', 'timestamp', 'record_kind') if key in p}
        context.update(run_id=run_id, session_id=self.session_id, sequence=sequence,
                       message_type=message['type'], received_monotonic=now)
        if message['type'] == 'snapshot':
            groups = [(None, p.get('farm', {}))]
            groups.extend((t['turbine_id'], t['channels']) for t in p.get('turbines', []))
            for turbine_id, channels in groups:
                for channel, record in channels.items():
                    self._append(context, turbine_id, channel, record)
        elif message['type'] == 'curve':
            self._append(context, p['turbine_id'], p['channel'], p['data'])
        elif message['type'] == 'training_stats':
            if p['record_kind'] == 'progress':
                self._append(context, None, '__progress__', {
                    'source_age_seconds': p['source_age_seconds'],
                    'stale_after_seconds': p['stale_after_seconds']})
            else:
                for channel, record in p['stats'].items():
                    self._append(context, None, channel, record)
        return True

    def _append(self, context, turbine_id, channel, record):
        key = (context['message_type'], context.get('record_kind'), turbine_id, channel)
        values = self.series.setdefault(key, deque(maxlen=self.capacity))
        if len(values) == self.capacity:
            self.dropped[key] = self.dropped.get(key, 0) + 1
        values.append(dict(context, turbine_id=turbine_id, channel=channel, data=deepcopy(record)))

    def snapshot(self):
        """Main thread takes only a bounded copy; JSON and disk I/O happen later."""
        return {'run_id': self.run_id, 'session_id': self.session_id, 'capacity': self.capacity,
                'series': [{'key': list(key), 'records': deepcopy(list(records)),
                            'dropped_count': self.dropped.get(key, 0)}
                           for key, records in self.series.items()]}


def export_document(snapshot, *, now_monotonic=None, now_unix=None):
    now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    doc = deepcopy(snapshot)
    doc.update(schema_version='wfrl.history.v1', exported_at_unix_seconds=time.time() if now_unix is None else now_unix,
               scope='Current run bounded history window; not a complete training archive')
    doc['truncated'] = any(series['dropped_count'] for series in doc['series'])
    for series in doc['series']:
        records = series['records']
        series['truncated'] = series['dropped_count'] > 0
        series['retained_count'] = len(records)
        series['capacity'] = doc['capacity']
        series['range'] = {'first': None, 'last': None}
        for label, record in zip(('first', 'last'), (records[0], records[-1]) if records else (None, None)):
            if record is not None:
                series['range'][label] = {key: deepcopy(record[key]) for key in
                                         ('sequence', 'step', 'iteration', 'phase', 'timestamp') if key in record}
        for record in records:
            data = record['data']
            source_age = data.get('source_age_seconds')
            record['source_age_at_receive_seconds'] = source_age
            record['age_at_export_seconds'] = (None if source_age is None else
                source_age + max(0., now_monotonic - record['received_monotonic']))
            validity = data.get('validity')
            if validity == 'valid' and record['age_at_export_seconds'] >= data['stale_after_seconds']:
                validity = 'stale'
            record['validity_at_export'] = validity
    return doc


class ExportJob:
    def __init__(self):
        self.thread = None
        self.status = 'IDLE'
        self.error = None
        self.path = None
        self._result = None

    def start(self, path, snapshot):
        if self.thread and self.thread.is_alive():
            raise ValueError('A history export is already running')
        self.status, self.error, self.path = 'WRITING', None, str(path)
        self._result = None
        # The caller supplies the bounded snapshot, detached from mutable state.
        def write():
            try:
                document = export_document(snapshot)
                with Path(path).open('w', encoding='utf-8') as output:
                    json.dump(document, output, ensure_ascii=False, allow_nan=False, indent=2)
                    output.write('\n')
                self._result = ('COMPLETE', None)
            except Exception as exc:
                self._result = ('FAILED', f'{type(exc).__name__}: {exc}')
        self.thread = threading.Thread(target=write, name='wfrl-history-export', daemon=True)
        self.thread.start()

    def poll(self):
        if self._result is not None:
            self.status, self.error = self._result
            self._result = None
        return self.status
