"""
texture.py —— 把三路视频里叶片表面的真实像素"贴回"重建出的三维叶片(每片叶片一张纹理图集)。

前置：recon.py 已经给出逐帧位姿(recon.json)。本程序不改位姿，只做纹理：
  1. 对每帧、每路相机、每片叶片的"观测"截面：纹素 → 三维表面点 → 投影到原图 → 取灰度；
  2. 每个样本带质量权重：像素脚印(展向 × 弦向，米/像素)、离轮廓边缘的距离、
     运动模糊(给了曝光时间时)、z-buffer 可见性；
  3. 光照归一化：每个视角的样本除以自身的低频分量(≈0.5 m 尺度的明暗)，只保留表面细节，
     不同视角、不同光照的样本才能无缝拼接；
  4. 融合(默认 best)：每个纹素取质量权重最高的那一个视角。
     实测(合成数据)：同一条裂纹在不同视角里沿展向相差 0~40 cm(位姿误差，展向 1 px ≈ 7 cm)，
     加权平均会把细线变成双线或抹淡；取最佳视角则保持清晰，代价是绝对位置有几十厘米误差。
     --fusion mean 可切换为加权平均(噪声低，适合位姿很准时)。
  5. (可选 --register) 第二遍：每个视角先在高通细节上配准到第一遍结果(展向 ±50 cm、弦向 ±6 cm)
     再融合。只能让视角彼此对齐，不能消除绝对位置误差；表面细节少时找不到可靠匹配，不会移动。

输出(--out 目录，默认 <recon 所在目录>/texture)：
  blade{1,2,3}_tex.png      纹理(BGRA；A=0 表示从未被看清，Blender 里显示为推断色)
  blade{1,2,3}_raw.png      未做光照归一化的原始亮度融合(对照用)
  blade{1,2,3}_res.png      最佳视角的展向分辨率伪彩图(厘米/像素)，说明每块纹理有多可信
  texture.json              图集定义、逐叶片覆盖率、分辨率统计、每个视角的配准偏移

    python texture.py --data data/synth_tex --recon out/synth_tex/recon.json
"""
import argparse
import json
import os
import hashlib
from pathlib import Path
import time
from types import SimpleNamespace

import cv2
import numpy as np

from atlas import Atlas
from camera import Camera
from model import Rotor, TurbineConfig, load_json, save_json
from raster import rasterize, sample, vertex_frames, triangulated_atlas_surface
from input_adapter import InputSequence
import recon as RC

POW = 4.0                   # 融合权重指数：越大越接近"只取最佳视角"
EDGE_PX = (1.5, 6.0)        # 离轮廓 <1.5 px 权重 0，>6 px 权重 1(位姿误差会把天空混进来)
MIN_COS = 0.08              # 视线与表面法向夹角的余弦下限(太掠射的样本不要)
LOW_SIGMA_M = (2.0, 0.6)    # 光照归一化的低频尺度(展向, 弦向)，米；要大于想保留的缺陷尺寸
REG_RANGE_M = (0.50, 0.06)  # 第二遍配准搜索范围(展向, 弦向)，米


def rows_cols_step(atlas):
    """每个纹素的展向、周向尺寸(米)。"""
    return atlas.dr, atlas.perim_rows / atlas.W


class View:
    """一个视角(一帧 × 一路相机 × 一片叶片)在图集上的样本。只保存覆盖到的行。"""

    def __init__(self, rows, val, w, res_s, res_c):
        self.rows = rows                # slice
        self.val, self.w = val, w       # (h,W) 归一化后的细节值 / 权重
        self.res_s, self.res_c = res_s, res_c   # (h,W) 展向/弦向 米每像素


