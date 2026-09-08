"""Value-only conversion of existing Trainer snapshots to protocol v1."""
import dataclasses
import math
import numpy as np

UNITS = dict(yaw='deg', pitch='deg', rotor_speed='rpm', power='MW', load='',
             torque='N m', pitch_command='deg', pitch_measured='deg', wind_speed='m/s', wind_direction='deg', reward='',
             m_flap='N m', m_edge='N m')


def json_value(value):
    if isinstance(value, np.ndarray): return json_value(value.tolist())
    if isinstance(value, np.generic): return json_value(value.item())
    if dataclasses.is_dataclass(value): return json_value(dataclasses.asdict(value))
    if isinstance(value, dict): return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): raise ValueError('non-finite source value')
    if value is None or type(value) in (str, bool, int, float): return value
    raise ValueError('unsupported source value')


class SnapshotAdapter:
    def __init__(self, scene, session_id, mode='interactive_training'):
        self.scene, self.session_id, self.mode = scene, session_id, mode
        self.ids = [t.id for t in scene.turbines]
        self.event_counter = 0
        self.backend_demo = False
        self.channel_sources = {}

    def channel(self, value, key, *, missing=False, threshold=2.0):
        source = self.channel_sources.get(key, {})
        derived_rpm = key == 'rotor_speed' and getattr(self.scene, 'backend', None) == 'fastfarm'
        synth = not missing and ((self.mode == 'demo' and not self.backend_demo) or derived_rpm)
        provenance = ({'formula': 'Trainer._demo_sequence' if self.mode == 'demo' else
                       'FastFarmDriver._rotor_speed: P/(efficiency*torque)/gear_ratio', 'channel': key}
                      if synth else {'backend_session': self.session_id, 'channel': key})
        fidelity = 'SYNTH' if synth else 'DIRECT'
        if source:
            fidelity = source['fidelity']
            provenance = json_value(source['provenance'])
        if self.backend_demo: provenance['model'] = 'FLORIS steady-state model output, not field measurements'
        validity, error = 'valid', None
        if missing or value is None:
            value, validity, error = None, 'unsupported', 'Source channel unavailable'
        else:
            try: value = json_value(value)
            except ValueError as exc: value, validity, error = None, 'invalid', str(exc)
        return dict(value=value, unit=UNITS.get(key, ''), validity=validity, error=error,
                    fidelity=fidelity, provenance=provenance,
                    source_age_seconds=0.0, stale_after_seconds=threshold)

    def context(self, snapshot):
        timestamp = snapshot.ctx.get('t', snapshot.step * (0.12 if self.mode == 'demo' else self.scene.dt))
        return dict(mode=self.mode, step=int(snapshot.step),
                    iteration=int(getattr(snapshot, 'iters_done', 0)), phase=getattr(snapshot, 'phase', 'waiting'),
                    timestamp=dict(value=float(timestamp), timebase='simulation_seconds'))

    def encode(self, snapshot):
        self.backend_demo = bool(snapshot.ctx.get('backend_demo'))
        self.channel_sources = snapshot.ctx.get('channel_sources', {})
        m = dict(snapshot.measure)
        if 'rotorspeed' in m:
            if 'rotor_speed' in m and not np.array_equal(m['rotorspeed'], m['rotor_speed'], equal_nan=True):
                raise ValueError('Conflicting rotor_speed aliases')
            m['rotor_speed'] = m.pop('rotorspeed')
        if 'pitch_meas' in m:
            if 'pitch' in m and 'pitch' in self.scene.controls: m['pitch_command'] = m['pitch']
            m['pitch_measured'] = m['pitch_meas']
            m['pitch'] = m['pitch_meas']
        turbines = []
        for i, tid in enumerate(self.ids):
            channels = {}
            for key in UNITS:
                value = m.get(key)
                if value is not None:
                    arr = np.asarray(value)
                    value = arr[i] if arr.ndim >= 1 and len(arr) == len(self.ids) else float('nan')
                channels[key] = self.channel(value, key, missing=key not in m)
            turbines.append(dict(turbine_id=tid, channels=channels))
        payload = dict(self.context(snapshot), turbines=turbines,
                    farm={'power': self.channel(snapshot.farm_power, 'power'),
                          'reward': self.channel(snapshot.reward, 'reward')})
        if hasattr(self.scene, 'to_dict'): payload['scene'] = self.scene.to_dict()
        return payload

    def safety_events(self, snapshot):
        for event in snapshot.events:
            raw = dataclasses.asdict(event) if dataclasses.is_dataclass(event) else dict(event)
            self.event_counter += 1
            tid = raw.get('turbine')
            yield dict(self.context(snapshot), event_id=f'{self.session_id}:{self.event_counter}',
                       turbine_id=tid if tid in self.ids else None,
                       severity={'warn': 'warning', 'error': 'critical', 'block': 'critical'}.get(raw.get('severity'), raw.get('severity', 'info')),
                       message=str(raw.get('detail') or raw.get('rule') or 'Safety event'),
                       data=self.channel(raw, 'safety_event', threshold=10.0))
