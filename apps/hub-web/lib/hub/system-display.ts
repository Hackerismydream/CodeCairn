const systemValueLabels: Record<string, string> = {
  configured: "已配置",
  disabled: "已关闭",
  failed: "失败",
  fastembed: "FastEmbed",
  dashscope: "DashScope",
  injected: "内置适配器",
  live_verified: "联机已验证",
  local: "本地",
  "memory text": "记忆文本",
  missing: "未配置",
  missing_key: "缺少密钥",
  network: "网络",
  none: "无",
  not_checked: "未联机检查",
  not_configured: "未配置",
  "trace excerpts": "会话片段",
  unconfigured: "未配置",
};

const recallReadinessLabels: Record<string, string> = {
  configuration_ready: "已配置",
  missing_key: "缺少密钥",
  not_configured: "未配置",
};

export function systemValueLabel(value: string): string {
  return systemValueLabels[value] ?? value;
}

export function queueIndicatorClass(state: string, count: number): string {
  if (count === 0) return "status-quiet";
  if (state === "failed" || state === "stale") return "status-failed";
  if (state === "pending" || state === "leased") return "status-quiet";
  return "";
}

export function providerIndicatorClass(
  key: string,
  value: string,
): string {
  if (value === "missing" || value === "missing_key" || value === "failed") {
    return "status-failed";
  }
  if (
    !key.endsWith("_state") ||
    value === "disabled" ||
    value === "not_checked" ||
    value === "none"
  ) {
    return "status-quiet";
  }
  return "";
}

export function remediationLabel(value: string): string {
  const labels: Record<string, string> = {
    "restore the latest namespace export": "从最近的命名空间导出中恢复",
    "Set CODECAIRN_EMBEDDING_API_KEY or DASHSCOPE_API_KEY and restart the Hub.":
      "设置 CODECAIRN_EMBEDDING_API_KEY 或 DASHSCOPE_API_KEY，并重启 Hub。",
  };
  return labels[value] ?? value;
}

export function recallReadinessLabel(
  state: string,
  liveChecked: boolean,
): string {
  const label = recallReadinessLabels[state] ?? state;
  if (state !== "configuration_ready") return label;
  return `${label}（${liveChecked ? "已联机检查" : "未联机检查"}）`;
}

export function recallReadinessIndicatorClass(
  state: string,
  liveChecked: boolean,
): string {
  if (state !== "configuration_ready") return "status-failed";
  return liveChecked ? "" : "status-quiet";
}

export function visibleQueueEntries(
  values: Record<string, number>,
): Array<[string, number]> {
  return Object.entries(values).filter(([, count]) => count > 0);
}
