"""BeamDyn v3 provenance and independently checked root-frame tip components."""
from pathlib import Path
import hashlib,json,sys
import numpy as np
from scripts.experiments.calib_blade_flex import read_fast_out
from scripts.lidar.package_prebend_probe import save_json,reference_payload
from wfrl.lidar.physics import read_surface
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'blender_frontend'))
from wfrl_blender.deflection import rigid_frame
from wfrl_blender.turbine_geometry import geometry_data


def supplement(output,report,manifest,times,poses,transforms):
    output=Path(output);farm=Path(report['case_dir'])/'FarmInputs'
    config=report['blade_config'];rest=Path(report['unloaded_reference'])
    unloaded=json.loads((rest/'probe-config.json').read_text())
    if not unloaded['unloaded'] or unloaded['reference_curve']!=config['reference_curve']:
        raise ValueError('Missing matching unloaded reference')
    save_json(output/'blade-reference.json',reference_payload(config,config['structural_source']))
    save_json(output/'source-run.json',report)
    for name in ('blade-reference.json','source-run.json'):
        manifest['files'][name]=hashlib.sha256((output/name).read_bytes()).hexdigest()
    def rest_surface(kind):
        path=next(p for p in (rest/'FarmInputs/vtk').glob(f'FFTest_WT1.{kind}Surface.*.vtp') if int(p.stem.rsplit('.',1)[1])==0)
        return read_surface(path)
    blades=[rest_surface(f'Blade{b}') for b in (1,2,3)]
    tower_points,tower_triangles=rest_surface('Tower')
    np.savez_compressed(output/'reference-surfaces.npz',blades=[b[0] for b in blades],triangles=blades[0][1],tower=tower_points,tower_triangles=tower_triangles)
    manifest['files']['reference-surfaces.npz']=hashlib.sha256((output/'reference-surfaces.npz').read_bytes()).hexdigest()
    scalars=geometry_data()['scalars'];tip=np.array([-1.,0.,scalars['TipRad']])
    refs=[]
    for b in (1,2,3):
        hub,_,axes=rigid_frame(scalars,[0]*6,b);refs.append(hub+axes@tip)
    with np.load(output/'tower-motion.npz') as a:nacelles=a['nacelle']
    maximum=0.;by_turbine={};telemetry={};sources={}
    for k,tid in enumerate(manifest['turbine_ids']):
        path=farm/f'Case.{tid}.out';columns=read_fast_out(path)
        lines=path.read_text().splitlines();units=dict(zip(lines[6].split(),lines[7].split()))
        for b in (1,2,3):
            for axis in 'xyz':
                if units[f'B{b}TipTD{axis}r']!='(m)':raise ValueError('Unexpected BeamDyn displacement unit')
        ids=np.searchsorted(columns['Time'],times)
        if not np.allclose(columns['Time'][ids],times,atol=1e-8,rtol=0):raise ValueError('Output timestamp mismatch')
        source_hash=hashlib.sha256(path.read_bytes()).hexdigest()
        sources[tid]=dict(path=str(path),sha256=source_hash)
        actual=[];rigid=[];components=[]
        sim=np.stack([np.column_stack([columns[f'B{b}TipTD{a}r'][ids] for a in 'xyz']) for b in (1,2,3)],axis=1)
        for i,t in enumerate(times):
            row=[];ar=[];rr=[]
            for b in (1,2,3):
                tr=transforms[i,k,b-1,-1];point=tr[:,:3]@refs[b-1]+tr[:,3]
                hub,_,axes=rigid_frame(scalars,poses[i,k],b,nacelles[i,k]);ref=hub+axes@tip
                row.append(axes.T@(point-ref));ar.append(point);rr.append(ref)
            components.append(row);actual.append(ar);rigid.append(rr)
        error=np.asarray(components)-sim
        value=float(np.max(abs(error)));maximum=max(maximum,value)
        by_turbine[tid]=dict(max_component_error_m=value,max_by_blade_m=np.max(abs(error),axis=(0,2)).tolist())
        if value>.005:raise ValueError(f'BeamDyn component mismatch {tid}: {value}')
        if k==0:
            payload=dict(schema='wfrl.tip-deflection.t1.v3',turbine_id='T1',unit='m',
                reference='BeamDyn structural tip; independent unloaded prebend',frame='BeamDyn pitched root xyz',
                geometry_sha256=manifest['files']['geometry.npz'],tower_motion_sha256=manifest['files']['tower-motion.npz'],
                reference_sha256=manifest['files']['blade-reference.json'],scalars=scalars,
                times=times.tolist(),poses=poses[:,0].tolist(),simulation=sim.tolist(),sources=sources.copy())
            save_json(output/'deflection-t1.json',payload)
        telemetry[tid]={}
        for name,channel,unit,scale in [('power','GenPwr','MW',.001),('torque','GenTq','N m',1000.)]:
            if channel in columns:telemetry[tid][name]=dict(values=(columns[channel][ids]*scale).tolist(),unit=unit,source_channel=channel)
    save_json(output/'telemetry.json',dict(schema='wfrl.farm-telemetry.v1',geometry_sha256=manifest['files']['geometry.npz'],times=times.tolist(),sources=sources,turbines=telemetry))
    save_json(output/'interface-audit.json',dict(max_component_error_m=maximum,turbines=by_turbine,reference_tip_world_m=refs and np.asarray(refs).tolist()))
    for name in ('deflection-t1.json','telemetry.json','interface-audit.json'):
        manifest['files'][name]=hashlib.sha256((output/name).read_bytes()).hexdigest()
    manifest.update(schema='wfrl.farm-flex-review.v3',structural_module='BeamDyn',model=config['model'],
        provenance_label='基于 NREL 5MW 的预弯改型 · BeamDyn · MAPPO 偏航 · 随机阵风 · 仿真回放',
        reference_frame='independent zero-load stationary solve',max_component_error_m=maximum,
        clearance_definition='Terminal contour surface minimum to same-global-height moving tower section; not whole-blade minimum',
        measurement_window_deg=3,diagnostic_window_deg=15)
