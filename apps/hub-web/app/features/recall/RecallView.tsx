"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  isHubConnectionFailure,
  type HubClient,
} from "../../../lib/hub/client";
import { memoryTypeLabels } from "../../../lib/hub/format";
import { createRequestGate } from "../../../lib/hub/memory-state";
import {
  activeScopesLabel,
  memoryScopeLabel,
} from "../../../lib/hub/scope";
import type {
  MemoryScope,
  RecallView as RecallData,
} from "../../../lib/hub/types";
import RequestError from "../../components/RequestError";

const candidateSourceLabels = {
  lexical: "关键词",
  vector: "向量",
} as const;

const admissionReasonLabels: Record<string, string> = {
  relevant_candidate: "候选记忆与任务相关",
  pinned_work_state: "固定工作状态",
  no_candidates: "没有候选记忆",
  below_threshold: "相关性低于门槛",
};

export function recallProcessingLabel(freshness: string, semanticState: string): string {
  if (semanticState === "failed") return "本次召回来自已同步索引；后续记忆提炼失败";
  return freshness === "semantic_pending" || semanticState === "pending"
    ? "本次关键词与向量检索已完成；后续记忆提炼待处理"
    : "本次检索与后续记忆提炼均已完成";
}

export function RecallLibraryContext({
  personId,
  requestingClient,
  activeScopes,
}: {
  personId: string;
  requestingClient: "hub" | undefined;
  activeScopes: MemoryScope[] | undefined;
}) {
  return (
    <div className="library-recall-context">
      <span>调用方：{requestingClient ?? "未报告"}</span>
      <span>Person：{personId.slice(0, 15)}</span>
      <span>生效范围：{activeScopesLabel(activeScopes)}</span>
    </div>
  );
}

export function RecallScopeSource({
  effectiveScope,
  sourceRepositoryKey,
}: {
  effectiveScope: MemoryScope | undefined;
  sourceRepositoryKey: string | undefined;
}) {
  if (effectiveScope === undefined) return null;
  return (
    <small>
      {memoryScopeLabel(effectiveScope)} · 来源 {sourceRepositoryKey ?? "未报告"}
    </small>
  );
}

export function ShadowedPreferenceNotices({
  shadowed,
}: {
  shadowed: NonNullable<
    RecallData["result"]["sidecar"]["shadowed"]
  >;
}) {
  return shadowed.map((item) => (
    <div className="omission-row" key={item.promotion_id}>
      <code>{item.promotion_id.slice(0, 16)}</code>
      <strong>
        全局偏好被当前仓库同主题偏好覆盖（{item.shadowed_by_memory_ids.length} 条）
      </strong>
    </div>
  ));
}

const omissionReasonLabels: Record<string, string> = {
  historical_filter: "旧版本默认不召回",
  relevance: "与当前任务相关性不足",
  type_cap: "同类记忆已达到上限",
  limit: "已达到本次召回数量上限",
  token_budget: "上下文容量不足",
};

