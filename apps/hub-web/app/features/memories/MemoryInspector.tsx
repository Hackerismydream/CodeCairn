import { useState } from "react";
import {
  displayValue,
  formatTime,
  memoryOriginLabels,
  memoryStatusLabels,
  memoryTypeLabels,
} from "../../../lib/hub/format";
import type { MemoriesView } from "../../../lib/hub/types";

type InspectorTab = "content" | "source" | "evolution";

export default function MemoryInspector({
  selected,
}: {
  selected: NonNullable<MemoriesView["selected"]>;
}) {
  const [tab, setTab] = useState<InspectorTab>("content");
  const { detail, history } = selected;
  const memory = detail.memory;
  const statuses = new Map(history.statuses);

  return (
    <aside className="memory-inspector" aria-label="记忆详情">
      <header className="inspector-head">
        <span>
          <code>{memory.memory_id.slice(0, 16)}</code>
          <small>{memoryTypeLabels[memory.memory_type]}</small>
        </span>
        <span
          className={`memory-status memory-status-${detail.status}`}
        >
          {memoryStatusLabels[detail.status]}
        </span>
        <h2>{memory.title}</h2>
        <p>{formatTime(memory.created_at_ms)}</p>
      </header>

      <div className="inspector-tabs" role="tablist">
        {(
          [
            ["content", "内容"],
            ["source", "来源"],
            ["evolution", "演化"],
          ] as const
        ).map(([key, label]) => (
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
          <p className="memory-content">{memory.content}</p>
          <dl className="memory-definition">
            <div>
              <dt>来源方式</dt>
              <dd>{memoryOriginLabels[memory.origin]}</dd>
            </div>
            <div>
              <dt>分类</dt>
              <dd>{memory.category}</dd>
            </div>
            <div>
              <dt>记忆 ID</dt>
              <dd>{memory.memory_id}</dd>
            </div>
            <div>
              <dt>任务片段</dt>
              <dd>{memory.episode_id ?? "无"}</dd>
            </div>
          </dl>
          <div className="payload-block">
            <h3>结构化内容</h3>
            {Object.entries(memory.payload).map(([key, value]) => (
              <div key={key}>
                <code>{key}</code>
                <span>{displayValue(value)}</span>
              </div>
            ))}
          </div>
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
        <section
          className="inspector-section evidence-list"
          role="tabpanel"
        >
          <p className="inspector-explainer">
            证据事实由规范化事件确定性生成；模型不能编造这些字段。
          </p>
          {memory.facts.length ? (
            memory.facts.map((fact, index) => (
              <article className="evidence-item" key={fact.fact_id}>
                <span>{index + 1}</span>
                <div>
                  <small>
                    {fact.fact_kind}
                    {fact.role ? ` · ${fact.role}` : ""}
                  </small>
                  <strong>{fact.value}</strong>
                  <details>
                    <summary>查看精确来源</summary>
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
              <strong>没有证据事实</strong>
              <p>CodeCairn 不会为智能体声明的内容伪造事件证据。</p>
            </div>
          )}
        </section>
      ) : (
        <section
          className="inspector-section evolution-list"
          role="tabpanel"
        >
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
                {record.relation_kind} · {record.proposer}
              </code>
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
