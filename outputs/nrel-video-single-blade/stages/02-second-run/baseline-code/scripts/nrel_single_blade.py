#!/usr/bin/env python3
"""Reproducible NREL three-video handoff / RGB observations / shape fitting CLI."""
from pathlib import Path
import argparse
import json
import sys
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fit_bundle(input_dir, observation_dir, output_dir, config_file):
    from wfrl.nrel_reconstruction.dataset import load_bundle, iter_rgb, write_json
    from wfrl.nrel_reconstruction.optimize import reconstruct
    import numpy as np
    root, obsroot = Path(input_dir).resolve(), Path(observation_dir).resolve()
    data, cal, moves = load_bundle(root)
    observations = json.loads((obsroot/'observations.json').read_text())
    annotations = json.loads((obsroot/'annotations.json').read_text())
    evidence = {(a['camera_id'],a['frame_id']):a for a in annotations['annotations']}
    expected_keys = {'camera_id','frame_id','sim_time_s','K','T_camera_cv_from_world','T_world_from_blade_root','labels_path','split'}
    ref = data['reference_state']
    if not ref or ref != annotations['reference_state']:
        raise ValueError('Reference state must be frozen in signed bundle and observations')
    for o in observations:
        if set(o) != expected_keys:
            raise ValueError('Unexpected observation fields')
        key=o['camera_id'],o['frame_id']; c=cal[key];m=moves[o['frame_id']]
        p=Path(o['labels_path']).resolve()
        if p.parent != obsroot or p.suffix != '.png':
            raise ValueError('Label path must be a local observation PNG')
        if not np.array_equal(o['K'],c['K']) or not np.array_equal(o['T_camera_cv_from_world'],c['T_camera_cv_from_world']) or not np.array_equal(o['T_world_from_blade_root'],m['T_world_from_blade_root']):
            raise ValueError('Observation calibration differs from permitted bundle')
        expected_split='heldout' if o['frame_id'] in ref['heldout_frame_ids'] else 'fit'
        if o['frame_id'] not in ref['fit_frame_ids']+ref['heldout_frame_ids'] or o['split'] != expected_split or o['sim_time_s'] != m['sim_time_s']:
            raise ValueError('Observation time/split mismatch')
        if evidence[key]['source_video_sha256'] != data['files'][f'{key[0]}.mp4']:
            raise ValueError('Observation video source mismatch')
    # The formal fitting process itself verifies actual decoded video pixels;
    # labels are only the selected RGB segmentation cache, not substitute PNGs.
    decode={'videos':{},'selected_rgb_hashes_verified':True,'streaming':True}
    for camera in ('C1','C2','C3'):
        ids={o['frame_id'] for o in observations if o['camera_id']==camera}
        count=0;c=cal[(camera,0)]
        for i,rgb in enumerate(iter_rgb(root/f'{camera}.mp4',c['width'],c['height'])):
            count+=1
            if i in ids and hashlib.sha256(rgb.tobytes()).hexdigest()!=evidence[(camera,i)]['rgb_sha256']:
                raise ValueError('Selected RGB differs from actual decoded MP4')
        if count!=data['sampling']['frame_count_per_camera']:
            raise ValueError('Formal fitting decode is incomplete')
        decode['videos'][camera]={'frames_decoded':count,'video_sha256':data['files'][f'{camera}.mp4']}
    config=json.loads(Path(config_file).read_text())
    result=reconstruct(observations,output_dir,ref,config)
    write_json(Path(output_dir)/'fit_decode_verification.json',decode)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('pack');p.add_argument('--capture',required=True);p.add_argument('--output',required=True)
    p.add_argument('--start',type=float,required=True);p.add_argument('--duration',type=float,required=True);p.add_argument('--fps',type=int,default=20)
    p=sub.add_parser('inspect');p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--frames',type=int,nargs='+')
    p=sub.add_parser('observe');p.add_argument('--input',required=True);p.add_argument('--output',required=True)
    p.add_argument('--fit-frames',type=int,nargs='+',required=True);p.add_argument('--heldout-frames',type=int,nargs='+',required=True);p.add_argument('--reference-frame',type=int,required=True)
    p=sub.add_parser('fit');p.add_argument('--input',required=True);p.add_argument('--observations',required=True);p.add_argument('--output',required=True);p.add_argument('--config',required=True)
    p=sub.add_parser('score');p.add_argument('--reconstruction',required=True);p.add_argument('--truth',required=True);p.add_argument('--truth-metadata',required=True);p.add_argument('--config',required=True);p.add_argument('--output',required=True)
    a=parser.parse_args()
    if a.command=='pack':
        from wfrl.nrel_reconstruction.pack import pack_capture
        result=pack_capture(a.capture,a.output,a.start,a.duration,a.fps)
    elif a.command=='inspect':
        from wfrl.nrel_reconstruction.dataset import decode_inspection
        result=decode_inspection(a.input,a.output,a.frames)
    elif a.command=='observe':
        from wfrl.nrel_reconstruction.observations import create_observations
        from wfrl.nrel_reconstruction.dataset import write_json
        _,result=create_observations(a.input,a.output,a.fit_frames,a.heldout_frames,a.reference_frame)
        p=Path(a.input)/'dataset.json';data=json.loads(p.read_text());data['reference_state']=result;write_json(p,data)
    elif a.command=='fit':
        result=fit_bundle(a.input,a.observations,a.output,a.config)
    else:
        from wfrl.nrel_reconstruction.evaluate import evaluate_run
        result=evaluate_run(a.reconstruction,a.truth,a.truth_metadata,scoring_config=a.config,output_dir=a.output)
        result={'status':result['status'],'image_contribution':result['comparison']['image_contribution']}
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
