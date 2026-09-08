"""Bounded structured observability for the existing formal training process."""
import json
import math
import threading
import time
from pathlib import Path

from .messages import validate_training_stats_payload

MAX_SAFE = 2**53 - 1


def resume_counts(checkpoint):
    """Require exact counts; never interpret legacy agent steps as control steps."""
    iteration = checkpoint.get('iteration')
    if type(iteration) is not int or not 0 <= iteration <= MAX_SAFE:
        raise ValueError('Observability resume requires a safe checkpoint iteration')
    metadata = checkpoint.get('training_progress')
    if metadata is not None:
        step, agent_step = metadata.get('step'), metadata.get('agent_step')
    else:
        hist = checkpoint.get('history', {})
        cfg = hist.get('cfg', {})
        n_steps = cfg.get('n_steps')
        obs_dim, state_dim = checkpoint.get('obs_dim'), checkpoint.get('state_dim')
        if (type(n_steps) is not int or n_steps <= 0 or
                type(obs_dim) is not int or obs_dim <= 0 or
                type(state_dim) is not int or state_dim <= 0 or state_dim % obs_dim or
                len(hist.get('reward', [])) != iteration):
            raise ValueError('Observability resume counts ambiguous: require exact history configuration')
        step = iteration * n_steps
        agent_step = step * (state_dim // obs_dim)
    if any(type(v) is not int or not 0 <= v <= MAX_SAFE for v in (step, agent_step)):
        raise ValueError('Observability resume counts must be nonnegative safe integers')
    return step, agent_step


class TrainingProgressWriter:
    def __init__(self, path, run_id, step=0, agent_step=0):
        self.path, self.run_id = str(path), run_id
        self.step, self.agent_step = step, agent_step
        self.iteration = 0
        self.failed = False
        self._file = None
        try:
            self._file = open(path, 'x', encoding='utf-8')
        except OSError as exc:
            self._write_fault(exc)

    def _write(self, kind, phase, iteration, stats):
        if self.failed:
            return
        record = dict(run_id=self.run_id, record_kind=kind, mode='formal_training',
                      step=self.step, agent_step=self.agent_step, iteration=iteration,
                      phase=phase, timestamp=dict(value=time.time(), timebase='unix_seconds'),
                      source_age_seconds=0., stale_after_seconds=30., stats=stats)
        try:
            validate_training_stats_payload(record)
            self._file.write(json.dumps(record, allow_nan=False) + '\n')
            self._file.flush()
        except Exception as exc:
            self._write_fault(exc)

    def _write_fault(self, exc):
        self.failed = True
        # Separate status file reports logging failure without parsing stdout.
        try:
            Path(self.path + '.error').write_text('Training observability write failed: ' + str(exc))
        except OSError:
            pass
        print('TRAINING_OBSERVABILITY_ERROR: ' + str(exc), flush=True)

    def progress(self, phase, iteration=None):
        if iteration is not None:
            self.iteration = iteration
        self._write('progress', phase, self.iteration, {})

    def iteration_stats(self, iteration, mean_power, mean_reward, value_loss, explained_variance):
        values = dict(mean_power=mean_power, mean_reward=mean_reward,
                      value_loss=value_loss, explained_variance=explained_variance,
                      episode_return=None, learning_rate=None)
        sources = dict(mean_power='B_power.mean(): mean farm total power per control step',
                       mean_reward='B_rew.mean(): normalized training reward',
                       value_loss='mean(v_log): actual optimization minibatches',
                       explained_variance='ev: 1 - var(ret - B_val)/(var(ret) + 1e-8)')
        stats = {}
        for name, value in values.items():
            unsupported = name in ('episode_return', 'learning_rate')
            valid = value is not None and math.isfinite(float(value))
            stats[name] = dict(value=float(value) if valid else None,
                unit='MW' if name == 'mean_power' else '',
                validity='valid' if valid else ('unsupported' if unsupported else 'invalid'),
                error=None if valid else ('Not emitted by trainer' if unsupported else
                    'No optimization minibatches' if name == 'value_loss' and value is None else 'Nonfinite training statistic'),
                fidelity='EXPORTED', provenance=dict(file=self.path, channel=name,
                    calculation=sources.get(name, 'Not emitted by trainer')),
                source_age_seconds=0., stale_after_seconds=30.)
        self._write('iteration_stats', 'updating', iteration, stats)

    def close(self):
        if self._file is not None:
            try: self._file.close()
            except OSError as exc: self._write_fault(exc)


class TrainingProgressReader:
    """Worker-owned byte-offset tailer. Polling never happens in Blender."""
    def __init__(self, path, run_id, emit, error, max_line_bytes=262144):
        self.path, self.run_id = Path(path), run_id
        self.emit, self.error = emit, error
        self.max_line_bytes = max_line_bytes
        self.offset = 0
        self.partial = b''
        self.previous = None
        self.failed = False
        self._stop = threading.Event()
        self._thread = None

    def _fault(self, message):
        if not self.failed:
            self.failed = True
            self.error('Training observability: ' + message)

    def read_available(self, final=False):
        if self.failed:
            return
        try:
            if Path(str(self.path) + '.error').exists():
                raise ValueError('producer reported a write failure')
            if not self.path.exists():
                if final:
                    raise ValueError('progress file was never created')
                return
            if self.path.stat().st_size < self.offset:
                raise ValueError('progress file truncated')
            with self.path.open('rb') as stream:
                stream.seek(self.offset)
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    self.offset += len(chunk)
                    self.partial += chunk
                    while b'\n' in self.partial:
                        line, self.partial = self.partial.split(b'\n', 1)
                        if len(line) > self.max_line_bytes:
                            raise ValueError('oversized progress line')
                        record = validate_training_stats_payload(json.loads(line))
                        if record['run_id'] != self.run_id:
                            raise ValueError('progress run_id mismatch')
                        order = tuple(record[k] for k in ('iteration', 'step', 'agent_step'))
                        stamp = record['timestamp']['value']
                        if self.previous and (any(a < b for a, b in zip(order, self.previous[0])) or stamp < self.previous[1]):
                            raise ValueError('progress record order regressed')
                        self.previous = order, stamp
                        age = max(0., time.time() - stamp)
                        record['source_age_seconds'] = max(record['source_age_seconds'], age)
                        for channel in record['stats'].values():
                            channel['source_age_seconds'] = max(channel['source_age_seconds'], age)
                        self.emit(record)
                    if len(self.partial) > self.max_line_bytes:
                        raise ValueError('oversized partial progress line')
            if final and self.partial:
                raise ValueError('incomplete final progress line')
        except Exception as exc:
            self._fault(str(exc))

    def start(self):
        def work():
            while not self._stop.wait(.05):
                self.read_available()
            self.read_available(final=True)
        self._thread = threading.Thread(target=work, name='training-progress-reader', daemon=True)
        self._thread.start()

    def finish(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
        else:
            self.read_available(final=True)
