# 2026-10-03 真实目录整理记录

六个阶段目录实际搬入 `stages/`，初始审阅材料实际搬入 `review/20261002-initial-materials/`；上次创建的隐藏目录入口与旧包链接已撤除。

- [manifest.json](manifest.json)：1,350 个原文件的迁移关系、字节长度与原 SHA-256。
- [verification.json](verification.json)：最终内容、路径和入口核验结果。
- `originals/`：132 份原辅助脚本、报告与 HTML 原文，以及两份方案文档原文，供核对历史签名；均为可见备份。

原始视频、网格、评分、冻结 JSON、日志、源码快照和审阅 ZIP 未因整理而重算。报告链接及可操作辅助脚本调整了路径，不能将这些调整后的文件冒充当时的原始签名来源；历史签名通过 `originals/` 中的原文核对。

`originals/repository/docs/proposal/` 下两份方案原文通过反向本轮目录链接修改恢复，并与既有冻结 SHA-256 核对一致。上次分类链接布局生成的旧说明、清单和恢复脚本已移入废纸篓。

统一路径转换位于 [artifact_paths.py](../../../../wfrl/nrel_reconstruction/artifact_paths.py)。`relocated_path` 将历史记录路径映射到当前位置，`frozen_path` 另外核对预期 SHA-256 并返回实际匹配的原文件或原文快照。
