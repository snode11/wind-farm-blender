"""Bounded consumption of native OpenFAST measurements alongside MPI control."""
from collections import deque
from numbers import Real
from pathlib import Path
import math
import os
import shutil

import numpy as np

from wfrl.blender_bridge.messages import SOLVER_OUTPUT_CHANNELS, TOWER_OUTPUT_CHANNELS, TORSION_OUTPUT_CHANNELS, OFFSHORE_OUTPUT_CHANNELS
from wfrl.blade_torsion import elastic_twist_degrees, TORSION_DEFINITION


def _time_value(value, name, *, positive=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f'{name} must be finite seconds')
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f'{name} must be finite seconds') from exc
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f'{name} must be finite and {"positive" if positive else "nonnegative"}')
    return value


def prepare_case(farm_path, physics=None, *, tower_outputs=False, substructure=None):
    """Configure output channels in an owned generated case before solver startup."""
    from wfcrl.simul_utils import FASTInputFile
    from wfcrl.fastfarm_physics import normalize_physics, configure_physics, write_physics_record
    physics = normalize_physics(physics)
    if any(physics.values()) or substructure is not None:
        configure_physics(farm_path, physics, substructure=substructure, apply=True)
    tower_outputs = tower_outputs or physics['tower_flex']
    farm_path = Path(farm_path).resolve()
    farm = FASTInputFile(str(farm_path))
    inflow_path = (farm_path.parent / str(farm['InflowFile']).strip('"')).resolve()
    inflow = FASTInputFile(str(inflow_path))
    if int(inflow['WindType']) == 3:
        wind_path = (inflow_path.parent / str(inflow['FileName_BTS']).strip('"')).resolve()
        if wind_path.parent != inflow_path.parent:
            local_wind = inflow_path.parent / 'wfrl_inflow.bts'
            shutil.copyfile(wind_path, local_wind)
            inflow['FileName_BTS'] = '"wfrl_inflow.bts"'
            inflow.write(str(inflow_path))
    definitions = []
    for turbine in farm['WindTurbines']:
        primary_path = (farm_path.parent / str(turbine[3]).strip('"')).resolve()
        primary = FASTInputFile(str(primary_path))
        ed_path = (primary_path.parent / str(primary['EDFile']).strip('"')).resolve()
        ed = FASTInputFile(str(ed_path))
        elastic = int(primary['CompElast'])
        nodes = int(ed['BldNodes'])
        gauges = sorted({1, (nodes + 1) // 2, nodes}) if elastic == 1 else []
        if elastic == 1 and len(gauges) != 3:
            raise ValueError('Three distinct blade gages require at least three ElastoDyn nodes')
        if gauges:
            ed['NBlGages'], ed['BldGagNd'] = 3, ', '.join(map(str, gauges))
        requested = [spec['native'] for spec in SOLVER_OUTPUT_CHANNELS.values()
                     if elastic == 1 or spec['blade'] is None]
        if tower_outputs:
            requested += [spec['native'] for spec in TOWER_OUTPUT_CHANNELS.values()]
        if substructure is not None:
            from wfcrl.fastfarm_substructure import PLATFORM_OUTPUTS
            requested += list(PLATFORM_OUTPUTS)
        if physics['blade_torsion']:
            requested += [f'BlPitch{blade}' for blade in (1, 2, 3)]
        existing = {word.strip('"').upper() for line in ed['OutList']
                    for word in line.replace(',', ' ').split()}
        ed['OutList'] = list(ed['OutList']) + [name for name in requested if name.upper() not in existing]
        ed.write(str(ed_path))
        dt = float(primary['DT'])
        output_dt = primary['DT_Out']
        output_dt = dt if str(output_dt).strip('"').lower() == 'default' else float(output_dt)
        if not math.isfinite(output_dt) or output_dt <= 0:
            raise ValueError('OpenFAST output interval must be positive and finite')
        output_dt = min(output_dt, .25, float(farm['DT_Low']))
        primary['DT_Out'] = max(1, int(round(output_dt / dt))) * dt
        primary['OutFileFmt'] = 3 if int(primary['OutFileFmt']) in (2, 3) else 1
        primary['OutFmt'] = '"ES17.9E3"'
        primary.write(str(primary_path))
        definitions.append(dict(gauge_nodes=gauges, comp_elast=elastic,
                                output_interval_seconds=float(primary['DT_Out'])))
        if tower_outputs:
            definitions[-1]['tower_outputs'] = True
        if physics['blade_torsion']:
            definitions[-1]['blade_torsion'] = True
        if substructure is not None:
            definitions[-1]['offshore_outputs'] = True
    if any(physics.values()) or substructure is not None:
        write_physics_record(farm_path, configure_physics(farm_path, physics, substructure=substructure))
    return definitions


class OutputReader:
    """Read only complete rows; retain future rows until their simulation time."""
    READ_LIMIT = 16 * 1024 * 1024
    LINE_LIMIT = 64 * 1024
    FUTURE_LIMIT = 10000

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.partial = b''
        self.identity = None
        self._header_lines = []
        self._read_tail = b''
        self.names = self.units = None
        self.last_time = None
        self._last_target = None
        self.latest = None
        self.future = deque()
        self.interval = []
        self.error = None

    def advance(self, target):
        """Read through a finite, nondecreasing target; ``interval`` keeps every consumed row."""
        target = _time_value(target, 'target')
        if self._last_target is not None and target < self._last_target:
            raise ValueError('Output target must be nondecreasing within this run')
        self._last_target = target
        self.interval = []
        if self.error:
            return None
        try:
            with self.path.open('rb') as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self.identity is not None and (identity != self.identity or stat.st_size < self.offset):
                    raise ValueError('Output file was replaced or truncated within this run')
                self.identity = identity
                # Cached rows belong to the same header and already-read file content.
                checkpoints = self._header_lines + [(self.offset - len(self._read_tail), self._read_tail)]
                for position, expected in checkpoints:
                    stream.seek(position)
                    if stream.read(len(expected)) != expected:
                        raise ValueError('Output file was rewritten within this run')
                while self.future and self.future[0][0] <= target + 1e-9:
                    row = self.future.popleft()
                    self.interval.append(row)
                    if self.latest is None or row[0] > self.latest[0]:
                        self.latest = row
                line_end = self.offset - len(self.partial)
                stream.seek(self.offset)
                chunk = stream.read(self.READ_LIMIT)
                self.offset += len(chunk)
                self._read_tail = (self._read_tail + chunk[-self.LINE_LIMIT:])[-self.LINE_LIMIT:]
            lines = (self.partial + chunk).split(b'\n')
            self.partial = lines.pop()
            if len(self.partial) > self.LINE_LIMIT:
                raise ValueError('Incomplete output row exceeds the size limit')
            for line in lines:
                line_start = line_end
                line_end += len(line) + 1
                words = line.decode('utf-8', errors='replace').split()
                if not words:
                    continue
                if self.names is None:
                    if words[0] == 'Time' and len(words) > 1:
                        if len(set(words)) != len(words):
                            raise ValueError('Duplicate output column names')
                        self.names = words
                        self._header_lines.append((line_start, line + b'\n'))
                    continue
                if self.units is None:
                    if len(words) != len(self.names) or words[0] != '(s)':
                        raise ValueError('Invalid output units header')
                    self.units = [word.strip('()') for word in words]
                    self._header_lines.append((line_start, line + b'\n'))
                    continue
                if len(words) != len(self.names):
                    raise ValueError('Complete output row has the wrong column count')
                time = float(words[0])
                if not math.isfinite(time) or time < 0 or (self.last_time is not None and time <= self.last_time):
                    raise ValueError('Output timestamps must be finite and strictly increasing')
                self.last_time = time
                row = (time, words)
                if time <= target + 1e-9:
                    self.latest = row
                    self.interval.append(row)
                else:
                    self.future.append(row)
                    if len(self.future) > self.FUTURE_LIMIT:
                        raise ValueError('Future output backlog exceeds the bounded reader capacity')
            return self.latest
        except (OSError, ValueError, OverflowError) as exc:
            if isinstance(exc, FileNotFoundError) and self.identity is None:
                return None
            self.error = ('Output file disappeared within this run'
                          if isinstance(exc, FileNotFoundError) else str(exc))
            self.latest = None
            self.future.clear()
            self.interval = []
            return None


class SolverOutputs:
    """Per-run files supply direct measurements with their own source timestamps."""
    def __init__(self, farm_path, physics=None, *, substructure=None):
        self.definitions = prepare_case(farm_path, physics, substructure=substructure)
        self.readers = []
        self.farm_path = None

    def start(self, loaded_inputs):
        self.farm_path = Path(loaded_inputs['farm_file']['path']).resolve()
        self.readers = [OutputReader(self.farm_path.with_name(f'{self.farm_path.stem}.T{i + 1}.out'))
                        for i in range(len(self.definitions))]

    def sample(self, solver_time, coupling_interval):
        solver_time = _time_value(solver_time, 'solver_time')
        coupling_interval = _time_value(coupling_interval, 'coupling_interval', positive=True)
        if len(self.readers) != len(self.definitions):
            raise RuntimeError('Native output readers require a started solver run')
        records = []
        catalog = dict(SOLVER_OUTPUT_CHANNELS)
        if any(definition.get('tower_outputs') for definition in self.definitions):
            catalog.update(TOWER_OUTPUT_CHANNELS)
        if any(definition.get('blade_torsion') for definition in self.definitions):
            catalog.update(TORSION_OUTPUT_CHANNELS)
        if any(definition.get('offshore_outputs') for definition in self.definitions):
            catalog.update(OFFSHORE_OUTPUT_CHANNELS)
        values = {key: np.full(len(self.readers), np.nan) for key in catalog}
        # Latest rows feed display; the interval peak feeds overspeed checks between control steps.
        values.update({key: np.full(len(self.readers), np.nan) for key in
                       ('rotor_speed_interval_max', 'rotor_speed_interval_max_time')})
        values['rotor_speed_interval_samples'] = np.zeros(len(self.readers), dtype=int)
        for ti, (reader, definition) in enumerate(zip(self.readers, self.definitions)):
            row = reader.advance(solver_time)
            columns = {name: j for j, name in enumerate(reader.names or ())}
            units_ready = (reader.units is not None and reader.names is not None
                           and len(reader.units) == len(reader.names))
            turbine = {}
            for key, spec in catalog.items():
                provenance = dict(source='OpenFAST native text output', channel=spec['native'],
                                  file=str(reader.path), farm_file=str(self.farm_path),
                                  native_unit=spec['native_unit'], scale=spec['scale'],
                                  coordinate_frame=spec['frame'], comp_elast=definition['comp_elast'])
                if spec['blade'] is not None:
                    provenance['blade_index'] = spec['blade']
                if spec['gauge'] is not None:
                    provenance['gage_index'] = spec['gauge']
                    if definition['gauge_nodes']:
                        provenance['elastodyn_node'] = definition['gauge_nodes'][spec['gauge'] - 1]
                if key in TORSION_OUTPUT_CHANNELS:
                    provenance.update(channels=spec['components'], derivation=TORSION_DEFINITION,
                                      channel=', '.join(spec['components']), value_kind='DERIVED',
                                      beamdyn_node=49, span_fraction=1., radius_from_root_m=61.5)
                    provenance.pop('scale')
                record = dict(value=None, unit=spec['unit'], validity='waiting',
                              error='No complete native output sample at or before the solver time',
                              fidelity='DIRECT', provenance=provenance,
                              source_age_seconds=0., stale_after_seconds=2.)
                if reader.error:
                    record.update(validity='invalid', error=reader.error)
                elif row is not None and units_ready:
                    time, words = row
                    record['timestamp'] = dict(value=time, timebase='simulation_seconds', source='openfast_output')
                    provenance['simulation_lag_seconds'] = max(0., solver_time - time)
                    index = columns.get(spec['native'])
                    components = spec.get('components', [spec['native']])
                    indices = [columns.get(name) for name in components]
                    if any(j is None or reader.units[j] == 'INVALID' for j in indices):
                        record.update(validity='unsupported', error='Native channel unavailable in this structural model')
                    elif any(reader.units[j] != spec['native_unit'] for j in indices):
                        record.update(validity='invalid', error='Native output unit disagrees with the channel contract')
                    elif time > solver_time + 1e-9:
                        record.update(validity='waiting', error='Printed native time is later than the solver frame')
                    elif solver_time - time > coupling_interval + definition['output_interval_seconds'] + 5.1e-5:
                        record.update(validity='waiting', error='Native output has not reached the current coupling interval')
                    else:
                        try:
                            if key in TORSION_OUTPUT_CHANNELS:
                                value = float(elastic_twist_degrees([float(words[j].replace('D', 'E')) for j in indices]))
                            else:
                                value = float(words[index].replace('D', 'E')) * spec['scale']
                            if not math.isfinite(value):
                                raise ValueError('Non-finite native measurement')
                            record.update(value=value, validity='valid', error=None)
                            values[key][ti] = value
                        except (ValueError, OverflowError):
                            record.update(validity='invalid', error='Invalid native measurement')
                turbine[key] = record
            records.append(turbine)
            peak, peak_time, count = self._interval_peak(reader, columns, 'rotor_speed')
            values['rotor_speed_interval_max'][ti] = peak
            values['rotor_speed_interval_max_time'][ti] = peak_time
            values['rotor_speed_interval_samples'][ti] = count
        return values, records

    @staticmethod
    def _interval_peak(reader, columns, key):
        """Largest finite native sample consumed in this coupling interval, with its time."""
        if (not reader.interval or reader.error or reader.units is None or reader.names is None
                or len(reader.units) != len(reader.names)):
            return np.nan, np.nan, 0
        spec = SOLVER_OUTPUT_CHANNELS[key]
        index = columns.get(spec['native'])
        if index is None or reader.units[index] != spec['native_unit']:
            return np.nan, np.nan, 0
        peak, peak_time, count = np.nan, np.nan, 0
        for time, words in reader.interval:
            try:
                value = float(words[index].replace('D', 'E')) * spec['scale']
            except (ValueError, OverflowError):
                continue
            if not math.isfinite(value):
                continue
            count += 1
            if not peak >= value:  # also true while peak is still NaN
                peak, peak_time = value, time
        return peak, peak_time, count