export default function RecallView({
  client,
  onConnected,
  onUnavailable,
}: {
  client: HubClient;
  onConnected: (repoKey: string) => void;
  onUnavailable: () => void;
}) {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [data, setData] = useState<RecallData | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const requestController = useRef<AbortController | null>(null);
  const requestGate = useRef(createRequestGate());

  useEffect(
    () => () => {
      requestController.current?.abort();
      requestGate.current.invalidate();
    },
    [],
  );

  async function runRecall(task: string) {
    const normalized = task.trim();
    if (!normalized || requestController.current) return;
    const controller = new AbortController();
    const requestToken = requestGate.current.begin();
    requestController.current = controller;
    setLoading(true);
    setError(null);
    setSubmittedQuery(normalized);
    try {
      const result = await client.recall(
        {
          query: normalized,
          limit: 20,
          tokenBudget: 8192,
        },
        controller.signal,
      );
      if (!requestGate.current.isCurrent(requestToken)) return;
      setData(result);
      onConnected(result.result.sidecar.repo_key);
    } catch (reason) {
      if (
        !controller.signal.aborted &&
        requestGate.current.isCurrent(requestToken)
      ) {
        setError(reason instanceof Error ? reason : new Error(String(reason)));
        if (isHubConnectionFailure(reason)) onUnavailable();
      }
    } finally {
      if (requestGate.current.isCurrent(requestToken)) {
        requestController.current = null;
        setLoading(false);
      }
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void runRecall(query);
  }

  const sidecar = data?.result.sidecar;
  const admission = sidecar?.admission_trace;
  const context = sidecar?.context_trace;

  return (
    <section className="view-shell">
      <header className="page-heading">
        <h1>召回</h1>
        <p>输入当前任务，查看 Myna 为什么接纳、遮蔽或拒绝一条记忆。</p>
      </header>

      <form className="recall-form" onSubmit={submit}>
        <label htmlFor="recall-query">当前任务</label>
        <textarea
          id="recall-query"
          value={query}
          maxLength={32768}
          placeholder="例如：修复重启后的重复投递"
          onChange={(event) => setQuery(event.target.value)}
        />
        <button type="submit" disabled={loading || !query.trim()}>
          {loading ? "正在召回" : "召回记忆"}
        </button>
      </form>

      {error ? (
        <RequestError
          error={error}
          retry={() => void runRecall(submittedQuery || query)}
        />
      ) : !data || !sidecar ? (
        <div className="empty-state recall-placeholder">
          <strong>等待真实任务</strong>
          <p>这里不会预填固定的召回样例。</p>
        </div>
      ) : (
        <>
          <div className="query-summary">
            <span>任务</span>
            <strong>{sidecar.query}</strong>
            <small>{sidecar.latency_ms.toFixed(1)} ms</small>
          </div>

          {sidecar.person_id ? (
            <RecallLibraryContext
              personId={sidecar.person_id}
              requestingClient={sidecar.requesting_client}
              activeScopes={sidecar.active_scopes}
            />
          ) : null}

          <section className="admission-panel">
            <div className="admission-summary">
              <span
                className={`admission-outcome admission-outcome-${
                  admission?.outcome ?? "abstained"
                }`}
              >
                {admission?.outcome === "admitted" ? "已接纳" : "未接纳记忆"}
              </span>
              <div>
                <h2>
                  {admission?.outcome === "admitted"
                    ? "找到与当前任务相关的记忆"
                    : "没有记忆通过相关性门槛"}
                </h2>
                <p>
                  {admissionReasonLabels[
                    admission?.reason ?? "no_admission_trace"
                  ] ?? "没有接纳记录"}
                  。最高向量相似度 {admission?.max_vector_score?.toFixed(3) ?? "无"}（接纳阈值{" "}
                  {admission ? admission.vector_threshold.toFixed(2) : "无"}）。
                </p>
              </div>
            </div>
            <details className="admission-technical">
              <summary>检索技术详情</summary>
              <dl className="admission-trace">
                <div><dt>检索配置</dt><dd>{sidecar.retrieval_profile}</dd></div>
                <div><dt>接纳策略</dt><dd>{admission?.policy ?? "无"}</dd></div>
                <div><dt>源游标</dt><dd>{sidecar.source_cursor}</dd></div>
                <div><dt>索引游标</dt><dd>{sidecar.index_cursor}</dd></div>
              </dl>
            </details>
          </section>

          <dl className="recall-metrics">
            <div>
              <dt>关键词候选</dt>
              <dd>{sidecar.lexical_candidate_count}</dd>
            </div>
            <div>
              <dt>向量候选</dt>
              <dd>{sidecar.vector_candidate_count}</dd>
            </div>
            <div>
              <dt>最终接纳</dt>
              <dd>{sidecar.ranked.length}</dd>
            </div>
            <div>
              <dt>上下文预算</dt>
              <dd>
                {context?.token_count ?? 0} / {context?.token_limit ?? 0}
              </dd>
            </div>
          </dl>

          <section className="candidate-panel">
            <header>
              <h2>排序结果</h2>
              <span>{sidecar.ranked.length} 条</span>
            </header>
            <p className="contract-caption">排序分只用于本次结果排序，不与向量相似度或接纳阈值直接比较。</p>
            {sidecar.ranked.length ? (
              <>
                <div className="candidate-head" aria-hidden="true">
                  <span>顺位</span>
                  <span>记忆</span>
                  <span>命中方式</span>
                  <span>排序分</span>
                  <span>摘要</span>
                </div>
                {sidecar.ranked.map((candidate) => (
                  <article className="candidate-row" key={candidate.memory_id}>
                    <span className="candidate-rank">{candidate.rank}</span>
                    <span className="candidate-memory">
                      <strong>{candidate.title}</strong>
                      <small>{memoryTypeLabels[candidate.memory_type]}</small>
                      <RecallScopeSource
                        effectiveScope={candidate.effective_scope}
                        sourceRepositoryKey={candidate.source?.repository_key}
                      />
                      <code>{candidate.memory_id.slice(0, 16)}</code>
                    </span>
                    <span className="candidate-sources">
                      {candidate.candidate_sources.map((source) => (
                        <span key={source}>
                          {candidateSourceLabels[source]}
                        </span>
                      ))}
                    </span>
                    <span className="candidate-score">
                      {candidate.final_score.toFixed(3)}
                    </span>
                    <p>
                      {candidate.snippets[0]?.text ?? candidate.summary}
                    </p>
                    <code className="candidate-uri">
                      {candidate.source_uri}
                    </code>
                  </article>
                ))}
              </>
            ) : (
              <div className="empty-state recall-empty">
                <strong>没有接纳任何记忆</strong>
                <p>拒答是召回接纳的正常结果。</p>
              </div>
            )}
          </section>

          <div className="recall-output-grid">
            <section className="compiled-context">
              <header>
                <div>
                  <h2>编译后的召回上下文</h2>
                  <p>这是智能体调用方实际收到的召回上下文。</p>
                </div>
                <code>{context?.renderer ?? "无渲染器"}</code>
              </header>
              <pre>{data.result.markdown}</pre>
              <dl>
                <div>
                  <dt>处理状态</dt>
                  <dd>{recallProcessingLabel(sidecar.freshness, sidecar.semantic_state)}</dd>
                </div>
              </dl>
            </section>
            <section className="omission-panel">
              <header>
                <h2>未进入上下文</h2>
                <span>{sidecar.omissions.length} 条</span>
              </header>
              <ShadowedPreferenceNotices shadowed={sidecar.shadowed ?? []} />
              {sidecar.omissions.map((omission) => (
                <div
                  className="omission-row"
                  key={`${omission.memory_id}-${omission.reason}`}
                >
                  <code>{omission.memory_id.slice(0, 16)}</code>
                  <strong>
                    {omissionReasonLabels[omission.reason] ?? omission.reason}
                  </strong>
                </div>
              ))}
              {!sidecar.omissions.length ? (
                <p>没有候选因生命周期、相关性或预算被省略。</p>
              ) : null}
            </section>
          </div>
        </>
      )}
    </section>
  );
}
