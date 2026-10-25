# Blade Recon v0.1：Blender查看与验证合同

该目录为10维轮廓基线：方位角、逐叶桨距、一阶挥舞和一阶摆振；二阶挥舞固定为0。当前13维、动态输入及纹理辅助研究见[blade_recon v0.2](../blade_recon0.2/README.md)，两者结果和状态不混用。方法、依赖和已有结果见[README](README.md)与[运行说明](docs/目录与运行说明.md)。

## 已有证据与限制

合成试跑（本地文件：`blade_recon/out/trial-20261004/试跑报告.md`）与Blender 5.2.1后台验证（本地文件：`blade_recon/out/trial-20261004/blender/BLENDER_VALIDATION.md`）覆盖合成200帧结果及选定帧的几何、证据色和真值虚影一致性。原版 `blender_view.py` 已在后台运行；其相机矩阵赋值存在外参问题，兼容wrapper使用 `mathutils.Matrix` 并设置输入分辨率。原版按fx近似fy的内参限制仍保留。

这些证据不包含原生交互窗口、实时FPS、渲染外观或真实风机精度。保存的blend是静态快照，Python帧回调不随文件持久保存；动画需重新运行wrapper，不能将静态重开称为动画恢复。

## 查看已有结果

从算法目录调用现有环境和兼容查看入口：

```bash
cd 'PROJECT_ROOT/blade_recon'
.venv-trial/bin/python -B evaluate.py \
  --recon out/trial-20261004/synth20/recon.json \
  --truth data/synth20/truth.json --skip 50
/Applications/Blender.app/Contents/MacOS/Blender --factory-startup \
  --python out/trial-20261004/blender/compat/blender_view_compat.py -- \
  --recon out/trial-20261004/synth20/recon.json \
  --truth data/synth20/truth.json --cameras data/synth20/cameras.json
```

该入口在新Blender进程中创建场景。核对真实模块路径、输入和结果身份，不以版本号代替载荷核验。绿色表示该帧图像轮廓支持，橙色表示模型推断；真值仅作独立评分或显示，不能进入求解输入。

## 新数据与评分

三路原图分别保留内外参、原生分辨率、帧号与时间，不拼接后再估计。v0.1使用静态 `cameras.json`；机舱、塔顶或相机运动时不能使用单时刻外参代表整段。共光心50°×40°扇形阵列与平台分区相机是不同配置，可观性与精度仅解释实际输入。

拟合前核对轮毂、预弯、轴倾、预锥、转向、方位角零点、桨距符号及相机坐标。已知状态叠图只用于对齐诊断；正式求解与评价数据隔离，输出使用独立目录。

新结果的原生验证应覆盖播放、暂停、随机寻址、自由视角和失效状态，并记录实际窗口来源。后台几何一致性不能替代这些操作证据。状态换算误差、最近表面距离、真实材料对应误差分别报告；只有明确同一定义和参考时刻的量才能比较。

合成自测采用理想针孔和近似健康模板，不能推广为实机外形、局部裂纹/凹坑深度或现场自动检出精度。软件、可见显示、性能和科学验收分别判断。
