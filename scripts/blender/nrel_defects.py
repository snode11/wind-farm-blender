"""NREL native editor and explicit evidence CLI, using existing MAPPO results."""
from pathlib import Path
import argparse
import json
import sys
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import wfrl_blender
from wfrl_blender.nrel_defects import editor
from wfrl_blender.nrel_defects.defect_schema import PRESETS,new_defect
from wfrl_blender.nrel_defects.defect_timeline import FrameMapping,summarize_intervals
from wfrl_blender.nrel_defects.defect_outputs import create_run,save_analysis,save_records,write_json,update_run,save_review,capture_images
p=argparse.ArgumentParser()
p.add_argument('mode',choices=['edit','prepare','analyze','scan','images'])
p.add_argument('--config',type=Path)
p.add_argument('--output',type=Path)
p.add_argument('--all-presets',action='store_true')
p.add_argument('--time',type=float)
p.add_argument('--duration',type=float,default=.1)
p.add_argument('--fps',type=float,default=60.)
p.add_argument('--save-blend',type=Path)
args=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else ['edit'])
if args.mode!='edit' and args.output is None:p.error('--output is required for evidence modes')
wfrl_blender.register();wfrl_blender.load_demo_scene()
s=editor.ensure(bpy.context.scene)
if args.config: s.load(args.config)
elif args.all_presets:
    doc=s.store.document
    doc['defects']=[new_defect(s.surface,kind) for kind in PRESETS]
    s.commit_document(doc)
t=float(s.flex.times[0]) if args.time is None else args.time
s.set_time(t)
if args.mode in ('prepare','analyze'):
    run=create_run(s,args.output)
    if args.mode=='analyze':save_analysis(s,run)
elif args.mode=='scan':
    if t+args.duration>s.flex.times[-1]+1e-9:p.error('scan exceeds source timeline')
    mapping=FrameMapping(t,args.duration,args.fps,60.,float(s.flex.times[0]))
    run=create_run(s,args.output,mapping=mapping);saved=float(s.scene['defect_time_s']);rows=[]
    try:
        for i in range(mapping.frame_count):
            sample=mapping.sample(i)
            rows.extend(s.analyze(sample['time_s'],i,run['manifest']['run_id'],s.store.version))
        save_records(run,rows)
        write_json(run['output']/'visible_intervals.json',dict(completed=True,completed_frames=mapping.frame_count,
            next_frame_index=None,frame_mapping=mapping.to_dict(),intervals=summarize_intervals(rows,mapping)))
        run['manifest']['products']['visible_intervals']=dict(state='GENERATED',complete=True)
        run['manifest']['state']='G_SCAN_COMPLETE';update_run(run);save_review(s,run)
    finally:s.set_time(saved)
elif args.mode=='images':capture_images(s,args.output)
if args.save_blend:
    args.save_blend.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.save_blend))
print('NREL_DEFECTS_COMPLETE',args.mode,len(s.store.document['defects']),flush=True)
