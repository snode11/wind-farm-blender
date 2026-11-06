"""OpenFAST 机型尺寸，供传感器位姿使用；不依赖渲染库。"""
from dataclasses import dataclass
import math
from pathlib import Path
import re


@dataclass(frozen=True)
class TurbineDimensions:
    """ElastoDyn 安装尺寸，长度单位 m，轴倾角单位 deg。"""

    tip_rad: float
    hub_rad: float
    overhang: float
    shft_tilt: float
    tower_ht: float
    twr2shft: float

    @property
    def hub_height(self):
        return self.tower_ht + self.twr2shft + abs(self.overhang) * math.sin(
            math.radians(abs(self.shft_tilt)))


def load_turbine_dimensions(template_dir=None):
    """从 FAST.Farm 模板读取相机挂载尺寸，缺少必需参数时抛出异常。"""
    if template_dir is None:
        try:
            from wfcrl.simul_utils import TEMPLATE_DIR
            template_dir = TEMPLATE_DIR.format("fastfarm")
        except ImportError:
            from wfrl import paths
            template_dir = Path(paths.ROOT) / "wfcrl-env/wfcrl/simulators/fastfarm/inputs/template"
    try:
        path = Path(template_dir) / "FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat"
    except TypeError as exc:
        raise ValueError("template_dir 必须为目录路径") from exc
    lines = path.read_text(encoding="latin-1").splitlines()
    number = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?")

    def scalar(key):
        for line in lines:
            if line.lstrip().startswith(("#", "!", "--", "==")):
                continue
            fields = line.split()
            if len(fields) >= 2 and fields[1] == key:
                token = fields[0]
                if not number.fullmatch(token):
                    raise ValueError(f"{path}: {key} 必须是有限数值")
                value = float(token.replace("D", "E").replace("d", "e"))
                if not math.isfinite(value):
                    raise ValueError(f"{path}: {key} 必须是有限数值")
                return value
        raise ValueError(f"{path}: 缺少 {key}")

    dimensions = TurbineDimensions(
        tip_rad=scalar("TipRad"), hub_rad=scalar("HubRad"),
        overhang=scalar("OverHang"), shft_tilt=scalar("ShftTilt"),
        tower_ht=scalar("TowerHt"), twr2shft=scalar("Twr2Shft"),
    )
    if not 0 <= dimensions.hub_rad < dimensions.tip_rad:
        raise ValueError(f"{path}: 半径须满足 0 <= HubRad < TipRad")
    if dimensions.tower_ht <= 0 or not math.isfinite(dimensions.hub_height) or dimensions.hub_height <= 0:
        raise ValueError(f"{path}: TowerHt 和轮毂高度必须为有限正数")
    return dimensions
