# 简历证据 — CodeCairn

- 独立实现面向 Coding Agent 的可审计长期记忆运行时, 采用 Markdown 真相源, SQLite 状态库与 LanceDB 混合检索, 支持证据门控和断点续传; 建立 {test_count} 项自动化测试, 覆盖率 {coverage_percent}%.
- 在 {retrieval_query_count} 条跨仓库隔离查询上取得 Recall@5 {retrieval_recall_at_5}%, MRR {retrieval_mrr}, P95 延迟 {retrieval_p95_latency_ms} ms; 删除索引后从 Markdown 重建一致率 {rebuild_consistency}%.
- 完成 {coding_run_count} 次隐藏验证器 CodingMemoryBench 隔离实验; memory-on 将任务通过率由 {coding_pass_rate_off}% 提升至 {coding_pass_rate_on}% (+{coding_pass_rate_delta_pp} 个百分点), 总 token 下降 {coding_token_reduction}%, 首次有效动作步数下降 {coding_first_action_reduction}%.
- 导入 LoCoMo 官方全部 {locomo_conversation_count} 个会话样本 ({locomo_session_count} 个 session, {locomo_turn_count} 条 turn), 生成 {accepted_memory_count} 条证据记忆, 记录 {rejected_memory_count} 条门控拒绝; 完成明确不计分的 {locomo_question_run_count} 问端到端 smoke, 基础设施失败为 0.

## 待完成——不可写成已测指标

{pending_lines}