def sample_view(cam, img, obs_mask, R, V, nrm, b, atlas, rows, row_ok, omega_deg_s, exposure_s):
    """纹素驱动的采样：对 rows 范围内每个纹素算三维点 → 投影 → 可见性 → 取值与权重。"""
    P = triangulated_atlas_surface(atlas, V[b], rows)               # (h,W,3)
    Nn = triangulated_atlas_surface(atlas, nrm[b], rows)
    Nn /= np.linalg.norm(Nn, axis=-1, keepdims=True) + 1e-12
    uv, z = cam.project(P)
    h, W = z.shape
    view = cam.center[None, None] - P
    dist = np.linalg.norm(view, axis=-1)
    cosv = (view * Nn).sum(-1) / dist
    u, v = uv[..., 0], uv[..., 1]
    ok = np.isfinite(u) & (u >= 1) & (u < cam.W - 2) & (v >= 1) & (v < cam.H - 2) & (cosv > MIN_COS)
    ok &= row_ok[:, None]
    if not ok.any():
        return None
    ui = np.clip(np.round(u).astype(int), 0, cam.W - 1)
    vi = np.clip(np.round(v).astype(int), 0, cam.H - 1)
    # z-buffer：该像素必须属于同一片叶片的正面，且深度一致
    fid = R.fid[vi, ui]
    zb = R.zbuf[vi, ui]
    tol = 0.05 + 0.004 * z
    ok &= (fid >= 0) & (fid // R.F == b) & (np.abs(z - zb) < tol)
    # 观测掩码内部、离边缘够远
    ok &= obs_mask[vi, ui] > 0
    edge_w = np.clip((R.edge_dt[vi, ui] - EDGE_PX[0]) / (EDGE_PX[1] - EDGE_PX[0]), 0, 1)
    ok &= edge_w > 0
    if ok.sum() < 50:
        return None
    # 像素脚印：纹素网格在像面上的步长 → 每像素对应多少米
    du_r = np.gradient(uv, axis=0)                                  # 每行(展向 dr)的像素位移
    du_c = np.gradient(uv, axis=1)                                  # 每列(周向)的像素位移
    ds, dc = rows_cols_step(atlas)
    dc = dc[rows][:, None]
    px_s = np.linalg.norm(du_r, axis=-1) / ds                       # 像素/米，展向
    px_c = np.linalg.norm(du_c, axis=-1) / dc
    area_px = np.abs(du_r[..., 0] * du_c[..., 1] - du_r[..., 1] * du_c[..., 0]) / (ds * dc)  # 像素/平方米
    with np.errstate(divide='ignore', invalid='ignore'):
        res_s, res_c = 1.0 / px_s, 1.0 / px_c
        res_area = 1.0 / np.sqrt(area_px)                           # 等效分辨率(米/像素)
    w = edge_w / (1.0 + (res_area / 0.01) ** 2)                     # 1 cm/px 为基准
    if exposure_s > 0:
        # 运动模糊：曝光时间内纹素在像面上的位移(像素)
        omg = np.radians(omega_deg_s)
        vel = np.cross(np.asarray(R.rot_axis)[None, None], P - R.hub[None, None]) * omg
        uv2, _ = cam.project(P + vel * exposure_s)
        blur = np.linalg.norm(uv2 - uv, axis=-1)
        w = w / (1.0 + (blur / 1.0) ** 2)
    g = sample(img, v, u, wrap_cols=False)
    w = np.where(ok, np.nan_to_num(w), 0.0).astype(np.float32)
    g = np.where(ok, g, 0.0).astype(np.float32)
    return g, w, np.where(ok, res_s, np.nan), np.where(ok, res_c, np.nan)


def normalize(g, w, atlas, rows):
    """光照归一化：除以带权低频(归一化卷积)。返回细节值 ≈ 1。"""
    m = (w > 0).astype(np.float32)
    ds, dc = rows_cols_step(atlas)
    sy = LOW_SIGMA_M[0] / ds
    sx = LOW_SIGMA_M[1] / float(np.median(dc[rows]))
    num = cv2.GaussianBlur(g * m, (0, 0), sigmaX=sx, sigmaY=sy, borderType=cv2.BORDER_REFLECT)
    den = cv2.GaussianBlur(m, (0, 0), sigmaX=sx, sigmaY=sy, borderType=cv2.BORDER_REFLECT)
    low = num / np.maximum(den, 1e-3)
    out = np.where(m > 0, g / np.maximum(low, 1.0), 0.0)
    return out.astype(np.float32)


def highpass(val, m, atlas, rows):
    """去掉 ≈10 cm 以上尺度的起伏，只留缺陷、斑纹这类细节，用于配准。"""
    ds, dc = rows_cols_step(atlas)
    s = 0.10 / ds, 0.10 / float(np.median(dc[rows]))
    mf = m.astype(np.float32)
    x = np.where(m, val, 0).astype(np.float32)
    low = cv2.GaussianBlur(x, (0, 0), sigmaX=s[1], sigmaY=s[0]) / np.maximum(
        cv2.GaussianBlur(mf, (0, 0), sigmaX=s[1], sigmaY=s[0]), 1e-3)
    return np.where(m, x - low, 0).astype(np.float32)


def shift_span_cols(array, dy, dx, fill=0):
    """整数平移：展向裁切并填无效值，只有闭合周向允许循环。"""
    out = np.full_like(array, fill)
    h = len(array)
    first, last = max(0, dy), min(h, h + dy)
    if last > first:
        out[first:last] = np.roll(array[first - dy:last - dy], dx, axis=1)
    return out


def register(val, w, ref, ref_m, atlas, rows):
    """把一个视角配准到锚点视角(该叶片最清楚的一次观测)：在高通细节上找整数纹素平移。
    返回 (drow, dcol, 相关系数提升)。没有可靠细节(平坦表面)时返回 0 平移。"""
    ds, dc = rows_cols_step(atlas)
    Rr = int(round(REG_RANGE_M[0] / ds))
    Rc = int(round(REG_RANGE_M[1] / float(np.median(dc[rows]))))
    mv = w > 0
    if (mv & ref_m).sum() < 500:
        return 0, 0, 0.0
    A = highpass(val, mv, atlas, rows)
    B = highpass(ref, ref_m, atlas, rows)

    def score(dy, dx):
        sh = shift_span_cols(A, dy, dx)
        mm = shift_span_cols(mv, dy, dx, fill=False) & ref_m
        if mm.sum() < 300:
            return -1.0
        x, y = sh[mm], B[mm]
        return float((x * y).sum() / np.sqrt((x * x).sum() * (y * y).sum() + 1e-12))
    s0 = score(0, 0)
    best = (s0, 0, 0)
    step_r = max(1, Rr // 6)
    for dy in range(-Rr, Rr + 1, step_r):
        for dx in range(-Rc, Rc + 1):
            s = score(dy, dx)
            if s > best[0]:
                best = (s, dy, dx)
    _, by, bx = best
    for dy in range(by - step_r + 1, by + step_r):
        for dx in range(bx - 1, bx + 2):
            s = score(dy, dx)
            if s > best[0]:
                best = (s, dy, dx)
    if best[0] < 0.2 or best[0] - s0 < 0.03:          # 细节不足或改善不明显 → 不动
        return 0, 0, 0.0
    return best[1], best[2], best[0] - s0


class Accum:
    def __init__(self, atlas, mode='both'):
        """Allocate the requested fusion path; direct callers retain both modes."""
        if mode not in ('best', 'mean', 'both'):
            raise ValueError('accumulator mode must be best, mean, or both')
        self.mode = mode
        H, W = atlas.H, atlas.W
        self.num = np.zeros((3, H, W), np.float32) if mode != 'best' else None
        self.raw = np.zeros((3, H, W), np.float32) if mode != 'best' else None
        self.den = np.zeros((3, H, W), np.float32)
        self.wmax = np.zeros((3, H, W), np.float32)
        self.res_s = np.full((3, H, W), np.nan, np.float32)
        self.res_c = np.full((3, H, W), np.nan, np.float32)
        self.nview = np.zeros((3, H, W), np.uint16)
        self.wsel = np.zeros((3, H, W), np.float32)
        self.best_val = np.zeros((3, H, W), np.float32) if mode != 'mean' else None
        self.best_raw = np.zeros((3, H, W), np.float32) if mode != 'mean' else None

    def add(self, b, rows, val, raw, w, rs, rc, shift=(0, 0)):
        if shift != (0, 0):
            # 移动到全图集的目标行，而不是在本视角的行窗口内循环。
            start = (rows.start or 0) + shift[0]
            first, last = max(0, -start), min(len(val), self.den.shape[1] - start)
            if last <= first:
                return
            rows = slice(start + first, start + last)
            val, raw, w, rs, rc = [np.roll(a[first:last], shift[1], axis=1)
                                   for a in (val, raw, w, rs, rc)]
        wp = w ** POW
        if self.num is not None:
            self.num[b, rows] += wp * val
            self.raw[b, rows] += wp * raw
        self.den[b, rows] += wp
        # 选视角用平滑后的权重(≈半米块)，同一块区域来自同一个视角，避免逐纹素拼接出接缝
        ws = cv2.GaussianBlur(w, (0, 0), sigmaX=6, sigmaY=12) * (w > 0)
        better = ws > self.wsel[b, rows]
        self.wsel[b, rows] = np.where(better, ws, self.wsel[b, rows])
        if self.best_val is not None:
            self.best_val[b, rows] = np.where(better, val, self.best_val[b, rows])
            self.best_raw[b, rows] = np.where(better, raw, self.best_raw[b, rows])
        self.wmax[b, rows] = np.where(better, w, self.wmax[b, rows])
        self.res_s[b, rows] = np.where(better, rs, self.res_s[b, rows])
        self.res_c[b, rows] = np.where(better, rc, self.res_c[b, rows])
        self.nview[b, rows] += (w > 0).astype(np.uint16)

    def result(self, mode='best'):
        """best：每个纹素取权重最高的那个视角(清晰，不会因视角间错位出现重影)；
        mean：按 w^POW 加权平均(噪声更低，但视角间有错位时细线会变成双线/变淡)。"""
        if mode not in ('best', 'mean') or self.mode not in (mode, 'both'):
            raise ValueError('requested fusion mode was not allocated: ' + mode)
        has = self.den > 0
        if mode == 'best':
            return np.where(has, self.best_val, 0), np.where(has, self.best_raw, 0), has
        d = np.maximum(self.den, 1e-20)
        return np.where(has, self.num / d, 0), np.where(has, self.raw / d, 0), has


def fill_holes(m):
    """叶片掩码填洞：亮的修补块、反光可能被阈值当成天空，但它们是叶片表面，正是要采的纹理。"""
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(m)
    cv2.drawContours(out, cs, -1, 255, -1)
    return out


def frame_views(rotor, atlas, cams, imgs, masks, st, seen, omega, exposure_s, faces):
    """一帧所有(相机, 叶片)视角的样本。"""
    V, _ = rotor.forward(st)
    nrm = np.stack([vertex_frames(V[b])[0] for b in range(3)])
    out = []
    # 每片叶片：只处理被观测的截面所覆盖的纹素行
    row_sec = atlas.i0
    for cam, img, om in zip(cams, imgs, masks):
        if not om.any():
            continue
        R = rasterize(cam, V, faces, normals=nrm)
        if len(R.ys) == 0:
            continue
        R.zbuf = R.depth_map()
        inside = ((R.fid >= 0) & (om > 0)).astype(np.uint8)
        R.edge_dt = cv2.distanceTransform(inside, cv2.DIST_L2, 3)
        R.hub = rotor.hub
        R.rot_axis = rotor.n * (-rotor.cfg.spin)
        gimg = img.astype(np.float32)
        visible_blades = set(np.unique(R.blade).tolist())
        for b in range(3):
            # sample_view requires fid//F == b.  Without a single rasterized
            # pixel for this blade it can contribute no weight, so avoid
            # constructing its complete atlas surface and normal arrays.
            if b not in visible_blades:
                continue
            sb = seen[b]
            row_ok = sb[row_sec] & sb[np.minimum(row_sec + 1, len(sb) - 1)]
            if not row_ok.any():
                continue
            idx = np.nonzero(row_ok)[0]
            rows = slice(int(idx[0]), int(idx[-1]) + 1)
            r = sample_view(cam, gimg, om, R, V, nrm, b, atlas, rows, row_ok[rows], omega, exposure_s)
            if r is None:
                continue
            g, w, rs, rc = r
            out.append((cam.name, b, rows, g, w, rs, rc))
    return out


def validate_reconstruction_frames(R, rotor):
    """位姿结果的身份/时间是输入合同，不用结果在列表里的顺序冒充原帧号。"""
    last_frame, last_t = None, None
    for f in R['frames']:
        frame, t = f.get('frame'), f.get('t')
        if isinstance(frame, bool) or not isinstance(frame, int) or not isinstance(t, (int, float)) or not np.isfinite(t):
            raise ValueError('reconstruction frame requires integer source frame and finite timestamp')
        if last_frame is not None and (frame <= last_frame or t <= last_t):
            raise ValueError('reconstruction source frames/timestamps must be strictly increasing')
        state, seen = np.asarray(f.get('state'), float), np.asarray(f.get('observed_sections'))
        if state.shape != (13,) or not np.isfinite(state).all() or seen.shape != (3, rotor.cfg.n_sections):
            raise ValueError('invalid reconstruction state/observed_sections at frame %s' % frame)
        if not np.isin(seen, [0, 1, False, True]).all():
            raise ValueError('observed_sections must be binary at frame %s' % frame)
        last_frame, last_t = frame, t


def validate_saved_cameras(frame, cameras):
    """新结果附带逐帧标定时，拒绝在别的相机/RGB 身份上贴图；旧结果保持兼容。"""
    saved = frame.get('cameras')
    if saved is None:
        return
    if len(saved) != len(cameras):
        raise ValueError('reconstruction/input camera count mismatch at frame %s' % frame['frame'])
    for d, cam in zip(saved, cameras):
        if d.get('name') != cam.name or (d.get('W'), d.get('H')) != (cam.W, cam.H):
            raise ValueError('reconstruction/input camera identity mismatch at frame %s' % frame['frame'])
        T = np.eye(4); T[:3, :3], T[:3, 3] = cam.R, cam.t
        matrix = d.get('T_cv_from_model', d.get('T_cv_from_world'))
        K, saved_T = np.asarray(d.get('K'), float), np.asarray(matrix, float)
        if (K.shape != (3, 3) or saved_T.shape != (4, 4) or not np.isfinite(K).all()
                or not np.isfinite(saved_T).all() or not np.allclose(K, cam.K, rtol=0, atol=1e-8)
                or not np.allclose(saved_T, T, rtol=0, atol=1e-8)):
            raise ValueError('reconstruction/input per-frame calibration mismatch at frame %s' % frame['frame'])


def _provenance_path(path, root=None):
    path = Path(path)
    if root is not None and path.is_absolute():
        raise ValueError('provenance declared input path must be relative to the bundle')
    resolved = ((root/path) if root is not None else path).resolve()
    if root is not None and not resolved.is_relative_to(root):
        raise ValueError('provenance declared input path escapes bundle')
    if any(p.lower() in ('evaluation', 'eval') or p.lower().startswith('truth') or
           'heldout' in p.lower() or 'holdout' in p.lower() or 'object_id' in p.lower() or
           p.lower() == 'geometry.npz' or p.lower().startswith('source-surface') for p in resolved.parts):
        raise ValueError('forbidden solver sidecar provenance path: ' + str(resolved))
    return resolved


def validate_geometry_sidecar_provenance(saved, R, source):
    """Check feature sidecar identity only; never sample its pixels or rebind.

    A sparse texture pass has only selected RGB in memory. Read exactly the
    original allowlisted manifest for all frame/time/camera declarations, and
    compare sidecar RGB hashes to the solver's frozen full input access log.
    Unselected RGB is not opened/decoded/rehashed for this metadata check.
    Selected RGB/masks, unknown domains and healthy assets retain their usual
    independent current-file checks in validate_input_provenance.
    """
    if getattr(source, 'bundle', None) is None:
        raise ValueError('solver RGB sidecar provenance requires an explicit input bundle')
    if saved.get('role') != 'solver_rgb_observations':
        raise ValueError('invalid solver RGB sidecar provenance role')
    expected = saved.get('sha256')
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('solver RGB sidecar provenance requires a SHA-256')
    path = _provenance_path(saved['path'])
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('solver RGB sidecar provenance hash mismatch')
    if 'bytes' in saved and saved['bytes'] != len(content):
        raise ValueError('solver RGB sidecar provenance byte count mismatch')
    root = Path(source.root).resolve()
    manifest_path = _provenance_path('input_bundle.json', root)
    manifest_content = manifest_path.read_bytes()
    manifest_hash = hashlib.sha256(manifest_content).hexdigest()
    declared = R['input_access_log']
    manifest_entries = [a for a in declared if a.get('role') == 'input_manifest'
                        and a.get('path') == 'input_bundle.json']
    if not manifest_entries or any(a.get('sha256') != manifest_hash for a in manifest_entries):
        raise ValueError('solver RGB sidecar original manifest hash mismatch')
    manifest = json.loads(manifest_content)
    if manifest.get('schema') != 'blade-surface-input.v1':
        raise ValueError('solver RGB sidecar original manifest schema mismatch')
    rgb_entries = {a['path']: a for a in declared if a.get('role') == 'RGB'}
    for frame in manifest['frames']:
        for camera in frame['cameras']:
            _provenance_path(camera['rgb'], root)
            entry = rgb_entries.get(camera['rgb'])
            if entry is None or not isinstance(entry.get('sha256'), str):
                raise ValueError('solver RGB sidecar full declared RGB identity missing')
    # The existing geometry reader validates role/schema/pixel convention and
    # every anchor/target frame, time, camera and RGB hash. Only its constructor
    # is used: prepare()/bind_anchors()/residuals() are never called by texture.
    full_declaration = SimpleNamespace(bundle=SimpleNamespace(manifest=manifest,
        frame_ids=[f['frame'] for f in manifest['frames']]), access_log=declared)
    checked = RC.TextureGeometry(path, full_declaration, Rotor(source.cfg))
    if checked.access_log[0]['sha256'] != expected:
        raise ValueError('solver RGB sidecar changed during provenance validation')
    return {**checked.access_log[0], 'schema': 'blade-texture-geometry.v1',
            'purpose': 'reconstruction provenance only',
            'used_for_texture_sampling': False, 'used_for_surface_binding': False,
            'identity_frame_scope': 'original full declared manifest, including unselected frames',
            'unselected_RGB_file_bytes_checked': False,
            'planned_tracks': len(checked.tracks),
            'planned_observations': sum(len(t['observations']) for t in checked.tracks)}


def validate_input_provenance(R, source, identities):
    """新结果声明的 RGB/掩码文件身份和摘要必须仍匹配；旧结果无此声明时保持兼容。"""
    declared = R.get('input_access_log')
    if declared is None:
        return []
    current = {(a['path'], a['role']): a for a in source.access_log}
    selected_frames = {f['frame'] for f in identities}
    sidecars = []
    for saved in declared:
        if saved.get('role') == 'solver_rgb_observations':
            sidecars.append(validate_geometry_sidecar_provenance(saved, R, source))
            continue
        if saved['role'] == 'explicit_mask' and getattr(source, 'bundle', None) is None:
            stem = Path(saved['path']).stem
            if stem.isdigit() and int(stem) not in selected_frames:
                continue
        key = (saved['path'], saved['role'])
        if key in getattr(source, 'unselected_access_keys', set()):
            continue
        if key not in current:
            raise ValueError('reconstruction/input declared file identity mismatch: ' + str(saved['path']))
        if 'sha256' in saved and saved['sha256'] != current[key].get('sha256'):
            raise ValueError('reconstruction/input file hash mismatch: ' + str(saved['path']))
    contract = R.get('input_contract', {})
    for key, actual in (('mask_polarity', RC.POLARITY), ('min_contrast', RC.MIN_CONTRAST),
                        ('min_area_frac', RC.MIN_AREA_FRAC)):
        if key in contract and contract[key] != actual:
            raise ValueError('reconstruction/texture automatic mask parameter mismatch: ' + key)
    return sidecars


def input_sequence_for_texture(args, R):
    """Bound bundle preload by actual reconstruction identities, including sparse rows."""
    frames = R['frames'][:args.max_frames] if args.max_frames else R['frames']
    return InputSequence(args.data, masks_dir=args.masks, segmenter=RC.segment,
                         selected_frames=[f['frame'] for f in frames] or None)


def run_pass(args, rotor, atlas, R, ref=None, sequence=None):
    acc = Accum(atlas, mode=getattr(args, 'fusion', 'best'))
    faces = rotor.tpl.faces()
    source = sequence or input_sequence_for_texture(args, R)
    exposure_s = args.exposure_ms / 1000.0
    t0, nview = time.time(), 0
    shifts = []
    identities = []
    try:
        if source.cfg.to_dict() != rotor.cfg.to_dict():
            raise ValueError('reconstruction/input turbine configuration mismatch')
        validate_reconstruction_frames(R, rotor)
        for index, f in enumerate(R['frames']):
            if args.max_frames and index >= args.max_frames:
                break
            record = source.read_frame(f['frame'], f['t'])
            validate_saved_cameras(f, record.cameras)
            identities.append({'frame': record.frame, 't': record.t,
                               'cameras': [c.name for c in record.cameras],
                               'source_indices': record.source_indices,
                               'saved_calibration_checked': 'cameras' in f})
            if index < args.skip:
                continue
            seen = np.asarray(f['observed_sections'], bool)
            if (not seen.any() or f.get('reconstruction_valid') is False
                    or f.get('reconstruction_status') in ('no_observations', 'prior_prediction', 'unidentifiable', 'unavailable')):
                continue
            imgs = [cv2.cvtColor(i, cv2.COLOR_RGB2GRAY) for i in record.rgbs]
            st = np.asarray(f['state'], float)
            omega = f.get('rpm', 0.0) * 6.0
            for name, b, rows, g, w, rs, rc in frame_views(rotor, atlas, record.cameras, imgs,
                    record.masks, st, seen, omega, exposure_s, faces):
                val = normalize(g, w, atlas, rows)
                shift = (0, 0)
                if ref is not None:
                    rv, rm = ref
                    dy, dx, gain = register(val, w, rv[b, rows], rm[b, rows], atlas, rows)
                    shift = (dy, dx)
                    shifts.append({'frame': record.frame, 't': record.t, 'camera': name, 'blade': b + 1,
                                   'shift_span_m': round(dy * atlas.dr, 4),
                                   'shift_chord_m': round(float(dx * np.median(atlas.perim_rows[rows]) / atlas.W), 4),
                                   'ncc_gain': round(gain, 3)})
                acc.add(b, rows, val, g, w, rs, rc, shift)
                nview += 1
            if index % 10 == 0:
                print('  帧 %4d  累计视角 %d  (%.2f s/帧)' % (record.frame, nview,
                    (time.time() - t0) / (index + 1)), flush=True)
        sidecars = validate_input_provenance(R, source, identities)
        acc.input_contract = {'metadata': source.metadata, 'frames': identities, 'access_log': source.access_log + sidecars,
                              'reconstruction_input_provenance_checked': 'input_access_log' in R,
                              'masks': 'exact shared input masks; no implicit hole filling'}
        if sidecars:
            acc.input_contract['solver_sidecar_provenance'] = sidecars
    finally:
        source.close()
    return acc, shifts


def write_outputs(out_dir, atlas, acc, rotor, shifts, args):
    os.makedirs(out_dir, exist_ok=True)
    val, raw, has = acc.result(args.fusion)
    stats = []
    for b in range(3):
        a = (has[b] & (acc.wmax[b] > 0)).astype(np.uint8) * 255
        tex = np.clip(150.0 * val[b], 0, 255).astype(np.uint8)
        tex[a == 0] = 128
        bgra = cv2.merge([tex, tex, tex, a])
        cv2.imwrite(os.path.join(out_dir, 'blade%d_tex.png' % (b + 1)), bgra)
        rw = raw[b].copy()
        rw[a == 0] = 128
        cv2.imwrite(os.path.join(out_dir, 'blade%d_raw.png' % (b + 1)),
                    cv2.merge([np.clip(rw, 0, 255).astype(np.uint8)] * 3 + [a]))
        rs = np.nan_to_num(acc.res_s[b], nan=1.0) * 100            # 厘米/像素
        col = cv2.applyColorMap(np.clip(255 - rs / 20.0 * 255, 0, 255).astype(np.uint8), cv2.COLORMAP_TURBO)
        col[a == 0] = 90
        cv2.imwrite(os.path.join(out_dir, 'blade%d_res.png' % (b + 1)), col)
        # 逐 5 m 统计：覆盖率(吸力面) + 展向/弦向分辨率中位数
        bands = []
        suction = atlas.c_grid > 0
        for r_lo in range(0, 65, 5):
            rr = (atlas.r_rows >= r_lo) & (atlas.r_rows < r_lo + 5)
            if not rr.any():
                continue
            sel = has[b][rr] & suction[rr]
            cover = float(sel.sum() / max(suction[rr].sum(), 1))          # 占背风面的比例
            rsb, rcb = acc.res_s[b][rr][sel], acc.res_c[b][rr][sel]
            bands.append({'r_m': [r_lo, r_lo + 5], 'suction_cover': round(cover, 3),
                          'res_span_cm_median': round(float(np.nanmedian(rsb)) * 100, 2) if sel.any() else None,
                          'res_chord_cm_median': round(float(np.nanmedian(rcb)) * 100, 2) if sel.any() else None,
                          'views_median': int(np.median(acc.nview[b][rr][sel])) if sel.any() else 0})
        stats.append({'blade': b + 1, 'bands': bands})
    np.savez_compressed(os.path.join(out_dir, 'texture_data.npz'),
                        val=val.astype(np.float16), raw=raw.astype(np.float16), has=has,
                        res_s=acc.res_s.astype(np.float16), res_c=acc.res_c.astype(np.float16),
                        wmax=acc.wmax.astype(np.float16), nview=acc.nview)
    save_json(os.path.join(out_dir, 'texture.json'), {
        'atlas': atlas.meta(), 'n_ring': rotor.cfg.n_ring, 'n_sections': rotor.cfg.n_sections,
        'png': {'tex': '光照归一化后的表面细节，150 = 叶片平均亮度；A 通道 0 = 未观测',
                'raw': '原始亮度融合(含明暗)', 'res': '最佳视角展向分辨率，红 = 好(<2 cm/px)，蓝 = 差(≥20 cm/px)'},
        'params': {'pow': POW, 'edge_px': EDGE_PX, 'min_cos': MIN_COS, 'low_sigma_m': LOW_SIGMA_M,
                   'fusion': args.fusion, 'register': args.register, 'reg_range_m': REG_RANGE_M,
                   'exposure_ms': args.exposure_ms, 'skip': args.skip},
        'blades': stats, 'registration': shifts, 'input_contract': acc.input_contract})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--recon', required=True)
    ap.add_argument('--out', default=None)
    ap.add_argument('--masks', default=None)
    ap.add_argument('--polarity', choices=['dark', 'bright'], default='dark')
    ap.add_argument('--atlas-dr', type=float, default=0.025, help='图集展向纹素尺寸(米)')
    ap.add_argument('--atlas-cols', type=int, default=768, help='图集周向纹素数')
    ap.add_argument('--exposure-ms', type=float, default=0.0, help='相机曝光时间；给出时按运动模糊降权')
    ap.add_argument('--skip', type=int, default=0, help='跳过前若干帧(位姿还没稳定)')
    ap.add_argument('--max-frames', type=int, default=0)
    ap.add_argument('--fusion', choices=['best', 'mean'], default='best',
                    help='best = 每纹素取最清楚的视角(默认)；mean = 加权平均')
    ap.add_argument('--register', action='store_true',
                    help='第二遍：各视角先配准到第一遍结果再融合(耗时加倍；纹理特征少时基本不起作用)')
    args = ap.parse_args()
    RC.POLARITY = args.polarity
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.recon)), 'texture')

    R = load_json(args.recon)
    sequence = input_sequence_for_texture(args, R)
    rotor = Rotor(TurbineConfig.from_dict(R.get('turbine') or sequence.cfg.to_dict()))
    atlas = Atlas(rotor.tpl, dr=args.atlas_dr, n_cols=args.atlas_cols)
    print('图集 %d × %d 纹素(展向 %.1f cm)' % (atlas.H, atlas.W, atlas.dr * 100))

    t0 = time.time()
    print('第一遍：采样与融合')
    acc, _ = run_pass(args, rotor, atlas, R, sequence=sequence)
    shifts = []
    if args.register:
        val, _, has = acc.result(args.fusion)
        print('第二遍：逐视角配准到第一遍合成结果后重新融合')
        acc, shifts = run_pass(args, rotor, atlas, R, ref=(val, has))
    write_outputs(out_dir, atlas, acc, rotor, shifts, args)
    print('完成，用时 %.0f s → %s' % (time.time() - t0, out_dir))


if __name__ == '__main__':
    main()
