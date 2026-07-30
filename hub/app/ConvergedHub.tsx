"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";

export type HubView = "overview" | "memories" | "recall" | "system";
export type InspectorTab = "content" | "source" | "evolution";

type MemoryType =
  | "repository_knowledge"
  | "task_experience"
  | "work_state"
  | "user_preference";
type MemoryStatus = "active" | "superseded";
type MemoryOrigin = "capture" | "agent_asserted" | "restored";
type MemoryFilter = "all" | MemoryType;
type StatusFilter = "all" | MemoryStatus;

type EvidenceFact = {
  id: string;
  kind:
    | "message"
    | "command"
    | "command_result"
    | "file_change"
    | "tool_call"
    | "tool_result"
    | "verification";
  label: string;
  role?: "user" | "assistant" | "tool" | "system";
  value: string;
  provider: "codex" | "claude" | "pico";
  sessionId: string;
  sourceGeneration: number;
  eventIndex: number;
  eventId: string;
  sourcePathSha256: string;
  eventSha256: string;
  tone?: "neutral" | "failed" | "verified";
};

type EvolutionRecord = {
  relation: string;
  reason: string;
  proposedBy: string;
  createdAt: string;
  predecessorId: string;
  predecessorTitle: string;
  successorId: string;
  successorTitle: string;
};

type Memory = {
  id: string;
  shortId: string;
  type: MemoryType;
  title: string;
  content: string;
  category: string;
  tags: string[];
  status: MemoryStatus;
  origin: MemoryOrigin;
  createdAt: string;
  createdAtMs: number;
  episodeId?: string;
  resourceUri: string;
  payload: Array<{ label: string; value: string }>;
  facts: EvidenceFact[];
  evolution?: EvolutionRecord;
};

const REPO_KEY = "github.com/Hackerismydream/CodeCairn";
const REPO_LABEL = "Hackerismydream / CodeCairn";

const memoryIds = {
  knowledge:
    "mem_4d2b0897c8d49cda3a21e037e9c15dbba2c9eaee9af721270bea81ee554d8d77",
  experience:
    "mem_750a903cc114f75c9083db5cb4e536b782365ed1b0acb84f1b8067f6f05b5592",
  workState:
    "mem_921ac81be2ef78a91254fb68fa13a92ec026c34ddb424862b95b00c77f981e5a",
  preference:
    "mem_c039ab7211fdc312c523b56013d57e67cd96c433f4bcb8102abb1e623f09a463",
  oldKnowledge:
    "mem_03a8df4bba11c320f044b186811382cae1a12e8e02aed8fbe1ec61f4c87bd337",
};

const evolution: EvolutionRecord = {
  relation: "contradicts",
  reason:
    "安装态重启验证发现重复投递，证明单元测试不能单独建立跨进程连续性。",
  proposedBy: "semantic_pipeline",
  createdAt: "2026-07-29 22:42",
  predecessorId: memoryIds.oldKnowledge,
  predecessorTitle: "单元测试通过即可证明连续性",
  successorId: memoryIds.knowledge,
  successorTitle: "连续性必须由全新进程验证",
};

const facts = {
  userTask: {
    id: "fact_3d653cd5447dc19f0d86f93cbe6479e74a5f542ceff7e4693b9db62736c8159f",
    kind: "message" as const,
    label: "用户任务",
    role: "user" as const,
    value: "让定时投递在网关重启后继续工作，并且不能重复发送。",
    provider: "pico" as const,
    sessionId: "pico_9f31",
    sourceGeneration: 1,
    eventIndex: 12,
    eventId: "evt_pico_0012",
    sourcePathSha256:
      "76d12f2373db489c4fb302d7573f10ac0a52fca5d23922a052102cb4195965f0",
    eventSha256:
      "3ce4af3401150240d1fae4c8f356250309d4952f230a7120f27477c212583b90",
    tone: "neutral" as const,
  },
  failedCommand: {
    id: "fact_4a3f1b60bd7aad89f944a17aa1c2ba843cc5f0a66fcc9a53549e1356a378a6d8",
    kind: "command_result" as const,
    label: "失败命令",
    value: "uv run pytest tests/test_scheduler_restart.py -q",
    provider: "pico" as const,
    sessionId: "pico_9f31",
    sourceGeneration: 1,
    eventIndex: 47,
    eventId: "evt_pico_0047",
    sourcePathSha256:
      "76d12f2373db489c4fb302d7573f10ac0a52fca5d23922a052102cb4195965f0",
    eventSha256:
      "ffb338557aff5a8fc0c09d1a83b2bba3b69b2bac8704a5427b5e143affb7250b",
    tone: "failed" as const,
  },
  fileChange: {
    id: "fact_f0c10e150b21240d7f643e973cbbd4d2f6c85a6ae6481db787c3a4812a69ef28",
    kind: "file_change" as const,
    label: "文件变更",
    value: "pico/scheduler/delivery.py · 确认前先持久化回执",
    provider: "pico" as const,
    sessionId: "pico_9f31",
    sourceGeneration: 1,
    eventIndex: 61,
    eventId: "evt_pico_0061",
    sourcePathSha256:
      "76d12f2373db489c4fb302d7573f10ac0a52fca5d23922a052102cb4195965f0",
    eventSha256:
      "bc76c2b6573734950211108054ca995979f331f24a86467ae85f302120384c67",
    tone: "neutral" as const,
  },
  verification: {
    id: "fact_f5cd79e4725fb58f525e88b5585294a63a0fa4ea1fbd8fa1caf26de40716e2cc",
    kind: "verification" as const,
    label: "验证结果",
    value: "安装态重启验证通过，两个断言均成功。",
    provider: "pico" as const,
    sessionId: "pico_9f31",
    sourceGeneration: 1,
    eventIndex: 74,
    eventId: "evt_pico_0074",
    sourcePathSha256:
      "76d12f2373db489c4fb302d7573f10ac0a52fca5d23922a052102cb4195965f0",
    eventSha256:
      "244cb5f3a352c44d41f11cd62003fdd6aca4dc013b95fa9971f0c045d8558c1e",
    tone: "verified" as const,
  },
  preference: {
    id: "fact_cda9be3aa206e6bedc0f4f191be0b667506803ceff729368e52ca21c1d0ff034",
    kind: "message" as const,
    label: "用户偏好来源",
    role: "user" as const,
    value:
      "汇报时区分代码实现、确定性检查、真实运行证据与未经验证的结论。",
    provider: "codex" as const,
    sessionId: "codex_26e2",
    sourceGeneration: 2,
    eventIndex: 8,
    eventId: "evt_codex_0008",
    sourcePathSha256:
      "c1f57e91aa79f69369a6238158a28e03e4ec8a9503a962db561ce39083ff9775",
    eventSha256:
      "559db00a0a68b05d89a1aed7f5c6263bd92a9ae25a3e4cb916f536036dfdcb16",
    tone: "neutral" as const,
  },
} satisfies Record<string, EvidenceFact>;

