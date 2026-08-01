import { useState } from "react";
import {
  displayValue,
  formatTime,
  memoryOriginLabels,
  memoryStatusLabels,
  memoryTypeLabels,
} from "../../../lib/hub/format";
import { memoryScopeLabel } from "../../../lib/hub/scope";
import type { MemoriesView } from "../../../lib/hub/types";
import type { GovernanceClient } from "../../../lib/governance/client";
import { HubApiError } from "../../../lib/hub/client";

type InspectorTab = "content" | "source" | "evolution";
const INSPECTOR_TABS = [["content", "内容"], ["source", "来源"], ["evolution", "演化"]] as const;
const factKindLabels: Record<string, string> = {
  message: "消息", command: "命令", command_result: "命令结果",
  file_change: "文件变更", tool_call: "工具调用",
  tool_result: "工具结果", verification: "验证事实",
};

export function promotionErrorMessage(reason: unknown): string {
  if (!(reason instanceof HubApiError)) return "无法更新偏好范围，请重试。";
  if (reason.code === "global_preference_conflict") {
    return "同一主题已有用于所有仓库的偏好。";
  }
  if (
    reason.code === "preference_not_eligible" ||
    reason.code === "memory_not_found"
  ) {
    return "这条偏好已变化，请刷新记忆列表。";
  }
  if (reason.code === "hub_unavailable" || reason.code === "unauthorized") {
    return "本地 Myna 治理模块当前不可用。";
  }
  return "无法更新偏好范围，请刷新后重试。";
}

