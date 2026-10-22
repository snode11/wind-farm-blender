"""一次 FAST.Farm 跑，解掉柔性叶片可视化的三个未知数。

跑法（必须经官方 mpiexec，见 memory: wfcrl-fastfarm-bringup）：
    & "C:\\Program Files\\Microsoft MPI\\Bin\\mpiexec.exe" -n 1 python \
        -m scripts.experiments.calib_blade_flex

产出（都落 results/runs/blade_flex/）：
  1. calib.json   弯矩 → 叶尖挠度的线性系数（挥舞/摆振各一个），供渲染器直接用
  2. 终端表格     MPI 回传的 load 六元组 vs 磁盘 RootMyc/RootMxc 的量级与相关性
                  —— 核对 avrSWAP(30-32)/(69-71) 的语义是否如 Bladed 标准所述
  3. 终端一行     reward 两项 mean(P/u^3) 与 mean(|load|) 的实测量级，定 load_coef

为什么标定要走磁盘而不是自己推：模态坐标与根部弯矩的关系要
∫EI(r)·φ''(r)^2 dr，还牵扯 ElastoDyn 内部的模态归一化与离心刚化，自己推容易
差个系数。RootMyc1 与 OoPDefl1 是同一个 .out 文件的同一行，直接回归，斜率就是
要的东西，不用管它内部怎么归一化。

注意 OutList 通道是往模板
`inputs/template/FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat`
里加的，DT_Out 从 3 s 改到 0.25 s（转子 ~9 rpm，3P≈0.45 Hz，0.25 s 够采）。
"""
import glob
import json
import os
import time

import numpy as np

from wfrl import paths
from wfrl.fastfarm_driver import FastFarmDriver

OUT_DIR = os.path.join(paths.RUNS, "blade_flex")
WIND_SPEED = 8.0        # 钉死来流，否则 reset 会重抽（见 memory: wfcrl-inflow-control）
MAX_STEPS = 60          # 60 × 3 s = 180 s 仿真，够采 ~27 个转子周期


# ---------------------------------------------------------------------------
# OpenFAST 文本输出（.out）解析
# ---------------------------------------------------------------------------
def read_fast_out(path):
    """读 OpenFAST 的 .out 文本，返回 {通道名: (N,) array}。

    格式：6~8 行文件头 → 通道名行 → 单位行 → 数据行（制表符分隔）。
    通道名行的判据是「以 Time 起头且后面还有别的列」。
    """
    with open(path, "r", encoding="latin-1") as f:
        lines = f.read().splitlines()
    hdr = None
    for i, ln in enumerate(lines):
        tok = ln.split()
        if tok and tok[0] == "Time" and len(tok) > 1:
            hdr = i
            break
    if hdr is None:
        raise ValueError(f"{path}: 找不到通道名行")
    names = lines[hdr].split()
    rows = []
    for ln in lines[hdr + 2:]:                 # +2 跳过单位行
        tok = ln.split()
        if len(tok) != len(names):
            continue
        try:
            rows.append([float(t) for t in tok])
        except ValueError:
            continue
    if not rows:
        raise ValueError(f"{path}: 没有数据行")
    arr = np.asarray(rows, dtype=float)
    return {nm: arr[:, j] for j, nm in enumerate(names)}


def find_out_files(case_dir):
    """定位本次算例每台机组的 .out。FAST.Farm 把它们放在 FarmInputs/ 下。"""
    pats = [os.path.join(case_dir, "FarmInputs", "*.out"),
            os.path.join(case_dir, "*.out")]
    got = []
    for p in pats:
        got.extend(sorted(glob.glob(p)))
        if got:
            break
    return got


