"""One explicit frame/clock/camera contract shared by geometry and texture.

Bundle mode delegates declared-file validation to blade_recon.input_bundle.
Legacy mode is intentionally static, with aligned numeric frame identities or
synchronized videos. It never discovers truth or silently substitutes masks.
"""
from dataclasses import dataclass
from copy import deepcopy
import json
import math
import hashlib
import shutil
import tempfile
import types
from pathlib import Path
import sys

import cv2
import numpy as np

from contour_support import UnknownContours
from camera import Camera
from model import TurbineConfig, healthy_template_sha256, validate_healthy_template


@dataclass
class FrameRecord:
    frame: int
    t: float
    cameras: list
    rgbs: list
    masks: list
    source_indices: list
    known_contours: list | None = None

    @property
    def cams(self):
        return self.cameras

    @property
    def imgs(self):
        return self.rgbs


def _bundle_loader():
    # Use a private package namespace: importing legacy camera/model must not
    # replace v0.2's top-level modules in sys.modules.
    legacy = Path(__file__).resolve().parents[1] / 'blade_recon'
    name = '_blade_recon_v02_strict_legacy'
    if name not in sys.modules:
        if not (legacy / 'input_bundle.py').exists():
            raise ImportError('missing legacy strict input adapter: ' + str(legacy))
        module = types.ModuleType(name)
        module.__path__ = [str(legacy)]
        sys.modules[name] = module
    return __import__(name + '.input_bundle', fromlist=['load_bundle']).load_bundle


def _bundle_indices(raw, max_frames, selected_frames):
    """Select declared identities, retaining indices in the original bundle."""
    if isinstance(max_frames, bool) or not isinstance(max_frames, int) or max_frames < 0:
        raise ValueError('max_frames must be a nonnegative integer')
    if selected_frames is None:
        indices = list(range(len(raw['frames'])))
    else:
        selected_frames = list(selected_frames)
        if (not selected_frames or any(isinstance(f, bool) or not isinstance(f, int)
                for f in selected_frames) or any(a >= b for a, b in zip(selected_frames, selected_frames[1:]))):
            raise ValueError('selected_frames must be nonempty strictly increasing integer source identities')
        by_id = {record['frame']: k for k, record in enumerate(raw['frames'])}
        absent = [frame for frame in selected_frames if frame not in by_id]
        if absent:
            raise ValueError('selected source frame absent from input: %s' % absent[0])
        indices = [by_id[frame] for frame in selected_frames]
    return indices[:max_frames] if max_frames else indices


