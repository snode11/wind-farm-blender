"""Check every saved structural tip against independent same-run scalar output."""
from pathlib import Path
import argparse,json,sys
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'blender_frontend'))
from wfrl_blender.farm_flex import read_package
from wfrl_blender.deflection import read_comparison,rigid_frame,compare
from wfrl_blender.tower_motion import read_tower


def validate(package,output):
    manifest,times,transforms,poses,readers=read_package(package)
    d=read_comparison(package,manifest,times,poses)
    support=read_tower(package,manifest,times)
    errors=[]
    for b in (1,2,3):
        hub,axes,_=rigid_frame(d['scalars'],[0]*6,b)
        rest=hub+axes[:,2]*d['scalars']['TipRad']
        for i,pose in enumerate(d['poses']):
            hub,axes,_=rigid_frame(d['scalars'],pose,b,d['nacelle'][i])
            ref=hub+axes[:,2]*d['scalars']['TipRad']
            tr=transforms[i,0,b-1,-1]
            actual=tr[:,:3]@rest+tr[:,3]
            errors.append(compare(actual,ref,axes,d['simulation'][i,b-1])['error'])
    maximum=np.max(np.abs(errors),axis=0)
    if np.max(maximum)>.0002:raise ValueError('Structural deflection disagreement: '+str(maximum))
    data=json.loads((Path(package)/'data.json').read_text())
    result=dict(status='PASS',saved_tips=len(errors),max_error_m=maximum.tolist(),
        tower_station_count=len(support['heights']),statistics={tid:p['statistics'] for tid,p in data.items()})
    Path(output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('package');p.add_argument('output');a=p.parse_args()
    validate(a.package,a.output)
