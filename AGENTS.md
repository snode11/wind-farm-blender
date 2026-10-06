# 项目开发规则

- 从实际 Git 仓库根目录工作，检查适用规则及 Git 状态，保留已有修改和未跟踪文件。
- 本项目唯一 GitHub 仓库入口是 https://github.com/snode11/wind-farm-blender ，对应本地 remote `blender-sync`。项目说明、网页 GPT 交接和源码差异核对统一使用此地址；其他 remote 或历史上传回执不得替代项目仓库身份。
- 叶片表面研究源码与三轮实验的精选讨论快照入口为 [research/blade-surface/2026-10-07/README.md](research/blade-surface/2026-10-07/README.md)。快照内文件保留采集时身份，不能据此推定产品源码、日常安装或几何/健康验收状态。
- 根目录 README.md 和 CHANGELOG.md 仅记录 GitHub Releases 已发布版本，按发布日期倒序列出功能、修复和已知限制。不要写入本机安装、磁盘清理、临时验证、未发布方案或开发流水账。发布时同步当前版本和安装包入口。
- Blender 原生前端开发与排障使用 [windfarm-blender-dev](.agents/skills/windfarm-blender-dev/SKILL.md)；按当前需求选择模块和资料，优先复用现有结构。
- 纯界面修改默认复用正式结果包，不重跑 FAST.Farm。保留数据来源、无效值和统计口径，区分仿真结果、刚性示意与现场实测。
- 正常开发完成相关验证及本次改动引入的问题修复；仅文档、方案、基线或报告任务遵守对应范围。交付结论区分“已运行通过”“仅静态检查”“尚未验证”，不同环境的证据不能相互替代。
