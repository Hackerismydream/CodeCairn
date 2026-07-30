"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";
import contractSnapshot from "../fixtures/codecairn-contract.json";

export type HubView = "memories" | "recall" | "system";
export type InspectorTab = "content" | "source" | "evolution";
export type RecallSampleKey = "admitted" | "abstained";

type MemoryType =
  | "repository_knowledge"
  | "task_experience"
  | "work_state"
  | "user_preference";
type MemoryStatus = "active" | "superseded";
type MemoryOrigin = "capture" | "agent_asserted" | "restored";
type MemoryFilter = "all" | MemoryType;
type StatusFilter = "all" | MemoryStatus;
type EvolutionRelation =
  | "work_state_update"
  | "preference_override"
  | "knowledge_obsolete"
  | "knowledge_contradiction"
  | "explicit_restore";
type EvolutionProposer = "capture_model" | "agent" | "user" | "system";

type EvidenceFact = {
  id: string;
  label: string;
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
  relation: EvolutionRelation;
  reason: string;
  proposedBy: EvolutionProposer;
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
  episodeId?: string;
  resourceUri: string;
  payload: Array<{ label: string; value: string }>;
  facts: EvidenceFact[];
  evolution?: EvolutionRecord;
};

type RecallCandidate = {
  rank: number;
  memoryId: string;
  memoryType: MemoryType;
  title: string;
  sources: Array<"lexical" | "vector">;
  score: number;
  snippet: string;
  status: MemoryStatus;
  sourceUri: string;
};

type RecallSample = {
  key: RecallSampleKey;
  switchLabel: string;
  title: string;
  description: string;
  query: string;
  outcome: "admitted" | "abstained";
  reason:
    | "relevant_candidate"
    | "pinned_work_state"
    | "no_candidates"
    | "below_threshold";
  policy: string;
  vectorThreshold: number;
  maxVectorScore: number | null;
  retrievalProfile: string;
  latencyMs: number;
  lexicalCandidateCount: number;
  vectorCandidateCount: number;
  tokenCount: number;
  tokenLimit: number;
  sourceCursor: number;
  indexCursor: number;
  candidates: RecallCandidate[];
  omissions: Array<{ memoryId: string; reason: string }>;
  markdown: string;
};

const REPO_LABEL = "Hackerismydream / CodeCairn";
const admittedContract = contractSnapshot.outputs.recall_admitted;
const admittedSidecar = admittedContract.sidecar;
const abstainedContract = contractSnapshot.outputs.recall_abstained;
const abstainedSidecar = abstainedContract.sidecar;
const evolutionContract =
  contractSnapshot.outputs.memory_history.evolutions[0];
const doctorContract = contractSnapshot.outputs.doctor;
const successorMemoryContract =
  contractSnapshot.outputs.memory_detail.memory;
const predecessorMemoryContract = contractSnapshot.outputs.list.find(
  (memory) => memory.memory_id === evolutionContract.predecessor_id,
);
const predecessorPageContract =
  contractSnapshot.outputs.memory_page.items.find(
    (memory) => memory.memory_id === evolutionContract.predecessor_id,
  );

function contractEnum<const Values extends readonly string[]>(
  value: string,
  allowed: Values,
): Values[number] {
  if (!(allowed as readonly string[]).includes(value)) {
    throw new Error(`Unsupported CodeCairn contract value: ${value}`);
  }
  return value as Values[number];
}

function contractTime(milliseconds: number): string {
  return `${new Date(milliseconds)
    .toISOString()
    .slice(0, 16)
    .replace("T", " ")} UTC`;
}

function contractPayload(
  payload: Record<string, string>,
): Array<{ label: string; value: string }> {
  return Object.entries(payload).map(([label, value]) => ({ label, value }));
}

if (!predecessorMemoryContract || !predecessorPageContract) {
  throw new Error("The checked-in Hub memory fixture has an incomplete lineage.");
}

const RENDERER_ID = admittedSidecar.context_trace.renderer;
const admittedRanked = admittedSidecar.ranked[0];

const memoryIds = {
  knowledge: admittedRanked.memory_id,
  experience:
    "mem_750a903cc114f75c9083db5cb4e536b782365ed1b0acb84f1b8067f6f05b5592",
  workState:
    "mem_921ac81be2ef78a91254fb68fa13a92ec026c34ddb424862b95b00c77f981e5a",
  preference:
    "mem_c039ab7211fdc312c523b56013d57e67cd96c433f4bcb8102abb1e623f09a463",
  oldKnowledge: evolutionContract.predecessor_id,
};

