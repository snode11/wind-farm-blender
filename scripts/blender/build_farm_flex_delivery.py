"""Verify matching runtime/data in staging and publish a complete, movable review directory."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import tempfile
from zipfile import ZipFile

try:
    from scripts.blender.build_extension import build
    from scripts.blender.validate_frontend_package import verify_archive
except ModuleNotFoundError:
    from build_extension import build
    from validate_frontend_package import verify_archive

ROOT=Path(__file__).resolve().parents[2]


def bundle(package,destination):
    package,destination=Path(package),Path(destination)
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    validation = manifest.get('controller_validation')
    if (manifest.get('diagnostic_only') or not isinstance(validation, list) or not validation
            or not all(isinstance(c, dict) and c.get('passed') is True for c in validation)):
        raise ValueError('Cannot deliver a diagnostic or unaccepted controller package')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f'Delivery destination already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'.{destination.name}-', dir=destination.parent) as directory:
        staged = Path(directory) / destination.name
        _populate_bundle(package, staged, manifest)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f'Delivery destination already exists: {destination}')
        staged.rename(destination)
    print(destination.resolve(),flush=True)


def _populate_bundle(package, destination, manifest):
    destination.mkdir(parents=True,exist_ok=False)
    built=build(destination/'runtime-archive',farm_package=package)
    verify_archive(built.archive, built.inventory)
    with ZipFile(built.archive) as archive:
        archive.extractall(destination/'blender_frontend/wfrl_blender')
        shutil.copytree(package,destination/'data')
        if json.loads(archive.read('assets/mappo/manifest.json')) != manifest:
            raise ValueError('Delivery data manifest changed during packaging')
        for name in {'manifest.json', *manifest['files']}:
            if (destination/'data'/name).read_bytes() != archive.read('assets/mappo/' + name):
                raise ValueError('Delivery data differs from runtime: ' + name)
    launcher=destination/'scripts/blender/open_farm_flex.py'
    launcher.parent.mkdir(parents=True)
    shutil.copyfile(ROOT/'scripts/blender/open_farm_flex.py',launcher)
    command=destination/'打开三机叶片回放.command'
    command.write_text('''#!/bin/zsh
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python "$SCRIPT_DIR/scripts/blender/open_farm_flex.py"
''', encoding='utf-8')
    command.chmod(0o755)
    (destination/'使用说明.md').write_text('''# 三机 MAPPO 叶片回放 · 开发验收版

在 Mac 上双击“打开三机叶片回放.command”，需要已安装 Blender 5.2。
右侧 N 侧栏 → MAPPO →“三机 MAPPO 回放”面板可播放、暂停、重播。
观察视角提供 T1/T2/T3 Down、T1 侧前方和风场全景；轨迹、播放与进度各保留一个入口。
面板分为“挠度 / 净空 / 工具”；工具页含视角与云台，净空页显示测距和叶尖—塔筒净空。
三机遥测、环境及截图录制在工具页；测量统计在净空页。三机回放时隐藏重复 Camera 面板。
扩展 ZIP 内置完整 MAPPO 数据；旧 66 秒脚本和专属面板已移除。
单步推进 1/60 秒；停止保留当前位置，复位回到起点，片尾不添加虚构顺桨过程。
功率、转矩、叶根载荷来自同源已保存仿真输出，奖励没有记录。
T1 Down、T1 侧前方和全景之间切换保留当前轨迹及播放进度；换机组仍从当前时刻重新记录。
彩色轨迹显示当前机组的三片叶尖：橙、蓝、红；关闭会清空，重新开启从当前时刻绘制。
片尾保持最后画面，点击“从头重播”手动重播。

整个目录可以移动；离线播放不需要原始 VTP、FAST.Farm、Python 环境、训练或网络。
本启动器加载随包运行时，不替换全局已安装的扩展。

这是 REVIEW_ONLY 开发验收包。三机采用独立物理结果和同源净空读数，MAPPO 只控制偏航；
11 rpm 目标的降额基线控制器负责转矩、变桨。下游机组受尾流影响可能低于目标转速。
显示实体保留真实形变量，平滑展示网格和红色叶尖涂装为可视化外形。
尚不具备时间步长/展向细化的正式数值精度证据，不用于现场精度、控制收益或安全认证。
''', encoding='utf-8')
    if manifest.get('tower_model') == 'elastodyn-flexible':
        with (destination/'使用说明.md').open('a', encoding='utf-8') as notes:
            notes.write('''
## 本次塔架柔性重算

三台塔架均开启前后、侧向两阶弯曲；机舱、叶轮和雷达安装随求解器运动。
塔架和叶片保持真实尺度，未放大摇晃幅度。T1 结构叶尖参考随塔顶平移和倾斜，
挠度比较去除了这部分整体运动。净空真值为叶尖参考点到同高度变形塔筒截面的距离。
固定标定的雷达估计公式未加入塔架弯曲补偿，数值偏差仍照实显示。

这是 0.3.1 的独立 REVIEW_ONLY 播放目录；不替换全局已安装扩展。
默认显示 T1 Down；切“叶轮”可观察整机，切“叶尖”可检查结构挠度参考。
现有“虚影 / 分量”仍仅针对 T1 叶片；塔筒暂无专用虚影、位移箭头和数值卡片。
''')
    if manifest.get('schema') == 'wfrl.farm-flex-review.v3':
        (destination/'使用说明.md').write_text('''# 预弯三机演示 · 本地 REVIEW_ONLY

**验收状态：FAILED_AVAILABILITY。有效测量误差通过，但三机覆盖率均未达标；T1 有 27/33 次叶片经过漏测。本包供审阅，不能称为客户 Demo 全项验收通过。**

双击“打开三机叶片回放.command”启动（macOS，Blender 5.2）。
右侧 MAPPO 面板提供播放/暂停、单步、进度拖动、复位和片尾重播。
“挠度”页可选 T1 叶片 1/2/3，查看预弯参考虚影、实际点和根系 xyz 三分量；
“净空”页显示三束提示和最近有效 B2 测量；“工具”页切换机组、Down、侧前方和全景。
彩色轨迹保留最近 1.5 仿真秒，逐渐变淡，暂停不衰退；切同机组观察视角保持轨迹。

物理模型是基于 NREL 5MW 的预弯改型：迎风二次预弯，叶尖 −1 m，保留 −2.5° 预锥。
叶片由 BeamDyn 计算，塔架仍由 ElastoDyn 计算；三台机组均来自本次独立 FAST.Farm 求解。
独立零载荷参考不包含首帧预平衡挠度；曲线、几何、源数据和回放时间通过哈希绑定。
结构挠度点、显示轨迹尖端与净空末截面表面点有不同定义，不互相替代。

净空真值取末截面轮廓表面至同高度变形塔壁的最近水平距离，不是整叶片三维碰撞间隙。
B2 估计沿用固定标定公式，未加入塔架弯曲补偿；7 m 是演示报警阈值，不是机型安全界限。
卡片显示最近有效测量及年龄，过期后不可用；不是连续真值或一次经过最小值。
未测到叶片不补零、不用真值填充。有效率、漏测和低净空盲区详见随附验收报告。

这是独立便携目录，不替换全局安装或旧基线数据；运行不需要求解器、训练环境或网络。
本次未对外发布。macOS 的后台/窗口证据不代表 Windows 实机验收、稳定 60 FPS、现场精度或安全认证。
''', encoding='utf-8')
    inventory={str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in destination.rglob('*') if p.is_file()}
    (destination/'inventory.json').write_text(json.dumps(dict(status='REVIEW_ONLY',
        runtime_sha256=built.sha256,files=inventory),indent=2), encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('package');p.add_argument('destination')
    a=p.parse_args();bundle(a.package,a.destination)
