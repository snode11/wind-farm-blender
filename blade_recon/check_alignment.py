"""
check_alignment.py —— 坐标/相机/机组参数对齐检查（跑重建之前必须先过这一关）。

用场景里"已知"的方位角、桨距(以及可选的叶尖挠度)画出模型轮廓，叠加到视频帧上。
轮廓贴合叶片边缘 → cameras.json、机组参数、转向、方位角零点都对；
整体偏移/旋转/镜像 → 先排查坐标换算，不要直接跑 recon.py。

    python check_alignment.py --data <数据目录> --frame 0 --psi 37.5 --pitch 0 0 0 \
                              [--flap 3 3 3] [--edge 0 0 0] --out check.png
psi：叶片 1 的方位角(度)，0 = 竖直向上，从上风看顺时针为正(spin=+1 时)。
"""
import argparse
import os

import cv2
import numpy as np

from camera import Camera
from model import Rotor, TurbineConfig, N_STATE, load_json
from recon import FrameSource, Obs, segment, silhouettes, densify

COLORS = [(0, 0, 255), (0, 200, 0), (255, 120, 0)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--frame', type=int, default=0)
    ap.add_argument('--psi', type=float, required=True)
    ap.add_argument('--pitch', type=float, nargs=3, default=[0, 0, 0])
    ap.add_argument('--flap', type=float, nargs=3, default=[0, 0, 0])
    ap.add_argument('--edge', type=float, nargs=3, default=[0, 0, 0])
    ap.add_argument('--out', default='check_alignment.png')
    args = ap.parse_args()

    meta = load_json(os.path.join(args.data, 'cameras.json'))
    rotor = Rotor(TurbineConfig.from_dict(meta.get('turbine')))
    cams = [Camera.from_dict(d) for d in meta['cameras']]
    st = np.zeros(N_STATE)
    st[0] = args.psi; st[1:4] = args.pitch; st[4:7] = args.flap; st[7:10] = args.edge

    tiles, report = [], []
    for c in cams:
        src = FrameSource(args.data, c.name)
        img = None
        for _ in range(args.frame + 1):
            img = src.read()
        if img is None:
            raise SystemExit('%s 读不到第 %d 帧' % (c.name, args.frame))
        if img.shape[:2] != (c.H, c.W):
            report.append('%s：视频 %dx%d 与 cameras.json %dx%d 不一致！'
                          % (c.name, img.shape[1], img.shape[0], c.W, c.H))
        vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        sil = densify(silhouettes(rotor, c, st))
        o = Obs(segment(img))
        d = o.sample_dt(sil.reshape(-1, 2)) if o.has else np.array([])
        d = d[np.isfinite(d)]
        for b in range(3):
            for side in range(2):
                p = sil[b, :, side]; ok = np.isfinite(p).all(1)
                if ok.sum() > 1:
                    cv2.polylines(vis, [np.round(p[ok]).astype(np.int32)], False, COLORS[b], 2)
        if len(d):
            report.append('%s：模型轮廓 %d 点入画，到图像轮廓距离 中位 %.1f px，P90 %.1f px'
                          % (c.name, len(d), np.median(d), np.percentile(d, 90)))
        elif not o.has:
            report.append('%s：画面里没有分割出叶片' % c.name)
        else:
            report.append('%s：模型轮廓不在画面内' % c.name)
        cv2.putText(vis, c.name, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        tiles.append(vis)
    h = min(t.shape[0] for t in tiles)
    tiles = [cv2.resize(t, (int(t.shape[1] * h / t.shape[0]), h)) for t in tiles]
    cv2.imwrite(args.out, np.hstack(tiles))
    print('\n'.join(report))
    print('判读：中位距离 < 2 px 为对齐良好；> 10 px 多半是坐标系、转向或方位角零点有误。')
    print('叠加图 →', args.out, '（红/绿/蓝 = 叶片 1/2/3）')


if __name__ == '__main__':
    main()
