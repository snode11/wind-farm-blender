# NREL 5MW 单叶片视频重建

两轮重建、P0–P5 诊断实验与审阅材料的统一入口。**正式结果仍为第二轮 v2；P4、P5 的候选分支未采用，完整几何验收仍待建立。**

## 直接查看

- [正式模型交互预览](stages/02-second-run/reconstruction/model_preview.html)
- [正式模型 T1_B1.ply](stages/02-second-run/reconstruction/T1_B1.ply)
- [第二轮实施报告](stages/02-second-run/README.md)
- [最终 P5 收尾报告](stages/06-p5-observation/README.md)
- 原始三路正式视频：[C1](stages/01-first-run/formal/algorithm-input/C1.mp4)、[C2](stages/01-first-run/formal/algorithm-input/C2.mp4)、[C3](stages/01-first-run/formal/algorithm-input/C3.mp4)
- [最终 P5 审阅包](review/20261003-p5-observation-review.zip)

## 目录

```text
nrel-video-single-blade/
├── README.md
├── stages/
│   ├── 01-first-run/
│   ├── 02-second-run/
│   ├── 03-diagnostics/
│   ├── 04-p3-center-scale/
│   ├── 05-p4-greville/
│   └── 06-p5-observation/
└── review/
    ├── 20261002-initial-materials/
    ├── 各阶段审阅 ZIP 与原校验文件
    └── layout-migration-20261003/
```

`stages/` 下均为实际搬入的文件夹，根目录中的旧阶段文件夹和旧包链接已撤除。

| 阶段 | 内容 |
| --- | --- |
| [01-first-run](stages/01-first-run/README.md) | 首轮采集、视频、重建与独立评分 |
| [02-second-run](stages/02-second-run/README.md) | 保留的正式 v2 模型、报告与评分 |
| [03-diagnostics](stages/03-diagnostics/README.md) | P0–P2 证据审计、面积对账与根部诊断 |
| [04-p3-center-scale](stages/04-p3-center-scale/README.md) | P3 中心与尺度受控诊断 |
| [05-p4-greville](stages/05-p4-greville/README.md) | P4 可逆坐标对照与 RGB 精审 |
| [06-p5-observation](stages/06-p5-observation/README.md) | P5 RGB 边界消融与本轮收尾 |

[初始审阅材料](review/20261002-initial-materials/上传顺序与文件清单.md) 与五份原审阅 ZIP 集中放在 `review/`。

## 整理记录

2026-10-03 已实际移动目录，并更新操作脚本与报告链接。视频、模型、评分数据及原审阅 ZIP 保留原内容；没有重跑采集、拟合、渲染或评分。

冻结 JSON、日志和源码快照保留当时记录的路径。当前脚本读取这些记录时，将旧路径映射到新位置；有路径修改的辅助脚本与报告原文作为可见备份保存在 [整理记录目录](review/layout-migration-20261003/README.md)，便于追溯原签名。该目录记录迁移关系与内容核验结果。
