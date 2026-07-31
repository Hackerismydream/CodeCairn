"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  HubApiError,
  isHubConnectionFailure,
  type HubClient,
} from "../../../lib/hub/client";
import {
  dateTimeValue,
  formatTime,
  memoryStatusLabels,
  memoryTypeLabels,
} from "../../../lib/hub/format";
import {
  createRequestGate,
  memoryFilterDisabled,
  memoryPaginationDisabled,
  retryFromFirstPage,
} from "../../../lib/hub/memory-state";
import type {
  MemoriesView as MemoriesData,
  MemoryStatus,
  MemoryType,
} from "../../../lib/hub/types";
import RequestError from "../../components/RequestError";
import MemoryInspector from "./MemoryInspector";

type TypeFilter = "all" | MemoryType;
type StatusFilter = "all" | MemoryStatus;

export default function MemoriesView({
  client,
  onConnected,
  onUnavailable,
}: {
  client: HubClient;
  onConnected: (repoKey: string) => void;
  onUnavailable: () => void;
}) {
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [cursor, setCursor] = useState<string | undefined>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>(
    [],
  );
  const [data, setData] = useState<MemoriesData | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [selecting, setSelecting] = useState(false);
  const [reload, setReload] = useState(0);
  const requestGate = useRef(createRequestGate());
  const transitionLocked = useRef(true);
  const selectionController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const requestToken = requestGate.current.begin();
    client
      .memories(
        {
          memoryType: typeFilter === "all" ? undefined : typeFilter,
          status: statusFilter === "all" ? undefined : statusFilter,
          limit: 50,
          cursor,
        },
        controller.signal,
      )
      .then((result) => {
        if (!requestGate.current.isCurrent(requestToken)) return;
        setData(result);
        onConnected(result.repo_key);
      })
      .catch((reason: unknown) => {
        if (
          !controller.signal.aborted &&
          requestGate.current.isCurrent(requestToken)
        ) {
          setError(reason instanceof Error ? reason : new Error(String(reason)));
          if (isHubConnectionFailure(reason)) onUnavailable();
        }
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          requestGate.current.isCurrent(requestToken)
        ) {
          transitionLocked.current = false;
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [
    client,
    cursor,
    onConnected,
    onUnavailable,
    reload,
    statusFilter,
    typeFilter,
  ]);

  useEffect(
    () => () => {
      selectionController.current?.abort();
      requestGate.current.invalidate();
    },
    [],
  );

  const selectMemory = useCallback(
    async (memoryId: string) => {
      if (transitionLocked.current || selectionController.current) return;
      const controller = new AbortController();
      const requestToken = requestGate.current.begin();
      selectionController.current = controller;
      setSelecting(true);
      setError(null);
      try {
        const result = await client.memories(
          {
            memoryType: typeFilter === "all" ? undefined : typeFilter,
            status: statusFilter === "all" ? undefined : statusFilter,
            limit: 50,
            cursor,
            selectedMemoryId: memoryId,
          },
          controller.signal,
        );
        if (!requestGate.current.isCurrent(requestToken)) return;
        setData(result);
        onConnected(result.repo_key);
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
          selectionController.current = null;
          setSelecting(false);
        }
      }
    },
    [client, cursor, onConnected, onUnavailable, statusFilter, typeFilter],
  );

  function beginListTransition(): boolean {
    if (transitionLocked.current || selectionController.current) return false;
    transitionLocked.current = true;
    requestGate.current.invalidate();
    setSelecting(false);
    setData(null);
    setLoading(true);
    setError(null);
    return true;
  }

  function resetPage(): boolean {
    if (!beginListTransition()) return false;
    setCursor(undefined);
    setCursorHistory([]);
    return true;
  }

  function retry() {
    if (!beginListTransition()) return;
    if (
      retryFromFirstPage(
        error instanceof HubApiError ? error.code : undefined,
      )
    ) {
      setCursor(undefined);
      setCursorHistory([]);
    }
    setReload((value) => value + 1);
  }

  function refreshFromFirstPage() {
    if (!beginListTransition()) return;
    setCursor(undefined);
    setCursorHistory([]);
    setReload((value) => value + 1);
  }

  const selectedId = data?.selected?.detail.memory.memory_id;
  const selectionWasRemoved =
    error instanceof HubApiError && error.code === "memory_not_found";
  const recoveryProps = selectionWasRemoved
    ? {
        action: refreshFromFirstPage,
        actionLabel: "刷新列表",
      }
    : { retry };

  return (
    <section className="view-shell">
      <header className="page-heading">
        <h1>记忆</h1>
        <p>查看当前记忆命名空间中真实持久化的编码记忆。</p>
      </header>
      {error && !data ? (
        <RequestError error={error} {...recoveryProps} />
      ) : (
        <div className="memory-workbench" aria-busy={loading || selecting}>
          <section className="memory-list-panel">
            <div className="memory-toolbar">
              <div className="filter-strip" aria-label="记忆类型">
                {(
                  [
                    ["all", "全部"],
                    ["repository_knowledge", "仓库知识"],
                    ["task_experience", "任务经验"],
                    ["work_state", "工作状态"],
                    ["user_preference", "工作偏好"],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    className={typeFilter === key ? "active" : ""}
                    aria-pressed={typeFilter === key}
                    disabled={memoryFilterDisabled(
                      loading,
                      selecting,
                      typeFilter === key &&
                        cursor === undefined &&
                        cursorHistory.length === 0,
                    )}
                    onClick={() => {
                      if (
                        typeFilter === key &&
                        cursor === undefined &&
                        cursorHistory.length === 0
                      ) {
                        return;
                      }
                      if (!resetPage()) return;
                      setTypeFilter(key);
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <label>
                召回状态
                <select
                  value={statusFilter}
                  disabled={loading || selecting}
                  onChange={(event) => {
                    if (!resetPage()) return;
                    setStatusFilter(event.target.value as StatusFilter);
                  }}
                >
                  <option value="all">全部</option>
                  <option value="active">默认召回中</option>
                  <option value="superseded">默认不召回</option>
                </select>
              </label>
            </div>
            <div className="memory-table-head" aria-hidden="true">
              <span>记忆</span>
              <span>类型</span>
              <span>创建时间</span>
              <span>召回状态</span>
            </div>
            <div className="memory-table">
              {loading && !data ? (
                <div className="empty-state">
                  <strong>正在读取记忆</strong>
                  <p>数据来自本地 CodeCairn 服务。</p>
                </div>
              ) : data?.page.items.length ? (
                data.page.items.map((memory) => (
                  <button
                    type="button"
                    className={`memory-row ${
                      selectedId === memory.memory_id ? "selected" : ""
                    }`}
                    aria-pressed={selectedId === memory.memory_id}
                    disabled={loading || selecting}
                    key={memory.memory_id}
                    onClick={() => void selectMemory(memory.memory_id)}
                  >
                    <span className="memory-row-title">
                      <strong>{memory.title}</strong>
                      <code>{memory.memory_id.slice(0, 16)}</code>
                    </span>
                    <span className="memory-row-type">
                      {memoryTypeLabels[memory.memory_type]}
                    </span>
                    <time dateTime={dateTimeValue(memory.created_at_ms)}>
                      {formatTime(memory.created_at_ms)}
                    </time>
                    <span
                      className={`memory-status memory-status-${memory.status}`}
                    >
                      {memoryStatusLabels[memory.status]}
                    </span>
                  </button>
                ))
              ) : (
                <div className="empty-state">
                  <strong>当前筛选下没有记忆</strong>
                  <p>Hub 不会用示例数据填充空命名空间。</p>
                </div>
              )}
            </div>
            <footer className="list-foot">
              <span>{data?.page.items.length ?? 0} 条</span>
              <div className="pagination">
                <button
                  type="button"
                  disabled={memoryPaginationDisabled(
                    loading,
                    selecting,
                    cursorHistory.length > 0,
                  )}
                  onClick={() => {
                    if (!beginListTransition()) return;
                    const previous = cursorHistory.at(-1);
                    setCursorHistory(cursorHistory.slice(0, -1));
                    setCursor(previous);
                  }}
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={memoryPaginationDisabled(
                    loading,
                    selecting,
                    Boolean(data?.page.next_cursor),
                  )}
                  onClick={() => {
                    if (!beginListTransition()) return;
                    setCursorHistory([...cursorHistory, cursor]);
                    setCursor(data?.page.next_cursor ?? undefined);
                  }}
                >
                  下一页
                </button>
              </div>
            </footer>
            {error && data ? (
              <RequestError error={error} {...recoveryProps} />
            ) : null}
          </section>
          {data?.selected ? (
            <MemoryInspector
              key={data.selected.detail.memory.memory_id}
              selected={data.selected}
            />
          ) : (
            <aside className="memory-inspector">
              <div className="empty-state">
                <strong>没有可检查的记忆</strong>
                <p>选择一条记忆后，这里会显示内容、来源和演化。</p>
              </div>
            </aside>
          )}
        </div>
      )}
    </section>
  );
}
