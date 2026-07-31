"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  HubApiError,
  isHubConnectionFailure,
} from "../../../lib/hub/client";
import { formatTime } from "../../../lib/hub/format";
import {
  canInstallCapture,
  type OnboardingClient,
} from "../../../lib/onboarding/client";
import type {
  CaptureClientKind,
  ContinuousState,
  HistoricalState,
  OnboardingApplyResult,
  OnboardingClientKind,
  OnboardingPreview,
  SourceCandidate,
} from "../../../lib/onboarding/types";
import RequestError from "../../components/RequestError";

const clientLabels: Record<OnboardingClientKind, string> = { codex: "Codex", claude: "Claude Code", pico: "Pico" };
const historicalLabels: Record<HistoricalState, string> = { available: "发现可导入历史", none_found: "未发现当前仓库历史", unsupported: "不支持旧会话扫描", unresolved: "部分记录无法安全归属" };
const continuousLabels: Record<ContinuousState, string> = { available: "可安装持续获取", installed: "已检测到持续接入", not_detected: "尚未检测到持续获取", manual_setup_required: "需要手动配置", unsupported: "不支持自动接入" };
const importStateLabels: Record<SourceCandidate["import_state"], string> = { new: "首次导入", incremental: "增量导入", already_imported: "已导入" };

const eventLabels = { stop: "任务停止时", session_end: "会话结束时" } as const;
const actionErrorLabels: Record<string, string> = {
  trace_invalid: "来源记录不完整",
  import_failed: "历史导入失败",
  unsupported_client: "客户端不受支持",
  hook_config_invalid: "持续获取配置无效",
  hook_preview_stale: "客户端配置已变化",
  hook_config_readback_failed: "无法验证持续获取配置",
  hook_write_failed: "无法写入持续获取配置",
};
const resultLabels = {
  outcome: { complete: "接入完成", noop: "无需变更", partial: "部分完成", failed: "接入失败" },
  import: { imported: "已导入", noop: "无新增内容", failed: "导入失败" },
  capture: { installed: "已安装", already_installed: "原已安装", failed: "安装失败" },
} as const;
const retentionColumns = [
  ["会保留", ["本地来源定位信息与导入游标", "规范化的 Agent Trace 事实", "有界证据事实", "由证据派生的编码记忆"]],
  ["不会保留", ["提供方凭证", "完整的原始会话副本"]],
] as const;

function actionError(code: string | null): string {
  return code ? `${actionErrorLabels[code] ?? "执行失败"}（支持编号 ${code}）` : "";
}

