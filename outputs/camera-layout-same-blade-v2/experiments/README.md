# 布局探索记录

这里只保留未交付的候选结果，不能用于当前布局导入或播放验收。`initial-fit` 是最初取景，`reference-fit` 是只对起始画面优化的中间版本，后者的 C4 在一次叶片形变时漏出叶尖，已由上级目录的最终 JSON 替换。上级的 `framing-check.json` 也是这个中间阶段的数据。

本次整理仅移动五张探索截图，映射与哈希见 organization-manifest.json；没有永久删除。此前91个文件的隔离清理计数不变。

上级目录的 probe/design/refine/preview/optimize 脚本用于探索，部分会重写布局文件；不要直接对已交付 JSON 运行。最终布局的只读复测使用 check_passages.py、render_final.py、benchmark.py，结果以交付 JSON 的 SHA-256 为准。
