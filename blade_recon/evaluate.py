"""
evaluate.py —— 重建结果 vs 真值(仅合成/仿真数据有真值)。

三片叶片外形相同，重建的"叶片 1"可能对应真值的任一片：先按 120° 循环对齐编号再评分。
误差按截面是否被图像观测到(observed_sections)分开统计 —— 推断区的误差不能混进观测区。

    python evaluate.py --recon out/synth/recon.json --truth data/synth/truth.json
"""
import argparse

import numpy as np

from model import Rotor, TurbineConfig, load_json


def align(st_rec, st_true):
    """找使方位角误差最小的叶片循环移位 m：重建叶片 b ↔ 真值叶片 (b+m)%3。"""
    best = None
    for m in range(3):
        d = (st_rec[0] - (st_true[0] + 120 * m) + 180) % 360 - 180
        if best is None or abs(d) < abs(best[1]):
            best = (m, d)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recon', required=True)
    ap.add_argument('--truth', required=True)
    ap.add_argument('--skip', type=int, default=2, help='跳过前几帧(初始化)')
    args = ap.parse_args()
    R = load_json(args.recon); T = load_json(args.truth)
    rotor = Rotor(TurbineConfig.from_dict(R['turbine']))
    tf = {f['frame']: np.array(f['state']) for f in T['frames']}

    psi_e, rows = [], {'obs': [], 'inf': []}
    par = {'pitch': ([], []), 'flap': ([], []), 'edge': ([], [])}
    tip = ([], [])
    for f in R['frames'][args.skip:]:
        sr, stt = np.array(f['state']), tf[f['frame']]
        m, dpsi = align(sr, stt)
        psi_e.append(dpsi)
        # 把真值叶片顺序转成与重建一致
        st_al = stt.copy()
        st_al[0] = stt[0] + 120 * m
        for g in range(4):
            blk = stt[1 + 3 * g: 4 + 3 * g]
            st_al[1 + 3 * g: 4 + 3 * g] = np.roll(blk, -m)
        Vr, Ar = rotor.forward(sr)
        Vt, At = rotor.forward(st_al)
        seen = np.array(f['observed_sections'], bool)          # (3,S)
        err = np.linalg.norm(Vr - Vt, axis=-1).mean(-1)        # (3,S) 截面平均顶点误差
        rows['obs'].extend(err[seen].tolist())
        rows['inf'].extend(err[~seen].tolist())
        for b in range(3):
            vis_b = seen[b].any()
            k = 0 if vis_b else 1
            par['pitch'][k].append(sr[1 + b] - st_al[1 + b])
            par['flap'][k].append(sr[4 + b] - st_al[4 + b])
            par['edge'][k].append(sr[7 + b] - st_al[7 + b])
            tip[0 if seen[b, -1] else 1].append(np.linalg.norm(Ar[b, -1] - At[b, -1]))

    def st(x):
        x = np.abs(np.asarray(x))
        return ('n=%4d  均值 %.3f  P95 %.3f  最大 %.3f' % (len(x), x.mean(), np.percentile(x, 95), x.max())
                if len(x) else 'n=   0')

    print('评分帧数 %d（跳过前 %d 帧）' % (len(psi_e), args.skip))
    print('方位角误差(°)            ', st(psi_e))
    for k, name in (('pitch', '桨距误差(°)'), ('flap', '挥舞幅值误差(m)'), ('edge', '摆振幅值误差(m)')):
        print('%-16s 叶片可见 %s' % (name, st(par[k][0])))
        print('%-16s 叶片不可见 %s' % ('', st(par[k][1])))
    print('叶尖位置误差(m)  叶尖被观测 %s' % st(tip[0]))
    print('                 叶尖为推断 %s' % st(tip[1]))
    print('截面网格误差(m)  观测区     %s' % st(rows['obs']))
    print('                 推断区     %s' % st(rows['inf']))
    n_obs, n_inf = len(rows['obs']), len(rows['inf'])
    print('观测区截面占比 %.1f%%' % (100.0 * n_obs / max(n_obs + n_inf, 1)))


if __name__ == '__main__':
    main()
