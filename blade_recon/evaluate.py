"""
evaluate.py —— 重建结果 vs 真值(仅合成/仿真数据有真值)。

三片叶片外形相同，重建的"叶片 1"可能对应真值的任一片：先按 120° 循环对齐编号再评分。
误差按截面是否被图像观测到(observed_sections)分开统计 —— 推断区的误差不能混进观测区。
评分模式为 same-model-state：两份输入须独立声明完整且相同的 turbine 模型身份。
缺少身份的旧真值须用 --truth-turbine 指定独立真值配置；不从重建输入推导真值模型。
不同模型的网格对应关系不受此评分器支持，必须拒绝评分。

    python evaluate.py --recon out/synth/recon.json --truth data/synth/truth.json
"""
import argparse
from dataclasses import fields

import numpy as np

from model import N_STATE, Rotor, TurbineConfig, load_json


def align(st_rec, st_true):
    """找使方位角误差最小的叶片循环移位 m：重建叶片 b ↔ 真值叶片 (b+m)%3。"""
    best = None
    for m in range(3):
        d = (st_rec[0] - (st_true[0] + 120 * m) + 180) % 360 - 180
        if best is None or abs(d) < abs(best[1]):
            best = (m, d)
    return best


def turbine_identity(payload, label):
    """Validate the full fixed model identity without filling missing dimensions."""
    defaults = TurbineConfig().to_dict()
    allowed = {field.name for field in fields(TurbineConfig)}
    message = label + ' turbine identity must contain a complete valid model configuration'
    if not isinstance(payload, dict) or not set(defaults) <= set(payload) or set(payload) - allowed:
        raise ValueError(message)
    for key, default in defaults.items():
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise ValueError(message + ': ' + key)
        if isinstance(default, int) and int(value) != value:
            raise ValueError(message + ': ' + key)
    try:
        cfg = TurbineConfig.from_dict(payload)
        if (cfg.hub_radius_m < 0 or cfg.tip_radius_m <= cfg.hub_radius_m
                or cfg.n_sections < 2 or cfg.n_ring < 4 or cfg.n_ring % 2
                or cfg.spin not in (-1, 1)):
            raise ValueError('unsupported dimensions or spin')
        # The constructor also verifies an explicit fixed healthy template.
        Rotor(cfg)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(message + ': ' + str(exc)) from exc
    return cfg


def frame_index(payload, label, n_sections=None):
    """Require unique sample IDs, complete finite states and aligned visibility."""
    if not isinstance(payload, dict) or not isinstance(payload.get('frames'), list):
        raise ValueError(label + ' frames must be a list')
    result = {}
    for frame in payload['frames']:
        if not isinstance(frame, dict):
            raise ValueError(label + ' frame must be an object')
        index = frame.get('frame')
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError(label + ' frame identity must be a nonnegative integer')
        if index in result:
            raise ValueError(label + ' duplicate frame: ' + str(index))
        message = label + ' frame %d state must be %d finite values' % (index, N_STATE)
        try:
            raw = np.asarray(frame.get('state'))
            state = np.asarray(raw, dtype=float)
            if raw.dtype.kind not in 'fiu' or state.shape != (N_STATE,) or not np.isfinite(state).all():
                raise ValueError(message)
        except (ValueError, TypeError) as exc:
            raise ValueError(message) from exc
        seen = None
        if n_sections is not None:
            message = label + ' frame %d observed_sections must have shape (3, %d) and boolean or 0/1 values' % (index, n_sections)
            try:
                seen = np.asarray(frame.get('observed_sections'))
                if (seen.shape != (3, n_sections) or seen.dtype.kind not in 'biu'
                        or not ((seen == 0) | (seen == 1)).all()):
                    raise ValueError(message)
            except (ValueError, TypeError) as exc:
                raise ValueError(message) from exc
            seen = seen.astype(bool)
        result[index] = (state, seen)
    return result



def frame_times(payload, label):
    """Validate supplied seconds independently of frame IDs and nominal FPS."""
    result = {}
    for frame in payload['frames']:
        if 't' not in frame:
            continue
        value = frame['t']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise ValueError(label + ' frame %d t must be finite seconds' % frame['frame'])
        result[frame['frame']] = float(value)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recon', required=True)
    ap.add_argument('--truth', required=True)
    ap.add_argument('--skip', type=int, default=2, help='跳过前几帧(初始化)')
    ap.add_argument('--mode', choices=['same-model-state'], default='same-model-state',
                    help='同一固定模型下的状态与对应网格误差；不支持跨模型网格评分')
    ap.add_argument('--truth-turbine', help='缺少 turbine 的旧真值所用独立配置 JSON（完整配置或含 turbine 的对象）')
    args = ap.parse_args()
    R = load_json(args.recon); T = load_json(args.truth)
    try:
        if args.skip < 0:
            raise ValueError('skip must be nonnegative')
        rec_cfg = turbine_identity(R.get('turbine'), 'reconstruction')
        truth_config = T.get('turbine')
        if args.truth_turbine:
            supplied = load_json(args.truth_turbine)
            supplied = supplied.get('turbine', supplied) if isinstance(supplied, dict) else supplied
            supplied_cfg = turbine_identity(supplied, 'truth')
            if truth_config is not None and turbine_identity(truth_config, 'truth').to_dict() != supplied_cfg.to_dict():
                raise ValueError('truth turbine identity conflicts with --truth-turbine')
            truth_config = supplied
        truth_cfg = turbine_identity(truth_config, 'truth')
        if rec_cfg.to_dict() != truth_cfg.to_dict():
            raise ValueError('model identity mismatch: same-model-state scoring requires identical independent turbine configurations; cross-model correspondence is unsupported')
        rf = frame_index(R, 'reconstruction', rec_cfg.n_sections)
        tf = frame_index(T, 'truth')
        rt = frame_times(R, 'reconstruction')
        tt = frame_times(T, 'truth')
        selected = R['frames'][args.skip:]
        if not selected:
            raise ValueError('no reconstruction frames remain after skip')
        for frame in selected:
            if frame['frame'] not in tf:
                raise ValueError('missing truth frame: ' + str(frame['frame']))
            index = frame['frame']
            if index in rt and index in tt and not np.isclose(rt[index], tt[index], rtol=0, atol=1e-9):
                raise ValueError('time identity mismatch for frame: ' + str(index))
    except ValueError as exc:
        ap.error(str(exc))
    rotor = Rotor(rec_cfg)

    psi_e, rows = [], {'obs': [], 'inf': []}
    par = {'pitch': ([], []), 'flap': ([], []), 'edge': ([], [])}
    tip = ([], [])
    for f in selected:
        sr, seen = rf[f['frame']]
        stt, _ = tf[f['frame']]
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

    print('评分模式 same-model-state（独立且相同的固定模型）')
    verified = sum(f['frame'] in rt and f['frame'] in tt for f in selected)
    print('时间配对：显式时间已验证 %d 帧；帧身份配对、时间未验证 %d 帧' % (verified, len(selected) - verified))
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
