"""Read solver-generated blade sections from an existing validated FAST.Farm run.

This is recorded-result playback, separate from Trainer's checkpoint policy
rollout. No solver, training process, pixel inversion, or synthetic fallback.
"""
from collections import OrderedDict
from pathlib import Path
import hashlib
import json
import math
import threading
import time
from types import SimpleNamespace
import numpy as np
from wfrl.lidar.replay import ReplayPackage
from wfrl.lidar.physics import read_surface
from wfrl.viz.tip_geometry import TurbineGeometry


class FastFarmTipReplay:
    def __init__(self, package_path):
        self.path = Path(package_path).resolve()
        self.package = ReplayPackage.load(self.path)
        m = self.package.manifest
        if m.get('source') != 'FAST.Farm' or m.get('model',{}).get('id') != 'nrel5mw':
            raise ValueError('Tip replay currently requires an NREL5MW FAST.Farm result package')
        sources = {Path(s['path']).resolve():s['sha256'] for s in m['raw_sources']}
        configs = [p for p in sources if p.name=='run_config.json' and p.parent.name==m['run_id']]
        if len(configs)!=1:raise ValueError('Missing unique base-run configuration')
        self.run = configs[0].parent
        def verified_bytes(path):
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=sources.get(path):
                raise ValueError('Result source integrity mismatch: '+str(path))
            return data
        def verified(path):
            return json.loads(verified_bytes(path))
        cfg=verified(configs[0]);hashes=verified(self.run/'surface_hashes.json')
        if (verified(self.run/'exit_status.json').get('exit_code') != 0
                or b'FAST.Farm terminated normally.' not in verified_bytes(self.run/'solver.log')):
            raise ValueError('FAST.Farm source run did not complete successfully')
        self.hashes={row['path']:row['sha256'] for row in hashes}
        self.fps=float(cfg['fps']);self.section_count=37 if cfg['span_refined'] else 19
        if not math.isfinite(self.fps) or self.fps<=0:raise ValueError('Invalid source FPS')
        inputs={row['path']:row['sha256'] for row in cfg['inputs']}
        eds=[p for p in inputs if p.startswith('FarmInputs/') and 'ElastoDyn' in p and p.endswith('.dat')]
        if len(eds)!=1:raise ValueError('Missing unique ElastoDyn geometry')
        ed=self.run/eds[0];data=ed.read_bytes()
        if hashlib.sha256(data).hexdigest()!=inputs[eds[0]]:raise ValueError('ElastoDyn geometry hash mismatch')
        parameters={}
        for line in data.decode().splitlines():
            fields=line.split()
            if len(fields)>=2:
                try:parameters[fields[1]]=float(fields[0])
                except ValueError:pass
        tilt=-parameters['ShftTilt'];overhang=parameters['OverHang']
        hub_height=parameters['TowerHt']+parameters['Twr2Shft']-overhang*math.sin(math.radians(tilt))
        self.geometry=TurbineGeometry(hub_height,-overhang*math.cos(math.radians(tilt)),tilt,
                                     parameters['TipRad'],3.,tower_top_radius_m=1.935,
                                     tower_height_m=parameters['TowerHt'],yaw_deg=180)
        segment=m['segment']
        self.motion=[row for row in self.package.motion if segment['start_s']<=row['time_s']<=segment['end_s']]
        self.times=[row['time_s'] for row in self.motion]
        if not self.times or self.times[0]!=segment['start_s'] or self.times[-1]!=segment['end_s']:
            raise ValueError('Source motion must span the complete segment')
        if not np.allclose(np.diff(self.times),1/self.fps,atol=1e-8):
            raise ValueError('Motion and surface output clocks differ')
        self._cache=OrderedDict()
        self.provenance={'file':str(self.path/'manifest.json'), 'channel':'blade_geometry',
                         'manifest_sha256':hashlib.sha256((self.path/'manifest.json').read_bytes()).hexdigest(),
                         'run_id':m['run_id'], 'source':'FAST.Farm',
                         'method':'native AeroDyn surface section centroids',
                         'coordinate_system':'FAST inertial: x downwind, y lateral, z up; tower center origin'}

    def at_index(self,index):
        if type(index) is not int or not 0<=index<len(self.motion):raise ValueError('Frame outside recorded segment')
        if index in self._cache:return self._cache[index]
        row=self.motion[index];frame=round(row['time_s']*self.fps)
        tid=self.package.manifest['turbine_id'];blades={}
        for bid in range(1,4):
            relative=f'FarmInputs/vtk/Case.{tid}.Blade{bid}Surface.{frame:05d}.vtp'
            path=self.run/relative
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=self.hashes.get(relative):
                raise ValueError('Blade surface integrity mismatch: '+relative)
            points,_=read_surface(path)
            if len(points)%self.section_count:raise ValueError('Unexpected blade section topology')
            sections=points.reshape(self.section_count,-1,3).mean(axis=1)
            blades[bid-1]={'P':sections[-1].tolist(),'sections':sections.tolist()}
        result=dict(time_s=row['time_s'],rpm=row['rotor_speed_rpm'],yaw_deg=row['yaw_deg'],
                    azimuth_deg=row['azimuth_deg'],pitch_deg=row['pitch_deg'],blades=blades,
                    provenance=self.provenance)
        self._cache[index]=result
        while len(self._cache)>2:self._cache.popitem(last=False)
        return result

    def wire_payload(self,index):
        sample=self.at_index(index)
        def channel(value,unit,name):
            return dict(value=value,unit=unit,validity='valid',error=None,fidelity='EXPORTED',
                        provenance={**self.provenance,'channel':name},source_age_seconds=0.,stale_after_seconds=2.)
        geometry={'time_s':sample['time_s'], 'source':'FAST.Farm',
                  'coordinate_system':self.provenance['coordinate_system'],
                  'blades':[dict(blade_id=bid+1,tip_m=data['P'],sections_m=data['sections'])
                            for bid,data in sample['blades'].items()]}
        channels={k:channel(sample[key],unit,k) for k,key,unit in (
            ('yaw','yaw_deg','deg'),('rotor_speed','rpm','rpm'),('azimuth','azimuth_deg','deg'))}
        channels['blade_geometry']=channel(geometry,'m','blade_geometry')
        # Original pitch channels contain one scalar per blade; do not reduce to a fake common pitch.
        channels['blade_pitch']=channel(sample['pitch_deg'],'deg','blade_pitch')
        return dict(mode='replay',step=index,phase='recorded_geometry',
                    timestamp={'value':sample['time_s'],'timebase':'simulation_seconds'},
                    turbines=[{'turbine_id':self.package.manifest['turbine_id'],'channels':channels}],farm={})


