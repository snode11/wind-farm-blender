"""
eval_texture.py —— 有真值时给纹理重建打分(合成数据用)，并出对比图。

对每个已知缺陷(truth.json 的 "defects")：
  · 覆盖：缺陷区域在重建纹理里是否被看到过；
  · 对比度：缺陷区平均值相对周围的变化，重建 / 真值(1 = 完全保留)；
  · 定位误差：在缺陷附近 ±1 m 内找重建纹理的异常质心，与真值中心比较(展向 / 弦向，米)；
  · 出图：真值纹理 | 重建纹理 | 最佳视角展向分辨率，同一窗口并排。

    python eval_texture.py --recon out/synth_tex/recon.json --truth data/synth_tex/truth.json \
                           --truth-tex data/synth_tex/truth_texture.npz [--tex out/synth_tex/texture]
"""
import argparse
import json
import os

import cv2
import numpy as np

from atlas import Atlas, validate_atlas_meta
from evaluate import align
from model import Rotor, TurbineConfig, load_json, save_json
from raster import sample


def truth_atlas_metadata(texture_archive, truth, rotor):
    """优先使用已保存真值坐标；仅原默认尺寸的旧包允许明确推定默认合同。"""
    albedo, height = texture_archive['albedo'], texture_archive['height']
    if albedo.ndim != 3 or albedo.shape[0] != 3 or height.shape != albedo.shape:
        raise ValueError('truth texture requires equal (3,H,W) albedo and height arrays')
    embedded = None
    if 'atlas_meta' in texture_archive:
        try:
            embedded = json.loads(texture_archive['atlas_meta'].item())
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('truth_texture.npz atlas_meta is invalid JSON') from exc
    sidecar = truth.get('atlas')
    if embedded is not None and sidecar is not None and embedded != sidecar:
        raise ValueError('truth atlas metadata differs between NPZ and truth.json')
    meta = embedded if embedded is not None else sidecar
    origin = 'embedded_npz' if embedded is not None else 'truth_json'
    if meta is None:
        # 旧 synth 交付使用默认模板；不能从另一个重建模型推定真值表面。
        source_rotor = Rotor(TurbineConfig.from_dict(truth.get('turbine')))
        meta = Atlas(source_rotor.tpl).meta()
        origin = 'legacy_default_inferred'
        if tuple(albedo.shape[-2:]) != (meta['H'], meta['W']):
            raise ValueError('non-default truth atlas requires saved atlas metadata')
    meta = validate_atlas_meta(meta, albedo.shape)
    if 'radii_m' not in meta:
        # 初期元数据只记录 r/dr；与同一模板范围匹配后补齐其周线坐标。
        source_rotor = Rotor(TurbineConfig.from_dict(truth.get('turbine')))
        known = Atlas(source_rotor.tpl).meta()
        if not np.allclose([meta['r0'], meta['r1']], [known['r0'], known['r1']]):
            raise ValueError('truth atlas needs saved physical surface mapping for this span')
        meta.update({k: known[k] for k in ('radii_m', 'perimeter_m', 'tau_le')})
    return meta, origin


def resample_truth_to_atlas(field, source_meta, target):
    """真值在重建纹素的物理 (r,c 米) 位置采样，尺寸不同也不复用行列下标。"""
    source_meta = validate_atlas_meta(source_meta, field.shape)
    if field.ndim != 3 or field.shape[0] != 3:
        raise ValueError('truth field must have shape (3,H,W)')
    if target.r0 < source_meta['r0'] - 1e-8 or target.r1 > source_meta['r1'] + 1e-8:
        raise ValueError('reconstruction atlas extends outside truth physical span')
    if 'radii_m' not in source_meta:
        raise ValueError('truth atlas physical surface mapping is required')
    radii = np.asarray(source_meta['radii_m'])
    per = np.interp(target.r_rows, radii, source_meta['perimeter_m'])
    le = np.interp(target.r_rows, radii, source_meta['tau_le'])
    rows = np.broadcast_to(((target.r_rows - source_meta['r0']) / source_meta['dr'] - 0.5)[:, None],
                           (target.H, target.W))
    tau = le[:, None] + target.c_grid / per[:, None]
    cols = tau * source_meta['W'] - 0.5
    return np.stack([sample(a.astype(np.float32), rows, cols) for a in field])


def window(atlas, r, c, half_r=1.0, half_c=0.8):
    row, col = atlas.row_of_r(r), atlas.col_of_c(r, c)
    per = float(np.interp(r, atlas.tpl.r, atlas.perim_v))
    hr, hc = int(half_r / atlas.dr), int(half_c / per * atlas.W)
    r0, r1 = int(max(row - hr, 0)), int(min(row + hr + 1, atlas.H))
    cols = np.arange(int(col) - hc, int(col) + hc + 1) % atlas.W
    return slice(r0, r1), cols, per