const memories: Memory[] = [
  {
    id: memoryIds.knowledge,
    shortId: "mem_4d2b",
    type: "repository_knowledge",
    title: "连续性必须由全新进程验证",
    content:
      "跨进程连续性成立的条件，是调度器在全新进程中重新加载同一个持久化任务标识，并且只投递一次。单元测试只能作为局部证据。",
    category: "constraint",
    tags: ["continuity", "restart", "scheduler"],
    status: "active",
    origin: "capture",
    createdAt: "2026-07-29 22:42",
    createdAtMs: 1785336120000,
    episodeId:
      "ep_3f0a6c80d62130638f431a228c2d20ea452f54d4063af3d4ab88ba63dedf1033",
    resourceUri: `codecairn://memory/${memoryIds.knowledge}`,
    payload: [
      { label: "subject_key", value: "scheduler.restart.continuity" },
      {
        label: "claim",
        value: "连续性需要全新进程重放并验证单次投递。",
      },
    ],
    facts: [
      facts.userTask,
      facts.failedCommand,
      facts.fileChange,
      facts.verification,
    ],
    evolution,
  },
  {
    id: memoryIds.experience,
    shortId: "mem_750a",
    type: "task_experience",
    title: "修复网关重启后的重复投递",
    content:
      "把投递回执的持久化提前到确认之前，关闭了安装态重启验证暴露出的竞态窗口。验证结果为 success。",
    category: "debugging",
    tags: ["delivery", "race-condition", "verified"],
    status: "active",
    origin: "capture",
    createdAt: "2026-07-29 22:39",
    createdAtMs: 1785335940000,
    episodeId:
      "ep_3f0a6c80d62130638f431a228c2d20ea452f54d4063af3d4ab88ba63dedf1033",
    resourceUri: `codecairn://memory/${memoryIds.experience}`,
    payload: [
      { label: "goal", value: "修复重启后的重复投递" },
      { label: "outcome", value: "success" },
      {
        label: "actions",
        value: "定位竞态；提前持久化回执；运行安装态验证",
      },
      { label: "result", value: "全新进程只投递一次" },
      { label: "blockers", value: "无" },
      { label: "verification_fact_ids", value: "1 条" },
    ],
    facts: [
      facts.userTask,
      facts.failedCommand,
      facts.fileChange,
      facts.verification,
    ],
  },
  {
    id: memoryIds.workState,
    shortId: "mem_921a",
    type: "work_state",
    title: "CodeCairn Hub v0.3",
    content:
      "当前正在把 CodeCairn 的只读能力映射成 Hub。下一步是用真实服务接口替换静态示例数据。",
    category: "task",
    tags: ["hub", "v0.3"],
    status: "active",
    origin: "agent_asserted",
    createdAt: "2026-07-30 10:16",
    createdAtMs: 1785377760000,
    resourceUri: `codecairn://memory/${memoryIds.workState}`,
    payload: [
      { label: "workstream_key", value: "codecairn-hub-v03" },
      { label: "workstream_state", value: "open" },
      { label: "goal", value: "让人看懂当前命名空间中的记忆" },
      { label: "progress", value: "完成能力审计与页面收口" },
      { label: "blockers", value: "尚无本地 HTTP 展示层" },
      { label: "next_step", value: "接入 CodeCairnApplication 只读适配层" },
      { label: "terminal_outcome", value: "无" },
    ],
    facts: [],
  },
  {
    id: memoryIds.preference,
    shortId: "mem_c039",
    type: "user_preference",
    title: "明确报告证据边界",
    content:
      "交付说明必须区分实现、确定性检查、真实运行证据、基础设施失败与未经验证的结论。",
    category: "output",
    tags: ["evidence", "reporting"],
    status: "active",
    origin: "agent_asserted",
    createdAt: "2026-07-27 18:20",
    createdAtMs: 1785147600000,
    resourceUri: `codecairn://memory/${memoryIds.preference}`,
    payload: [
      { label: "subject_key", value: "delivery.evidence.boundary" },
      {
        label: "preference",
        value: "明确区分已验证事实与推断。",
      },
      { label: "source_fact_ids", value: "1 条" },
    ],
    facts: [facts.preference],
  },
  {
    id: memoryIds.oldKnowledge,
    shortId: "mem_03a8",
    type: "repository_knowledge",
    title: "单元测试通过即可证明连续性",
    content:
      "这是为审计保留的历史结论。安装态重启验证发现重复投递后，该结论已被新版本替代。",
    category: "constraint",
    tags: ["continuity", "historical"],
    status: "superseded",
    origin: "capture",
    createdAt: "2026-07-18 09:12",
    createdAtMs: 1784337120000,
    episodeId:
      "ep_50a7314da869079dad842e898a425ad88abcb7f29289549826268b4ae957f16b",
    resourceUri: `codecairn://memory/${memoryIds.oldKnowledge}`,
    payload: [
      { label: "subject_key", value: "scheduler.restart.continuity" },
      { label: "claim", value: "单元测试通过即可证明连续性。" },
    ],
    facts: [facts.userTask],
    evolution,
  },
];

