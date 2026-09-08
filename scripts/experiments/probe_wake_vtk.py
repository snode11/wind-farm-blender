"""探一次 FAST.Farm 的 DisXY 切面：文件落在哪、什么格式、多大、多快。

跑法：
    & "C:\\Program Files\\Microsoft MPI\\Bin\\mpiexec.exe" -n 1 python \
        -m scripts.experiments.probe_wake_vtk

要回答的问题（写解析器之前必须先知道，别猜）：
  1. 文件目录与命名（vtk_ff/ 还是案例根？.t 序号从 0 还是 1）
  2. ASCII 还是 BINARY，DIMENSIONS/ORIGIN/SPACING 各是多少
  3. 单帧字节数 × 帧数 = 一个回合的磁盘代价
  4. 开切面后 Sim/CPU 掉多少（I/O 开销值不值）
"""
import glob
import os
import time

import numpy as np

from wfrl.fastfarm_driver import FastFarmDriver

STEPS = 6


def main():
    t0 = time.time()
    drv = FastFarmDriver("Dec_Turb3_Row1_Fastfarm", max_steps=STEPS,
                         controls=("yaw",), wake_vtk=True, wake_vtk_z=90.0)
    m = drv.reset(wind_speed=8.0)
    print(f"[probe] reset OK ({time.time()-t0:.1f}s)  case={drv.case_dir}",
          flush=True)
    while not m["done"]:
        m = drv.step(np.zeros(drv.n))
        print(f"  step {m['step']}  P={np.round(m['power'],2).tolist()}  "
              f"t={time.time()-t0:.0f}s", flush=True)
    drv.close()

    # --- 1. 文件在哪 ---------------------------------------------------------
    print("\n=== 落盘位置 ===", flush=True)
    hits = []
    for root, _dirs, files in os.walk(drv.case_dir):
        vtks = [f for f in files if f.lower().endswith(".vtk")]
        if vtks:
            rel = os.path.relpath(root, drv.case_dir)
            print(f"{rel}/  共 {len(vtks)} 个 .vtk，前 3: {sorted(vtks)[:3]}")
            hits += [os.path.join(root, f) for f in vtks]
    if not hits:
        raise SystemExit("没有 .vtk —— 检查 NOutDisWindXY 是否真写进案例 .fstf")

    dis = sorted(f for f in hits if "DisXY" in f)
    print(f"DisXY 帧数 = {len(dis)}  单帧 = "
          f"{os.path.getsize(dis[0])/1e6:.2f} MB  "
          f"合计 = {sum(os.path.getsize(f) for f in dis)/1e6:.1f} MB")

    # --- 2. 头部长什么样 ----------------------------------------------------
    print("\n=== 头部 12 行（第一帧）===")
    with open(dis[0], "rb") as fh:
        head = fh.read(400).decode("latin-1", "replace")
    for ln in head.splitlines()[:12]:
        print(f"  {ln[:90]}")

    # --- 3. 用我们的解析器读，核对物理 ---------------------------------------
    from wfrl.viz.wakevtk import read_disxy
    fr = read_disxy(dis[-1])
    print(f"\n=== 解析结果（最后一帧 t={fr.t}）===")
    print(f"网格 {fr.u.shape}  x:{fr.x[0]:.0f}→{fr.x[-1]:.0f} m  "
          f"y:{fr.y[0]:.0f}→{fr.y[-1]:.0f} m")
    print(f"u: min={np.nanmin(fr.u):.2f}  max={np.nanmax(fr.u):.2f}  "
          f"mean={np.nanmean(fr.u):.2f} m/s   (来流钉在 8.0)")
    # 尾流亏损：机组下游 3D 处沿 y 取最小值，应显著低于 8
    for xd in (63.0, 315.0, 630.0):
        j = int(np.argmin(np.abs(fr.x - xd)))
        col = fr.u[:, j]
        print(f"  x={fr.x[j]:6.0f} m  沿 y 最小 u = {np.nanmin(col):.2f} m/s  "
              f"(亏损 {100*(1-np.nanmin(col)/8.0):.0f}%)")

    # --- 4. 轮询器行为 ------------------------------------------------------
    # `latest()` 的语义是"最新一张写完的"，不是"下一张没交付过的"：仿真结束后一次性
    # 补读，它只会给**一帧**（最新的那张），历史帧被跳过 —— 这是可视化想要的行为
    # （宁愿丢帧也不落后于实时）。所以这里逐张读来核对时间步是否连续。
    from wfrl.viz.wakevtk import DisXYReader, read_disxy
    rd = DisXYReader(drv.case_dir)
    fr1 = rd.latest()
    print(f"\nlatest() 交付 t={fr1.t if fr1 else None}"
          f"（末帧留给'正在写'保护，不交付）；再问一次 = {rd.latest()}（无新帧）")
    print(f"盘上时间步 = {[read_disxy(f).t for f in dis]}")
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