export default function MemoryInspector({
  selected,
  governanceClient,
}: {
  selected: NonNullable<MemoriesView["selected"]>;
  governanceClient: GovernanceClient;
}) {
  const [tab, setTab] = useState<InspectorTab>("content");
  const [promotionState, setPromotionState] = useState(
    selected.governance?.state,
  );
  const [promotionReceipt, setPromotionReceipt] = useState<string | null>(null);
  const [promotionError, setPromotionError] = useState<string | null>(null);
  const [promoting, setPromoting] = useState(false);
  const { detail, history } = selected;
  const memory = detail.memory;
  const statuses = new Map(history.statuses);
  const references = memory.facts.map((fact) => fact.reference);
  const providers = [
    ...new Set(references.map((reference) => reference.provider)),
  ].map((provider) => provider[0].toUpperCase() + provider.slice(1));
  const sessions = [...new Set(references.map((reference) => reference.session_id))];
  const commandResult = memory.facts.find((fact) => fact.fact_kind === "command_result");
  const fileChange = memory.facts.find((fact) => fact.fact_kind === "file_change");
  const outcome = commandResult?.attributes.outcome;
  const outcomeLabel = outcome === "success" ? "成功" : outcome === "failure" ? "失败" : "未知";
  const exitCodeLabel = commandResult?.attributes.exit_code === undefined ? "未捕获" : displayValue(commandResult.attributes.exit_code);
  const definitions = [
    ["生效范围", memoryScopeLabel(selected.effective_scope)],
    ["来源仓库", selected.source_repository_key ?? "来源仓库未报告"],
    ["来源方式", memoryOriginLabels[memory.origin]],
    ["分类", memory.category],
    ["记忆 ID", memory.memory_id],
    ["任务片段", memory.episode_id ?? "无"],
  ] as const;

  async function promotePreference() {
    if (promoting || !selected.governance?.eligible) return;
    setPromoting(true);
    setPromotionError(null);
    try {
      const response = await governanceClient.promotePreference(memory.memory_id);
      setPromotionState("promoted");
      setPromotionReceipt(
        response.receipt.outcome === "created"
          ? `已用于所有仓库 · ${response.receipt.promotion.promotion_id.slice(0, 20)}`
          : `此前已用于所有仓库 · ${response.receipt.promotion.promotion_id.slice(0, 20)}`,
      );
    } catch (reason) {
      setPromotionError(promotionErrorMessage(reason));
    } finally {
      setPromoting(false);
    }
  }

  return (
    <aside className="memory-inspector" aria-label="记忆详情">
      <header className="inspector-head">
        <span>
          <code>{memory.memory_id.slice(0, 16)}</code>
          <small>{memoryTypeLabels[memory.memory_type]}</small>
        </span>
        <span className={`memory-status memory-status-${detail.status}`}>
          召回状态：{memoryStatusLabels[detail.status]}
        </span>
        <h2>{memory.title}</h2>
        <p>
          {formatTime(memory.created_at_ms)} · 证据状态：
          {memory.facts.length ? `已附 ${memory.facts.length} 条证据事实` : "未附证据事实"}
        </p>
        {references.length ? <p>来源 Agent：{providers.join("、")} · 会话：<code>{sessions.join("、")}</code></p> : null}
        {commandResult ? (
          <p>
            首条命令结果证据：<code>{commandResult.value}</code> · 该次结果：{outcomeLabel} ·
            退出码 {exitCodeLabel}
          </p>
        ) : null}
        {fileChange ? <p>首条文件变更证据：<code>{fileChange.value}</code></p> : null}
        {memory.memory_type === "user_preference" && selected.governance ? (
          <div className="promotion-control">
            <div>
              <strong>偏好范围</strong>
              <small>
                {promotionState === "promoted"
                  ? "所有仓库"
                  : selected.governance.error_code === "global_preference_conflict"
                    ? "同一主题已有全局偏好"
                    : "仅当前仓库"}
              </small>
            </div>
            {selected.governance.eligible && promotionState !== "promoted" ? (
              <button type="button" disabled={promoting} onClick={() => void promotePreference()}>
                {promoting ? "正在保存" : "用于所有仓库"}
              </button>
            ) : null}
            {promotionReceipt ? <p className="promotion-receipt">{promotionReceipt}</p> : null}
            {promotionError ? <p className="promotion-error">{promotionError}</p> : null}
          </div>
        ) : null}
      </header>

      <div className="inspector-tabs" role="tablist">
        {INSPECTOR_TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "content" ? (
        <section className="inspector-section" role="tabpanel">
          {!memory.facts.length && memory.origin === "agent_asserted" ? (
            <p className="inspector-explainer">这是智能体声明，不代表已经附有证据事实。</p>
          ) : null}
          <p className="memory-content">{memory.content}</p>
          <dl className="memory-definition">
            {definitions.map(([term, value]) => (
              <div key={term}>
                <dt>{term}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          <details className="payload-block">
            <summary>结构化内容</summary>
            {Object.entries(memory.payload).map(([key, value]) => (
              <div key={key}>
                <code>{key}</code>
                <span>
                  {key === "verification_fact_ids" &&
                  Array.isArray(value) &&
                  !value.length
                    ? "未显式标记验证事实"
                    : displayValue(value)}
                </span>
              </div>
            ))}
          </details>
          {memory.tags.length ? (
            <div className="tag-list" aria-label="标签">
              {memory.tags.map((tag) => (
                <span key={tag}>{tag}</span>
              ))}
            </div>
          ) : null}
          <div className="truth-note">
            <strong>记忆资源 URI</strong>
            <code>{detail.resource_uri}</code>
          </div>
        </section>
      ) : tab === "source" ? (
        <section className="inspector-section evidence-list" role="tabpanel">
          <p className="inspector-explainer">证据事实由规范化事件确定性生成；模型不能编造这些字段。</p>
          {memory.facts.length ? (
            memory.facts.map((fact, index) => (
              <article className="evidence-item" key={fact.fact_id}>
                <span>{index + 1}</span>
                <div>
                  <small>
                    {factKindLabels[fact.fact_kind] ?? fact.fact_kind}
                    {fact.role ? ` · ${fact.role}` : ""}
                  </small>
                  <strong>{fact.value}</strong>
                  <details>
                    <summary>查看技术溯源</summary>
                    <dl>
                      {Object.entries(fact.reference).map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>{String(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  </details>
                </div>
              </article>
            ))
          ) : (
            <div className="empty-state compact">
              <strong>未附证据事实</strong>
              <p>Myna 不会为智能体声明的内容伪造事件证据。</p>
            </div>
          )}
        </section>
      ) : (
        <section className="inspector-section evolution-list" role="tabpanel">
          <p className="inspector-explainer">默认不召回的记忆仍保留在历史中；是否参与默认召回以每条记忆的召回状态为准。</p>
          {history.memories.map((revision) => (
            <article
              className={`evolution-memory ${
                revision.memory_id === memory.memory_id ? "current" : ""
              }`}
              key={revision.memory_id}
            >
              <small>
                {memoryStatusLabels[
                  statuses.get(revision.memory_id) ?? "active"
                ]}
              </small>
              <strong>{revision.title}</strong>
              <code>{revision.memory_id}</code>
            </article>
          ))}
          {history.evolutions.map((record) => (
            <div className="evolution-reason" key={record.evolution_id}>
              <small>{formatTime(record.created_at_ms)}</small>
              <p>{record.reason}</p>
              <code>
                {record.predecessor_id.slice(0, 16)} →{" "}
                {record.successor_id.slice(0, 16)}
              </code>
              <small>
                {record.relation_kind} · {record.proposer}
              </small>
            </div>
          ))}
          {!history.evolutions.length ? (
            <div className="empty-state compact">
              <strong>尚无演化记录</strong>
              <p>这条记忆目前没有替代关系历史。</p>
            </div>
          ) : null}
        </section>
      )}
    </aside>
  );
}
