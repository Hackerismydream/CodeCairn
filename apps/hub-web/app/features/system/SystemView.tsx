"use client";

import { useEffect, useState } from "react";
import {
  isHubConnectionFailure,
  type HubClient,
} from "../../../lib/hub/client";
import { formatTime } from "../../../lib/hub/format";
import {
  providerIndicatorClass,
  queueIndicatorClass,
  recallReadinessIndicatorClass,
  recallReadinessLabel,
  remediationLabel,
  systemValueLabel,
  visibleQueueEntries,
} from "../../../lib/hub/system-display";
import type { SystemView as SystemData } from "../../../lib/hub/types";
import RequestError from "../../components/RequestError";

const subsystemLabels: Record<string, string> = {
  config: "配置",
  hooks: "会话钩子",
  index_queue: "索引队列",
  lancedb: "搜索索引",
  markdown: "Markdown 真源",
  privacy: "隐私",
  semantic_queue: "语义队列",
  source_import: "来源导入",
  sqlite: "运行状态",
};

const subsystemStatusLabels: Record<string, string> = {
  ok: "正常",
  degraded: "异常",
  not_configured: "未配置",
};

const queueStateLabels: Record<string, string> = {
  pending: "待处理",
  leased: "处理中",
  indexed: "已索引",
  completed: "已完成",
  failed: "失败",
  stale: "已过期",
};

const providerLabels: Record<string, string> = {
  retrieval: "检索",
  retrieval_state: "检索状态",
  semantic: "语义提取",
  semantic_state: "语义状态",
};

const privacyLabels: Record<string, string> = {
  storage: "记忆存储",
  embedding: "向量计算",
  semantic_extraction: "语义提取",
  source_content_egress: "来源内容外发",
};

