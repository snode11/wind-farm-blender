#!/usr/bin/env python3
"""Reproducible NREL three-video handoff / RGB observations / shape fitting CLI."""
from pathlib import Path
import argparse
import json
import sys
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wfrl.nrel_reconstruction.artifact_paths import relocated_path


def fit_bundle(input_dir, observation_dir, output_dir, config_file):
    from wfrl.nrel_reconstruction.dataset import load_bundle, iter_rgb, write_json
    from wfrl.nrel_reconstruction.optimize import reconstruct
    import numpy as np
    from PIL import Image
    root, obsroot = relocated_path(input_dir), relocated_path(observation_dir)
    data, cal, moves = load_bundle(root)
    observations = json.loads((obsroot/'observations.json').read_text())
    annotations = json.loads((obsroot/'annotations.json').read_text())
    expected_keys = {'camera_id','frame_id','sim_time_s','K','T_camera_cv_from_world','T_world_from_blade_root','labels_path','split'}
    contour_keys = {'contour_points_path','contour_valid_path','contour_uncertainty_px'}
    ref = data['reference_state']
    if not ref or ref != annotations['reference_state']:
        raise ValueError('Reference state must be frozen in signed bundle and observations')
    selected_frames = ref['fit_frame_ids'] + ref['heldout_frame_ids']
    expected_pairs = {(camera, frame) for camera in ('C1', 'C2', 'C3') for frame in selected_frames}
    pairs = [(o.get('camera_id'), o.get('frame_id')) for o in observations]
    if (any(type(frame) is not int or camera not in ('C1', 'C2', 'C3') for camera, frame in pairs)
            or len(pairs) != len(set(pairs)) or set(pairs) != expected_pairs):
        raise ValueError('Observations must contain each frozen camera/frame pair exactly once')
    annotation_pairs = [(a.get('camera_id'), a.get('frame_id')) for a in annotations['annotations']]
    if (any(type(frame) is not int or camera not in ('C1', 'C2', 'C3') for camera, frame in annotation_pairs)
            or len(annotation_pairs) != len(set(annotation_pairs)) or set(annotation_pairs) != expected_pairs):
        raise ValueError('RGB annotations must contain each frozen camera/frame pair exactly once')
    evidence = {(a['camera_id'], a['frame_id']): a for a in annotations['annotations']}
    for o in observations:
        if set(o) not in (expected_keys, expected_keys | contour_keys):
            raise ValueError('Unexpected observation fields')
        key=o['camera_id'],o['frame_id']; c=cal[key];m=moves[o['frame_id']]
        p=relocated_path(o['labels_path'])
        o['labels_path'] = str(p)
        if p.parent != obsroot or p.suffix != '.png':
            raise ValueError('Label path must be a local observation PNG')
        with Image.open(p) as image:
            if image.size != (c['width'], c['height']):
                raise ValueError('FBU image dimensions differ from calibrated native video pixels')
            labels = np.array(image, copy=True)
        if labels.ndim != 2 or not np.isin(labels, [0, 1, 2]).all():
            raise ValueError('Labels must be native-size single-channel F/B/U pixels')
        if 'contour_points_path' in o:
            for field, suffix in [('contour_points_path','.npy'),('contour_valid_path','.png')]:
                p=relocated_path(o[field])
                o[field] = str(p)
                if p.parent != obsroot or p.suffix != suffix:
                    raise ValueError('Contour paths must stay within the observation directory')
            if type(o['contour_uncertainty_px']) not in (int,float) or not np.isfinite(o['contour_uncertainty_px']) or o['contour_uncertainty_px'] < 0:
                raise ValueError('Invalid contour uncertainty')
            points = np.load(o['contour_points_path'], allow_pickle=False)
            if (points.ndim != 2 or points.shape[1] != 2 or len(points) == 0
                    or points.dtype.kind not in 'fiu' or not np.isfinite(points).all()):
                raise ValueError('Contour points must be finite Nx2 native pixel coordinates')
            if (np.any(points < 0) or np.any(points[:, 0] > c['width'] - 1)
                    or np.any(points[:, 1] > c['height'] - 1)):
                raise ValueError('Contour points lie outside calibrated native pixel coordinates')
            with Image.open(o['contour_valid_path']) as image:
                if image.size != (c['width'], c['height']):
                    raise ValueError('Contour domain dimensions differ from native video pixels')
                valid = np.array(image, copy=True)
            if valid.ndim != 2 or not np.isin(valid, [0, 1, 255]).all():
                raise ValueError('Contour domain must be a binary single-channel native image')
            rounded = np.rint(points).astype(np.int64)
            if not np.all(valid[rounded[:, 1], rounded[:, 0]] != 0):
                raise ValueError('Saved contour enters a crop or occlusion exclusion domain')
            contour_source = evidence[key].get('trusted_contour', {})
            if (contour_source.get('source') != 'thresholded MP4 RGB component before FBU uncertainty band'
                    or contour_source.get('point_correspondence') != 'none; per-frame silhouette only'
                    or contour_source.get('points') != len(points)
                    or contour_source.get('uncertainty_native_px') != o['contour_uncertainty_px']):
                raise ValueError('Trusted contour provenance or native pixel units mismatch')
        if not np.array_equal(o['K'],c['K']) or not np.array_equal(o['T_camera_cv_from_world'],c['T_camera_cv_from_world']) or not np.array_equal(o['T_world_from_blade_root'],m['T_world_from_blade_root']):
            raise ValueError('Observation calibration differs from permitted bundle')
        expected_split='heldout' if o['frame_id'] in ref['heldout_frame_ids'] else 'fit'
        if o['frame_id'] not in ref['fit_frame_ids']+ref['heldout_frame_ids'] or o['split'] != expected_split or o['sim_time_s'] != m['sim_time_s']:
            raise ValueError('Observation time/split mismatch')
        if evidence[key]['source_video_sha256'] != data['files'][f'{key[0]}.mp4']:
            raise ValueError('Observation video source mismatch')
        if evidence[key].get('truth_segmentation_used') is not False:
            raise ValueError('RGB observation provenance must explicitly exclude truth segmentation')
        rgb_signature = evidence[key].get('rgb_sha256')
        if (not isinstance(rgb_signature, str) or len(rgb_signature) != 64
                or any(character not in '0123456789abcdef' for character in rgb_signature)):
            raise ValueError('RGB observation needs a decoded pixel SHA256 signature')
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
    config=json.loads(relocated_path(config_file).read_text())
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
    p.add_argument('--with-contours',action='store_true')
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
        _,result=create_observations(a.input,a.output,a.fit_frames,a.heldout_frames,a.reference_frame,with_contours=a.with_contours)
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
