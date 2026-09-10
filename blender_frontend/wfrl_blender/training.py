"""Training observations with independent phase and per-field source clocks."""
from copy import deepcopy
import time

STAT_UNITS = {'mean_power': 'MW', 'mean_reward': '', 'mean_raw_reward': '',
              'value_loss': '', 'explained_variance': '', 'episode_return': '',
              'learning_rate': ''}
STAT_LABELS = {'mean_power': 'Mean farm power', 'mean_reward': 'Normalized training reward',
               'mean_raw_reward': 'Mean raw reward', 'value_loss': 'Value loss',
               'explained_variance': 'Explained variance', 'episode_return': 'Episode return',
               'learning_rate': 'Learning rate'}


class TrainingDashboard:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset()

    def reset(self, run_id=None):
        self.run_id = run_id
        self.latest = None
        self.received = None
        self.metrics = {}
        self.last_sequence = -1

    def ingest(self, message, *, negotiated=False):
        from .protocol import ProtocolError, validate_message
        validate_message(message)
        if message['type'] != 'training_stats' or not negotiated:
            raise ProtocolError('training_stats_v1 not negotiated')
        payload = message['payload']
        if self.run_id is None or payload['run_id'] != self.run_id:
            raise ProtocolError('training stats do not belong to the active run')
        if message['sequence'] <= self.last_sequence:
            raise ProtocolError('training sequence must increase')
        self.last_sequence = message['sequence']
        now = self.clock()
        self.latest, self.received = deepcopy(payload), now
        if payload['record_kind'] == 'iteration_stats':
            for key, record in payload['stats'].items():
                self.metrics[key] = (deepcopy(record), now, deepcopy(payload))
        return True

    def phase(self):
        if self.latest is None:
            return {'phase': 'waiting', 'validity': 'waiting', 'age': None,
                    'reason': 'Waiting for first trainer progress record'}
        age = self.latest['source_age_seconds'] + max(0, self.clock() - self.received)
        return {'phase': self.latest['phase'], 'age': age,
                'validity': 'stale' if age >= self.latest['stale_after_seconds'] else 'valid',
                'step': self.latest['step'], 'agent_step': self.latest['agent_step'],
                'iteration': self.latest['iteration']}

    def metric(self, key, *, negotiated=True):
        if not negotiated:
            return {'value': None, 'validity': 'unsupported', 'error': 'training_stats_v1 not negotiated'}
        if key not in self.metrics and self.metrics:
            return {'value': None, 'validity': 'unsupported', 'error': 'Not emitted by this trainer'}
        if key not in self.metrics:
            return {'value': None, 'validity': 'waiting', 'error': 'No iteration statistics produced yet'}
        record, received, context = self.metrics[key]
        result = deepcopy(record)
        result['age'] = record['source_age_seconds'] + max(0, self.clock() - received)
        if result['validity'] == 'valid' and result['age'] >= result['stale_after_seconds']:
            result['validity'] = 'stale'
            result['error'] = 'Training statistic exceeded its source freshness threshold'
        result['context'] = {k: context[k] for k in ('iteration', 'phase', 'step', 'agent_step', 'timestamp')}
        if 'episode' in context:
            result['context']['episode'] = context['episode']
        return result