def _load_bundle(root, raw, indices, force_manifest=False):
    loader = _bundle_loader()
    if indices == list(range(len(raw['frames']))) and not force_manifest:
        return loader(root)
    # Explicit source identities bound eager declared-file loading. A temporary
    # allowlist uses hardlinks/copies of declared files only; original bundles
    # and neighboring truth are untouched. Validation remains the legacy loader.
    selected = dict(raw)
    selected['frames'] = [deepcopy(raw['frames'][k]) for k in indices]
    index_map = {original: selected for selected, original in enumerate(indices)}
    original_access = []
    with tempfile.TemporaryDirectory(prefix='blade-v02-input-selection-') as tmp:
        target = Path(tmp).resolve()
        materialized = set()
        def declared(relative, role, **identity):
            relative = str(relative)
            path = (root / relative).resolve()
            if Path(relative).is_absolute() or not path.is_relative_to(root):
                raise ValueError('input path must stay inside declared bundle: ' + relative)
            parts = [p.lower() for p in path.relative_to(root).parts]
            if any(p == 'evaluation' or p.startswith('truth') or 'object_id' in p or
                   p == 'geometry.npz' or p.startswith('source-surface') for p in parts):
                raise ValueError('forbidden solver input: ' + relative)
            content = path.read_bytes()
            original_access.append({'path': relative, 'role': role,
                'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content), **identity})
            canonical = path.relative_to(root).as_posix()
            dest = target / canonical
            if not dest.is_relative_to(target):
                raise ValueError('declared path escapes temporary input selection: ' + relative)
            if canonical not in materialized:
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dest.hardlink_to(path)
                except OSError:
                    shutil.copyfile(path, dest)
                materialized.add(canonical)
            return content, canonical
        for record in selected['frames']:
            for cam in record['cameras']:
                _, cam['rgb'] = declared(cam['rgb'], 'RGB', frame=record['frame'], camera=cam['name'])
                _, cam['mask'] = declared(cam['mask'], 'RGB_derived_mask', frame=record['frame'], camera=cam['name'])
        if raw.get('annotations'):
            content, selected['annotations'] = declared(raw['annotations'], 'human_2D_annotations')
            ann = json.loads(content)
            # Annotations use bundle-list indices, not source frame identities.
            # Remap only selected observations into the temporary allowlist.
            ann['tracks'] = [dict(track, observations=[dict(o, frame_index=index_map[o['frame_index']])
                                  for o in track['observations'] if o['frame_index'] in index_map])
                             for track in ann['tracks']]
            dest = target / selected['annotations']
            dest.unlink()  # never write through a hardlink into the original
            dest.write_text(json.dumps(ann))
        (target / 'input_bundle.json').write_text(json.dumps(selected))
        bundle = loader(target)
        content = (root / 'input_bundle.json').read_bytes()
        bundle.access_log = [{'path': 'input_bundle.json', 'role': 'input_manifest',
                'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}] + original_access
        bundle.root = root
        return bundle


def _healthy_design(root, meta, cfg):
    """Explicit opt-in allowlist for a fixed healthy design, never replay truth."""
    declaration = meta.get('healthy_template')
    if declaration is None:
        if cfg.healthy_template is not None:
            validate_healthy_template(cfg.healthy_template, cfg)
        return []
    if cfg.healthy_template is not None:
        raise ValueError('declare either an embedded or a file healthy template, not both')
    if not isinstance(declaration, dict) or set(declaration) != {'path', 'sha256'}:
        raise ValueError('healthy_template declaration requires path and sha256')
    relative, expected = declaration['path'], declaration['sha256']
    if not isinstance(relative, str) or not isinstance(expected, str) or len(expected) != 64:
        raise ValueError('invalid explicit healthy template declaration')
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError('healthy template path must stay inside declared bundle')
    parts = [p.lower() for p in path.relative_to(root).parts]
    if any(p == 'evaluation' or p.startswith('truth') or 'object_id' in p or
           p == 'geometry.npz' or p.startswith('source-surface') for p in parts):
        raise ValueError('forbidden solver input: ' + relative)
    content = path.read_bytes()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ValueError('healthy template file integrity mismatch')
    payload = json.loads(content)
    cfg.healthy_template = payload
    cfg.healthy_template_sha256 = healthy_template_sha256(payload)
    validate_healthy_template(payload, cfg)
    return [{'path': relative, 'role': 'explicit_fixed_healthy_design', 'sha256': actual,
             'payload_sha256': cfg.healthy_template_sha256, 'bytes': len(content)}]

def _proper_camera(d):
    if not isinstance(d, dict):
        raise ValueError('camera record must be an object')
    name = d.get('name')
    if not isinstance(name, str) or not name or name in ('.', '..') or Path(name).name != name:
        raise ValueError('camera name must be a single nonempty identity')
    if 'T_cv_from_model' in d:
        raise ValueError('MODEL extrinsics require explicit input_bundle.json per-frame records; '
                         'cannot read them as a static world camera')
    K, T = np.asarray(d.get('K'), float), np.asarray(d.get('T_cv_from_world'), float)
    if K.shape != (3, 3) or T.shape != (4, 4) or not np.isfinite(K).all() or not np.isfinite(T).all():
        raise ValueError('invalid camera matrix for ' + str(d.get('name')))
    if K[0, 0] <= 0 or K[1, 1] <= 0 or not np.allclose(K[2], [0, 0, 1]):
        raise ValueError('invalid intrinsics for ' + str(d.get('name')))
    if not np.allclose(T[3], [0, 0, 0, 1]) or not np.allclose(T[:3, :3] @ T[:3, :3].T, np.eye(3), atol=1e-6) or np.linalg.det(T[:3, :3]) < 0:
        raise ValueError('extrinsics must be a proper rigid transform')
    cam = Camera.from_dict(d)
    if cam.W < 2 or cam.H < 2 or isinstance(d['W'], bool) or isinstance(d['H'], bool) or cam.W != d['W'] or cam.H != d['H']:
        raise ValueError('image dimensions must be integral and >=2')
    return cam


class RGBSource:
    """Strict legacy video/frame reader. read() returns RGB and retains identity."""
    def __init__(self, data_dir, name):
        root = Path(data_dir)
        self.name, self.cap, self.files, self.k = name, None, None, 0
        self.frame_ids, self.last_frame, self.last_t = None, None, None
        video = root / (name + '.mp4')
        if video.exists():
            self.cap = cv2.VideoCapture(str(video))
            if not self.cap.isOpened():
                raise ValueError('unreadable video: ' + str(video))
            self.fps = float(self.cap.get(cv2.CAP_PROP_FPS))
            self.count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if not np.isfinite(self.fps) or self.fps <= 0 or self.count <= 0:
                raise ValueError('invalid video frame count/fps: ' + str(video))
            self.paths = [str(video)]
        else:
            self.files = sorted(list((root / name).glob('*.png')) + list((root / name).glob('*.jpg')))
            if not self.files:
                raise FileNotFoundError('missing %s.mp4 or %s frame directory' % (name, name))
            if any(not p.stem.isdigit() for p in self.files):
                raise ValueError('frame filenames must carry numeric source identities: ' + name)
            self.files.sort(key=lambda p: int(p.stem))
            self.frame_ids = [int(p.stem) for p in self.files]
            if len(set(self.frame_ids)) != len(self.frame_ids):
                raise ValueError('duplicate frame identity: ' + name)
            self.count, self.fps = len(self.files), None
            self.width = self.height = None
            self.paths = [str(p) for p in self.files]

    def read(self):
        if self.k >= self.count:
            return None
        if self.cap is not None:
            ok, bgr = self.cap.read()
            if not ok or bgr is None:
                raise ValueError('bad or truncated video frame %d for %s' % (self.k, self.name))
            frame = self.k
            actual_t = float(self.cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
            expected_t = frame / self.fps
            if not math.isfinite(actual_t) or abs(actual_t - expected_t) > max(1e-4, 0.002 / self.fps):
                raise ValueError('video presentation timestamp differs from declared uniform clock at frame %d for %s; '
                                 'use an explicit input bundle for nonuniform/offset clocks' % (frame, self.name))
        else:
            bgr = cv2.imread(str(self.files[self.k]), cv2.IMREAD_COLOR)
            if bgr is None:
                raise ValueError('unreadable image: ' + str(self.files[self.k]))
            frame = self.frame_ids[self.k]
        self.last_frame = frame
        self.last_t = frame / self.fps if self.fps else None
        self.k += 1
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def close(self):
        if self.cap is not None:
            self.cap.release()


class InputSequence:
    def __init__(self, data_dir, masks_dir=None, segmenter=None, max_frames=0, selected_frames=None):
        self.root = Path(data_dir).resolve()
        self.masks_dir = Path(masks_dir).resolve() if masks_dir is not None else None
        self.segmenter, self.k, self.bundle = segmenter, 0, None
        self.sources, self.access_log = [], []
        self.unselected_access_keys = set()
        if (self.root / 'input_bundle.json').exists():
            if masks_dir is not None:
                raise ValueError('bundle declares masks explicitly; --masks cannot override their identity')
            raw = json.loads((self.root / 'input_bundle.json').read_text())
            if not isinstance(raw, dict) or not isinstance(raw.get('frames'), list) or not raw['frames']:
                raise ValueError('input manifest requires a nonempty explicit frame list')
            for record in raw['frames']:
                if not isinstance(record, dict) or not isinstance(record.get('cameras'), list) or not record['cameras']:
                    raise ValueError('each frame requires an explicit nonempty camera list')
                if any(not isinstance(camera, dict) for camera in record['cameras']):
                    raise ValueError('frame camera record must be an object')
            ids = [f.get('frame') for f in raw.get('frames', [])]
            if any(isinstance(f, bool) or not isinstance(f, int) for f in ids) or any(a >= b for a, b in zip(ids, ids[1:])):
                raise ValueError('bundle source frame ids must be integer and strictly increasing')
            camera_names = None
            for f in raw.get('frames', []):
                names = [c.get('name') for c in f.get('cameras', [])]
                if camera_names is None:
                    camera_names = names
                if names != camera_names:
                    raise ValueError('cross-frame camera identities/order differ; use explicit synchronized camera slots')
                for c in f.get('cameras', []):
                    for key in ('W', 'H'):
                        if isinstance(c.get(key), bool) or not isinstance(c.get(key), int):
                            raise ValueError('bundle image dimensions must be integral')
            self.bundle_source_indices = _bundle_indices(raw, max_frames, selected_frames)
            selected_indices = set(self.bundle_source_indices)
            self.unselected_access_keys = {(str(c[key]), role) for k, record in enumerate(raw['frames'])
                if k not in selected_indices for c in record['cameras']
                for key, role in (('rgb', 'RGB'), ('mask', 'RGB_derived_mask')) if key in c}
            # v0.2's fixed healthy-design extension is validated locally; the
            # remaining manifest retains the unchanged strict legacy contract.
            legacy_raw = dict(raw)
            legacy_raw.pop('healthy_template', None)
            legacy_raw['turbine'] = dict(raw['turbine'])
            for key in ('healthy_template', 'healthy_template_sha256'):
                legacy_raw['turbine'].pop(key, None)
            self.bundle = _load_bundle(self.root, legacy_raw, self.bundle_source_indices,
                                       force_manifest=legacy_raw != raw)
            # A declared file may be shared by selected and unselected frames.
            self.unselected_access_keys.difference_update((a['path'], a['role']) for a in self.bundle.access_log)
            self.cfg = TurbineConfig.from_dict(raw['turbine'])
            health_access = _healthy_design(self.root, raw, self.cfg)
            self.fps = float(1 / np.median(np.diff(self.bundle.times))) if len(self.bundle.times) > 1 else 10.0
            self.metadata = {'mode': 'blade-surface-input.v1', 'extrinsic_from_frame': 'model',
                             'mask_source': 'explicit declared masks'}
            self.access_log = self.bundle.access_log + health_access
            if self.cfg.healthy_template is not None:
                self.metadata['healthy_template_sha256'] = self.cfg.healthy_template_sha256
                self.metadata['healthy_template_source'] = 'explicit fixed design; no dynamic source geometry'
            self.unknown_contours = UnknownContours(self.root, self.bundle)
            self.count = len(self.bundle.times)
        else:
            path = self.root / 'cameras.json'
            meta = json.loads(path.read_text())
            if not isinstance(meta, dict) or not isinstance(meta.get('cameras'), list):
                raise ValueError('cameras.json requires an explicit camera list')
            if meta.get('turbine') is not None and not isinstance(meta['turbine'], dict):
                raise ValueError('healthy turbine template must be an object')
            self.cfg = TurbineConfig.from_dict(meta.get('turbine'))
            health_access = _healthy_design(self.root, meta, self.cfg)
            self.fps = float(meta.get('fps', 10.0))
            if not math.isfinite(self.fps) or self.fps <= 0:
                raise ValueError('declared fps must be finite and positive')
            self.cameras = [_proper_camera(d) for d in meta['cameras']]
            if not self.cameras or len(set(c.name for c in self.cameras)) != len(self.cameras):
                raise ValueError('static input requires unique camera names')
            self.sources = [RGBSource(self.root, c.name) for c in self.cameras]
            self.count = self.sources[0].count
            ids = self.sources[0].frame_ids
            for cam, source in zip(self.cameras, self.sources):
                if source.count != self.count or source.frame_ids != ids:
                    raise ValueError('cross-camera frame identities/count differ: ' + source.name)
                if source.fps is not None:
                    if not np.isclose(source.fps, self.fps, rtol=1e-4, atol=1e-6):
                        raise ValueError('cross-camera/declared video timestamps differ: ' + source.name)
                    if (source.width, source.height) != (cam.W, cam.H):
                        raise ValueError('video calibration size mismatch: ' + source.name)
                self.access_log += [{'path': p, 'role': 'RGB'} for p in source.paths]
            self.access_log.insert(0, {'path': str(path), 'role': 'static_camera_manifest'})
            self.access_log += health_access
            self.metadata = {'mode': 'legacy-static', 'extrinsic_from_frame': 'world=model',
                             'mask_source': 'explicit masks' if masks_dir is not None else 'automatic segmentation'}

    def read(self):
        if self.k >= self.count:
            return None
        k = self.k
        if self.bundle is not None:
            record = FrameRecord(self.bundle.frame_ids[k], float(self.bundle.times[k]),
                                 [Camera(c.name, c.W, c.H, c.K, np.vstack([np.column_stack([c.R, c.t]), [0, 0, 0, 1]]))
                                  for c in self.bundle.cameras[k]],
                                 self.bundle.rgbs[k], self.bundle.masks[k],
                                 [self.bundle_source_indices[k]] * len(self.bundle.cameras[k]))
            known = [self.unknown_contours.known(record.frame, c.name, m.shape)
                     for c, m in zip(record.cameras, record.masks)]
            record.known_contours = known
            record.masks = [m if support.all() else np.where(support,m,0).astype(np.uint8)
                            for m,support in zip(record.masks,known)]
        else:
            imgs = [s.read() for s in self.sources]
            frame = self.sources[0].last_frame
            if any(s.last_frame != frame for s in self.sources):
                raise ValueError('cross-camera frame identities differ at index %d' % k)
            masks = []
            for cam, img in zip(self.cameras, imgs):
                if img is None or img.shape != (cam.H, cam.W, 3):
                    raise ValueError('RGB/calibration size mismatch at frame %s camera %s' % (frame, cam.name))
                gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
                if self.masks_dir is not None:
                    path = self.masks_dir / cam.name / ('%06d.png' % frame)
                    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) if path.exists() else None
                    if mask is None:
                        raise ValueError('missing or unreadable explicit mask: ' + str(path))
                    if mask.shape != gray.shape:
                        raise ValueError('explicit mask/calibration size mismatch: ' + str(path))
                    mask = (mask > 127).astype(np.uint8) * 255
                    self.access_log.append({'path': str(path), 'role': 'explicit_mask'})
                elif self.segmenter is not None:
                    mask = self.segmenter(gray)
                else:
                    raise ValueError('legacy input requires explicit masks or a declared segmentation callback')
                masks.append(mask)
            record = FrameRecord(frame, frame / self.fps, self.cameras, imgs, masks, [k] * len(imgs))
        self.k += 1
        return record

    def read_frame(self, frame, t):
        """Forward-only identity lookup, allowing explicitly skipped recon rows."""
        while True:
            record = self.read()
            if record is None:
                raise ValueError('reconstruction references unavailable input frame %s' % frame)
            if record.frame > frame:
                raise ValueError('reconstruction source frame absent from input: %s' % frame)
            if record.frame == frame:
                if not np.isclose(record.t, float(t), rtol=0, atol=1e-7):
                    raise ValueError('reconstruction/input timestamp mismatch for frame %s' % frame)
                return record

    def close(self):
        for source in self.sources:
            source.close()
