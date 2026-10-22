"""FAST.Farm 真实扰动风场切面（DisXY VTK）—— 开启、读取、随仿真跟进。

替掉可视化里那张 **FLORIS-proxy** 尾流面。proxy 的问题不是画得糙，是物理不对：
它拿 FAST.Farm 的实测风速/偏航去喂一个**稳态**尾流模型，所以画面上没有尾流
输运时延、没有蜿蜒（meandering）、没有湍流结构 —— 而这三样正是动态后端相对
FLORIS 的全部意义所在（见 memory: wfcrl-fastfarm-train-viz 的 2.5× 功率差）。

FAST.Farm 不向 Python 暴露实时流场（MPI 只有每台 12 个 double），但它可以把
低分辨率域的扰动风写成 VTK 切面落盘：

    NOutDisWindXY=1, OutDisWindZ=90.0, WrDisDT=DT_Low
      → <案例>/vtk_ff/<Root>.Low.DisXY01.t<n>.vtk

于是流程是「仿真写盘 → 我们轮询目录 → 落后一两个文件地跟着放」。这是**准**实时：
每个控制步一张，和控制步同频，但落在磁盘上，比 MPI 慢一个 I/O 往返。

用法：
    enable_disturbed_wind(drv.env.mdp.interface._simul_file, z=90.0)   # reset 之前
    ...
    rd = DisXYReader(case_dir)
    fr = rd.latest()           # 最新一帧；没有新帧返回 None
    fr.x, fr.y, fr.u           # (nx,) (ny,) (ny,nx) 水平风速幅值

只改**生成好的案例文件**、不动模板：模板一改，训练也跟着每 3 s 写一次盘。
"""
import os
import re
from dataclasses import dataclass

import numpy as np


# --- 开启切面输出 -----------------------------------------------------------
def enable_disturbed_wind(fstf_file, z=90.0, dt=None, full=False):
    """把案例 .fstf 的 VISUALIZATION 段打开，返回实际生效的 (z, WrDisDT)。

    `fstf_file` 是 `interface._simul_file`（`create_ff_case` 生成的那份）。必须在
    `reset()` **之前**调用 —— exe 是在 reset 里 spawn 的，之后改文件没用。
    `reset_simul_file` 每次 reset 从这份 copy2 出新的，所以改一次覆盖全部 reset。

    `full=True` 才写 `WrDisWind`（整个低分辨率 + 高分辨率 3D 体）。默认不写：
    380×138×70 的体每帧 ~15 MB，30 帧就 450 MB，而我们只要轮毂高度那一层。
    """
    from openfast_toolbox.io.fast_input_file import FASTInputFile

    f = FASTInputFile(fstf_file)
    f["NOutDisWindXY"] = 1
    f["OutDisWindZ"] = float(z)
    f["WrDisWind"] = bool(full)
    if dt is not None:
        f["WrDisDT"] = float(dt)
    f.write(fstf_file)
    return float(f["OutDisWindZ"]), float(f["WrDisDT"])


def case_dir_of(interface):
    """由 interface 反推案例根目录（`_simul_file` 在 <case>/FarmInputs/ 下）。"""
    return os.path.dirname(os.path.dirname(interface._simul_file))


# --- 读 legacy VTK ----------------------------------------------------------
@dataclass
class DisXYFrame:
    """一张轮毂高度的扰动风切面。"""

    t: int                  # 文件名里的时间步序号
    path: str
    x: np.ndarray           # (nx,) 惯性系 X (m)
    y: np.ndarray           # (ny,) 惯性系 Y (m)
    u: np.ndarray           # (ny, nx) 水平风速幅值 (m/s)
    vec: np.ndarray = None  # (ny, nx, 3) 三分量，需要画流线时用


_KV = re.compile(rb"^\s*(\w+)\s+(.*)$")


