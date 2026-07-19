# 简历证据 — CodeCairn

- 独立实现面向 Coding Agent 的可审计长期记忆运行时, 采用 Markdown 真相源, SQLite 状态库与 LanceDB 混合检索, 支持证据门控和断点续传; 建立 169 项自动化测试, 覆盖率 83.85%.
- 在 100 条跨仓库隔离查询上取得 Recall@5 96%, MRR 0.798, P95 延迟 10.91 ms; 删除索引后从 Markdown 重建一致率 100%.
- 完成 120 次隐藏验证器 CodingMemoryBench 隔离实验; memory-on 将任务通过率由 85% 提升至 100% (+15 个百分点), 总 token 下降 2.26%, 首次有效动作步数下降 3.41%.
- 在 LoCoMo 官方类别 1-4 的 1540 问全量评测中, 每题执行 3 次独立评审且基础设施失败为 0; LoCoMo 准确率 47.73%, 共导入 272 个 session 和 5882 条 turn.

## 待完成——不可写成已测指标

- CodingMemoryBench provider cost: pending.