export function presentableError(reason: unknown): HubApiError {
  const safeCodes = ["snapshot_stale", "consent_expired", "consent_invalid", "invalid_selection", "hub_unavailable", "unauthorized", "invalid_response"];
  if (reason instanceof HubApiError) {
    return new HubApiError("接入失败", {
      code: safeCodes.includes(reason.code) ? reason.code : "onboarding_failed",
      retryable: reason.retryable,
      requestId: reason.requestId,
    });
  }
  return new HubApiError("接入失败", { code: "hub_unavailable" });
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function selectItem<T>(current: T[], item: T, selected: boolean): T[] {
  return selected
    ? [...new Set([...current, item])]
    : current.filter((value) => value !== item);
}

export function ResultView({
  result,
  onRescan,
}: {
  result: OnboardingApplyResult;
  onRescan: () => void;
}) {
  const indexLabels = {
    ready: "记忆已保存，检索索引已就绪",
    pending: "记忆已保存，检索索引正在处理",
    failed: "记忆已保存，检索索引未就绪，需要修复后再召回",
    not_requested: result.totals.created_memories
      ? "记忆已保存，本次未请求检索索引"
      : "本次没有新增需要索引的记忆",
  } as const;

  return (
    <div className="onboarding-report" aria-live="polite">
      <header>
        <small>真实执行报告</small>
        <h2>{resultLabels.outcome[result.outcome]}</h2>
        <p>{result.repo_key}</p>
      </header>
      <dl className="report-totals">
        {([[
          "导入会话", result.totals.imported_sessions,
        ], [
          "新建记忆", result.totals.created_memories,
        ], [
          "跳过会话", result.totals.skipped_sessions,
        ], [
          "失败动作", result.totals.failed_actions,
        ]] as const).map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>

      {result.imports.length ? (
        <section className="report-section">
          <h3>历史导入</h3>
          {result.imports.map((item, index) => (
            <div className="report-row" key={item.source_id}>
              <span>
                <strong>{clientLabels[item.client]}</strong>
                <small>历史会话 {index + 1}</small>
              </span>
              <span>
                {resultLabels.import[item.outcome]} · 新建 {item.created_memory_count} 条
                {item.error_code ? ` · ${actionError(item.error_code)}` : ""}
              </span>
            </div>
          ))}
        </section>
      ) : null}

      {result.capture.length ? (
        <section className="report-section">
          <h3>持续获取</h3>
          {result.capture.map((item) => (
            <div className="report-row" key={item.client}>
              <strong>{clientLabels[item.client]}</strong>
              <span>
                {resultLabels.capture[item.outcome]} · {eventLabels[item.event]}
                {item.error_code ? ` · ${actionError(item.error_code)}` : ""}
              </span>
            </div>
          ))}
        </section>
      ) : null}

      <div className="report-index">
        <span>检索索引</span>
        <strong>{indexLabels[result.index_state]}</strong>
      </div>
      <div className="action-row">
        {result.requires_new_preview ? (
          <button className="secondary-action" type="button" onClick={onRescan}>
            重新扫描
          </button>
        ) : null}
        <Link className="primary-action" href="/">
          回到真实记忆
        </Link>
      </div>
    </div>
  );
}

export default function OnboardingView({
  client,
  onConnected,
  onUnavailable,
}: {
  client: OnboardingClient;
  onConnected: (repoKey: string) => void;
  onUnavailable: () => void;
}) {
  const [preview, setPreview] = useState<OnboardingPreview | null>(null);
  const [result, setResult] = useState<OnboardingApplyResult | null>(null);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [captureClients, setCaptureClients] = useState<CaptureClientKind[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"discover" | "preview" | "apply" | null>(
    "discover",
  );
  const [error, setError] = useState<Error | null>(null);
  const [reload, setReload] = useState(0);
  const actionController = useRef<AbortController | null>(null);

  const acceptPreview = useCallback(
    (next: OnboardingPreview) => {
      setPreview(next);
      setSelectedSourceIds(
        next.sources.flatMap((source) =>
          source.candidates
            .filter((candidate) => candidate.selected)
            .map((candidate) => candidate.source_id),
        ),
      );
      setCaptureClients(
        next.sources.flatMap((source) =>
          source.client !== "pico" && source.capture_selected
            ? [source.client]
            : [],
        ),
      );
      setDirty(false);
      setConfirmed(false);
      onConnected(next.repo_key);
    },
    [onConnected],
  );

  useEffect(() => {
    const controller = new AbortController();
    actionController.current = controller;
    client
      .preview({}, controller.signal)
      .then(acceptPreview)
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(presentableError(reason));
        if (isHubConnectionFailure(reason)) onUnavailable();
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          actionController.current = null;
          setBusy(null);
        }
      });
    return () => controller.abort();
  }, [acceptPreview, client, onUnavailable, reload]);

  useEffect(
    () => () => {
      actionController.current?.abort();
    },
    [],
  );

  const selectedCount = selectedSourceIds.length;
  const canApply = Boolean(
    preview?.consent_token && confirmed && !dirty && !busy,
  );
  const hasPlannedActions = Boolean(
    preview?.consent_token &&
      (preview.selected_import_count > 0 || captureClients.length > 0),
  );
  function toggleSource(sourceId: string, selected: boolean) {
    setSelectedSourceIds((current) => selectItem(current, sourceId, selected));
    setDirty(true);
    setConfirmed(false);
  }

  function toggleCapture(clientKind: OnboardingClientKind, selected: boolean) {
    if (clientKind === "pico") return;
    setCaptureClients((current) => selectItem(current, clientKind, selected));
    setDirty(true);
    setConfirmed(false);
  }

  async function runAction(
    kind: "preview" | "apply",
    action: (signal: AbortSignal) => Promise<void>,
  ) {
    if (actionController.current) return;
    const controller = new AbortController();
    actionController.current = controller;
    setBusy(kind);
    setError(null);
    try {
      await action(controller.signal);
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(presentableError(reason));
        if (isHubConnectionFailure(reason)) onUnavailable();
      }
    } finally {
      if (!controller.signal.aborted) {
        actionController.current = null;
        setBusy(null);
      }
    }
  }

  function updatePreview() {
    return runAction("preview", async (signal) => {
      const next = await client.preview(
        { selectedSourceIds, installCaptureFor: captureClients },
        signal,
      );
      acceptPreview(next);
    });
  }

  function applyPlan() {
    if (!preview?.consent_token || !canApply) return;
    return runAction("apply", async (signal) => {
      const next = await client.apply(preview, signal);
      setResult(next);
      onConnected(next.repo_key);
    });
  }

  function retryDiscovery() {
    if (actionController.current) return;
    setPreview(null);
    setResult(null);
    setError(null);
    setBusy("discover");
    setReload((value) => value + 1);
  }

  return (
    <section className="view-shell onboarding-view">
      <header className="page-heading">
        <h1>接入当前仓库的记忆</h1>
        <p>只发现能够明确归属于当前仓库的本机记录；确认前不会写入。</p>
      </header>

      {busy === "discover" && !preview ? (
        <div className="onboarding-loading" aria-live="polite">
          <strong>正在发现本机历史</strong>
          <p>扫描 Codex 与 Claude Code 的当前仓库记录，不读取 Pico 旧会话。</p>
        </div>
      ) : error && !preview ? (
        <RequestError error={error} retry={retryDiscovery} />
      ) : result ? (
        <ResultView result={result} onRescan={retryDiscovery} />
      ) : preview ? (
        <div className="onboarding-panel" aria-busy={busy !== null}>
          <header className="onboarding-summary">
            <span>
              <small>当前仓库</small>
              <strong>{preview.repo_key}</strong>
            </span>
            <span>{selectedCount} 个历史会话，{captureClients.length} 个持续获取动作</span>
          </header>

          <section className="onboarding-section">
            <div className="section-heading">
              <span>
                <small>步骤 1</small>
                <h2>选择历史与持续获取</h2>
              </span>
              {preview.truncated ? <em>结果已截断</em> : null}
            </div>
            <div className="source-ledger">
              {preview.sources.map((source) => (
                <section className="onboarding-source" key={source.client}>
                  <header>
                    <span>
                      <h3>{clientLabels[source.client]}</h3>
                      <small>{historicalLabels[source.historical_state]}</small>
                    </span>
                    <small>{continuousLabels[source.continuous_state]}</small>
                  </header>

                  {source.candidates.map((candidate, index) => (
                    <label
                      className="onboarding-candidate"
                      key={candidate.source_id}
                    >
                      <input
                        type="checkbox"
                        checked={selectedSourceIds.includes(candidate.source_id)}
                        disabled={busy !== null}
                        onChange={(event) =>
                          toggleSource(candidate.source_id, event.target.checked)
                        }
                      />
                      <span>
                        <strong>
                          {clientLabels[source.client]} 历史会话 {index + 1}
                        </strong>
                        <small>
                          {importStateLabels[candidate.import_state]} · {candidate.raw_event_count} 条原始事件 · {formatBytes(candidate.estimated_bytes)}
                        </small>
                      </span>
                      <time>
                        {candidate.latest_activity_ms === null
                          ? "无可信时间"
                          : formatTime(candidate.latest_activity_ms)}
                      </time>
                    </label>
                  ))}

                  {source.candidates.length === 0 ? (
                    <p className="source-note">
                      {source.client === "pico"
                        ? "Pico 不支持旧会话扫描。"
                        : "没有可安全归属到当前仓库的历史会话。"}
                    </p>
                  ) : null}

                  {source.client === "pico" ? (
                    <p className="source-note">
                      持续获取需要在 Pico 中手动选择 CodeCairn Memory Backend。
                    </p>
                  ) : source.continuous_state === "installed" ? (
                    <p className="source-note">已检测到持续接入，本次不会重复安装。</p>
                  ) : canInstallCapture(source.continuous_state) ? (
                    <label className="capture-choice">
                      <input
                        type="checkbox"
                        checked={captureClients.includes(source.client)}
                        disabled={busy !== null}
                        onChange={(event) =>
                          toggleCapture(source.client, event.target.checked)
                        }
                      />
                      <span>持续获取以后完成的新会话</span>
                    </label>
                  ) : null}

                  {source.unresolved_count || source.invalid_count ? (
                    <p className="source-warning">
                      未归属 {source.unresolved_count} 条，格式无效 {source.invalid_count} 条；这些内容不会写入。
                    </p>
                  ) : null}
                  {source.continuous_state === "not_detected" ? (
                    <p className="source-note">
                      未找到可安全写入的客户端配置，本次不可自动安装。
                    </p>
                  ) : null}
                </section>
              ))}
            </div>
          </section>

          <section className="onboarding-section">
            <div className="section-heading">
              <span>
                <small>步骤 2</small>
                <h2>检查保留边界</h2>
              </span>
              <span>保留策略 v1</span>
            </div>
            <div className="retention-grid">
              {retentionColumns.map(([heading, items]) => (
                <div key={heading}>
                  <h3>{heading}</h3>
                  <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
              ))}
            </div>
            <p className="egress-note">
              {preview.retention.source_content_egress === "none"
                ? "原始来源内容不会离开本机。"
                : "仅编码后的记忆文本会发送给已配置的嵌入提供方；原始来源内容不会发送。"}
            </p>
          </section>

          <section className="onboarding-section">
            <div className="section-heading">
              <span>
                <small>步骤 3</small>
                <h2>确认计划写入</h2>
              </span>
              <span>{preview.selected_import_count} 个会话</span>
            </div>
            {hasPlannedActions ? (
              <ul className="planned-writes">
                {preview.selected_import_count ? (
                  <li>导入 {preview.selected_import_count} 个当前仓库历史会话</li>
                ) : null}
                {preview.sources
                  .filter((source) => source.capture_selected)
                  .map((source) => (
                    <li key={source.client}>
                      为 {clientLabels[source.client]} 安装持续获取
                    </li>
                  ))}
              </ul>
            ) : (
              <p className="source-note">当前选择不会产生写入。</p>
            )}

            {dirty ? (
              <div className="preview-stale" role="status">
                <span>选择已改变，需要重新生成写入预览。</span>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void updatePreview()}
                >
                  {busy === "preview" ? "正在更新" : "更新写入预览"}
                </button>
              </div>
            ) : null}

            <label className="consent-row">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={busy !== null || dirty || !hasPlannedActions}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>我已检查来源、保留边界和计划写入，并同意执行本次接入。</span>
            </label>
            <div className="confirm-row">
              <small>执行结果只以服务返回的接入报告为准。</small>
              <button
                className="primary-action"
                type="button"
                disabled={!canApply}
                onClick={() => void applyPlan()}
              >
                {busy === "apply" ? "正在接入" : "确认并接入"}
              </button>
            </div>
            {error ? (
              <RequestError
                action={retryDiscovery}
                actionLabel="重新扫描"
                error={error}
              />
            ) : null}
          </section>
        </div>
      ) : null}
    </section>
  );
}
