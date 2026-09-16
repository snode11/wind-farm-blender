"""Build an isolated, movable Blender review bundle with matching runtime/data."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
from zipfile import ZipFile

from scripts.blender.build_extension import build

ROOT=Path(__file__).resolve().parents[2]


def bundle(package,destination):
    package,destination=Path(package),Path(destination)
    manifest=json.loads((package/'manifest.json').read_text())
    if manifest.get('diagnostic_only') or not all(c['passed'] for c in manifest['controller_validation']):
        raise ValueError('Cannot deliver a diagnostic or unaccepted controller package')
    destination.mkdir(parents=True,exist_ok=False)
    built=build(destination/'runtime-archive')
    with ZipFile(built.archive) as archive:
        archive.extractall(destination/'blender_frontend/wfrl_blender')
    shutil.copytree(package,destination/'data')
    launcher=destination/'scripts/blender/open_farm_flex.py'
    launcher.parent.mkdir(parents=True)
    shutil.copyfile(ROOT/'scripts/blender/open_farm_flex.py',launcher)
    command=destination/'打开三机叶片回放.command'
    command.write_text('''#!/bin/zsh
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python "$SCRIPT_DIR/scripts/blender/open_farm_flex.py"
''')
    command.chmod(0o755)
    (destination/'使用说明.md').write_text('''# 三机 MAPPO 叶片回放 · 开发验收版

在 Mac 上双击“打开三机叶片回放.command”，需要已安装 Blender 5.2。
右侧 N 侧栏 → MAPPO →“三机 MAPPO 回放”面板可播放、暂停、重播。
观察视角提供 T1/T2/T3 Down、T1 侧前方和风场全景；轨迹、播放与进度各保留一个入口。
“更多视角”内可展开测量区侧视、雷达特写和云台控制；“雷达与净空”分别显示测距与叶尖—塔筒净空。
三机遥测与曲线、环境与截图录制、测量统计分别放在折叠区。三机回放时隐藏重复 Camera 面板。
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
''')
    inventory={str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in destination.rglob('*') if p.is_file()}
    (destination/'inventory.json').write_text(json.dumps(dict(status='REVIEW_ONLY',
        runtime_sha256=built.sha256,files=inventory),indent=2))
    print(destination.resolve(),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('package');p.add_argument('destination')
    a=p.parse_args();bundle(a.package,a.destination)
