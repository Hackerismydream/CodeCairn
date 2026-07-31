import type {
  MemoryOrigin,
  MemoryStatus,
  MemoryType,
} from "./types";

export const memoryTypeLabels: Record<MemoryType, string> = {
  repository_knowledge: "仓库知识",
  task_experience: "任务经验",
  work_state: "工作状态",
  user_preference: "工作偏好",
};

export const memoryStatusLabels: Record<MemoryStatus, string> = {
  active: "默认召回中",
  superseded: "默认不召回",
};

export const memoryOriginLabels: Record<MemoryOrigin, string> = {
  capture: "会话捕获",
  agent_asserted: "智能体声明",
  restored: "历史恢复",
};

export function formatTime(milliseconds: number): string {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "源事件未提供可信时间";
  const date = new Date(milliseconds);
  if (Number.isNaN(date.getTime())) return "源事件未提供可信时间";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export function dateTimeValue(milliseconds: number): string | undefined {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return undefined;
  const date = new Date(milliseconds);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "无";
  if (Array.isArray(value)) {
    return value.length ? value.map(displayValue).join("；") : "无";
  }
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export function errorLabel(code: string): string {
  const labels: Record<string, string> = {
    cursor_invalid: "分页位置已经失效，请回到第一页。",
    hub_unavailable: "本地 Memory OS 尚未连接。",
    index_not_ready: "记忆索引尚未就绪。",
    memory_not_found: "这条记忆已不存在，请刷新列表。",
    namespace_unavailable: "当前仓库的记忆命名空间不可用。",
    snapshot_stale: "接入预览已变化。",
    consent_expired: "接入预览已过期。",
    consent_invalid: "本次接入授权已失效。",
    invalid_selection: "接入选择不可执行。",
    onboarding_failed: "接入 Memory OS 时发生错误。",
    provider_not_configured: "召回所需的检索提供方尚未配置。",
    unauthorized: "Hub 会话已经失效，请重新启动。",
    invalid_response: "Hub 前后端版本不一致，无法安全读取数据。",
  };
  return labels[code] ?? "读取 Memory OS 时发生错误。";
}

export function errorRemediation(
  code: string,
  fallback: string | null,
): string | null {
  const labels: Record<string, string> = {
    cursor_invalid: "请返回第一页重新读取。",
    hub_unavailable: "请在仓库根目录运行 make hub-dev。",
    index_not_ready: "请先完成本地索引同步，再重新召回。",
    memory_not_found: "请刷新记忆列表。",
    namespace_unavailable: "请检查当前仓库的 CodeCairn 初始化状态。",
    snapshot_stale: "本机历史或客户端配置已变化，请重新扫描后确认。",
    consent_expired: "请重新扫描，并再次检查写入计划。",
    consent_invalid: "请重新扫描，以建立新的本地授权。",
    invalid_selection: "请重新扫描，仅选择当前预览中可用的项目。",
    onboarding_failed: "请重新扫描；若仍失败，请保留请求编号用于排查。",
    provider_not_configured: "请先完成检索提供方配置，再重新启动 Hub。",
    unauthorized: "请重新运行 make hub-dev 建立本地会话。",
    invalid_response: "请确认 Hub 前后端版本一致后重新启动。",
  };
  return labels[code] ?? fallback;
}
