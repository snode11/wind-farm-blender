# 旧非正式窗口日志的哈希差异

本说明仅引用本轮 `old_evidence_audit.json` 已有记录；没有重新广泛扫描旧证据。审计记录 UTC：2026-10-06T02:38:08.573991+00:00。

- 精确路径：`REPOSITORY_ROOT/outputs/blade-surface-20261005-manual-a01/validation/final_comparison_window_attempt02.log`。
- 清理前历史 `artifact_hashes_final.json` 记录的 SHA-256：`e5e52b17e53731c33b2b64adee9e9fa9d98fdff9b694fbfa36002990cffd7ade`。
- 本轮审计读取时实际 SHA-256：`3d577f8439fbcc6dc22c856baa2226ff1960cd2e3384b4ec5b53983a1861c6fa`。
- 实际状态：文件存在；分类为 `HASH_MISMATCH_NONFORMAL`。这是旧非正式窗口日志，未作为 P2b-0 正式输入、状态、q 或独立评价数组使用。

现有审计没有保存本轮开始前这条日志的独立哈希。历史清理前总表与本轮读取时的值不同，只能证明两次记录之间字节曾发生差异；无法据此确认是否在本轮开始前已经变化，也无法归因到本轮、任何对话、任何人或具体时间。不把该差异写作本轮造成、正式数据丢失或正式冻结证据失败。

旧清理前总表共 544 条：449 条当前存在且哈希匹配，94 条是有清理映射的非正式可恢复移走路径，1 条是以上非正式日志差异。正式冻结/输入/评价的 110 项检查均通过。当前冻结模块和历史模板身份也通过，旧结果维持 `COMPLETED_REVIEW_ONLY`。