export default function SystemView({
  client,
  onConnected,
  onUnavailable,
}: {
  client: HubClient;
  onConnected: (repoKey: string) => void;
  onUnavailable: () => void;
}) {
  const [data, setData] = useState<SystemData | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    client
      .system(controller.signal)
      .then((result) => {
        setData(result);
        onConnected(result.repo_key);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason : new Error(String(reason)));
          if (isHubConnectionFailure(reason)) onUnavailable();
        }
      });
    return () => controller.abort();
  }, [client, onConnected, onUnavailable, reload]);

  const indexJobs = data ? visibleQueueEntries(data.index_jobs) : [];
  const semanticJobs = data ? visibleQueueEntries(data.semantic_jobs) : [];
  const recallNeedsConfiguration =
    data?.recall_readiness.state !== "configuration_ready";

  return (
    <section className="view-shell">
      <header className="page-heading">
        <h1>系统</h1>
        <p>一次即时系统诊断快照，不代表后台常驻或持续监控。</p>
      </header>
      {error ? (
        <RequestError
          error={error}
          retry={() => {
            setError(null);
            setReload((value) => value + 1);
          }}
        />
      ) : !data ? (
        <div className="empty-state">
          <strong>正在读取系统状态</strong>
        </div>
      ) : (
        <>
          <section className="doctor-summary">
            <div>
              <span
                className={`status-dot ${
                  data.status === "ok" && !recallNeedsConfiguration
                    ? ""
                    : "status-failed"
                }`}
              />
              <span>
                <strong>
                  {data.status !== "ok"
                    ? "当前状态需要处理"
                    : recallNeedsConfiguration
                      ? "基础状态正常，召回需配置"
                      : "当前状态正常"}
                </strong>
                <code>
                  {data.runtime_schema} · {formatTime(data.observed_at_ms)}
                </code>
              </span>
            </div>
            <dl
              className={
                data.counts.conflicted_recovery > 0
                  ? "has-conflict"
                  : undefined
              }
            >
              <div>
                <dt>记忆</dt>
                <dd>{data.counts.memories}</dd>
              </div>
              <div>
                <dt>导入</dt>
                <dd>{data.counts.imports}</dd>
              </div>
              <div>
                <dt>事件</dt>
                <dd>{data.counts.observed_events}</dd>
              </div>
              <div>
                <dt>待恢复</dt>
                <dd>{data.counts.pending_recovery}</dd>
              </div>
              {data.counts.conflicted_recovery > 0 ? (
                <div className="metric-failed">
                  <dt>恢复冲突</dt>
                  <dd>{data.counts.conflicted_recovery}</dd>
                </div>
              ) : null}
            </dl>
          </section>

          <div className="system-grid">
            <section className="status-group">
              <h2>子系统</h2>
              <div>
                {Object.entries(data.subsystems).map(([key, value]) => (
                  <div className="status-row" key={key}>
                    <span
                      className={`status-dot ${
                        value.status === "ok"
                          ? ""
                          : value.status === "degraded"
                            ? "status-failed"
                            : "status-quiet"
                      }`}
                    />
                    <strong>{subsystemLabels[key] ?? key}</strong>
                    <span>
                      {subsystemStatusLabels[value.status] ?? value.status}
                    </span>
                    {value.status === "ok" ? null : (
                      <small>{remediationLabel(value.remediation)}</small>
                    )}
                  </div>
                ))}
              </div>
            </section>

            <section className="status-group">
              <h2>队列</h2>
              <div>
                {indexJobs.map(([key, value]) => (
                  <div className="status-row" key={`index-${key}`}>
                    <span
                      className={`status-dot ${queueIndicatorClass(key, value)}`}
                    />
                    <strong>索引 · {queueStateLabels[key] ?? key}</strong>
                    <span>{value}</span>
                    <small>索引队列</small>
                  </div>
                ))}
                {semanticJobs.map(([key, value]) => (
                  <div className="status-row" key={`semantic-${key}`}>
                    <span
                      className={`status-dot ${queueIndicatorClass(key, value)}`}
                    />
                    <strong>语义 · {queueStateLabels[key] ?? key}</strong>
                    <span>{value}</span>
                    <small>语义队列</small>
                  </div>
                ))}
                {!indexJobs.length && !semanticJobs.length ? (
                  <p className="status-group-empty">当前没有队列任务。</p>
                ) : null}
              </div>
            </section>

            <section className="status-group">
              <h2>提供方</h2>
              <div>
                <div className="status-row">
                  <span
                    className={`status-dot ${recallReadinessIndicatorClass(
                      data.recall_readiness.state,
                      data.recall_readiness.live_checked,
                    )}`}
                  />
                  <strong>召回准备</strong>
                  <span>
                    {recallReadinessLabel(
                      data.recall_readiness.state,
                      data.recall_readiness.live_checked,
                    )}
                  </span>
                  {data.recall_readiness.remediation ? (
                    <small>
                      {remediationLabel(data.recall_readiness.remediation)}
                    </small>
                  ) : (
                    <small>{data.recall_readiness.profile}</small>
                  )}
                </div>
                {Object.entries(data.providers)
                  .filter(([key]) => key !== "retrieval_state")
                  .map(([key, value]) => (
                    <div className="status-row" key={key}>
                      <span
                        className={`status-dot ${providerIndicatorClass(
                          key,
                          value,
                        )}`}
                      />
                      <strong>{providerLabels[key] ?? key}</strong>
                      <span>{systemValueLabel(value)}</span>
                      <small>当前配置</small>
                    </div>
                  ))}
              </div>
            </section>

            <section className="status-group">
              <h2>隐私</h2>
              <div>
                {Object.entries(data.privacy).map(([key, value]) => (
                  <div className="status-row" key={key}>
                    <span className="status-dot status-quiet" />
                    <strong>{privacyLabels[key] ?? key}</strong>
                    <span>
                      {systemValueLabel(value)}
                    </span>
                    <small>系统诊断快照</small>
                  </div>
                ))}
              </div>
            </section>
          </div>
          <p className="doctor-note">
            系统页不触发提供方在线检查，也不会把运行目录暴露给浏览器。
          </p>
        </>
      )}
    </section>
  );
}