const evolution: EvolutionRecord = {
  relation: contractEnum(evolutionContract.relation_kind, [
    "work_state_update",
    "preference_override",
    "knowledge_obsolete",
    "knowledge_contradiction",
    "explicit_restore",
  ] as const),
  reason: evolutionContract.reason,
  proposedBy: contractEnum(evolutionContract.proposer, [
    "capture_model",
    "agent",
    "user",
    "system",
  ] as const),
  createdAt: contractTime(evolutionContract.created_at_ms),
  predecessorId: evolutionContract.predecessor_id,
  predecessorTitle: predecessorMemoryContract.title,
  successorId: evolutionContract.successor_id,
  successorTitle: successorMemoryContract.title,
};

const facts = {
  userTask: {
    id: "fact_3d653cd5447dc19f0d86f93cbe6479e74a5f542ceff7e4693b9db62736c8159f",
    label: "用户任务",
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
    label: "用户偏好来源",
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
    shortId: memoryIds.knowledge.slice(0, 12),
    type: contractEnum(successorMemoryContract.memory_type, [
      "repository_knowledge",
      "task_experience",
      "work_state",
      "user_preference",
    ] as const),
    title: successorMemoryContract.title,
    content: successorMemoryContract.content,
    category: successorMemoryContract.category,
    tags: successorMemoryContract.tags,
    status: contractEnum(contractSnapshot.outputs.memory_detail.status, [
      "active",
      "superseded",
    ] as const),
    origin: contractEnum(successorMemoryContract.origin, [
      "capture",
      "agent_asserted",
      "restored",
    ] as const),
    createdAt: contractTime(successorMemoryContract.created_at_ms),
    resourceUri: contractSnapshot.outputs.memory_detail.resource_uri,
    payload: contractPayload(successorMemoryContract.payload),
    facts: [],
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
    resourceUri: `codecairn://memory/${memoryIds.workState}`,
    payload: [
      { label: "workstream_key", value: "codecairn-hub-v03" },
      { label: "workstream_state", value: "open" },
      { label: "goal", value: "让人看懂当前命名空间中的记忆" },
      { label: "progress", value: "完成能力审计与页面收口" },
      { label: "blockers", value: "尚无本地 Hub 展示适配层" },
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
    shortId: memoryIds.oldKnowledge.slice(0, 12),
    type: contractEnum(predecessorMemoryContract.memory_type, [
      "repository_knowledge",
      "task_experience",
      "work_state",
      "user_preference",
    ] as const),
    title: predecessorMemoryContract.title,
    content: predecessorMemoryContract.content,
    category: predecessorMemoryContract.category,
    tags: predecessorMemoryContract.tags,
    status: contractEnum(predecessorPageContract.status, [
      "active",
      "superseded",
    ] as const),
    origin: contractEnum(predecessorMemoryContract.origin, [
      "capture",
      "agent_asserted",
      "restored",
    ] as const),
    createdAt: contractTime(predecessorMemoryContract.created_at_ms),
    resourceUri: `codecairn://memory/${memoryIds.oldKnowledge}`,
    payload: contractPayload(predecessorMemoryContract.payload),
    facts: [],
    evolution,
  },
];

const memoryTypeLabels: Record<MemoryType, string> = {
  repository_knowledge: "仓库知识",
  task_experience: "任务经验",
  work_state: "工作状态",
  user_preference: "仓库工作偏好",
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

const omissionLabels: Record<string, string> = {
  historical_filter: "历史版本过滤",
  relevance: "相关性不足",
  type_cap: "类型上限",
  limit: "结果数量上限",
  token_budget: "上下文预算",
};

if (
  admittedSidecar.ranked.length !== 1 ||
  admittedRanked.snippets.length !== 1 ||
  admittedSidecar.omissions.length !== 0 ||
  abstainedSidecar.ranked.length !== 0
) {
  throw new Error("The checked-in Hub recall fixture has an unexpected shape.");
}

const recallSamples: Record<RecallSampleKey, RecallSample> = {
  admitted: {
    key: "admitted",
    switchLabel: "相关记忆已接纳",
    title: "相关记忆已进入上下文",
    description:
      "查询命中一条仓库知识，相关性门控通过后再进行排序与上下文编译。",
    query: admittedSidecar.query,
    outcome: contractEnum(admittedSidecar.admission_trace.outcome, [
      "admitted",
      "abstained",
    ] as const),
    reason: contractEnum(admittedSidecar.admission_trace.reason, [
      "relevant_candidate",
      "pinned_work_state",
      "no_candidates",
      "below_threshold",
    ] as const),
    policy: admittedSidecar.admission_trace.policy,
    vectorThreshold: admittedSidecar.admission_trace.vector_threshold,
    maxVectorScore: admittedSidecar.admission_trace.max_vector_score,
    retrievalProfile: admittedSidecar.retrieval_profile,
    latencyMs: admittedSidecar.latency_ms,
    lexicalCandidateCount: admittedSidecar.lexical_candidate_count,
    vectorCandidateCount: admittedSidecar.vector_candidate_count,
    tokenCount: admittedSidecar.context_trace.token_count,
    tokenLimit: admittedSidecar.context_trace.token_limit,
    sourceCursor: admittedSidecar.source_cursor,
    indexCursor: admittedSidecar.index_cursor,
    candidates: [
      {
        rank: admittedRanked.rank,
        memoryId: admittedRanked.memory_id,
        memoryType: contractEnum(admittedRanked.memory_type, [
          "repository_knowledge",
          "task_experience",
          "work_state",
          "user_preference",
        ] as const),
        title: admittedRanked.title,
        sources: admittedRanked.candidate_sources.map((source) =>
          contractEnum(source, ["lexical", "vector"] as const),
        ),
        score: admittedRanked.final_score,
        snippet: admittedRanked.snippets[0].text,
        status: contractEnum(admittedRanked.status, [
          "active",
          "superseded",
        ] as const),
        sourceUri: admittedRanked.source_uri,
      },
    ],
    omissions: [],
    markdown: admittedContract.markdown,
  },
  abstained: {
    key: "abstained",
    switchLabel: "无关查询已拒答",
    title: "没有记忆进入上下文",
    description:
      "向量候选低于当前检索方案的相关性阈值。CodeCairn 返回明确拒答，而不是选择最不相关的记忆。",
    query: abstainedSidecar.query,
    outcome: contractEnum(abstainedSidecar.admission_trace.outcome, [
      "admitted",
      "abstained",
    ] as const),
    reason: contractEnum(abstainedSidecar.admission_trace.reason, [
      "relevant_candidate",
      "pinned_work_state",
      "no_candidates",
      "below_threshold",
    ] as const),
    policy: abstainedSidecar.admission_trace.policy,
    vectorThreshold: abstainedSidecar.admission_trace.vector_threshold,
    maxVectorScore: abstainedSidecar.admission_trace.max_vector_score,
    retrievalProfile: abstainedSidecar.retrieval_profile,
    latencyMs: abstainedSidecar.latency_ms,
    lexicalCandidateCount: abstainedSidecar.lexical_candidate_count,
    vectorCandidateCount: abstainedSidecar.vector_candidate_count,
    tokenCount: abstainedSidecar.context_trace.token_count,
    tokenLimit: abstainedSidecar.context_trace.token_limit,
    sourceCursor: abstainedSidecar.source_cursor,
    indexCursor: abstainedSidecar.index_cursor,
    candidates: [],
    omissions: abstainedSidecar.omissions.map((omission) => ({
      memoryId: omission.memory_id,
      reason: omission.reason,
    })),
    markdown: abstainedContract.markdown,
  },
};

const navItems: Array<{ view: HubView; label: string }> = [
  { view: "memories", label: "记忆" },
  { view: "recall", label: "召回" },
  { view: "system", label: "系统" },
];

function CairnMark() {
  return (
    <span className="cairn-mark" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

function StatusDot({
  state = "healthy",
}: {
  state?: "healthy" | "quiet" | "failed";
}) {
  return <span className={`status-dot status-${state}`} aria-hidden="true" />;
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

      <nav aria-label="记忆中心导航">
        {navItems.map((item) => (
          <button
            aria-current={view === item.view ? "page" : undefined}
            className={view === item.view ? "active" : ""}
            key={item.view}
            onClick={() => setView(item.view)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </nav>
    </aside>
  );
}

function SystemBar() {
  return (
    <header className="system-bar">
      <div>
        <small>当前命名空间</small>
        <strong>{REPO_LABEL}</strong>
      </div>
      <span>只读原型 · 示例数据</span>
    </header>
  );
}

function PageHeading({
  title,
  body,
}: {
  title: string;
  body: string;
}) {
  return (
    <div className="page-heading">
      <h1>{title}</h1>
      <p>{body}</p>
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
    <section className="memory-list-panel">
      <div className="memory-toolbar">
        <div className="filter-strip" aria-label="记忆类型筛选">
          {(
            [
              ["all", "全部"],
              ["repository_knowledge", "仓库知识"],
              ["task_experience", "任务经验"],
              ["work_state", "工作状态"],
              ["user_preference", "仓库工作偏好"],
            ] as Array<[MemoryFilter, string]>
          ).map(([key, label]) => (
            <button
              aria-pressed={typeFilter === key}
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

      <div className="memory-table">
        <div className="memory-table-head" aria-hidden="true">
          <span>记忆</span>
          <span>类型</span>
          <span>记录时间</span>
          <span>状态</span>
        </div>
        {filtered.map((memory) => (
          <button
            aria-pressed={selectedMemory === memory.id}
            className={`memory-row ${
              selectedMemory === memory.id ? "selected" : ""
            }`}
            key={memory.id}
            onClick={() => setSelectedMemory(memory.id)}
            type="button"
          >
            <span className="memory-row-title">
              <strong>{memory.title}</strong>
              <code>{memory.shortId}</code>
            </span>
            <span className="memory-row-type">
              {memoryTypeLabels[memory.type]}
            </span>
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

      <footer className="list-foot">
        <span>
          对应 <code>list_memories</code>
        </span>
        <span>{filtered.length} 条结果 · 无下一页游标</span>
      </footer>
    </section>
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
      <header className="inspector-head">
        <span>
          <small>{memoryTypeLabels[memory.type]}</small>
          <code>{memory.shortId}</code>
        </span>
        <span className={`memory-status memory-status-${memory.status}`}>
          {statusLabels[memory.status]}
        </span>
        <h2>{memory.title}</h2>
        <p>
          {originLabels[memory.origin]} · {memory.createdAt}
        </p>
      </header>

      <div className="inspector-tabs" role="tablist" aria-label="记忆详情">
        {tabs.map((item) => (
          <button
            aria-controls={`inspector-panel-${item.key}`}
            aria-selected={tab === item.key}
            className={tab === item.key ? "active" : ""}
            id={`inspector-tab-${item.key}`}
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
        <div
          aria-labelledby="inspector-tab-content"
          className="inspector-section"
          id="inspector-panel-content"
          role="tabpanel"
        >
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

          <section className="payload-block">
            <h3>类型载荷</h3>
            {memory.payload.map((row) => (
              <div key={row.label}>
                <code>{row.label}</code>
                <span>{row.value}</span>
              </div>
            ))}
          </section>

          <div className="tag-list">
            {memory.tags.map((tag) => (
              <span key={tag}>#{tag}</span>
            ))}
          </div>

          <div className="truth-note">
            <strong>记忆资源 URI</strong>
            <code>{memory.resourceUri}</code>
          </div>
        </div>
      )}

      {tab === "source" && (
        <div
          aria-labelledby="inspector-tab-source"
          className="inspector-section evidence-list"
          id="inspector-panel-source"
          role="tabpanel"
        >
          <p className="inspector-explainer">
            来源由标准化事件确定。这里只显示公开摘要，不暴露本机原始路径。
          </p>
          {memory.facts.length > 0 ? (
            memory.facts.map((fact, index) => (
              <article
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
              </article>
            ))
          ) : (
            <div className="empty-state compact">
              <strong>没有来源事实</strong>
              <p>
                这是一条 <code>agent_asserted</code>{" "}
                记忆，不伪造事件证据。
              </p>
            </div>
          )}
        </div>
      )}

      {tab === "evolution" && (
        <div
          aria-labelledby="inspector-tab-evolution"
          className="inspector-section evolution-list"
          id="inspector-panel-evolution"
          role="tabpanel"
        >
          {memory.evolution ? (
            <>
              <p className="inspector-explainer">
                演化记录不可变，前后两个版本都会保留以供审计。
              </p>
              <div className="evolution-memory">
                <small>前一版本 · 已替代</small>
                <strong>{memory.evolution.predecessorTitle}</strong>
                <code>{memory.evolution.predecessorId.slice(0, 12)}…</code>
              </div>
              <div className="evolution-reason">
                <code>{memory.evolution.relation}</code>
                <p>{memory.evolution.reason}</p>
                <small>
                  {memory.evolution.proposedBy} · {memory.evolution.createdAt}
                </small>
              </div>
              <div className="evolution-memory current">
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
    <div className="view-shell">
      <PageHeading
        title="记忆"
        body="按类型和生命周期浏览记录，再查看内容、来源事实与不可变演化历史。"
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

function RecallCandidateRow({ candidate }: { candidate: RecallCandidate }) {
  return (
    <article className="candidate-row">
      <span className="candidate-rank">#{candidate.rank}</span>
      <span className="candidate-memory">
        <strong>{candidate.title}</strong>
        <code>{candidate.memoryId.slice(0, 12)}…</code>
        <small>{memoryTypeLabels[candidate.memoryType]}</small>
      </span>
      <span className="candidate-sources">
        {candidate.sources.map((source) => (
          <span key={source}>{source === "lexical" ? "关键词" : "向量"}</span>
        ))}
      </span>
      <strong className="candidate-score">
        {candidate.score.toFixed(2)}
      </strong>
      <p>{candidate.snippet}</p>
      <code className="candidate-uri">{candidate.sourceUri}</code>
    </article>
  );
}

function AdmissionTrace({ sample }: { sample: RecallSample }) {
  return (
    <section className="admission-panel">
      <div className="admission-summary">
        <span
          className={`admission-outcome admission-outcome-${sample.outcome}`}
        >
          {sample.outcome === "admitted" ? "已接纳" : "已拒答"}
        </span>
        <div>
          <h2>{sample.title}</h2>
          <p>{sample.description}</p>
        </div>
      </div>
      <dl className="admission-trace">
        <div>
          <dt>门控策略</dt>
          <dd>{sample.policy}</dd>
        </div>
        <div>
          <dt>结果</dt>
          <dd>{sample.outcome}</dd>
        </div>
        <div>
          <dt>原因</dt>
          <dd>{sample.reason}</dd>
        </div>
        <div>
          <dt>向量阈值</dt>
          <dd>{sample.vectorThreshold.toFixed(2)}</dd>
        </div>
        <div>
          <dt>最高向量相似度</dt>
          <dd>
            {sample.maxVectorScore === null
              ? "无"
              : sample.maxVectorScore.toFixed(2)}
          </dd>
        </div>
      </dl>
      <p className="contract-caption">
        对应 <code>sidecar.admission_trace</code>
      </p>
    </section>
  );
}

function RecallView({
  sampleKey,
  setSampleKey,
}: {
  sampleKey: RecallSampleKey;
  setSampleKey: (sample: RecallSampleKey) => void;
}) {
  const sample = recallSamples[sampleKey];

  return (
    <div className="view-shell">
      <PageHeading
        title="召回"
        body="查看两个固定的 RecallResult 契约样例。样例切换不会发起服务调用。"
      />

      <div className="sample-switch" aria-label="召回契约样例">
        {(Object.values(recallSamples) as RecallSample[]).map((item) => (
          <button
            aria-pressed={sampleKey === item.key}
            className={sampleKey === item.key ? "active" : ""}
            key={item.key}
            onClick={() => setSampleKey(item.key)}
            type="button"
          >
            {item.switchLabel}
          </button>
        ))}
      </div>

      <section className="query-summary">
        <span>任务</span>
        <strong>{sample.query}</strong>
        <small>固定示例 · 不执行真实召回</small>
      </section>

      <AdmissionTrace sample={sample} />

      <dl className="recall-metrics">
        <div>
          <dt>延迟</dt>
          <dd>{sample.latencyMs.toFixed(1)} 毫秒</dd>
        </div>
        <div>
          <dt>候选</dt>
          <dd>
            {sample.lexicalCandidateCount} 关键词 ·{" "}
            {sample.vectorCandidateCount} 向量
          </dd>
        </div>
        <div>
          <dt>上下文预算</dt>
          <dd>
            {sample.tokenCount} / {sample.tokenLimit}
          </dd>
        </div>
        <div>
          <dt>游标</dt>
          <dd>
            {sample.sourceCursor} / {sample.indexCursor} · fresh
          </dd>
        </div>
      </dl>

      <section className="candidate-panel">
        <header>
          <h2>已接纳记忆</h2>
          <span>{sample.candidates.length} 条</span>
        </header>
        <div className="candidate-head" aria-hidden="true">
          <span>排序</span>
          <span>记忆</span>
          <span>来源</span>
          <span>分数</span>
          <span>精确片段</span>
        </div>
        {sample.candidates.length > 0 ? (
          sample.candidates.map((candidate) => (
            <RecallCandidateRow
              candidate={candidate}
              key={candidate.memoryId}
            />
          ))
        ) : (
          <div className="empty-state recall-empty">
            <strong>未接纳任何记忆</strong>
            <p>候选都因相关性不足被省略，排名结果为空。</p>
          </div>
        )}
      </section>

      <div className="recall-output-grid">
        <section className="compiled-context">
          <header>
            <div>
              <h2>最终编译上下文</h2>
              <p>Markdown</p>
            </div>
            <code>{RENDERER_ID}</code>
          </header>
          <pre>{sample.markdown}</pre>
          <dl>
            <div>
              <dt>渲染器</dt>
              <dd>{RENDERER_ID}</dd>
            </div>
            <div>
              <dt>检索方案</dt>
              <dd>{sample.retrievalProfile}</dd>
            </div>
          </dl>
        </section>

        <section className="omission-panel">
          <header>
            <h2>未进入上下文</h2>
            <span>{sample.omissions.length} 条</span>
          </header>
          {sample.omissions.map((omission) => (
            <div
              className="omission-row"
              key={`${omission.memoryId}-${omission.reason}`}
            >
              <code>{omission.memoryId.slice(0, 14)}…</code>
              <strong>{omissionLabels[omission.reason]}</strong>
              <small>{omission.reason}</small>
            </div>
          ))}
          <p>
            省略项只展示记忆 ID 与契约返回的原因，不补造标题或分数。
          </p>
        </section>
      </div>
    </div>
  );
}

function StatusList({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="status-group">
      <h2>{title}</h2>
      <div>{children}</div>
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
  state?: "healthy" | "quiet" | "failed";
}) {
  return (
    <div className="status-row">
      <StatusDot state={state} />
      <strong>{label}</strong>
      <span>{value}</span>
      {note && <small>{note}</small>}
    </div>
  );
}

function SystemView() {
  return (
    <div className="view-shell">
      <PageHeading
        title="系统"
        body="查看一次确定性的本地 Doctor 快照，包括处理队列、Provider 与隐私边界。"
      />

      <section className="doctor-summary">
        <div>
          <StatusDot />
          <span>
            <strong>核心存储与队列正常</strong>
            <code>schema {doctorContract.schema}</code>
            <code>{doctorContract.repo_key}</code>
          </span>
        </div>
        <dl>
          <div>
            <dt>导入</dt>
            <dd>{doctorContract.imports}</dd>
          </div>
          <div>
            <dt>事件</dt>
            <dd>{doctorContract.observed_events}</dd>
          </div>
          <div>
            <dt>记忆</dt>
            <dd>{doctorContract.memories}</dd>
          </div>
          <div>
            <dt>恢复冲突</dt>
            <dd>{doctorContract.conflicted_recovery}</dd>
          </div>
        </dl>
      </section>

      <div className="system-grid">
        <StatusList title="本地组件">
          <StatusRow
            label="配置"
            value={
              doctorContract.subsystems.config.status === "ok"
                ? "正常"
                : "异常"
            }
            note={doctorContract.subsystems.config.remediation}
          />
          <StatusRow
            label="来源导入"
            value={
              doctorContract.subsystems.source_import.status === "ok"
                ? "正常"
                : "异常"
            }
            note={doctorContract.subsystems.source_import.remediation}
          />
          <StatusRow label="Markdown" value="正常" note="持久真源" />
          <StatusRow label="SQLite" value="正常" note="运行状态" />
          <StatusRow
            label="LanceDB"
            value="正常"
            note="可重建搜索投影"
          />
          <StatusRow
            label="Hooks"
            value="未配置"
            note={`${doctorContract.hook_receipts.total} 回执 · ${doctorContract.hook_receipts.failed} 失败`}
            state="quiet"
          />
        </StatusList>

        <StatusList title="处理队列">
          <StatusRow
            label="索引待处理"
            value={String(doctorContract.index_jobs.pending)}
          />
          <StatusRow
            label="索引已完成"
            value={String(doctorContract.index_jobs.indexed)}
          />
          <StatusRow
            label="索引失败"
            value={String(doctorContract.index_jobs.failed)}
          />
          <StatusRow
            label="索引陈旧"
            value={String(doctorContract.index_jobs.stale)}
          />
          <StatusRow
            label="语义已完成"
            value={String(doctorContract.semantic_jobs.completed)}
          />
          <StatusRow
            label="语义失败"
            value={String(doctorContract.semantic_jobs.failed)}
          />
        </StatusList>

        <StatusList title="Provider 与隐私">
          <StatusRow
            label="检索方案"
            value={doctorContract.providers.retrieval}
            note={
              doctorContract.providers.retrieval_state === "configured"
                ? "已配置"
                : doctorContract.providers.retrieval_state
            }
          />
          <StatusRow
            label="语义提取"
            value="已关闭"
            note={doctorContract.providers.semantic}
            state="quiet"
          />
          <StatusRow
            label="存储"
            value={
              doctorContract.privacy.storage === "local"
                ? "本地"
                : doctorContract.privacy.storage
            }
          />
          <StatusRow
            label="嵌入"
            value={
              doctorContract.privacy.embedding === "local"
                ? "本地"
                : doctorContract.privacy.embedding
            }
          />
          <StatusRow
            label="来源内容出站"
            value={
              doctorContract.privacy.source_content_egress === "none"
                ? "无"
                : doctorContract.privacy.source_content_egress
            }
            note="取决于当前配置"
          />
        </StatusList>
      </div>

      <p className="doctor-note">
        这是一次 Doctor 结果快照，不代表后台服务常驻或远程连接在线。
      </p>
    </div>
  );
}

export default function ConvergedHub({
  initialView,
  initialInspectorTab,
  initialRecallSample,
}: {
  initialView: HubView;
  initialInspectorTab: InspectorTab;
  initialRecallSample: RecallSampleKey;
}) {
  const [view, setViewState] = useState<HubView>(initialView);
  const [selectedMemory, setSelectedMemory] = useState(memoryIds.knowledge);
  const [inspectorTab, setInspectorTabState] =
    useState<InspectorTab>(initialInspectorTab);
  const [recallSample, setRecallSampleState] =
    useState<RecallSampleKey>(initialRecallSample);

  const updateLocation = useCallback(
    (
      nextView: HubView,
      nextTab: InspectorTab = inspectorTab,
      nextSample: RecallSampleKey = recallSample,
    ) => {
      const url = new URL(window.location.href);
      url.searchParams.set("view", nextView);
      if (nextView === "memories") {
        url.searchParams.set("detail", nextTab);
      } else {
        url.searchParams.delete("detail");
      }
      if (nextView === "recall") {
        url.searchParams.set("sample", nextSample);
      } else {
        url.searchParams.delete("sample");
      }
      window.history.replaceState({}, "", url);
    },
    [inspectorTab, recallSample],
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

  const setRecallSample = useCallback(
    (nextSample: RecallSampleKey) => {
      setRecallSampleState(nextSample);
      updateLocation("recall", inspectorTab, nextSample);
    },
    [inspectorTab, updateLocation],
  );

  return (
    <main className="hub-root">
      <Sidebar setView={setView} view={view} />
      <section className="hub-stage">
        <SystemBar />
        <div className="workspace">
          {view === "memories" && (
            <MemoriesView
              inspectorTab={inspectorTab}
              selectedMemory={selectedMemory}
              setInspectorTab={setInspectorTab}
              setSelectedMemory={setSelectedMemory}
            />
          )}
          {view === "recall" && (
            <RecallView
              sampleKey={recallSample}
              setSampleKey={setRecallSample}
            />
          )}
          {view === "system" && <SystemView />}
        </div>
      </section>
    </main>
  );
}