# ---------------------------------------------------------------------------
# 弯矩 → 挠度 的线性标定
# ---------------------------------------------------------------------------
def fit_affine(x, y):
    """仿射最小二乘 δ = k·M + b，返回 (k, b, R², k_origin, R²_origin)。

    为什么要截距（一开始我按"零弯矩必须零挠度"写成过原点，实测被否掉）：
      - 8 m/s 下叶片被推力静态压弯，叶尖挠度均值约 -1 m 量级，这是**真实的**
        静态变形，不是拟合瑕疵；重力还额外贡献一个随方位角走的分量。
      - RootMyc/RootMxc 是锥形坐标系下的**根部**弯矩，含重力与离心项，
        和 OoPDefl 之间本就不是同一根弹簧的纯比例关系。
      - 实测：过原点 flap R²=0.89 / edge R²=0.58，带截距 0.95 / 0.995。
    运行区间离 M=0 很远（p95 弯矩 ~5900 kN·m），所以"截距导致零载时残余变形"
    在实际用到的区间里不会出现，模型只在观测范围内取值。

    R² 用**相对均值**的定义。原来写的是 1 - SSres/Σy²（相对 0），当 y 的均值
    远离 0 时会把 R² 抬到 0.999 的假高分 —— 就是它掩盖了过原点其实拟合很差。
    """
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if x.size < 10 or not np.any(x):
        nan = float("nan")
        return nan, nan, nan, nan, nan

    def r2_of(pred):
        ss_tot = float(((y - y.mean()) ** 2).sum())
        return (1.0 - float(((y - pred) ** 2).sum()) / ss_tot
                if ss_tot > 0 else float("nan"))

    A = np.stack([x, np.ones_like(x)], 1)
    (k, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    k0 = float(x @ y / (x @ x))
    return float(k), float(b), r2_of(A @ [k, b]), k0, r2_of(k0 * x)


def calibrate(ch):
    """三片叶合并回归，挥舞/摆振各得一个 m/(N·m) 系数。

    三片叶共用一条系数是对的：同一支叶片、同一套模态，弯矩不同只是因为
    方位角不同。合并回归等于把 3 倍样本喂给同一个拟合，还顺便验证了
    "三片叶一致" 这个前提（若 R^2 很低就说明前提不成立）。
    """
    out = {}
    for tag, m_key, d_key in (("flap", "RootMyc", "OoPDefl"),
                              ("edge", "RootMxc", "IPDefl")):
        xs, ys = [], []
        for b in (1, 2, 3):
            mk, dk = f"{m_key}{b}", f"{d_key}{b}"
            if mk in ch and dk in ch:
                xs.append(ch[mk] * 1e3)        # OpenFAST 输出 kN·m → N·m
                ys.append(ch[dk])              # m
        if not xs:
            out[tag] = {"ok": False, "note": f"缺 {m_key}/{d_key} 通道"}
            continue
        x, y = np.concatenate(xs), np.concatenate(ys)
        k, b, r2, k0, r2_0 = fit_affine(x, y)
        out[tag] = {
            "ok": True, "k_m_per_Nm": k, "b_m": b, "r2": r2,
            "k_origin_m_per_Nm": k0, "r2_origin": r2_0, "n": int(x.size),
            "moment_Nm_p95": float(np.nanpercentile(np.abs(x), 95)),
            "defl_m_p95": float(np.nanpercentile(np.abs(y), 95)),
        }
    return out


# ---------------------------------------------------------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    print(f"[calib] 启动 FAST.Farm（3T，dt=3s，钉死 {WIND_SPEED} m/s）…",
          flush=True)
    drv = FastFarmDriver("Dec_Turb3_Row1_Fastfarm", max_steps=MAX_STEPS,
                         controls=("yaw",))
    m = drv.reset(wind_speed=WIND_SPEED)
    case_dir = os.path.dirname(os.path.dirname(
        drv.env.mdp.interface._simul_file))
    print(f"[calib] reset OK ({time.time()-t0:.1f}s)  u_inf={drv.u_inf:.2f} m/s\n"
          f"        case_dir={case_dir}", flush=True)

    # MPI 侧时序：每步记下六元组，之后和磁盘对量级
    mpi_flap, mpi_edge, powers = [], [], []
    while not m["done"]:
        m = drv.step(np.zeros(drv.n))          # 零动作：只看自由响应，不掺控制
        mpi_flap.append(m["m_flap"].copy())
        mpi_edge.append(m["m_edge"].copy())
        powers.append(m["power"].copy())
        if m["step"] % 10 == 0:
            print(f"  step {m['step']:3d}  P={np.round(m['power'],2).tolist()}  "
                  f"|Mflap| T1={np.abs(m['m_flap'][0]).mean()/1e3:7.1f} kNm  "
                  f"t={time.time()-t0:.0f}s", flush=True)
    drv.close()
    print(f"[calib] 回合结束，仿真已排空（{time.time()-t0:.1f}s）", flush=True)

    # 末步落在截断轮里，AEC 会清空 infos ⇒ power=0、弯矩=nan，会污染均值，剔掉
    keep = [i for i, p in enumerate(powers) if np.any(np.asarray(p) > 0)]
    mpi_flap = np.asarray(mpi_flap)[keep]      # (steps, n_turb, 3)
    mpi_edge = np.asarray(mpi_edge)[keep]
    powers = np.asarray(powers)[keep]
    # 先落盘再分析：分析代码出错也不至于白跑一遍仿真
    np.savez(os.path.join(OUT_DIR, "mpi_moments.npz"),
             m_flap=mpi_flap, m_edge=mpi_edge, power=powers)
    report(case_dir, mpi_flap, mpi_edge, powers, float(drv.u_inf), t0)


