"""Summarize explicit probe boundaries; never infer missing input/display times."""
import argparse
import json
import math
from pathlib import Path
import statistics


def stats(values):
    values=sorted(values)
    return {'n':len(values),'median_s':statistics.median(values),
            'p95_nearest_rank_s':values[math.ceil(.95*len(values))-1],'max_s':max(values)} if values else {'n':0}


def stages(events, received):
    t2=next((e for e in events if e['kind']=='t2' and e['at']>=received['at']),None)
    if t2 is None:return None
    t3=next((e for e in events if e['kind']=='t3' and e['at']>=t2['at']
        and e.get('renderer')==t2.get('renderer') and e['version']==t2['version']),None)
    if t3 is None:return None
    t4=next((e for e in events if e['kind']=='t4' and e['at']>=t3['at']
        and e.get('renderer')==t3.get('renderer') and e['version']==t3['version']
        and e['request']==t3['request']),None)
    if t4 is None:return None
    return dict(refresh_wait_s=t2['at']-received['at'],draw_s=t3['at']-t2['at'],
        display_callback_s=t4['at']-t3['at'],handler_to_callback_s=t4['at']-received['at'])


def summarize(window, external):
    window,external=Path(window),Path(external)
    events=json.loads((window/'trace.json').read_text())
    numeric=[row for e in events if e['kind']=='t1' and e.get('input')=='numeric'
             if (row:=stages(events,e)) is not None]
    result={'scripted_numeric_raw':numeric,
            'scripted_numeric_summary':{k:stats([row[k] for row in numeric]) for k in numeric[0]} if numeric else {},
            'window_result':json.loads((window/'result.json').read_text())}
    raw=json.loads((external/'external-input.json').read_text())
    events=json.loads((external/'external-trace.json').read_text())
    clock=raw['clock_mapping'];offset=clock['unix_s']-clock['monotonic_s']
    rows=[]
    for action,start,end in raw['actions']:
        if not action.startswith('numeric'):continue
        t0=start/1000-offset
        t1=next((e for e in events if e['kind']=='t1' and e.get('input')=='numeric' and t0<=e['at']<t0+2),None)
        if t1 is None:continue
        timing=stages(events,t1)
        if timing:
            rows.append(dict(action=action,input_wait_s=t1['at']-t0,**timing,
                             external_to_callback_s=t1['at']-t0+timing['handler_to_callback_s']))
    result['external_numeric_raw']=rows
    cancel_start=next(start for action,start,end in raw['actions'] if action=='capture_cancel_direct')/1000-offset
    manifest=next(json.loads(p.read_text()) for p in external.glob('capture_*/manifest.json')
        if (m:=json.loads(p.read_text())).get('cancel_accepted_at',0)>=cancel_start)
    result['external_cancel']={'n':1,'completed_groups':manifest['completed_samples'],
        'external_to_accept_s':manifest['cancel_accepted_at']-cancel_start,
        'accept_to_stop_s':manifest['cancel_stopped_at']-manifest['cancel_accepted_at'],
        'external_to_stop_s':manifest['cancel_stopped_at']-cancel_start}
    result['limitations']=raw['limitations']+[
        'External t0 is entry into the CUA input method (millisecond wall-clock), including tool dispatch overhead.',
        't4 is viewport draw callback completion, not physical screen illumination.',
        'Scripted continuous direction calls are not physical OS pointer event measurements.']
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('window');parser.add_argument('external');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    payload=summarize(args.window,args.external)
    Path(args.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(payload['scripted_numeric_summary'],indent=2))