def sample_from_snapshot(payload,turbine_id=None):
    """Receive the same typed geometry channel from the actual Bridge wire."""
    timestamp=payload['timestamp']
    if timestamp['timebase']!='simulation_seconds':raise ValueError('Geometry requires simulation time')
    candidates=[t for t in payload['turbines'] if turbine_id is None or t['turbine_id']==turbine_id]
    if len(candidates)!=1:raise ValueError('Select one turbine for tip tracking')
    channels=candidates[0]['channels'];channel=channels['blade_geometry']
    if channel['validity']!='valid' or channel['fidelity'] not in ('DIRECT','EXPORTED'):
        raise ValueError('Blade geometry is missing, invalid, stale, or synthetic')
    if channel['source_age_seconds']>=channel['stale_after_seconds']:raise ValueError('Stale blade geometry')
    value=channel['value']
    if value['source']!='FAST.Farm' or value['time_s']!=timestamp['value']:
        raise ValueError('Geometry source or timestamp mismatch')
    if value['coordinate_system']!='FAST inertial: x downwind, y lateral, z up; tower center origin':
        raise ValueError('Unsupported blade coordinate system')
    blades={}
    for blade in value['blades']:
        bid=blade['blade_id']
        if type(bid) is not int or bid not in (1,2,3) or bid-1 in blades:raise ValueError('Invalid blade IDs')
        points=np.asarray(blade['sections_m'],dtype=float);tip=np.asarray(blade['tip_m'],dtype=float)
        if (points.ndim!=2 or points.shape[1]!=3 or not 2<=len(points)<=1024 or tip.shape!=(3,)
                or not np.isfinite(points).all() or not np.isfinite(tip).all()
                or not np.allclose(points[-1],tip,atol=1e-6)):
            raise ValueError('Invalid blade sections/tip')
        blades[bid-1]={'P':tip,'sections':points}
    if len(blades)!=3:raise ValueError('Three blade records required')
    for key in ('rotor_speed','yaw'):
        c=channels[key]
        if c['validity']!='valid' or not math.isfinite(c['value']):raise ValueError('Missing valid '+key)
    return dict(time_s=value['time_s'],rpm=channels['rotor_speed']['value'],yaw_deg=channels['yaw']['value'],
                blades=blades,provenance=channel['provenance'])


class RecordedTipTrainer:
    """BackendSession-compatible, cancellable recorded playback worker."""
    def __init__(self,scene,*,tip_package,on_snapshot,demo=False,replay=True,**options):
        if options:raise ValueError('Unsupported recorded tip options: '+','.join(options))
        if demo or not replay or scene.backend!='fastfarm':raise ValueError('Recorded tips require FAST.Farm replay')
        self.reader=FastFarmTipReplay(tip_package)
        if self.reader.package.manifest['turbine_id'] not in {t.id for t in scene.turbines}:
            raise ValueError('Recorded turbine is absent from selected scene')
        self.on_snapshot=on_snapshot;self.runtime=None;self.running=False;self.error=None
        self._stop=threading.Event();self._thread=None

    def start(self):
        self.running=True
        def work():
            try:
                for index in range(len(self.reader.times)):
                    if self._stop.is_set():break
                    begin=time.monotonic()
                    self.on_snapshot(SimpleNamespace(payload=self.reader.wire_payload(index),wire_events=[]))
                    if self._stop.wait(max(0,1/self.reader.fps-(time.monotonic()-begin))):break
            except Exception as exc:
                self.error=str(exc)
            finally:self.running=False
        self._thread=threading.Thread(target=work,name='recorded-fastfarm-tips',daemon=True)
        self._thread.start()

    def stop(self,timeout=5):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)