def report(case_dir, mpi_flap, mpi_edge, powers, u_inf, t0):
    """磁盘侧解析 + 三项核对 + 落盘。和仿真解耦，便于用旧算例重算。"""

    # --- 磁盘侧 -----------------------------------------------------------
    outs = find_out_files(case_dir)
    print(f"\n[calib] 找到 {len(outs)} 个 .out: "
          f"{[os.path.basename(p) for p in outs]}", flush=True)
    if not outs:
        raise SystemExit("没有 .out 文件 —— 检查模板 OutList 与 OutFileFmt=1")
    ch = read_fast_out(outs[0])                # WT1
    print(f"[calib] WT1 通道 {len(ch)} 个，{len(ch['Time'])} 行，"
          f"t={ch['Time'][0]:.2f}→{ch['Time'][-1]:.2f}s", flush=True)

    # --- 1. 语义核对：MPI 六元组 vs 磁盘 RootMyc/RootMxc ------------------
    print("\n=== avrSWAP 记录号语义核对（WT1，单位 kN·m）===")
    print(f"{'量':<26}{'MPI mean|·|':>13}{'磁盘 mean|·|':>14}{'比值':>9}")
    rows = []
    for tag, mpi_arr, dk in (("挥舞 to_SC(7:9)/RootMyc", mpi_flap, "RootMyc"),
                             ("摆振 to_SC(10:12)/RootMxc", mpi_edge, "RootMxc")):
        mpi_v = np.nanmean(np.abs(mpi_arr[:, 0, :])) / 1e3
        dsk = np.concatenate([np.abs(ch[f"{dk}{b}"]) for b in (1, 2, 3)
                              if f"{dk}{b}" in ch])
        dsk_v = float(np.nanmean(dsk))
        print(f"{tag:<26}{mpi_v:13.1f}{dsk_v:14.1f}"
              f"{mpi_v/dsk_v if dsk_v else float('nan'):9.3f}")
        rows.append({"tag": tag, "mpi_kNm": mpi_v, "disk_kNm": dsk_v})
    print("比值 ≈ 1 即语义与 Bladed 标准记录号一致（MPI 是控制步瞬时值、"
          "磁盘是全时序均值，量级同阶就算通过）")

    # --- 2. 弯矩 → 挠度 -------------------------------------------------
    calib = calibrate(ch)
    print("\n=== 弯矩 → 叶尖挠度 标定 ===")
    for tag, d in calib.items():
        if not d.get("ok"):
            print(f"{tag}: {d['note']}")
            continue
        print(f"{tag:5s}  δ = {d['k_m_per_Nm']:.4e}·M {d['b_m']:+.4f}  "
              f"R²={d['r2']:.4f}   (过原点: k={d['k_origin_m_per_Nm']:.4e} "
              f"R²={d['r2_origin']:.4f})")
        print(f"       n={d['n']}  p95: |M|={d['moment_Nm_p95']/1e3:.0f} kN·m, "
              f"|δ|={d['defl_m_p95']:.3f} m")

    # --- 3. reward 两项量级 ---------------------------------------------
    # multiagent_env.py:220-226 的算法：powers[MW]*1e3/u^3 与 mean(|load|)（已 /1e7）
    p_term = float(np.nanmean(powers * 1e3 / u_inf ** 3))
    l_all = np.concatenate([np.abs(mpi_flap.ravel()), np.abs(mpi_edge.ravel())])
    l_term = float(np.nanmean(l_all) / 1e7)
    print("\n=== reward 两项实测量级 ===")
    print(f"mean(P/u³) = {p_term:.4f}   mean(|load|) = {l_term:.4f}   "
          f"等权所需 load_coef ≈ {p_term/l_term if l_term else float('nan'):.3f}")
    print("（现在 driver 传的是 load_coef=1.0；差一个量级 pitch 就没有可换的东西）")

    res = {
        "wind_speed": WIND_SPEED, "u_inf": u_inf,
        "max_steps": MAX_STEPS, "case_dir": case_dir,
        "channel_check": rows, "calib": calib,
        "reward_terms": {"power": p_term, "load": l_term,
                         "load_coef_equal_weight":
                             p_term / l_term if l_term else None},
    }
    with open(os.path.join(OUT_DIR, "calib.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(f"\n[calib] 写入 {OUT_DIR}/calib.json  (总 {time.time()-t0:.1f}s)",
          flush=True)


def reuse():
    """不重跑仿真，用最近一次算例的 .out + 存下的 npz 重算（改标定逻辑时用）。"""
    cands = sorted(glob.glob(os.path.join(paths.ROOT, "__simul__", "fastfarm",
                                          "FastFarm__*3T*")),
                   key=os.path.getmtime)
    if not cands:
        raise SystemExit("没有找到 3T 算例目录，先完整跑一次")
    case_dir = cands[-1]
    print(f"[calib] 复用 {case_dir}", flush=True)
    p = os.path.join(OUT_DIR, "mpi_moments.npz")
    if os.path.exists(p):
        npz = np.load(p)
        mf, me, pw = npz["m_flap"], npz["m_edge"], npz["power"]
    else:
        # 没有 MPI 侧记录就全从磁盘重建（量级估计够用，但 MPI-vs-磁盘 那项
        # 核对退化成磁盘自比，比值恒为 1，没有意义 —— 会显式标出来）
        print("[calib] 缺 mpi_moments.npz，弯矩/功率全部退回磁盘通道", flush=True)
        ch = read_fast_out(find_out_files(case_dir)[0])
        mf = np.stack([ch[f"RootMyc{b}"] for b in (1, 2, 3)], 1)[:, None, :] * 1e3
        me = np.stack([ch[f"RootMxc{b}"] for b in (1, 2, 3)], 1)[:, None, :] * 1e3
        pw = (ch["GenPwr"] / 1e3)[:, None]              # kW → MW
    report(case_dir, mf, me, pw, WIND_SPEED, time.time())


if __name__ == "__main__":
    import sys
    reuse() if "--reuse" in sys.argv else main()