def anomaly(img, has, d, atlas, rows, cols):
    """缺陷响应：相对局部中位数的偏差；鼓包看梯度(明暗)而不是亮度。"""
    x = img[rows][:, cols].astype(np.float32)
    h = has[rows][:, cols]
    med = np.median(x[h]) if h.any() else 1.0
    a = np.where(h, x - med, 0.0)
    if d['type'] == 'bump':
        a = np.where(h, cv2.GaussianBlur(x, (0, 0), 3) - med, 0.0)
    return a, med


def centroid(a, sign, atlas, rows, cols, per):
    s = np.clip(sign * a, 0, None)
    thr = 0.5 * s.max() if s.max() > 0 else 1
    s = np.where(s >= thr, s, 0)
    if s.sum() <= 0:
        return None
    yy, xx = np.mgrid[0:s.shape[0], 0:s.shape[1]]
    cy, cx = (s * yy).sum() / s.sum(), (s * xx).sum() / s.sum()
    r = atlas.r0 + (rows.start + cy + 0.5) * atlas.dr
    c = (cx - (len(cols) - 1) / 2) * per / atlas.W
    return r, c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recon', required=True)
    ap.add_argument('--truth', required=True)
    ap.add_argument('--truth-tex', required=True)
    ap.add_argument('--tex', default=None)
    ap.add_argument('--fig', default=None)
    args = ap.parse_args()
    tex_dir = args.tex or os.path.join(os.path.dirname(os.path.abspath(args.recon)), 'texture')
    R, T = load_json(args.recon), load_json(args.truth)
    rotor = Rotor(TurbineConfig.from_dict(R['turbine']))
    tj = load_json(os.path.join(tex_dir, 'texture.json'))
    atlas = Atlas(rotor.tpl, dr=tj['atlas']['dr'], n_cols=tj['atlas']['W'])
    D = np.load(os.path.join(tex_dir, 'texture_data.npz'))
    val, has, res_s = D['val'].astype(np.float32), D['has'], D['res_s'].astype(np.float32)
    validate_atlas_meta(tj['atlas'], val.shape)
    if val.shape != (3, atlas.H, atlas.W) or has.shape != val.shape or res_s.shape != val.shape:
        raise ValueError('reconstruction texture arrays differ from atlas metadata')
    if not np.allclose([tj['atlas']['r0'], tj['atlas']['r1']], [atlas.r0, atlas.r1]):
        raise ValueError('reconstruction atlas metadata differs from turbine physical span')
    for key in ('radii_m', 'perimeter_m', 'tau_le'):
        if key in tj['atlas']:
            declared, actual = np.asarray(tj['atlas'][key], float), np.asarray(atlas.meta()[key], float)
            if declared.shape != actual.shape or not np.allclose(declared, actual, rtol=0, atol=1e-8):
                raise ValueError('reconstruction atlas physical surface mapping differs from turbine: ' + key)
    TT = np.load(args.truth_tex)
    truth_meta, truth_meta_origin = truth_atlas_metadata(TT, T, rotor)
    alb = resample_truth_to_atlas(TT['albedo'], truth_meta, atlas)
    hgt = resample_truth_to_atlas(TT['height'], truth_meta, atlas)
    tf = {f['frame']: f for f in T['frames']}
    f0 = R['frames'][len(R['frames']) // 2]
    m, _ = align(np.array(f0['state']), np.array(tf[f0['frame']]['state']))

    rows_out, tiles = [], []
    print('%-4s %-34s %6s %8s %10s %10s' % ('编号', '缺陷', '覆盖', '对比保留', '展向误差m', '弦向误差m'))
    for d in T['defects']:
        bt = d['blade']
        br = (bt - m) % 3                                  # 重建编号
        rows, cols, per = window(atlas, d['r'], d['c'])
        cover_mask = np.zeros((rows.stop - rows.start, len(cols)), bool)
        rr = atlas.r_rows[rows][:, None]
        cc = atlas.c_grid[rows][:, cols]
        core = (np.abs(rr - d['r']) <= max(d['span'], 0.05) / 2) & (np.abs(cc - d['c']) <= max(d['chord'], 0.05) / 2)
        cover = float(has[br][rows][:, cols][core].mean()) if core.any() else 0.0
        if d['type'] == 'bump':
            # 真值只有高度图：用重建纹理的明暗变化判断能否看出，不比较亮度
            ta = np.gradient(hgt[bt][rows][:, cols], axis=1)
            sign = 0
            contrast = float(np.abs(anomaly(val[br], has[br], d, atlas, rows, cols)[0])[core].mean())
            keep = None
        else:
            ta, tmed = anomaly(alb[bt] / 0.95, np.ones_like(has[br]), d, atlas, rows, cols)
            ra, rmed = anomaly(val[br], has[br], d, atlas, rows, cols)
            sign = np.sign(d.get('albedo', 1.0) - 0.95)
            t_c = float((ta[core]).mean()); r_c = float((ra[core]).mean())
            keep = r_c / t_c if abs(t_c) > 1e-6 else None
            contrast = r_c
        loc = None
        if cover > 0.5 and sign != 0:
            ra, _ = anomaly(val[br], has[br], d, atlas, rows, cols)
            c_rec = centroid(ra, sign, atlas, rows, cols, per)
            ta2, _ = anomaly(alb[bt] / 0.95, np.ones_like(has[br]), d, atlas, rows, cols)
            c_tru = centroid(ta2, sign, atlas, rows, cols, per)
            if c_rec and c_tru:
                loc = (c_rec[0] - c_tru[0], c_rec[1] - c_tru[1])
        rs = float(np.nanmedian(res_s[br][rows][:, cols][core])) if cover > 0 else float('nan')
        rows_out.append({'id': d['id'], 'name': d['name'], 'blade_true': bt + 1, 'blade_recon': br + 1,
                         'cover': round(cover, 3), 'contrast_keep': None if keep is None else round(keep, 3),
                         'err_span_m': None if loc is None else round(loc[0], 3),
                         'err_chord_m': None if loc is None else round(loc[1], 3),
                         'res_span_cm': round(rs * 100, 1) if np.isfinite(rs) else None})
        print('%-4s %-34s %5.0f%% %8s %10s %10s   展向分辨率 %s cm/px' % (
            d['id'], d['name'], 100 * cover, '—' if keep is None else '%.2f' % keep,
            '—' if loc is None else '%+.3f' % loc[0], '—' if loc is None else '%+.3f' % loc[1],
            '—' if not np.isfinite(rs) else '%.1f' % (rs * 100)))
        # 对比图
        def up(x, k=3):
            return cv2.resize(x, (x.shape[1] * k, x.shape[0] * k), interpolation=cv2.INTER_NEAREST)
        if d['type'] == 'bump':
            g = np.gradient(hgt[bt][rows][:, cols], axis=1) / (per / atlas.W)
            tv = np.clip(150 + 1500 * g, 0, 255)
        else:
            tv = np.clip(150 * alb[bt][rows][:, cols] / 0.95, 0, 255)
        rv = np.where(has[br][rows][:, cols], np.clip(150 * val[br][rows][:, cols], 0, 255), 128)
        tiles.append((d, up(tv.astype(np.uint8)), up(rv.astype(np.uint8))))
    save_json(os.path.join(tex_dir, 'eval_texture.json'), {
        'blade_shift': int(m), 'defects': rows_out,
        'truth_metadata_origin': truth_meta_origin, 'truth_atlas': truth_meta,
        'evaluation_atlas': atlas.meta(), 'alignment': 'resampled at reconstruction physical r,c coordinates (m)'})
    if args.fig:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DengXian']
        n = len(tiles)
        fig, axs = plt.subplots(2, n, figsize=(2.6 * n, 6.2))
        for j, (d, tv, rv) in enumerate(tiles):
            o = rows_out[j]
            axs[0, j].imshow(tv, cmap='gray', vmin=0, vmax=255)
            axs[0, j].set_title('%s 真值\n%s' % (d['id'], d['name']), fontsize=8)
            axs[1, j].imshow(rv, cmap='gray', vmin=0, vmax=255)
            sub = '覆盖 %.0f%%' % (100 * o['cover'])
            if o['err_span_m'] is not None:
                sub += '\n定位误差 展%+.0f / 弦%+.0f cm' % (100 * o['err_span_m'], 100 * o['err_chord_m'])
            axs[1, j].set_title('重建纹理  ' + sub, fontsize=8)
            for ax in axs[:, j]:
                ax.set_xticks([]); ax.set_yticks([])
        axs[0, 0].set_ylabel('↑叶根  展向 2 m  叶尖↓', fontsize=8)
        fig.suptitle('叶片表面图集窗口(展向 2 m × 沿表面弦向 1.6 m)：上 = 真值，下 = 由视频反投影融合', fontsize=10)
        fig.savefig(args.fig, bbox_inches='tight', dpi=130)
        print('图 →', args.fig)


if __name__ == '__main__':
    main()