def read_disxy(path):
    """读一张 FAST.Farm 的 DisXY VTK（legacy STRUCTURED_POINTS，ASCII 或 BINARY）。

    FAST.Farm 写的是 3 分量向量场，点序是 X 最快、然后 Y、然后 Z（Z 只有 1 层）。
    BINARY 段是 **big-endian float32**（legacy VTK 规定），Windows 上直接
    `np.fromfile` 会读成小端乱码 —— 必须显式 `>f4`。
    """
    with open(path, "rb") as fh:
        blob = fh.read()
    # 头部是文本行；找到 LOOKUP_TABLE / VECTORS 之后的数据起点
    head_end = blob.find(b"\n", blob.find(b"VECTORS"))
    head = blob[:head_end].decode("latin-1", "replace")

    def _grab(key, cast=float, n=3):
        m = re.search(rf"{key}\s+([-\d.eE+\s]+)", head)
        if not m:
            raise ValueError(f"{path}: 缺 {key}")
        return [cast(v) for v in m.group(1).split()[:n]]

    nx, ny, nz = _grab("DIMENSIONS", int)
    ox, oy, oz = _grab("ORIGIN")
    dx, dy, dz = _grab("SPACING")
    binary = "BINARY" in head

    want = nx * ny * nz * 3
    if binary:
        raw = np.frombuffer(blob, dtype=">f4", count=want,
                            offset=head_end + 1).astype(np.float32)
    else:
        raw = np.asarray(blob[head_end + 1:].split()[:want], dtype=np.float32)
    if raw.size < want:
        raise ValueError(f"{path}: 数据不全（{raw.size}/{want}），可能还在写")
    vec = raw.reshape(nz, ny, nx, 3)[0]          # nz=1（XY 切面）

    x = ox + dx * np.arange(nx)
    y = oy + dy * np.arange(ny)
    u = np.hypot(vec[:, :, 0], vec[:, :, 1])     # 水平幅值；竖向分量不进色标
    t = _step_of(path)
    return DisXYFrame(t=t, path=path, x=x, y=y, u=u, vec=vec)


# 实测文件名是 `Case.Low.DisXY01.000.vtk` —— **没有** 手册里写的 `.t` 前缀，
# 序号紧跟在切面编号后面。按手册写的 `\.t(\d+)` 一个都匹配不上（首次探测时
# 全部返回 -1、轮询器一帧都不交付），所以这里只认末尾的纯数字段。
_TSTEP = re.compile(r"\.(\d+)\.vtk$", re.I)


def _step_of(path):
    m = _TSTEP.search(os.path.basename(path))
    return int(m.group(1)) if m else -1


# --- 目录轮询 ---------------------------------------------------------------
class DisXYReader:
    """轮询案例目录，按时间步顺序交付 DisXY 切面。

    仿真是边跑边写的，所以**最后一个**文件可能正写到一半 —— 读它会拿到截断的
    数据或直接抛异常。策略：只交付「已经有后继文件」的那一张（后继存在 ⇒ 前一张
    写完了），代价是恒定落后一帧（3 s），换来不会读到半张图。
    """

    def __init__(self, case_dir, plane=1):
        self.case_dir = case_dir
        self.pat = f".Low.DisXY{plane:02d}."
        self._served = -1
        # 实测落在 FarmInputs/vtk_ff/（vtk_ff 是相对 .fstf 所在目录建的，而 .fstf
        # 在 FarmInputs 下）。另两个位置留作兜底，万一 wfcrl 改了案例布局。
        self._dirs = [os.path.join(case_dir, "FarmInputs", "vtk_ff"),
                      os.path.join(case_dir, "vtk_ff"), case_dir]

    def _files(self):
        for d in self._dirs:
            if not os.path.isdir(d):
                continue
            got = [os.path.join(d, f) for f in os.listdir(d)
                   if self.pat in f and f.lower().endswith(".vtk")]
            if got:
                return sorted(got, key=_step_of)
        return []

    def latest(self):
        """最新一张**写完**的切面；没有新的返回 None。"""
        files = self._files()
        if len(files) < 2:
            return None
        cand = files[-2]                      # 倒数第二张 ⇒ 一定写完了
        t = _step_of(cand)
        if t <= self._served:
            return None
        try:
            fr = read_disxy(cand)
        except Exception as e:                                  # noqa: BLE001
            print(f"[wakevtk] 读 {os.path.basename(cand)} 失败，跳过: {e!r}")
            return None
        self._served = t
        return fr

    def wait_first(self, timeout=60.0, poll=0.5):
        """阻塞等第一张切面（建场景要先知道网格范围）。超时返回 None。"""
        import time
        t0 = time.time()
        while time.time() - t0 < timeout:
            fr = self.latest()
            if fr is not None:
                return fr
            time.sleep(poll)
        return None
