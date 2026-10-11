"""
coverage.py —— 不渲染，只做几何：给定三摄安装，叶片每个展向位置在一圈里有多少比例可见。

    python coverage.py --back 6 --down 3 --pitch-down 0
输出：展向 × 方位角 的可见表(任一相机入画即算可见，不含遮挡)，以及每段展向的可见方位角比例。
用来在跑重建之前回答"这个安装位置能看到叶片的哪一段、在哪些方位角"。
"""
import argparse

import numpy as np

from camera import build_rig
from model import Rotor, TurbineConfig, default_state


def coverage(rotor, cams, n_psi=360, margin=0.0):
    psis = np.arange(n_psi) * 360.0 / n_psi
    r = rotor.tpl.r
    vis = np.zeros((len(r), n_psi), bool)
    pix = np.full((len(r), n_psi), np.nan)          # 该截面弦长在像面上的最大像素数
    for k, p in enumerate(psis):
        _, A = rotor.forward(default_state(psi=p))
        ax = A[0]                                    # 只看叶片 1，其余叶片相差 120° 等价
        for c in cams:
            uv, z = c.project(ax)
            ok = ((uv[:, 0] >= margin) & (uv[:, 0] < c.W - margin)
                  & (uv[:, 1] >= margin) & (uv[:, 1] < c.H - margin))
            ok &= np.isfinite(uv[:, 0])
            vis[ok, k] = True
            fpx = c.K[0, 0] * rotor.tpl.chord / np.maximum(z, 1e-6)
            pix[ok, k] = np.fmax(pix[ok, k], fpx[ok])
    return psis, r, vis, pix


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--back', type=float, default=6.0)
    ap.add_argument('--down', type=float, default=3.0)
    ap.add_argument('--pitch-down', type=float, default=0.0)
    ap.add_argument('--yaw-step', type=float, default=50.0)
    args = ap.parse_args()
    rotor = Rotor(TurbineConfig())
    cams = build_rig(rotor, args.back, args.down, args.pitch_down, args.yaw_step)
    psis, r, vis, pix = coverage(rotor, cams)
    print('安装：轮毂后 %.1f m、下 %.1f m，下俯 %.0f°，三路偏航 ±%.0f°'
          % (args.back, args.down, args.pitch_down, args.yaw_step))
    print('%8s %10s %22s %12s' % ('展向r(m)', '可见比例', '可见方位角范围(°,0=上)', '弦长像素'))
    for i in range(0, len(r), 3):
        frac = vis[i].mean()
        if frac > 0:
            ps = psis[vis[i]]
            rng = '%5.0f ~ %5.0f' % (ps.min(), ps.max()) if len(ps) else '-'
            print('%8.1f %9.0f%% %22s %12.0f' % (r[i], 100 * frac, rng, np.nanmax(pix[i])))
        else:
            print('%8.1f %9s %22s %12s' % (r[i], '0%', '-', '-'))
    full = vis.any(1)
    print('一圈中至少可见一次的最外截面：r = %.1f m' % (r[full].max() if full.any() else float('nan')))


if __name__ == '__main__':
    main()