const memoryTypeLabels: Record<MemoryType, string> = {
  repository_knowledge: "仓库知识",
  task_experience: "任务经验",
  work_state: "工作状态",
  user_preference: "用户偏好",
};

const memoryTypeMarks: Record<MemoryType, string> = {
  repository_knowledge: "知",
  task_experience: "经",
  work_state: "态",
  user_preference: "偏",
};

const statusLabels: Record<MemoryStatus, string> = {
  active: "有效",
  superseded: "已替代",
};

const originLabels: Record<MemoryOrigin, string> = {
  capture: "捕获生成",
  agent_asserted: "智能体声明",
  restored: "从历史恢复",
};

const navItems: Array<{
  view: HubView;
  label: string;
  mark: string;
}> = [
  { view: "overview", label: "概览", mark: "总" },
  { view: "memories", label: "记忆", mark: "记" },
  { view: "recall", label: "召回", mark: "召" },
  { view: "system", label: "系统", mark: "系" },
];

function StatusDot({
  state = "healthy",
}: {
  state?: "healthy" | "pending" | "quiet" | "failed";
}) {
  return <span className={`status-dot status-${state}`} aria-hidden="true" />;
}

function CairnMark() {
  return (
    <span className="cairn-mark" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

function MemoryMark({ memory }: { memory: Memory }) {
  return (
    <span className={`memory-mark memory-mark-${memory.type}`}>
      {memoryTypeMarks[memory.type]}
    </span>
  );
}

function PrototypeBadge() {
  return (
    <span className="prototype-badge">
      只读原型 <i>·</i> 示例数据
    </span>
  );
}

function Sidebar({
  view,
  setView,
}: {
  view: HubView;
  setView: (view: HubView) => void;
}) {
  return (
    <aside className="hub-sidebar">
      <div className="brand">
        <CairnMark />
        <span>
          <strong>CodeCairn</strong>
          <small>记忆中心</small>
        </span>
      </div>

      <div className="namespace-card">
        <span className="namespace-monogram">CC</span>
        <span>
          <small>当前命名空间</small>
          <strong>CodeCairn</strong>
          <code>github.com/Hackerismydream</code>
        </span>
      </div>

      <nav aria-label="记忆中心导航">
        {navItems.map((item) => (
          <button
            aria-current={view === item.view ? "page" : undefined}
            className={view === item.view ? "active" : ""}
            key={item.view}
            onClick={() => setView(item.view)}
            type="button"
          >
            <span>{item.mark}</span>
            <strong>{item.label}</strong>
          </button>
        ))}
      </nav>

      <div className="sidebar-summary">
        <span>
          <strong>5</strong>
          <small>条记忆</small>
        </span>
        <span>
          <strong>4</strong>
          <small>条有效</small>
        </span>
      </div>

      <div className="sidebar-health">
        <div>
          <StatusDot />
          <span>
            <strong>Doctor 快照正常</strong>
            <small>schema codecairn-v01-5</small>
          </span>
        </div>
        <p>存储本地 · 嵌入本地 · 语义提取关闭</p>
      </div>
    </aside>
  );
}

function SystemBar({
  setView,
}: {
  setView: (view: HubView) => void;
}) {
  return (
    <header className="system-bar">
      <div className="system-context">
        <small>当前命名空间</small>
        <strong>{REPO_LABEL}</strong>
      </div>
      <div className="system-signals">
        <span>
          <StatusDot /> Doctor 快照正常
        </span>
        <span>
          <StatusDot /> fastembed 已配置
        </span>
      </div>
      <button
        className="primary-action"
        onClick={() => setView("recall")}
        type="button"
      >
        发起召回
      </button>
      <PrototypeBadge />
    </header>
  );
}

function PageHeading({
  eyebrow,
  title,
  body,
  action,
}: {
  eyebrow: string;
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="heading-body">{body}</p>
      </div>
      {action}
    </div>
  );
}

function StatRow() {
  const stats = [
    { value: "5", label: "持久记忆", detail: "4 有效 · 1 已替代" },
    { value: "3", label: "导入批次", detail: "Codex · Claude · Pico" },
    { value: "148", label: "观测事件", detail: "标准化来源事件" },
    { value: "0", label: "失败队列", detail: "索引与语义任务" },
  ];

  return (
    <section className="stat-row" aria-label="命名空间统计">
      {stats.map((stat) => (
        <div key={stat.label}>
          <strong>{stat.value}</strong>
          <span>{stat.label}</span>
          <small>{stat.detail}</small>
        </div>
      ))}
    </section>
  );
}

function ProductFlow() {
  const stages = [
    {
      mark: "01",
      label: "来源",
      title: "Agent Trace",
      detail: "Codex · Claude · Pico",
    },
    {
      mark: "02",
      label: "标准化",
      title: "Evidence Facts",
      detail: "角色 · 命令 · 文件 · 结果",
    },
    {
      mark: "03",
      label: "持久化",
      title: "Coding Memory",
      detail: "四类记忆 · Markdown 真源",
    },
    {
      mark: "04",
      label: "检索",
      title: "Recall Ranking",
      detail: "关键词 · 向量 · 生命周期",
    },
    {
      mark: "05",
      label: "输出",
      title: "Recall Context",
      detail: "有界上下文 · Sidecar",
    },
  ];

  return (
    <section className="product-flow">
      <div className="section-heading">
        <div>
          <p className="eyebrow">当前产品闭环</p>
          <h2>从来源事件到可审计上下文</h2>
        </div>
        <span className="surface-note">真实契约 · 示例数据</span>
      </div>
      <div className="flow-track">
        {stages.map((stage, index) => (
          <div className="flow-stage" key={stage.mark}>
            <span>{stage.mark}</span>
            <small>{stage.label}</small>
            <strong>{stage.title}</strong>
            <p>{stage.detail}</p>
            {index < stages.length - 1 && (
              <i className="flow-connector" aria-hidden="true">
                ›
              </i>
            )}
          </div>
        ))}
      </div>
      <div className="flow-foot">
        <span>
          <StatusDot /> 来源字段由标准化事件确定
        </span>
        <span>
          <StatusDot /> Markdown 是持久真源
        </span>
        <span>
          <StatusDot /> SQLite 与索引可重建
        </span>
      </div>
    </section>
  );
}

function RecentMemories({
  setView,
  setSelectedMemory,
}: {
  setView: (view: HubView) => void;
  setSelectedMemory: (id: string) => void;
}) {
  const openMemory = (id: string) => {
    setSelectedMemory(id);
    setView("memories");
  };

  return (
    <section className="grouped-panel">
      <div className="section-heading section-heading-inset">
        <div>
          <p className="eyebrow">list_memories</p>
          <h2>最近记录</h2>
        </div>
        <button onClick={() => setView("memories")} type="button">
          查看全部 <span>›</span>
        </button>
      </div>
      <div className="recent-list">
        {memories.slice(0, 4).map((memory) => (
          <button
            className="recent-row"
            key={memory.id}
            onClick={() => openMemory(memory.id)}
            type="button"
          >
            <MemoryMark memory={memory} />
            <span>
              <small>{memoryTypeLabels[memory.type]}</small>
              <strong>{memory.title}</strong>
              <code>{memory.shortId}</code>
            </span>
            <time>{memory.createdAt}</time>
            <span className={`memory-status memory-status-${memory.status}`}>
              {statusLabels[memory.status]}
            </span>
            <i>›</i>
          </button>
        ))}
      </div>
    </section>
  );
}

function RuntimeSnapshot({ setView }: { setView: (view: HubView) => void }) {
  const rows = [
    {
      label: "持久真源",
      value: "Markdown",
      state: "正常",
      dot: "healthy" as const,
    },
    {
      label: "搜索投影",
      value: "5 indexed · 0 stale",
      state: "正常",
      dot: "healthy" as const,
    },
    {
      label: "语义队列",
      value: "2 completed · 0 failed",
      state: "正常",
      dot: "healthy" as const,
    },
    {
      label: "Hook 回执",
      value: "3 total · 0 failed",
      state: "正常",
      dot: "healthy" as const,
    },
    {
      label: "语义提取",
      value: "disabled",
      state: "关闭",
      dot: "quiet" as const,
    },
  ];

  return (
    <section className="grouped-panel runtime-snapshot">
      <div className="section-heading section-heading-inset">
        <div>
          <p className="eyebrow">doctor</p>
          <h2>系统快照</h2>
        </div>
        <button onClick={() => setView("system")} type="button">
          查看系统 <span>›</span>
        </button>
      </div>
      <div className="snapshot-list">
        {rows.map((row) => (
          <div className="snapshot-row" key={row.label}>
            <StatusDot state={row.dot} />
            <strong>{row.label}</strong>
            <code>{row.value}</code>
            <span>{row.state}</span>
          </div>
        ))}
      </div>
      <p className="snapshot-foot">
        这是一次 Doctor 结果快照，不代表后台服务常驻或远程连接在线。
      </p>
    </section>
  );
}

function OverviewView({
  setView,
  setSelectedMemory,
}: {
  setView: (view: HubView) => void;
  setSelectedMemory: (id: string) => void;
}) {
  return (
    <div className="view-shell overview-view">
      <PageHeading
        eyebrow="当前命名空间"
        title="CodeCairn"
        body="查看这个仓库已经保存了什么、证据从哪里来，以及一次召回会怎样编译成 Agent 上下文。"
        action={
          <span className="snapshot-badge">
            <StatusDot /> 状态正常
          </span>
        }
      />
      <StatRow />
      <ProductFlow />
      <div className="overview-grid">
        <RecentMemories
          setSelectedMemory={setSelectedMemory}
          setView={setView}
        />
        <RuntimeSnapshot setView={setView} />
      </div>
      <div className="boundary-note">
        <span>当前边界</span>
        <p>
          已有 CLI、stdio MCP、Codex / Claude Hooks 与 Pico
          Adapter；尚无本地 HTTP 展示层、常驻 daemon、登录或远程协作。
        </p>
      </div>
    </div>
  );
}

function MemoryTable({
  selectedMemory,
  setSelectedMemory,
}: {
  selectedMemory: string;
  setSelectedMemory: (id: string) => void;
}) {
  const [typeFilter, setTypeFilter] = useState<MemoryFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const filtered = useMemo(
    () =>
      memories.filter(
        (memory) =>
          (typeFilter === "all" || memory.type === typeFilter) &&
          (statusFilter === "all" || memory.status === statusFilter),
      ),
    [statusFilter, typeFilter],
  );

  const chooseType = (next: MemoryFilter) => {
    setTypeFilter(next);
    const first = memories.find(
      (memory) =>
        (next === "all" || memory.type === next) &&
        (statusFilter === "all" || memory.status === statusFilter),
    );
    if (first) setSelectedMemory(first.id);
  };

  const chooseStatus = (next: StatusFilter) => {
    setStatusFilter(next);
    const first = memories.find(
      (memory) =>
        (typeFilter === "all" || memory.type === typeFilter) &&
        (next === "all" || memory.status === next),
    );
    if (first) setSelectedMemory(first.id);
  };

  return (
    <div className="memory-ledger-panel">
      <div className="memory-toolbar">
        <div className="filter-strip" aria-label="记忆类型筛选">
          {(
            [
              ["all", "全部"],
              ["repository_knowledge", "仓库知识"],
              ["task_experience", "任务经验"],
              ["work_state", "工作状态"],
              ["user_preference", "用户偏好"],
            ] as Array<[MemoryFilter, string]>
          ).map(([key, label]) => (
            <button
              className={typeFilter === key ? "active" : ""}
              key={key}
              onClick={() => chooseType(key)}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
        <label>
          <span>状态</span>
          <select
            onChange={(event) =>
              chooseStatus(event.target.value as StatusFilter)
            }
            value={statusFilter}
          >
            <option value="all">全部</option>
            <option value="active">有效</option>
            <option value="superseded">已替代</option>
          </select>
        </label>
      </div>
      <div className="memory-ledger">
        <div className="memory-ledger-head">
          <span>记忆</span>
          <span>类型</span>
          <span>记录时间</span>
          <span>状态</span>
        </div>
        {filtered.map((memory) => (
          <button
            className={`memory-ledger-row ${
              selectedMemory === memory.id ? "selected" : ""
            }`}
            key={memory.id}
            onClick={() => setSelectedMemory(memory.id)}
            type="button"
          >
            <span className="ledger-memory-title">
              <MemoryMark memory={memory} />
              <span>
                <strong>{memory.title}</strong>
                <code>{memory.shortId}</code>
              </span>
            </span>
            <span>{memoryTypeLabels[memory.type]}</span>
            <time>{memory.createdAt}</time>
            <span className={`memory-status memory-status-${memory.status}`}>
              {statusLabels[memory.status]}
            </span>
          </button>
        ))}
        {filtered.length === 0 && (
          <div className="empty-state">
            <strong>没有符合条件的记忆</strong>
            <p>调整类型或状态筛选后再查看。</p>
          </div>
        )}
      </div>
      <div className="ledger-foot">
        <span>
          本页映射 <code>list_memories</code>
        </span>
        <span>{filtered.length} 条结果 · 无下一页游标</span>
      </div>
    </div>
  );
}

function Inspector({
  memory,
  tab,
  setTab,
}: {
  memory: Memory;
  tab: InspectorTab;
  setTab: (tab: InspectorTab) => void;
}) {
  const tabs: Array<{ key: InspectorTab; label: string }> = [
    { key: "content", label: "内容" },
    { key: "source", label: "来源" },
    { key: "evolution", label: "演化" },
  ];

  return (
    <aside className="memory-inspector">
      <div className="inspector-label">
        <span>记忆详情</span>
        <code>{memory.shortId}</code>
      </div>
      <div className="inspector-title">
        <MemoryMark memory={memory} />
        <span className={`memory-status memory-status-${memory.status}`}>
          {statusLabels[memory.status]}
        </span>
      </div>
      <p className="eyebrow">{memoryTypeLabels[memory.type]}</p>
      <h2>{memory.title}</h2>
      <div className="inspector-meta">
        <span>{originLabels[memory.origin]}</span>
        <span>{memory.createdAt}</span>
      </div>

      <div className="inspector-tabs" role="tablist" aria-label="记忆详情">
        {tabs.map((item) => (
          <button
            aria-selected={tab === item.key}
            className={tab === item.key ? "active" : ""}
            key={item.key}
            onClick={() => setTab(item.key)}
            role="tab"
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "content" && (
        <div className="inspector-section">
          <p className="memory-content">{memory.content}</p>
          <dl className="memory-definition">
            <div>
              <dt>类型</dt>
              <dd>{memory.type}</dd>
            </div>
            <div>
              <dt>分类</dt>
              <dd>{memory.category}</dd>
            </div>
            <div>
              <dt>来源</dt>
              <dd>{memory.origin}</dd>
            </div>
            <div>
              <dt>证据事实</dt>
              <dd>{memory.facts.length} 条</dd>
            </div>
            {memory.episodeId && (
              <div>
                <dt>Episode</dt>
                <dd>{memory.episodeId.slice(0, 11)}…</dd>
              </div>
            )}
          </dl>
          <div className="payload-block">
            <p className="eyebrow">类型载荷</p>
            {memory.payload.map((row) => (
              <div key={row.label}>
                <code>{row.label}</code>
                <span>{row.value}</span>
              </div>
            ))}
          </div>
          <div className="tag-list">
            {memory.tags.map((tag) => (
              <span key={tag}>#{tag}</span>
            ))}
          </div>
          <div className="truth-note">
            <CairnMark />
            <span>
              <strong>Markdown 持久真源</strong>
              <code>{memory.resourceUri}</code>
            </span>
          </div>
        </div>
      )}

      {tab === "source" && (
        <div className="inspector-section evidence-list">
          <p className="inspector-explainer">
            来源由标准化事件确定。界面只展示公开摘要，不暴露本机原始路径。
          </p>
          {memory.facts.length > 0 ? (
            memory.facts.map((fact, index) => (
              <div
                className={`evidence-item evidence-${fact.tone ?? "neutral"}`}
                key={fact.id}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <small>{fact.label}</small>
                  <strong>{fact.value}</strong>
                  <p>
                    {fact.provider} · {fact.sessionId} · generation{" "}
                    {fact.sourceGeneration} · event {fact.eventIndex}
                  </p>
                  <details>
                    <summary>查看证据标识</summary>
                    <dl>
                      <div>
                        <dt>fact_id</dt>
                        <dd>{fact.id.slice(0, 18)}…</dd>
                      </div>
                      <div>
                        <dt>event_id</dt>
                        <dd>{fact.eventId}</dd>
                      </div>
                      <div>
                        <dt>source_path_sha256</dt>
                        <dd>{fact.sourcePathSha256.slice(0, 18)}…</dd>
                      </div>
                      <div>
                        <dt>event_sha256</dt>
                        <dd>{fact.eventSha256.slice(0, 18)}…</dd>
                      </div>
                    </dl>
                  </details>
                </div>
              </div>
            ))
          ) : (
            <div className="empty-state compact">
              <strong>没有来源事实</strong>
              <p>
                这是一条 <code>agent_asserted</code> 工作状态，不伪造事件证据。
              </p>
            </div>
          )}
        </div>
      )}

      {tab === "evolution" && (
        <div className="inspector-section evolution-list">
          {memory.evolution ? (
            <>
              <p className="inspector-explainer">
                演化记录不可变，前后两个版本都会保留以供审计。
              </p>
              <div
                className={`evolution-memory ${
                  memory.status === "superseded" ? "current" : "old"
                }`}
              >
                <small>前一版本 · 已替代</small>
                <strong>{memory.evolution.predecessorTitle}</strong>
                <code>{memory.evolution.predecessorId.slice(0, 12)}…</code>
              </div>
              <div className="evolution-reason">
                <span>{memory.evolution.relation}</span>
                <p>{memory.evolution.reason}</p>
                <small>
                  {memory.evolution.proposedBy} · {memory.evolution.createdAt}
                </small>
              </div>
              <div
                className={`evolution-memory ${
                  memory.status === "active" ? "current" : "next"
                }`}
              >
                <small>当前版本 · 有效</small>
                <strong>{memory.evolution.successorTitle}</strong>
                <code>{memory.evolution.successorId.slice(0, 12)}…</code>
              </div>
            </>
          ) : (
            <div className="empty-state compact">
              <strong>尚无演化记录</strong>
              <p>这条记忆当前没有 supersede 或 restore 关系。</p>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

function MemoriesView({
  selectedMemory,
  setSelectedMemory,
  inspectorTab,
  setInspectorTab,
}: {
  selectedMemory: string;
  setSelectedMemory: (id: string) => void;
  inspectorTab: InspectorTab;
  setInspectorTab: (tab: InspectorTab) => void;
}) {
  const memory =
    memories.find((item) => item.id === selectedMemory) ?? memories[0];

  return (
    <div className="view-shell memories-view">
      <PageHeading
        eyebrow="持久记忆"
        title="记忆"
        body="按真实类型和生命周期浏览记录，再查看内容、来源事实与不可变演化历史。"
        action={<span className="count-badge">5 条记录</span>}
      />
      <div className="memory-workbench">
        <MemoryTable
          selectedMemory={memory.id}
          setSelectedMemory={setSelectedMemory}
        />
        <Inspector
          memory={memory}
          setTab={setInspectorTab}
          tab={inspectorTab}
        />
      </div>
    </div>
  );
}

type RecallCandidate = {
  memoryId: string;
  sources: Array<"lexical" | "vector">;
  score: number;
  pinned?: boolean;
  snippets: Array<{ kind: "memory" | "fact" | "snippet"; text: string }>;
};

const baseRecallCandidates: RecallCandidate[] = [
  {
    memoryId: memoryIds.knowledge,
    sources: ["lexical", "vector"],
    score: 0.92,
    snippets: [
      {
        kind: "snippet",
        text: "跨进程连续性成立的条件，是调度器在全新进程中重新加载同一个持久化任务标识。",
      },
    ],
  },
  {
    memoryId: memoryIds.experience,
    sources: ["vector"],
    score: 0.86,
    snippets: [
      {
        kind: "fact",
        text: "把投递回执的持久化提前到确认之前，关闭竞态窗口。",
      },
    ],
  },
  {
    memoryId: memoryIds.workState,
    sources: ["lexical"],
    score: 0.73,
    pinned: true,
    snippets: [
      {
        kind: "memory",
        text: "下一步是用真实服务接口替换静态示例数据。",
      },
    ],
  },
  {
    memoryId: memoryIds.preference,
    sources: ["vector"],
    score: 0.64,
    snippets: [
      {
        kind: "snippet",
        text: "明确区分已验证事实与推断。",
      },
    ],
  },
];

const omissionLabels: Record<string, string> = {
  historical_filter: "历史版本过滤",
  type_cap: "类型上限",
  limit: "结果数量上限",
  token_budget: "上下文预算",
};

function RecallCandidateRow({
  candidate,
  rendered,
}: {
  candidate: RecallCandidate;
  rendered: boolean;
}) {
  const memory =
    memories.find((item) => item.id === candidate.memoryId) ?? memories[0];

  return (
    <div className={`candidate-row ${rendered ? "rendered" : "omitted"}`}>
      <span className="candidate-rank">
        {candidate.pinned ? "置顶" : "候选"}
      </span>
      <span className="candidate-memory">
        <code>{memory.shortId}</code>
        <strong>{memory.title}</strong>
        <small>{memoryTypeLabels[memory.type]}</small>
      </span>
      <span className="candidate-sources">
        {candidate.sources.map((source) => (
          <i key={source}>{source === "lexical" ? "关键词" : "向量"}</i>
        ))}
      </span>
      <strong>{candidate.score.toFixed(2)}</strong>
      <span className={`decision decision-${rendered ? "rendered" : "omitted"}`}>
        {rendered ? "已编译" : "未编译"}
      </span>
      <p>{candidate.snippets[0]?.text}</p>
    </div>
  );
}

function RecallView() {
  const [query, setQuery] = useState("新增可安全恢复的定时投递");
  const [lastQuery, setLastQuery] = useState(query);
  const [limit, setLimit] = useState(20);
  const [workstream, setWorkstream] = useState("codecairn-hub-v03");
  const [includeSuperseded, setIncludeSuperseded] = useState(false);
  const [runCount, setRunCount] = useState(1);

  const candidates = useMemo(() => {
    const next = [...baseRecallCandidates];
    if (includeSuperseded) {
      next.push({
        memoryId: memoryIds.oldKnowledge,
        sources: ["lexical"],
        score: 0.59,
        snippets: [
          {
            kind: "memory",
            text: "历史结论被保留，但默认不会进入活跃上下文。",
          },
        ],
      });
    }
    return next.slice(0, limit);
  }, [includeSuperseded, limit]);

  const renderedIds = [
    memoryIds.knowledge,
    memoryIds.experience,
    memoryIds.workState,
  ];

  const omissions = useMemo(() => {
    const rows: Array<{ id: string; reason: string }> = [];
    if (limit < baseRecallCandidates.length) {
      rows.push({ id: memoryIds.preference, reason: "limit" });
    } else {
      rows.push({ id: memoryIds.preference, reason: "type_cap" });
    }
    rows.push({
      id: memoryIds.oldKnowledge,
      reason: includeSuperseded ? "token_budget" : "historical_filter",
    });
    return rows;
  }, [includeSuperseded, limit]);

  const runRecall = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    setLastQuery(trimmed);
    setRunCount((value) => value + 1);
  };

  return (
    <div className="view-shell recall-view">
      <PageHeading
        eyebrow="recall"
        title="召回测试"
        body="发起一次即时查询，检查候选、精确片段、生命周期过滤与最终上下文预算。"
        action={
          <span className="ephemeral-badge">
            <StatusDot state="quiet" /> 结果不会持久化
          </span>
        }
      />

      <form className="recall-console" onSubmit={runRecall}>
        <label className="query-field">
          <span>当前任务</span>
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="描述这一次编码任务"
            value={query}
          />
        </label>
        <label>
          <span>工作流</span>
          <input
            onChange={(event) => setWorkstream(event.target.value)}
            value={workstream}
          />
        </label>
        <label>
          <span>候选上限</span>
          <select
            onChange={(event) => setLimit(Number(event.target.value))}
            value={limit}
          >
            <option value={3}>3</option>
            <option value={5}>5</option>
            <option value={20}>20</option>
          </select>
        </label>
        <label className="check-field">
          <input
            checked={includeSuperseded}
            onChange={(event) => setIncludeSuperseded(event.target.checked)}
            type="checkbox"
          />
          <span>包含已替代记忆</span>
        </label>
        <button className="primary-action" disabled={!query.trim()} type="submit">
          运行召回
        </button>
      </form>

      <section className="recall-result" key={runCount}>
        <div className="recall-result-head">
          <div>
            <p className="eyebrow">本次示例结果</p>
            <h2>“{lastQuery}”</h2>
          </div>
          <span>
            <StatusDot /> fresh
          </span>
        </div>

        <div className="recall-metrics">
          <div>
            <strong>42.6</strong>
            <span>毫秒</span>
            <small>latency_ms</small>
          </div>
          <div>
            <strong>3 / 4</strong>
            <span>关键词 / 向量候选</span>
            <small>candidate_count</small>
          </div>
          <div>
            <strong>624 / 8192</strong>
            <span>预算计数</span>
            <small>UTF-8 上界估算</small>
          </div>
          <div>
            <strong>148 / 148</strong>
            <span>真源 / 索引游标</span>
            <small>fresh</small>
          </div>
        </div>

        <div className="candidate-table">
          <div className="candidate-head">
            <span>排序</span>
            <span>候选记忆</span>
            <span>候选来源</span>
            <span>分数</span>
            <span>结果</span>
            <span>精确片段</span>
          </div>
          {candidates.map((candidate) => (
            <RecallCandidateRow
              candidate={candidate}
              key={candidate.memoryId}
              rendered={renderedIds.includes(candidate.memoryId)}
            />
          ))}
        </div>

        <div className="recall-lower-grid">
          <div className="compiled-context">
            <div className="compiled-context-head">
              <span>
                <p className="eyebrow">markdown</p>
                <h3>最终编译上下文</h3>
              </span>
              <code>renderer: codecairn/context-v1</code>
            </div>
            <pre>
              {`# CodeCairn Recall Context

## 仓库知识
跨进程连续性需要全新进程重放并验证单次投递。

## 任务经验
在确认投递前持久化回执，随后运行安装态重启验证。

## 当前工作状态
${workstream || "未指定工作流"}：下一步接入只读展示适配层。`}
            </pre>
            <div className="context-trace">
              <span>3 memories</span>
              <span>5 facts</span>
              <span>0 omitted snippets</span>
              <span>fastembed</span>
            </div>
          </div>

          <div className="omission-panel">
            <div>
              <p className="eyebrow">omissions</p>
              <h3>未进入上下文</h3>
            </div>
            {omissions.map((omission) => (
              <div className="omission-row" key={`${omission.id}-${omission.reason}`}>
                <code>{omission.id.slice(0, 12)}…</code>
                <span>{omissionLabels[omission.reason]}</span>
                <small>{omission.reason}</small>
              </div>
            ))}
            <p className="omission-foot">
              当前契约只返回记忆 ID 与遗漏原因，不补造标题或分数。
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}

function StatusList({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow: string;
  children: ReactNode;
}) {
  return (
    <section className="status-group">
      <div className="status-group-head">
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      <div className="status-group-body">{children}</div>
    </section>
  );
}

function StatusRow({
  label,
  value,
  note,
  state = "healthy",
}: {
  label: string;
  value: string;
  note?: string;
  state?: "healthy" | "quiet" | "pending" | "failed";
}) {
  return (
    <div className="status-list-row">
      <StatusDot state={state} />
      <strong>{label}</strong>
      <span>{value}</span>
      {note && <small>{note}</small>}
    </div>
  );
}

function SystemView() {
  return (
    <div className="view-shell system-view">
      <PageHeading
        eyebrow="doctor"
        title="系统"
        body="查看一次本地运行状态快照、队列、Provider、隐私边界与当前接入面。"
        action={
          <span className="snapshot-badge">
            <StatusDot /> 快照正常
          </span>
        }
      />

      <section className="doctor-banner">
        <div>
          <StatusDot />
          <span>
            <strong>所有已配置子系统正常</strong>
            <small>
              schema codecairn-v01-5 · repo_key {REPO_KEY}
            </small>
          </span>
        </div>
        <dl>
          <div>
            <dt>导入</dt>
            <dd>3</dd>
          </div>
          <div>
            <dt>事件</dt>
            <dd>148</dd>
          </div>
          <div>
            <dt>记忆</dt>
            <dd>5</dd>
          </div>
          <div>
            <dt>恢复冲突</dt>
            <dd>0</dd>
          </div>
        </dl>
      </section>

      <div className="system-grid">
        <StatusList eyebrow="subsystems" title="本地组件">
          <StatusRow label="配置" value="ok" note="codecairn init" />
          <StatusRow label="来源导入" value="ok" note="codecairn import" />
          <StatusRow label="Markdown" value="ok" note="持久真源" />
          <StatusRow label="SQLite" value="ok" note="运行状态" />
          <StatusRow label="LanceDB" value="ok" note="可重建搜索投影" />
          <StatusRow label="Hooks" value="ok" note="3 回执 · 0 失败" />
        </StatusList>

        <StatusList eyebrow="queues" title="处理队列">
          <StatusRow label="索引待处理" value="0" />
          <StatusRow label="索引已完成" value="5" />
          <StatusRow label="索引失败" value="0" />
          <StatusRow label="索引陈旧" value="0" />
          <StatusRow label="语义已完成" value="2" />
          <StatusRow label="语义失败" value="0" />
        </StatusList>

        <StatusList eyebrow="providers" title="Provider 与隐私">
          <StatusRow label="检索方案" value="fastembed" note="configured" />
          <StatusRow
            label="语义提取"
            value="disabled"
            note="未启用网络模型"
            state="quiet"
          />
          <StatusRow label="存储" value="local" />
          <StatusRow label="嵌入" value="local" />
          <StatusRow
            label="来源内容出站"
            value="none"
            note="取决于当前这组配置"
          />
        </StatusList>

        <StatusList eyebrow="recent_hook_receipts" title="最近 Hook 回执">
          <StatusRow
            label="Codex · stop"
            value="imported"
            note="38 ms · codex 0.9"
          />
          <StatusRow
            label="Claude · session_end"
            value="imported"
            note="44 ms · claude 1.0"
          />
          <StatusRow
            label="Codex · stop"
            value="noop"
            note="12 ms · 重复触发幂等"
            state="quiet"
          />
          <p className="group-foot">
            最近回执来自服务接口，不是通用活动流水账。
          </p>
        </StatusList>
      </div>

      <section className="surface-map">
        <div className="section-heading section-heading-inset">
          <div>
            <p className="eyebrow">当前产品接入面</p>
            <h2>已有能力与明确边界</h2>
          </div>
          <span className="surface-note">origin/main · a501fe2</span>
        </div>
        <div className="surface-map-grid">
          <div>
            <span className="surface-state complete">已实现</span>
            <strong>CLI</strong>
            <p>导入、处理、召回、演化、索引、导出与诊断。</p>
          </div>
          <div>
            <span className="surface-state complete">已实现</span>
            <strong>stdio MCP</strong>
            <p>7 个工具与 1 个 Markdown 记忆资源。</p>
          </div>
          <div>
            <span className="surface-state complete">已实现</span>
            <strong>Agent 接入</strong>
            <p>Codex / Claude Hooks 与 Pico MemoryBackend Adapter。</p>
          </div>
          <div>
            <span className="surface-state boundary">尚未提供</span>
            <strong>Hub 连接层</strong>
            <p>没有 HTTP、daemon、登录、远程同步或团队空间。</p>
          </div>
        </div>
        <div className="pico-contract">
          <span>Pico 集成证据</span>
          <p>
            Adapter 与跨进程连续性已验证。联合评测未通过正向收益声明门，因此本原型只展示“上下文已编译”，不宣称任务被改善。
          </p>
        </div>
      </section>
    </div>
  );
}

export default function ConvergedHub({
  initialView,
  initialInspectorTab,
}: {
  initialView: HubView;
  initialInspectorTab: InspectorTab;
}) {
  const [view, setViewState] = useState<HubView>(initialView);
  const [selectedMemory, setSelectedMemory] = useState(memoryIds.knowledge);
  const [inspectorTab, setInspectorTabState] =
    useState<InspectorTab>(initialInspectorTab);

  const updateLocation = useCallback(
    (nextView: HubView, nextTab: InspectorTab = inspectorTab) => {
      const url = new URL(window.location.href);
      url.searchParams.set("view", nextView);
      if (nextView === "memories") {
        url.searchParams.set("detail", nextTab);
      } else {
        url.searchParams.delete("detail");
      }
      window.history.replaceState({}, "", url);
    },
    [inspectorTab],
  );

  const setView = useCallback(
    (nextView: HubView) => {
      setViewState(nextView);
      updateLocation(nextView);
    },
    [updateLocation],
  );

  const setInspectorTab = useCallback(
    (nextTab: InspectorTab) => {
      setInspectorTabState(nextTab);
      updateLocation("memories", nextTab);
    },
    [updateLocation],
  );

  return (
    <main className="hub-root">
      <Sidebar setView={setView} view={view} />
      <section className="hub-stage">
        <SystemBar setView={setView} />
        <div className="workspace">
          {view === "overview" && (
            <OverviewView
              setSelectedMemory={setSelectedMemory}
              setView={setView}
            />
          )}
          {view === "memories" && (
            <MemoriesView
              inspectorTab={inspectorTab}
              selectedMemory={selectedMemory}
              setInspectorTab={setInspectorTab}
              setSelectedMemory={setSelectedMemory}
            />
          )}
          {view === "recall" && <RecallView />}
          {view === "system" && <SystemView />}
        </div>
      </section>
    </main>
  );
}
