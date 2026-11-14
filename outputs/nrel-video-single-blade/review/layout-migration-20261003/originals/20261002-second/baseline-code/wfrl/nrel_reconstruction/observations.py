"""Auditable RGB-only, agent-reviewed segmentation for the first blade passage.

This deliberately small semi-automatic baseline uses visible paint contrast and
documented exclusion boxes. It uses neither renderer IDs nor depth/truth masks.
It is specific to the reviewed material-rendered passage, not a general segmenter.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .dataset import load_bundle, iter_rgb, write_json


def segment_rgb(rgb, camera_id):
    rgb = np.asarray(rgb, dtype=np.uint8)
    h, w = rgb.shape[:2]
    channels = rgb.astype(float)
    low, high = channels.min(axis=2), channels.max(axis=2)
    white = (low > 105) & (high-low < 65)
    red = (channels[:, :, 0] > 120) & (channels[:, :, 0] > 1.5*channels[:, :, 1]) & (channels[:, :, 0] > 1.3*channels[:, :, 2])
    excluded = np.zeros((h,w), dtype=bool)
    boxes = []
    if camera_id == 'C1':
        # Chosen from actual decoded RGB: left nacelle / upper antenna and
        # structural strut / lower mounting structure. No negative constraint.
        boxes = [[0,0,.25,1], [0,0,1,.23], [0,.83,1,1]]
    for x0,y0,x1,y1 in boxes:
        excluded[int(y0*h):int(y1*h), int(x0*w):int(x1*w)] = True
    candidate = (white | red) & ~excluded
    candidate = ndimage.binary_closing(candidate, iterations=2)
    components, count = ndimage.label(candidate)
    sizes = np.bincount(components.ravel()); sizes[0] = 0
    if count == 0 or sizes.max() < 100:
        raise ValueError(f'No usable foreground in reviewed {camera_id} frame')
    largest = sizes.argmax()
    foreground = ndimage.binary_fill_holes(components == largest)
    labels = np.zeros((h,w), dtype=np.uint8)
    labels[excluded | (candidate & ~foreground)] = 2
    inner = ndimage.binary_erosion(foreground, iterations=2)
    outer = ndimage.binary_dilation(foreground, iterations=3)
    labels[outer & ~inner] = 2
    labels[inner & ~excluded] = 1
    # Outside image and the border band are unknown, not silhouette edges.
    labels[:3] = 2; labels[-3:] = 2; labels[:,:3] = 2; labels[:,-3:] = 2
    yy,xx = np.nonzero(labels == 1)
    center = [float(xx.mean()),float(yy.mean())]
    return labels, {'method': 'RGB contrast + largest connected component in reviewed B1 passage',
                    'white_threshold': {'min_channel_gt':105, 'max_minus_min_lt':65},
                    'red_threshold': {'r_gt':120, 'r_over_g_gt':1.5, 'r_over_b_gt':1.3},
                    'excluded_boxes_normalized':boxes,
                    'edge_uncertainty_px':3,'foreground_centroid_px':center,
                    'foreground_pixels':int(len(xx)), 'background_pixels':int((labels==0).sum()),
                    'uncertain_pixels':int((labels==2).sum()),
                    'truth_segmentation_used':False, 'human_user_prompts':0,
                    'reviewer':'Codex visual review of actual MP4 RGB; semi-automatic validation'}


def create_observations(bundle_dir, out_dir, fit_frame_ids, heldout_frame_ids, reference_id):
    root, out = Path(bundle_dir), Path(out_dir)
    data, calibration, motion = load_bundle(root)
    out.mkdir(parents=True, exist_ok=False)
    chosen = set(fit_frame_ids) | set(heldout_frame_ids)
    entries, annotations, thumbnails = [], [], []
    for camera in ('C1','C2','C3'):
        c0=calibration[(camera,0)]
        for i,rgb in enumerate(iter_rgb(root/f'{camera}.mp4', c0['width'], c0['height'])):
            if i not in chosen:
                continue
            labels, annotation = segment_rgb(rgb,camera)
            filename = f'{camera}_{i:06d}_FBU.png'
            Image.fromarray(labels).save(out/filename)
            key = f'{camera}_{i:06d}'
            image = Image.fromarray(rgb); image.thumbnail((480,270)); image.save(out/f'{key}_rgb.jpg',quality=94)
            palette=np.asarray([[20,70,130],[0,245,80],[255,180,0]],dtype=np.uint8)
            overlay=(rgb.astype(float)*.65+palette[labels]*.35).astype(np.uint8)
            image=Image.fromarray(overlay);image.thumbnail((480,270)); image.save(out/f'{key}_overlay.jpg',quality=94)
            thumbnails.append((camera,i,image.copy()))
            cal=calibration[(camera,i)]
            entries.append({'camera_id':camera,'frame_id':i,'sim_time_s':motion[i]['sim_time_s'],
                            'K':cal['K'],'T_camera_cv_from_world':cal['T_camera_cv_from_world'],
                            'T_world_from_blade_root':motion[i]['T_world_from_blade_root'],
                            'labels_path':str((out/filename).resolve()),
                            'split':'heldout' if i in heldout_frame_ids else 'fit'})
            annotations.append({'camera_id':camera,'frame_id':i,'sim_time_s':motion[i]['sim_time_s'],
                                'rgb_sha256':hashlib.sha256(rgb.tobytes()).hexdigest(),
                                'source_video_sha256':data['files'][f'{camera}.mp4'], **annotation})
    if len(entries) != len(chosen)*3:
        raise ValueError('Selected frames were not all decoded')
    reference={'t_ref_sim_time_s':motion[reference_id]['sim_time_s'],'frame_id':reference_id,
               'selection_basis':'Actual MP4 RGB: B1 body visible in C1/C2 and painted tip contour clear in C3; identity checked with declared ideal B1 rigid pose. No truth geometry used.',
               'fit_frame_ids':list(fit_frame_ids),'heldout_frame_ids':list(heldout_frame_ids)}
    write_json(out/'observations.json',entries)
    write_json(out/'annotations.json',{'annotations':annotations,'reference_state':reference,
                                      'correspondence_tracks':{'reliable_matches':0,'reason':'first silhouette baseline; no point tracker deployed'},
                                      'identity_association':'One contiguous B1 passage selected by RGB and declared root pose; other passages excluded'})
    cols=3; rows=(len(thumbnails)+cols-1)//cols
    canvas=Image.new('RGB',(480*cols,294*rows),'white');draw=ImageDraw.Draw(canvas)
    for j,(camera,i,im) in enumerate(thumbnails):
        x,y=(j%cols)*480,(j//cols)*294;canvas.paste(im,(x,y));draw.text((x+5,y+272),f'{camera} frame {i} / '+('heldout' if i in heldout_frame_ids else 'fit'),fill='black')
    canvas.save(out/'FBU_review.jpg',quality=94)
    return entries, reference
