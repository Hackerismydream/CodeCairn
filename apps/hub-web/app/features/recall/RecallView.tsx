"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  isHubConnectionFailure,
  type HubClient,
} from "../../../lib/hub/client";
import { memoryTypeLabels } from "../../../lib/hub/format";
import { createRequestGate } from "../../../lib/hub/memory-state";
import type { RecallView as RecallData } from "../../../lib/hub/types";
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

const freshnessLabels: Record<string, string> = {
  fresh: "最新",
  semantic_pending: "等待语义处理",
};

const semanticStateLabels: Record<string, string> = {
  complete: "完整",
  pending: "等待处理",
  failed: "处理失败",
  disabled: "已关闭",
};

const omissionReasonLabels: Record<string, string> = {
  historical_filter: "历史记忆已过滤",
  relevance: "相关性不足",
  type_cap: "类型数量达到上限",
  limit: "召回数量达到上限",
  token_budget: "超出上下文预算",
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
        <p>输入当前任务，查看 CodeCairn 为什么接纳或拒绝一条记忆。</p>
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

          <section className="admission-panel">
            <div className="admission-summary">
              <span
                className={`admission-outcome admission-outcome-${
                  admission?.outcome ?? "abstained"
                }`}
              >
                {admission?.outcome === "admitted" ? "已接纳" : "已拒答"}
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
                  ] ?? "没有接纳记录"}{" "}
                  ·{" "}
                  {admission?.policy ?? "无接纳策略"}
                </p>
              </div>
            </div>
            <dl className="admission-trace">
              <div>
                <dt>检索配置</dt>
                <dd>{sidecar.retrieval_profile}</dd>
              </div>
              <div>
                <dt>向量阈值</dt>
                <dd>{admission?.vector_threshold ?? "无"}</dd>
              </div>
              <div>
                <dt>最高向量分</dt>
                <dd>{admission?.max_vector_score ?? "无"}</dd>
              </div>
              <div>
                <dt>源游标</dt>
                <dd>{sidecar.source_cursor}</dd>
              </div>
              <div>
                <dt>索引游标</dt>
                <dd>{sidecar.index_cursor}</dd>
              </div>
            </dl>
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
            {sidecar.ranked.length ? (
              <>
                <div className="candidate-head" aria-hidden="true">
                  <span>顺位</span>
                  <span>记忆</span>
                  <span>来源</span>
                  <span>分数</span>
                  <span>摘要</span>
                </div>
                {sidecar.ranked.map((candidate) => (
                  <article className="candidate-row" key={candidate.memory_id}>
                    <span className="candidate-rank">{candidate.rank}</span>
                    <span className="candidate-memory">
                      <strong>{candidate.title}</strong>
                      <small>{memoryTypeLabels[candidate.memory_type]}</small>
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
                  <dt>新鲜度</dt>
                  <dd>
                    {sidecar.semantic_state === "failed"
                      ? "语义处理失败"
                      : (freshnessLabels[sidecar.freshness] ??
                        sidecar.freshness)}
                  </dd>
                </div>
                <div>
                  <dt>语义状态</dt>
                  <dd>
                    {semanticStateLabels[sidecar.semantic_state] ??
                      sidecar.semantic_state}
                  </dd>
                </div>
              </dl>
            </section>
            <section className="omission-panel">
              <header>
                <h2>省略记录</h2>
                <span>{sidecar.omissions.length} 条</span>
              </header>
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
