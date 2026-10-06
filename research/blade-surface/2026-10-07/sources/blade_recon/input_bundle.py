"""Strict solver-input loader: explicit RGB, masks, calibration, 2D annotations.

This loader has no neighboring-truth discovery or display-reader dependency.
Every opened file is declared in input_bundle.json and recorded with SHA-256.
Images are returned RGB in original image pixel-center coordinates.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

try:
    from .camera import Camera
    from .model import TurbineConfig
except ImportError:
    from camera import Camera
    from model import TurbineConfig


@dataclass
class InputBundle:
    root: Path
    manifest: dict
    turbine: TurbineConfig
    times: np.ndarray
    frame_ids: list
    cameras: list
    rgbs: list
    masks: list
    tracks: list
    access_log: list


def _keys(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ValueError(label + ' must be an object')
    extra, missing = set(value) - set(allowed), set(required) - set(value)
    if extra or missing:
        raise ValueError('%s keys: extra=%s missing=%s' % (label, sorted(extra), sorted(missing)))


def load_bundle(root, manifest_path='input_bundle.json'):
    root = Path(root).resolve()
    access = []

    def read_bytes(relative, role):
        relative = str(relative)
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or Path(relative).is_absolute():
            raise ValueError('input path must stay inside declared bundle: ' + relative)
        # Reject truth-like paths even when mistakenly added to an allowlist.
        parts = [p.lower() for p in path.relative_to(root).parts]
        if any(p == 'evaluation' or p.startswith('truth') or 'object_id' in p or
               p == 'geometry.npz' or p.startswith('source-surface') for p in parts):
            raise ValueError('forbidden solver input: ' + relative)
        content = path.read_bytes()
        access.append({'path': relative, 'role': role,
                       'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)})
        return content

    meta = json.loads(read_bytes(manifest_path, 'input_manifest'))
    _keys(meta, {'schema', 'extrinsic_from_frame', 'pixel_coordinates', 'color_order',
                 'turbine', 'frames', 'annotations', 'split', 'provenance'},
          {'schema', 'extrinsic_from_frame', 'pixel_coordinates', 'color_order', 'turbine', 'frames'}, 'manifest')
    if meta['schema'] != 'blade-surface-input.v1' or meta['extrinsic_from_frame'] != 'model':
        raise ValueError('requires blade-surface-input.v1 with explicit MODEL→CV extrinsics')
    if meta['pixel_coordinates'] != 'original_image_pixel_centers' or meta['color_order'] != 'RGB':
        raise ValueError('requires original image pixel centers and RGB color order')
    template_keys = set(TurbineConfig().to_dict())
    _keys(meta['turbine'], template_keys, set(), 'healthy turbine template')
    cfg = TurbineConfig.from_dict(meta['turbine'])
    if cfg.n_sections < 3 or cfg.n_ring < 8 or cfg.n_ring % 2 or cfg.tip_radius_m <= cfg.hub_radius_m:
        raise ValueError('invalid healthy-template dimensions')
    frames = meta['frames']
    if not isinstance(frames, list) or not frames:
        raise ValueError('frames must be a nonempty explicit list')
    times, frame_ids, cameras, rgbs, masks = [], [], [], [], []
    previous = -np.inf
    for record in frames:
        _keys(record, {'frame', 't', 'cameras'}, {'frame', 't', 'cameras'}, 'frame')
        t = float(record['t'])
        if not np.isfinite(t) or t <= previous:
            raise ValueError('timestamps must be finite and strictly increasing')
        previous = t
        frame_id = int(record['frame'])
        if frame_id in frame_ids:
            raise ValueError('duplicate frame identity')
        frame_ids.append(frame_id); times.append(t)
        cc, ii, mm, names = [], [], [], set()
        for c in record['cameras']:
            _keys(c, {'name', 'W', 'H', 'K', 'T_cv_from_model', 'rgb', 'mask'},
                  {'name', 'W', 'H', 'K', 'T_cv_from_model', 'rgb', 'mask'}, 'camera')
            if c['name'] in names:
                raise ValueError('duplicate camera name within frame')
            names.add(c['name'])
            K, T = np.asarray(c['K'], float), np.asarray(c['T_cv_from_model'], float)
            if K.shape != (3, 3) or T.shape != (4, 4) or not np.isfinite(K).all() or not np.isfinite(T).all():
                raise ValueError('invalid camera matrix dimensions or values')
            if K[0, 0] <= 0 or K[1, 1] <= 0 or not np.allclose(K[2], [0, 0, 1]):
                raise ValueError('invalid intrinsic matrix')
            if not np.allclose(T[3], [0, 0, 0, 1]) or not np.allclose(T[:3, :3] @ T[:3, :3].T, np.eye(3), atol=1e-6) or np.linalg.det(T[:3, :3]) < 0:
                raise ValueError('extrinsics must be a proper rigid MODEL→CV transform')
            cam = Camera(c['name'], c['W'], c['H'], K, T)
            if cam.W < 2 or cam.H < 2:
                raise ValueError('invalid image dimensions')
            raw_rgb = np.frombuffer(read_bytes(c['rgb'], 'RGB'), np.uint8)
            raw_mask = np.frombuffer(read_bytes(c['mask'], 'RGB_derived_mask'), np.uint8)
            bgr = cv2.imdecode(raw_rgb, cv2.IMREAD_COLOR)
            mask = cv2.imdecode(raw_mask, cv2.IMREAD_GRAYSCALE)
            if bgr is None or mask is None or bgr.shape[:2] != (cam.H, cam.W) or mask.shape != (cam.H, cam.W):
                raise ValueError('unreadable image/mask or calibration size mismatch')
            cc.append(cam); ii.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)); mm.append((mask > 127).astype(np.uint8) * 255)
        if not cc:
            raise ValueError('frame contains no input camera')
        cameras.append(cc); rgbs.append(ii); masks.append(mm)
    tracks = []
    if meta.get('annotations'):
        ann = json.loads(read_bytes(meta['annotations'], 'human_2D_annotations'))
        _keys(ann, {'schema', 'author', 'method', 'source', 'pixel_coordinates', 'tracks', 'regions', 'provenance'},
              {'schema', 'author', 'method', 'source', 'pixel_coordinates', 'tracks'}, 'annotation')
        if ann['schema'] != 'blade-surface-annotations.v1' or ann['pixel_coordinates'] != 'original_image_pixel_centers':
            raise ValueError('annotation schema/pixel convention mismatch')
        tracks = ann['tracks']
        for track in tracks:
            _keys(track, {'id', 'blade_id', 'group', 'observations'}, {'id', 'blade_id', 'observations'}, 'track')
            if int(track['blade_id']) not in (0, 1, 2):
                raise ValueError('annotation blade identity must be explicit model candidate 0..2')
            for o in track['observations']:
                _keys(o, {'frame_index', 'camera_index', 'uv', 'image_visible', 'weight', 'source_rgb', 'comment'},
                      {'frame_index', 'camera_index', 'uv', 'image_visible'}, 'point observation')
                f, cidx = int(o['frame_index']), int(o['camera_index'])
                if not 0 <= f < len(frames) or not 0 <= cidx < len(cameras[f]):
                    raise ValueError('annotation reference outside input split')
                uv = np.asarray(o['uv'], float)
                cam = cameras[f][cidx]
                if uv.shape != (2,) or not np.isfinite(uv).all() or not (0 <= uv[0] <= cam.W - 1 and 0 <= uv[1] <= cam.H - 1):
                    raise ValueError('annotation point outside original image')
                if not isinstance(o['image_visible'], bool) or float(o.get('weight', 1)) <= 0:
                    raise ValueError('image support and positive weight must be explicit')
    return InputBundle(root, meta, cfg, np.asarray(times), frame_ids, cameras, rgbs, masks, tracks, access)
