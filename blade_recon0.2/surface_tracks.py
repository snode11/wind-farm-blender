"""Fixed-anchor RGB feature identities for optional geometry constraints.

Only declared fit RGB, derived occupancy, original calibration/frame/t, and
explicit RGB unknown domains are read. No evaluator or reconstructed/source
geometry is imported here. Shi-Tomasi points are image features, not named
defect boundaries. A lost identity is never reacquired. The geometry consumer
may bind each anchor once to its own healthy estimated mesh.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
import argparse
import hashlib
import json
from collections import Counter

import cv2
import numpy as np

from rgb_masks import known_observation_domain


@dataclass(frozen=True)
class TrackConfig:
    sensor_roi_fraction_xyxy: tuple = (.25, .20, .75, .80)
    max_features_per_camera: int = 16
    shi_tomasi_quality: float = .02
    minimum_feature_distance_px: float = 14.
    feature_block_size_px: int = 7
    seed_subpixel_window_px: int = 5
    lk_window_px: int = 31
    lk_pyramid_levels: int = 4
    lk_iterations: int = 50
    lk_epsilon_px: float = .001
    gray_sigma_px: float = .8
    descriptor_size_px: int = 25
    independent_peak_exclusion_radius_px: int = 8
    match_peak_agreement_max_px: float = 2.
    localization_sigma_max_px: float = 1.
    forward_backward_max_px: float = 1.
    ncc_min: float = .8
    contrast_gray_min: float = 12.
    identity_margin_min: float = 2.
    photometric_noise_floor_gray: float = 1.
    affine_rgb_seed: bool = True
    affine_background_sigma_px: float = 40.
    affine_iterations: int = 300
    affine_epsilon: float = 1e-7
    affine_correlation_min: float = .8
    affine_angle_seeds_deg: tuple = (-30., -15., 0., 15., 30.)
    affine_normalized_lk_pyramid_levels: int = 1


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def declared_input_path(root, relative, role):
    """Validate a declared role/path before reading any observation bytes.

    This is a bundle file allowlist, not a process-level filesystem sandbox.
    Both the lexical declaration and the symlink-resolved target are checked.
    """
    allowed={'declared_solver_input':{'.json'},'declared_RGB_unknown_domains':{'.json'},
             'RGB':{'.png','.jpg','.jpeg'},'derived_occupancy_mask':{'.png'}}
    if role not in allowed:
        raise ValueError('unsupported solver observation file role: '+str(role))
    if not isinstance(relative,str) or not relative or Path(relative).is_absolute():
        raise ValueError('observation input must be a declared relative bundle path')
    root=Path(root).resolve();declared=Path(relative);path=(root/declared).resolve()
    if not path.is_relative_to(root):
        raise ValueError('observation path escapes declared bundle: '+relative)
    def forbidden(parts):
        for part in parts:
            p=part.lower()
            if (p=='evaluation' or p.startswith('truth') or 'object_id' in p
                    or p in ('geometry.npz','source.json','states.npz')
                    or p.startswith(('source-surface','dynamic-source','source-geometry','source-state'))):
                return True
        return False
    if forbidden(declared.parts) or forbidden(path.relative_to(root).parts):
        raise ValueError('forbidden solver observation input: '+relative)
    if declared.suffix.lower() not in allowed[role] or path.suffix.lower() not in allowed[role]:
        raise ValueError('declared observation extension does not match its role: '+relative)
    if not path.is_file():
        raise ValueError('missing declared observation input: '+relative)
    return path


def _gray(rgb, config):
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError('tracker requires original uint8 HxWx3 RGB')
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return cv2.GaussianBlur(gray, (0, 0), config.gray_sigma_px)


def _roi(shape, config):
    h, w = shape
    xy = np.rint(np.asarray(config.sensor_roi_fraction_xyxy) * [w, h, w, h]).astype(int)
    if not (0 <= xy[0] < xy[2] <= w and 0 <= xy[1] < xy[3] <= h):
        raise ValueError('sensor ROI must lie in original RGB dimensions')
    return xy


def _patch(gray, uv, size):
    u, v = np.asarray(uv, float)
    radius = size // 2
    if not np.isfinite([u, v]).all() or u < radius or v < radius or u >= gray.shape[1]-radius or v >= gray.shape[0]-radius:
        return None
    return cv2.getRectSubPix(gray.astype(np.float32), (size, size), (float(u), float(v)))


def _known_patch(known, uv, size):
    p = _patch(known.astype(np.float32), uv, size)
    return p is not None and float(p.min()) >= 1.-1e-6


def _affine_seed(anchor_gray, target_gray, roi, config, initial_full=None):
    """Whole sensor RGB affine motion estimate; no feature boundary/model input.

    A fixed low-frequency subtraction reduces illumination slopes. This seed
    handles image rotation/shear before local LK. It does not assign identities;
    each point must still pass its independent local RGB/FB/competition gates.
    """
    x0,y0,x1,y1=roi
    images=[]
    for gray in (anchor_gray,target_gray):
        g=gray[y0:y1,x0:x1].astype(np.float32)
        images.append(g-cv2.GaussianBlur(g,(0,0),config.affine_background_sigma_px))
    initial=np.eye(2,3,dtype=np.float32)
    if initial_full is not None:
        initial=np.asarray(initial_full[:2],np.float32).copy()
        initial[:,2]+=initial[:,:2]@np.asarray([x0,y0])-np.asarray([x0,y0])
    candidates=[]
    center=np.asarray([images[0].shape[1]/2.,images[0].shape[0]/2.])
    base=np.eye(3,dtype=float);base[:2]=initial
    for angle in config.affine_angle_seeds_deg:
        radians=np.deg2rad(angle);rotation=np.asarray([[np.cos(radians),-np.sin(radians)],[np.sin(radians),np.cos(radians)]])
        change=np.eye(3);change[:2,:2]=rotation;change[:2,2]=center-rotation@center
        attempt=(change@base)[:2].astype(np.float32)
        try:
            cc,matrix=cv2.findTransformECC(images[0],images[1],attempt,cv2.MOTION_AFFINE,
                (cv2.TERM_CRITERIA_COUNT|cv2.TERM_CRITERIA_EPS,config.affine_iterations,config.affine_epsilon),
                None,5)
            if np.isfinite(matrix).all():candidates.append((float(cc),matrix.copy(),angle))
        except cv2.error:
            continue
    if not candidates:
        return None, {'status':'RGB_AFFINE_REGISTRATION_FAILED'}
    cc,matrix,chosen_angle=max(candidates,key=lambda v:v[0])
    if not np.isfinite(matrix).all() or cc<config.affine_correlation_min:
        return None, {'status':'RGB_AFFINE_CORRELATION_REJECTED','correlation':float(cc)}
    full=np.eye(3,dtype=float);full[:2]=matrix
    full[:2,2]+=np.asarray([x0,y0])-full[:2,:2]@np.asarray([x0,y0])
    return full, {'status':'RGB_AFFINE_SEED','correlation':float(cc),'anchor_to_target_affine':full[:2].tolist(),
        'chosen_RGB_multistart_angle_deg':chosen_angle,'RGB_multistart_candidates':len(candidates),
        'input':'original sensor ROI RGB highpass only; no model/region boundary/source truth'}


def _quality(anchor_gray, target_gray, anchor_uv, uv, roi, config):
    """Conditional RGB localization covariance plus independent match peak.

    The covariance describes local translation under an affine brightness model;
    it is an estimate, not an independent truth error or a geometric precision
    claim. Forward/backward and competition gates remain required separately.
    """
    a = _patch(anchor_gray, anchor_uv, config.descriptor_size_px)
    b = _patch(target_gray, uv, config.descriptor_size_px)
    if a is None or b is None:
        return {}, 'OUT_OF_IMAGE_DESCRIPTOR'
    contrast = min(float(np.percentile(a, 98)-np.percentile(a, 2)),
                   float(np.percentile(b, 98)-np.percentile(b, 2)))
    ac, bc = a-a.mean(), b-b.mean()
    denominator = float(np.linalg.norm(ac)*np.linalg.norm(bc))
    ncc = float(np.clip(np.sum(ac*bc)/denominator,-1.,1.)) if denominator > 1e-6 else 0.
    design = np.column_stack([a.reshape(-1), np.ones(a.size)])
    fit = np.linalg.lstsq(design, b.reshape(-1), rcond=None)[0]
    residual = b.reshape(-1)-design@fit
    gy, gx = np.gradient(b.astype(float))
    gradients = np.column_stack([gx.reshape(-1), gy.reshape(-1)])
    eigen = np.linalg.eigvalsh(gradients.T@gradients)
    noise_variance = max(config.photometric_noise_floor_gray**2,
                         float(np.sum(residual**2)/max(a.size-2, 1)))
    sigma = float(np.sqrt(noise_variance/eigen[0])) if eigen[0] > 1e-9 else None
    x0, y0, x1, y1 = roi
    search = target_gray[y0:y1, x0:x1].astype(np.float32)
    if min(search.shape) < config.descriptor_size_px:
        return {}, 'NO_SENSOR_SEARCH_SUPPORT'
    response = cv2.matchTemplate(search, a, cv2.TM_CCOEFF_NORMED)
    _, best, _, loc = cv2.minMaxLoc(response)
    radius = config.descriptor_size_px//2
    best_uv = np.asarray([loc[0]+x0+radius, loc[1]+y0+radius], float)
    agreement = float(np.linalg.norm(best_uv-uv))
    yy, xx = np.mgrid[:response.shape[0], :response.shape[1]]
    independent = (xx-loc[0])**2+(yy-loc[1])**2 > config.independent_peak_exclusion_radius_px**2
    runner = float(np.max(response[independent])) if independent.any() else None
    # Z-normalized squared descriptor error is proportional to (1-NCC).
    # Compare peaks on the same integer sampling grid. Comparing the exact
    # subpixel self patch (NCC=1) to integer competitor peaks would falsely
    # certify duplicate appearances at an anchor as uniquely identifiable.
    margin = float((1.-runner)/max(1.-float(best), 1e-6)) if runner is not None else None
    detail = {'localization_sigma_px': sigma, 'localization_sigma_method':
              'conditional local translation RGB Hessian covariance after affine brightness fit; not truth error',
              'ncc': ncc, 'contrast_gray': contrast, 'identity_margin': margin,
              'identity_margin_method': 'runner/winner ZSSD ratio over fixed sensor ROI; independent peak outside 8px',
              'independent_runner_ncc': runner, 'best_integer_match_ncc': float(best),
              'match_peak_agreement_px': agreement, 'photometric_residual_sigma_gray': float(np.sqrt(noise_variance)),
              'gradient_hessian_min_eigenvalue': float(eigen[0])}
    if sigma is None or sigma > config.localization_sigma_max_px:
        return detail, 'LOCALIZATION_UNCERTAIN'
    if ncc < config.ncc_min:
        return detail, 'PHOTOMETRIC_MISMATCH'
    if contrast < config.contrast_gray_min:
        return detail, 'INSUFFICIENT_RGB_CONTRAST'
    if agreement > config.match_peak_agreement_max_px:
        return detail, 'COMPETING_MATCH_PEAK'
    if margin is None or margin < config.identity_margin_min:
        return detail, 'AMBIGUOUS_RGB_IDENTITY'
    return detail, None


def track_sequence(records, config=TrackConfig()):
    """Track one camera's fixed first-frame RGB corners, without reacquisition.

    records contain frame/t/camera/rgb ndarray/known bool/occupancy bool and RGB
    hash. Cross-camera names never imply corresponding surface identities.
    """
    if not records:
        return [], {'planned_points': 0, 'status': 'MISSING_CAMERA_SEQUENCE'}
    if len({r['camera'] for r in records}) != 1:
        raise ValueError('camera identities cannot be merged without RGB matching')
    records = sorted(records, key=lambda r: r['t'])
    if len({r['frame'] for r in records}) != len(records):
        raise ValueError('duplicate camera/frame observations')
    gray = [_gray(r['rgb'], config) for r in records]
    if len({g.shape for g in gray}) != 1:
        raise ValueError('original camera dimensions changed')
    roi = _roi(gray[0].shape, config)
    x0, y0, x1, y1 = roi
    support = records[0]['known'] & records[0]['occupancy']
    seed_mask = np.zeros(gray[0].shape, np.uint8)
    seed_mask[y0:y1, x0:x1] = support[y0:y1, x0:x1]
    # Every descriptor pixel must have declared known measured blade support.
    seed_mask = cv2.erode(seed_mask, np.ones((config.descriptor_size_px,)*2, np.uint8))
    points = cv2.goodFeaturesToTrack(gray[0], config.max_features_per_camera,
        config.shi_tomasi_quality, config.minimum_feature_distance_px,
        mask=seed_mask, blockSize=config.feature_block_size_px, useHarrisDetector=False)
    if points is None:
        return [], {'planned_points': 0, 'status': 'NO_TWO_DIMENSIONAL_RGB_FEATURE',
                    'sensor_roi_xyxy': roi.tolist()}
    cv2.cornerSubPix(gray[0], points, (config.seed_subpixel_window_px,)*2, (-1, -1),
        (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, config.lk_iterations, config.lk_epsilon_px))
    points = points.reshape(-1, 2)
    tracks = []
    motion=[(np.eye(3),{'status':'ANCHOR_IDENTITY','correlation':1.})]
    for target in gray[1:]:
        motion.append(_affine_seed(gray[0],target,roi,config,initial_full=motion[-1][0]) if config.affine_rgb_seed else
                      (np.eye(3),{'status':'NO_AFFINE_SEED'}))
    lk = dict(winSize=(config.lk_window_px,)*2, maxLevel=config.lk_pyramid_levels,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, config.lk_iterations, config.lk_epsilon_px),
        minEigThreshold=1e-5)
    for index, p in enumerate(points):
        anchor = records[0]
        identity = f"{anchor['camera']}_{anchor['frame']}_{index:03d}"
        track = {'id': identity, 'anchor': {k: anchor[k] for k in ('frame','t','camera','rgb_sha256')},
                 'observations': []}
        track['anchor']['uv'] = p.astype(float).tolist()
        active = True
        for j, row in enumerate(records):
            obs = {k: row[k] for k in ('frame','t','camera','rgb_sha256')}
            obs.update(uv=None, status='IDENTITY_TERMINATED', accept=False, known=False, quality={})
            if active:
                matrix,motion_detail=motion[j]
                obs['RGB_motion_seed']=motion_detail
                if j == 0:
                    uv, fb, lk_ok = p.copy(), 0., True
                else:
                    start = p.astype(np.float32).reshape(1,1,2)
                    if matrix is not None and config.affine_rgb_seed:
                        aligned=cv2.warpAffine(gray[j],matrix[:2].astype(np.float32),
                            (gray[0].shape[1],gray[0].shape[0]),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP)
                        # Global RGB motion is already removed. Coarse full-image
                        # pyramids otherwise pull local points toward the artificial
                        # warp border instead of estimating the remaining motion.
                        normalized_lk={**lk,'maxLevel':config.affine_normalized_lk_pyramid_levels}
                        local,forward_ok,_=cv2.calcOpticalFlowPyrLK(gray[0],aligned,start,None,**normalized_lk)
                        returned,backward_ok,_=cv2.calcOpticalFlowPyrLK(aligned,gray[0],local,None,**normalized_lk)
                        uv=(matrix@np.r_[local.reshape(2),1.])[:2]
                    else:
                        next_uv, forward_ok, _ = cv2.calcOpticalFlowPyrLK(gray[0], gray[j], start, None, **lk)
                        returned, backward_ok, _ = cv2.calcOpticalFlowPyrLK(gray[j], gray[0], next_uv, None, **lk)
                        uv = next_uv.reshape(2)
                    fb = float(np.linalg.norm(returned.reshape(2)-p))
                    lk_ok = bool(forward_ok[0,0] and backward_ok[0,0])
                if j>0 and matrix is not None and config.affine_rgb_seed:
                    reference=cv2.warpAffine(gray[0].astype(np.float32),matrix[:2].astype(np.float32),
                        (gray[j].shape[1],gray[j].shape[0]),flags=cv2.INTER_LINEAR)
                    reference_uv=(matrix@np.r_[p,1.])[:2]
                    # The target-grid reference descriptor pulls RGB from an
                    # affine footprint in the original anchor. Its support is
                    # different from the observed target descriptor's support.
                    reference_support=cv2.warpAffine(
                        (anchor['known'] & anchor['occupancy']).astype(np.float32),
                        matrix[:2].astype(np.float32),(gray[j].shape[1],gray[j].shape[0]),
                        flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
                    reference_known=_known_patch(reference_support,reference_uv,config.descriptor_size_px)
                    quality,reason=_quality(reference,gray[j],reference_uv,uv,roi,config)
                    quality['descriptor_reference']='fixed anchor RGB warped by whole sensor ROI RGB affine seed'
                else:
                    reference_known=_known_patch(anchor['known'] & anchor['occupancy'],p,
                        config.descriptor_size_px)
                    quality, reason = _quality(gray[0], gray[j], p, uv, roi, config)
                quality['reference_descriptor_known']=bool(reference_known)
                quality['reference_descriptor_support_source']='original anchor known domain and measured occupancy; same RGB affine warp and descriptor sampling'
                quality['forward_backward_error_px'] = fb
                quality['forward_backward_coordinate_type'] = 'original_anchor_image_pixel_centers'
                quality['forward_backward_method'] = 'independent local forward/backward LK returned anchor UV minus fixed original anchor UV'
                known = _known_patch(row['known'] & row['occupancy'], uv, config.descriptor_size_px)
                if not lk_ok:
                    reason = 'LK_FAILED'
                elif fb > config.forward_backward_max_px:
                    reason = 'FORWARD_BACKWARD_MISMATCH'
                elif not known:
                    reason = 'UNKNOWN_OR_UNMEASURED_RGB_DOMAIN'
                elif not reference_known:
                    reason = 'UNKNOWN_OR_UNMEASURED_ANCHOR_RGB_DOMAIN'
                obs.update(quality=quality, known=bool(known))
                if reason is None:
                    obs.update(uv=uv.astype(float).tolist(), status='RGB_TRACK_OBSERVED', accept=True)
                else:
                    obs.update(status=reason, diagnostic_candidate_uv=uv.astype(float).tolist())
                    active = False
            track['observations'].append(obs)
        tracks.append(track)
    return tracks, {'status': 'RGB_FIXED_ANCHOR_TRACKS', 'planned_points': len(tracks),
                    'planned_observations': len(tracks)*len(records), 'sensor_roi_xyxy': roi.tolist()}


def prepare(bundle_path, camera_names, output, config=TrackConfig()):
    """Read only the declared input bundle and named fit camera sequences."""
    declaration=Path(bundle_path).absolute()
    bundle_path=declared_input_path(declaration.parent,declaration.name,'declared_solver_input')
    output=Path(output).resolve()
    manifest = json.loads(bundle_path.read_text())
    if manifest.get('schema') != 'blade-surface-input.v1':
        raise ValueError('tracker requires explicit blade-surface-input.v1 bundle')
    root = bundle_path.parent
    annotations_path = declared_input_path(root,manifest['annotations'],'declared_RGB_unknown_domains') if manifest.get('annotations') else None
    annotations = json.loads(annotations_path.read_text()) if annotations_path else {'regions': []}
    accesses = [{'role': 'declared_solver_input', 'path': str(bundle_path), 'sha256': sha256(bundle_path)}]
    if annotations_path:
        accesses.append({'role': 'declared_RGB_unknown_domains', 'path': str(annotations_path), 'sha256': sha256(annotations_path)})
    by_camera = {name: [] for name in camera_names}
    domain_records = []
    for frame in manifest['frames']:
        if frame.get('split','fit')!='fit':
            raise ValueError('heldout/non-fit frame cannot enter solver RGB tracker')
        for cam in frame['cameras']:
            if cam['name'] not in by_camera:
                continue
            if cam.get('split','fit')!='fit':
                raise ValueError('heldout/non-fit camera cannot enter solver RGB tracker')
            rgb_path=declared_input_path(root,cam['rgb'],'RGB')
            mask_path=declared_input_path(root,cam['mask'],'derived_occupancy_mask')
            for role, path in [('RGB',rgb_path),('derived_occupancy_mask',mask_path)]:
                accesses.append({'role':role,'path':str(path),'sha256':sha256(path)})
            rgb = cv2.cvtColor(cv2.imread(str(rgb_path)), cv2.COLOR_BGR2RGB)
            occupancy = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE) > 127
            if rgb.shape != (cam['H'],cam['W'],3) or occupancy.shape != (cam['H'],cam['W']):
                raise ValueError('RGB/mask original calibration dimensions mismatch')
            regions = [r for r in annotations.get('regions',[]) if r.get('kind')=='unknown_contour'
                and r['frame']==frame['frame'] and r['camera']==cam['name']]
            for region in regions:
                if (not np.isclose(region['t'],frame['t'],atol=1e-9,rtol=0)
                    or region['source_rgb'] != cam['rgb'] or region['source_rgb_sha256'] != sha256(rgb_path)):
                    raise ValueError('unknown domain frame/RGB identity mismatch')
            known = known_observation_domain(occupancy.shape, [r['polygon_xy'] for r in regions])
            by_camera[cam['name']].append({'frame':frame['frame'],'t':frame['t'],'camera':cam['name'],
                'rgb':rgb,'known':known,'occupancy':occupancy,'rgb_sha256':sha256(rgb_path)})
            domain_records.append({'frame':frame['frame'],'t':frame['t'],'camera':cam['name'],
                'observed_occupancy_pixels':int(occupancy.sum()),'unknown_domain_pixels':int((~known).sum()),
                'unknown_overlap_with_occupancy_pixels':int((~known&occupancy).sum()),
                'policy':'unknown excluded separately; observed occupancy unchanged; no completion'})
    output.mkdir(parents=True, exist_ok=False)
    protocol = {'schema':'blade-rgb-track-protocol.v1','config':asdict(config),
        'method':'fixed-anchor Shi-Tomasi subpixel RGB points; RGB-only whole-ROI affine seed, direct pyramidal LK plus backward check and affine-warped sensor ROI descriptor competition',
        'no_reacquisition':True,'cross_camera_policy':'no cross-camera point identity without independent RGB correspondence',
        'declared_camera_names':list(camera_names),'planned_frames':[f['frame'] for f in manifest['frames']]}
    # Protocol is written before the first non-anchor image match is attempted.
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    frozen=output/'frozen-source';frozen.mkdir()
    for path in [Path(__file__),Path(__file__).with_name('rgb_masks.py')]:
        (frozen/path.name).write_bytes(path.read_bytes())
    tracks, summaries = [], {}
    for name, rows in by_camera.items():
        found, summary = track_sequence(rows,config)
        tracks.extend(found); summaries[name]=summary
    counts = Counter(o['status'] for t in tracks for o in t['observations'])
    payload = {'schema':'blade-texture-geometry.v1','pixel_coordinates':'original_image_pixel_centers','provenance':{
        'role':'solver_rgb_observations','method':protocol['method'],
        'allowed_inputs':['declared_rgb','derived_mask','camera_calibration','frame_t'],
        'access_log':accesses,'protocol_sha256':sha256(output/'protocol.json'),
        'source_geometry_or_truth_read':False,'heldout_RGB_read':False},
        'quality_gates':{'localization_sigma_max_px':config.localization_sigma_max_px,
        'forward_backward_max_px':config.forward_backward_max_px,'ncc_min':config.ncc_min,
        'contrast_gray_min':config.contrast_gray_min,'identity_margin_min':config.identity_margin_min},
        'tracks':tracks,'summary':{'cameras':summaries,'planned_tracks':len(tracks),
        'planned_observations':sum(len(t['observations']) for t in tracks),'status_counts':dict(counts)},
        'unknown_domain_audit':domain_records}
    (output/'tracks.json').write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
    for name, rows in by_camera.items():
        for row in rows:
            rgb = row['rgb'].copy()
            for track in tracks:
                obs = next((o for o in track['observations'] if o['camera']==name and o['frame']==row['frame']),None)
                if obs and (obs['uv'] is not None or obs.get('diagnostic_candidate_uv') is not None):
                    uv = obs['uv'] if obs['uv'] is not None else obs['diagnostic_candidate_uv']
                    xy = tuple(np.rint(uv).astype(int)); color=(0,220,40) if obs['accept'] else (240,40,40)
                    cv2.circle(rgb,xy,5,color,1,cv2.LINE_AA)
                    cv2.putText(rgb,track['id'].rsplit('_',1)[1],(xy[0]+6,xy[1]),cv2.FONT_HERSHEY_SIMPLEX,.35,color,1)
            cv2.imwrite(str(output/f"{name}_{row['frame']:06d}_RGB_tracks.png"),cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR))
    freeze={'schema':'blade-rgb-track-freeze.v1','tracks_sha256':sha256(output/'tracks.json'),
        'protocol_sha256':sha256(output/'protocol.json'),'source_sha256':sha256(__file__),
        'rgb_masks_sha256':sha256(Path(__file__).with_name('rgb_masks.py')),'summary':payload['summary']}
    (output/'freeze.json').write_text(json.dumps(freeze,indent=2)+'\n')
    return payload['summary']


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--bundle',required=True)
    parser.add_argument('--cameras',nargs='+',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.bundle,args.cameras,args.out),indent=2))
