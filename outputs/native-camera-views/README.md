# 原生相机观察本地更新

默认单路、四路入口为独立 Blender 原生观察窗口，共用场景和回放时钟；默认材质预览继承原窗口的环境与灯光，保留草地纹理。原图导出保留独立 HFOV/VFOV 与输出尺寸。

2026-09-23 已移除旧离屏四格实现并完成谨慎清理。当前包、安装核对和最终验证见 [最新清理与更新记录](../camera-cleanup/README.md)，后续以该记录为准。旧 package/、重复截图和备份文件已转移到可恢复隔离目录，映射见 [清单](../camera-cleanup/manifest.json)。本目录 quad.png / single.png 保留为先前材质版画面；installed-final/check.json 保留为历史短测，不代表优化后性能。

原生完整材质四路的卡顿主要已验证因素是动态阴影绘制，见 [诊断结果](../camera-lag-diagnosis/REPORT.md)。当前仍未降低默认阴影质量，不能承诺流畅播放。
