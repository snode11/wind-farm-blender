"""Explicit independent RGB unknown regions; no geometry or truth discovery."""
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np

class UnknownContours:
    def __init__(self, root, bundle):
        self.masks = {}
        self.records = []
        declared = bundle.manifest.get('annotations')
        if not declared:
            return
        content = (Path(root) / declared).read_bytes()
        audited = next(a for a in bundle.access_log if a['path'] == declared and a['role'] == 'human_2D_annotations')
        if hashlib.sha256(content).hexdigest() != audited['sha256']:
            raise ValueError('unknown contour annotations changed after validated loading')
        ann = json.loads(content)
        frames = {f['frame']: f for f in bundle.manifest['frames']}
        rgb_hashes = {a['path']: a['sha256'] for a in bundle.access_log if a['role'] == 'RGB'}
        required = {'kind','frame','t','camera','polygon_xy','source_rgb','source_rgb_sha256','reason'}
        for region in ann.get('regions', []):
            if region.get('kind') != 'unknown_contour':
                continue
            if not required <= region.keys():
                raise ValueError('unknown contour region requires identity, polygon and source RGB hash')
            frame = region['frame']
            if frame not in frames:
                continue  # selected source identities retain original frame references
            row = frames[frame]
            cameras = {c['name']: c for c in row['cameras']}
            if region['camera'] not in cameras or not np.isclose(region['t'], row['t'], atol=1e-9, rtol=0):
                raise ValueError('unknown contour frame/time/camera mismatch')
            cam = cameras[region['camera']]
            if region['source_rgb'] != cam['rgb'] or region['source_rgb_sha256'] != rgb_hashes[cam['rgb']]:
                raise ValueError('unknown contour source RGB identity/hash mismatch')
            points = np.asarray(region['polygon_xy'], dtype=float)
            if (points.ndim != 2 or points.shape[1] != 2 or len(points) < 3
                    or not np.isfinite(points).all() or not (points >= 0).all()
                    or (points[:,0] > cam['W']-1).any() or (points[:,1] > cam['H']-1).any()):
                raise ValueError('unknown contour polygon must stay in original pixels')
            key = (frame, region['camera'])
            mask = self.masks.setdefault(key, np.ones((cam['H'],cam['W']), np.uint8))
            cv2.fillPoly(mask, [np.rint(points).astype(np.int32)], 0)
            self.records.append({k: region[k] for k in required})

    def known(self, frame, camera, shape):
        mask = self.masks.get((frame,camera))
        if mask is None:
            return np.ones(shape, bool)
        if mask.shape != shape:
            raise ValueError('unknown contour calibration size mismatch')
        return mask.astype(bool)
