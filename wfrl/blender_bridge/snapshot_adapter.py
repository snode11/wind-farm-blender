"""Value-only conversion of existing Trainer snapshots to protocol v1."""
import dataclasses
import math
import numpy as np

from wfrl.scene.power import farm_power_total
from .messages import BLADE_COMPONENTS, SOLVER_OUTPUT_CHANNELS, ALL_SOLVER_OUTPUT_CHANNELS, SOLVER_OUTPUT_CATALOGS

UNITS = dict(yaw='deg', yaw_command='deg', yaw_measured='deg', pitch='deg', rotor_speed='rpm', power='MW', load='',
             torque='N m', pitch_command='deg', pitch_measured='deg', wind_speed='m/s', wind_direction='deg', reward='',
             m_flap='N m', m_edge='N m')
UNITS.update({name: 'N m' for name in BLADE_COMPONENTS})
UNITS.update({name: spec['unit'] for name, spec in ALL_SOLVER_OUTPUT_CHANNELS.items()})


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
        synth = not missing and self.mode == 'demo' and not self.backend_demo
        provenance = ({'formula': 'Trainer._demo_sequence', 'channel': key}
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
            try:
                if key == 'power':
                    value = farm_power_total([value], 1)
                    if not math.isfinite(value):
                        raise ValueError('power must be a finite scalar number')
                value = json_value(value)
            except (ValueError, OverflowError) as exc:
                value, validity, error = None, 'invalid', str(exc)
        return dict(value=value, unit=UNITS.get(key, ''), validity=validity, error=error,
                    fidelity=fidelity, provenance=provenance,
                    source_age_seconds=0.0, stale_after_seconds=threshold)

    def context(self, snapshot):
        timestamp = snapshot.ctx.get('t', snapshot.step * (0.12 if self.mode == 'demo' else self.scene.dt))
        context = dict(mode=self.mode, step=int(snapshot.step),
                    iteration=int(getattr(snapshot, 'iters_done', 0)), phase=getattr(snapshot, 'phase', 'waiting'),
                    timestamp=dict(value=float(timestamp), timebase='simulation_seconds'))
        if snapshot.ctx.get('timestamp') is not None:
            context['timestamp'] = json_value(snapshot.ctx['timestamp'])
        if 'control_elapsed_seconds' in snapshot.ctx:
            context['control_elapsed_seconds'] = float(snapshot.ctx['control_elapsed_seconds'])
        episode = getattr(snapshot, 'episode', None)
        if episode is not None:
            context['episode'] = int(episode)
        return context

    def encode(self, snapshot):
        self.backend_demo = bool(snapshot.ctx.get('backend_demo'))
        self.channel_sources = snapshot.ctx.get('channel_sources', {})
        m = dict(snapshot.measure)
        wind_record = m.get('wind_conditions') or {}
        coordinates = wind_record.get('coordinates', {})
        direction = (wind_record.get('application', {}).get('wind_direction') or {})
        transform = coordinates.get('transform') or {}
        if 'yaw_measured' in m:
            m['yaw'] = m['yaw_measured']
        elif getattr(self.scene, 'backend', None) == 'fastfarm' and self.mode != 'demo':
            if 'yaw' in getattr(self.scene, 'controls', ()):
                m.setdefault('yaw_command', m.get('yaw'))
            m.pop('yaw', None)
        if 'rotorspeed' in m:
            if 'rotor_speed' in m and not np.array_equal(m['rotorspeed'], m['rotor_speed'], equal_nan=True):
                raise ValueError('Conflicting rotor_speed aliases')
            m['rotor_speed'] = m.pop('rotorspeed')
        if 'pitch_meas' in m:
            if 'pitch' in m and 'pitch' in getattr(self.scene, 'controls', ()): m['pitch_command'] = m['pitch']
            m['pitch_measured'] = m['pitch_meas']
            m['pitch'] = m['pitch_meas']
        turbines = []
        clock = m.get('solver_clock')
        native = m.get('output_channels')
        if native is not None and (not isinstance(native, list) or len(native) != len(self.ids)):
            raise ValueError('Native output records require one mapping per turbine')
        for i, tid in enumerate(self.ids):
            channels = {}
            for key in UNITS:
                source_key, component = BLADE_COMPONENTS.get(key, (key, None))
                value = m.get(source_key)
                if value is not None:
                    try:
                        if key == 'power':
                            arr = value if isinstance(value, np.ndarray) else np.asarray(value, dtype=object)
                        else:
                            arr = np.asarray(value)
                        shape_ok = arr.ndim >= 1 and len(arr) == len(self.ids)
                        if key == 'power':
                            shape_ok = shape_ok and arr.ndim == 1
                        if source_key in ('m_flap', 'm_edge'):
                            shape_ok = arr.shape == (len(self.ids), 3)
                        value = ((arr[i, component] if component is not None else arr[i])
                                 if shape_ok else float('nan'))
                    except (TypeError, ValueError):
                        value = float('nan')
                record = self.channel(value, key, missing=source_key not in m)
                if component is not None:
                    record['provenance'].update(channel=source_key, blade_index=component + 1)
                if key in ('yaw', 'yaw_measured', 'yaw_command'):
                    record['provenance']['role'] = 'command' if key == 'yaw_command' else 'measurement'
                    if transform:
                        record['provenance'].update(frame='wind_aligned_solver',
                            world_yaw_offset_deg=transform['world_yaw_offset_deg'])
                    if key == 'yaw' and 'yaw_measured' in m:
                        record['provenance']['channel'] = 'yaw_measured'
                if clock:
                    sample_time = float(clock['value'])
                    source = 'coupling_boundary'
                    if key in ('power', 'torque'):
                        sample_time -= clock['servo_time_steps'][i]
                        source = 'previous_servo_step'
                        if key == 'power':
                            record['provenance']['power_window_steps'] = clock['power_window_steps']
                    record['timestamp'] = dict(value=sample_time, timebase='simulation_seconds', source=source)
                channels[key] = record
            if native is not None:
                if (not isinstance(native[i], dict) or
                        frozenset(native[i]) not in SOLVER_OUTPUT_CATALOGS):
                    raise ValueError('Native output records must match the channel catalog')
                for key, record in native[i].items():
                    channels[key] = json_value(record)
                    channels[key]['provenance']['backend_session'] = self.session_id
            elif getattr(self.scene, 'backend', None) == 'fastfarm' and self.mode != 'demo':
                for key in SOLVER_OUTPUT_CHANNELS:
                    if key not in self.channel_sources:
                        channels[key] = self.channel(None, key, missing=True)
            turbines.append(dict(turbine_id=tid, channels=channels))
        farm_power = self.channel(snapshot.farm_power, 'power', missing='power' not in m)
        if clock and len(set(clock['servo_time_steps'])) == 1:
            farm_power['timestamp'] = dict(value=clock['value'] - clock['servo_time_steps'][0],
                                          timebase='simulation_seconds', source='previous_servo_step')
        if 'power' in m and (not math.isfinite(farm_power_total(m['power'], len(self.ids)))
                             or any(t['channels']['power']['validity'] != 'valid' for t in turbines)):
            farm_power.update(value=None, validity='invalid',
                              error='Farm power requires one finite value per expected turbine and a finite total')
        payload = dict(self.context(snapshot), turbines=turbines,
                    farm={'power': farm_power,
                          'reward': self.channel(snapshot.reward, 'reward')})
        if hasattr(self.scene, 'to_dict'): payload['scene'] = self.scene.to_dict()
        physics = m.get('physics', {})
        marine = physics.get('substructure') if isinstance(physics, dict) else None
        if marine is not None:
            from .messages import OFFSHORE_OUTPUT_CHANNELS
            computed = self.mode != 'demo' and bool(physics.get('identity')) and all(
                t['channels'].get(key, {}).get('validity') == 'valid'
                and t['channels'][key].get('fidelity') == 'DIRECT'
                and t['channels'][key].get('provenance', {}).get('physics_identity') == physics['identity']
                for t in turbines for key in OFFSHORE_OUTPUT_CHANNELS)
            payload['hydrodynamics'] = dict(
                status='computed' if computed else 'configured', model='oc3_monopile',
                physics_identity=physics.get('identity'), wave_field=marine['wave_field'],
                requested=json_value(marine['requested']))
        elif getattr(self.scene, 'substructure', None) is not None:
            payload['hydrodynamics'] = dict(status='requested', model='oc3_monopile')
        else:
            payload['hydrodynamics'] = dict(status='not_computed', model=None)
        if direction.get('status') in ('applied', 'MATCHES_FIXED_COORDINATES', 'input_file') and 'scene' in payload:
            payload['scene']['inflow']['direction'] = direction['value']
            if transform:
                payload['wind_coordinates'] = json_value(transform)
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
