from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Literal

from codecairn.memory.context import (
    CONTEXT_DIRECT_MATCH_PRIOR,
    CONTEXT_EVIDENCE_SLOT_KINDS,
    CONTEXT_EVIDENCE_SLOT_POLICY_ID,
    CONTEXT_RENDERER_ID,
    CONTEXT_TOKENIZER_ID,
)
from codecairn.memory.evidence_selector import (
    FACT_SELECTOR_ID,
    MAX_FACT_RERANK_CANDIDATES,
    MAX_FACT_RERANK_CANDIDATES_PER_PARENT,
    MAX_FACT_RERANK_DOCUMENT_CHARS,
    MAX_SELECTED_FACTS_PER_PARENT,
)
from codecairn.memory.models import RecallRoute

RecallPlannerMode = Literal["episode-only", "hierarchy-no-neighbors", "hierarchy"]
TemporalOperation = Literal["none", "point", "duration", "order", "latest"]
SetOperation = Literal["none", "union", "intersection"]
RelationRequirementKind = Literal["temporal_order", "procedure_order"]
ProvenanceStage = Literal["failure", "change", "verification"]
QueryVariantKind = Literal["original", "entity", "temporal"]
ContextEvidenceSlotKind = Literal[
    "high_confidence_parent",
    "prior_state",
    "quantity_transition",
    "semantic_child_support",
    "vocative_alias",
]

_FACT_CUES = re.compile(
    r"\b(when|where|who|which|what|how many|how much|before|after|first|last|"
    r"date|time|year|month|day|name|called|color|age|old)\b",
    re.IGNORECASE,
)
_EPISODE_CUES = re.compile(
    r"\b(why|how did|describe|summari[sz]e|issue|problem|solution|approach|"
    r"debug|fix|failure|failed|resolve|resolved)\b",
    re.IGNORECASE,
)
_PROCEDURE_CUES = re.compile(
    r"\b(how\s+did|steps?|procedure|fix(?:ed|es|ing)?|verif(?:y|ied|ies|ying))\b",
    re.IGNORECASE,
)
_QUANTITY_CUES = re.compile(
    r"\b(how\s+many|number\s+of|count\s+of)\b",
    re.IGNORECASE,
)
_INFERENCE_CUES = re.compile(
    r"\b(could|would|might|likely|potentially|considering|infer)\b",
    re.IGNORECASE,
)
_ALIAS_CUES = re.compile(
    r"\b(nickname|also\s+called|known\s+as)\b",
    re.IGNORECASE,
)
_TEMPORAL_ORDER_CUES = re.compile(
    r"\b(before|after|first|then|order)\b",
    re.IGNORECASE,
)
_PRIOR_STATE_CUES = re.compile(
    r"^\s*(?:was|were|did)\s+[A-Z][A-Za-z0-9_-]{1,63}\b(?P<predicate>.*?)\bbefore\b",
    re.IGNORECASE,
)
_PRIOR_STATE_PREDICATE_CUES = re.compile(
    r"\b(alone|lonel\w*|happ\w*|joy\w*|friend\w*|single|dating|relationship)\b",
    re.IGNORECASE,
)
_FAILURE_CUES = re.compile(r"\b(error|fail(?:ed|ure)?|timeout)\b", re.IGNORECASE)
_CHANGE_CUES = re.compile(
    r"\b(change(?:d)?|fix(?:ed|es|ing)?|patch)\b",
    re.IGNORECASE,
)
_VERIFICATION_CUES = re.compile(
    r"\b(pass(?:ed)?|verif(?:y|ied|ies|ying))\b",
    re.IGNORECASE,
)
_NAMED_ANCHOR = re.compile(r"\b[A-Z][A-Za-z0-9_-]{1,63}\b")
_MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{4})\b",
    re.IGNORECASE,
)
_DAY_MONTH_YEAR = re.compile(
    r"\b(0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+"
    r"(0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.IGNORECASE,
)
_MONTH_NUMBERS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_ANCHOR_STOPWORDS = {
    "A",
    "An",
    "And",
    "Are",
    "Did",
    "Do",
    "Does",
    "Has",
    "Have",
    "How",
    "In",
    "Is",
    "On",
    "The",
    "What",
    "When",
    "Where",
    "Which",
    "Who",
    "Why",
    "Was",
    "Were",
}
_TOPIC_TERM = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_TOPIC_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "count",
        "did",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "many",
        "number",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class EntityCoverageRequirement:
    entity_key: str


@dataclass(frozen=True, slots=True)
class TemporalCoverageRequirement:
    operation: TemporalOperation
    prefixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SetCoverageRequirement:
    operation: SetOperation
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RelationCoverageRequirement:
    relation: RelationRequirementKind


@dataclass(frozen=True, slots=True)
class ProvenanceCoverageRequirement:
    stages: tuple[ProvenanceStage, ...]


@dataclass(frozen=True, slots=True)
class QueryVariant:
    kind: QueryVariantKind
    text: str


@dataclass(frozen=True, slots=True)
class ContextEvidenceSlot:
    """One deterministic admission lane used before global fact-score packing."""

    kind: ContextEvidenceSlotKind
    max_facts: int
    anchors: tuple[str, ...] = ()
    topic_terms: tuple[str, ...] = ()
    minimum_parent_score: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in CONTEXT_EVIDENCE_SLOT_KINDS:
            raise ValueError("Unknown context evidence slot kind")
        if type(self.max_facts) is not int or not 1 <= self.max_facts <= 20:
            raise ValueError("Context evidence slot max_facts must be within [1, 20]")
        if self.minimum_parent_score is not None and (
            isinstance(self.minimum_parent_score, bool)
            or not isinstance(self.minimum_parent_score, int | float)
            or not isfinite(float(self.minimum_parent_score))
        ):
            raise ValueError("Context evidence slot minimum parent score must be finite")


CoverageRequirement = (
    EntityCoverageRequirement
    | TemporalCoverageRequirement
    | SetCoverageRequirement
    | RelationCoverageRequirement
    | ProvenanceCoverageRequirement
)

QUERY_SKETCHER_ID = "codecairn/deterministic-query-sketch-v5"


@dataclass(frozen=True, slots=True)
class QuerySketch:
    """Small deterministic query contract used for coverage, never hard routing."""

    anchors: tuple[str, ...]
    temporal_op: TemporalOperation
    set_op: SetOperation
    wants_procedure: bool
    coverage_slots: tuple[str, ...]
    temporal_prefixes: tuple[str, ...]
    coverage_requirements: tuple[CoverageRequirement, ...] = ()
    query_variants: tuple[QueryVariant, ...] = ()
    evidence_slots: tuple[ContextEvidenceSlot, ...] = ()
    sketcher_id: str = QUERY_SKETCHER_ID
    query_time_llm_calls: Literal[0] = 0


@dataclass(frozen=True, slots=True)
class ExpansionPlan:
    """One bounded expansion request for entity, time, and provenance facts."""

    max_hops: int = 1
    max_total_facts: int = 24
    max_entity_facts: int = 12
    max_time_facts: int = 8
    max_provenance_facts: int = 8

    def __post_init__(self) -> None:
        hard_ceilings = {
            "max_hops": 1,
            "max_total_facts": 24,
            "max_entity_facts": 12,
            "max_time_facts": 8,
            "max_provenance_facts": 8,
        }
        for field_name, hard_ceiling in hard_ceilings.items():
            value = getattr(self, field_name)
            if type(value) is not int or value < 0 or value > hard_ceiling:
                raise ValueError(f"{field_name} exceeds its expansion hard ceiling")
        if self.max_hops != 1:
            raise ValueError("max_hops must equal the one-hop expansion hard ceiling")
        if (
            max(
                self.max_entity_facts,
                self.max_time_facts,
                self.max_provenance_facts,
            )
            > self.max_total_facts
        ):
            raise ValueError("An expansion lane cannot exceed the global expansion limit")


@dataclass(frozen=True, slots=True)
class RecallPlannerConfig:
    """Auditable knobs for hierarchical retrieval and its ablations."""

    mode: RecallPlannerMode = "hierarchy"
    primary_candidate_multiplier: int = 2
    secondary_candidate_multiplier: int = 1
    minimum_primary_candidates: int = 40
    minimum_secondary_candidates: int = 20
    maximum_channel_candidates: int = 64
    rerank_candidate_multiplier: int = 5
    minimum_rerank_candidates: int = 32
    maximum_rerank_candidates: int = 96
    maximum_exploration_results: int = 4
    neighbor_window: int = 1
    neighbor_snippet_budget: int = 20
    matched_facts_per_memory: int = 3
    diverse_matched_facts_per_memory: int = 1
    sibling_facts_per_memory: int = 2
    temporal_sibling_facts_per_memory: int = 5
    fact_rerank_max_candidates: int = MAX_FACT_RERANK_CANDIDATES
    fact_rerank_max_candidates_per_parent: int = MAX_FACT_RERANK_CANDIDATES_PER_PARENT
    fact_rerank_max_selected_per_parent: int = MAX_SELECTED_FACTS_PER_PARENT
    fact_rerank_max_document_chars: int = MAX_FACT_RERANK_DOCUMENT_CHARS
    context_max_chars: int = 23_900
    context_max_tokens: int = 4_000
    context_summary_chars: int = 60
    # Retained in the frozen protocol for backward compatibility. Exact source
    # facts are atomic evidence and are never truncated to this legacy hint.
    context_snippet_chars: int = 200
    context_snippets_per_memory: int = 5
    context_temporal_snippets_per_memory: int = 8
    context_semantic_support_fact_limit: int = 16
    context_quantity_transition_fact_limit: int = 12
    context_vocative_alias_fact_limit: int = 2
    context_prior_state_fact_limit: int = 4
    context_high_confidence_parent_fact_limit: int = 4
    context_high_confidence_parent_score_threshold: float = 5.5
    expansion_plan: ExpansionPlan = ExpansionPlan()

    def __post_init__(self) -> None:
        if self.primary_candidate_multiplier < 1:
            raise ValueError("primary_candidate_multiplier must be positive")
        if self.secondary_candidate_multiplier < 1:
            raise ValueError("secondary_candidate_multiplier must be positive")
        if self.minimum_primary_candidates < 1:
            raise ValueError("minimum_primary_candidates must be positive")
        if self.minimum_secondary_candidates < 1:
            raise ValueError("minimum_secondary_candidates must be positive")
        if self.maximum_channel_candidates < max(
            self.minimum_primary_candidates,
            self.minimum_secondary_candidates,
        ):
            raise ValueError("maximum_channel_candidates must cover the minimum candidates")
        if self.rerank_candidate_multiplier < 1:
            raise ValueError("rerank_candidate_multiplier must be positive")
        if self.minimum_rerank_candidates < 1:
            raise ValueError("minimum_rerank_candidates must be positive")
        if self.maximum_rerank_candidates < self.minimum_rerank_candidates:
            raise ValueError("maximum_rerank_candidates must cover the minimum rerank candidates")
        if self.maximum_exploration_results < 0:
            raise ValueError("maximum_exploration_results must not be negative")
        if self.neighbor_window < 0:
            raise ValueError("neighbor_window must not be negative")
        if self.neighbor_snippet_budget < 0:
            raise ValueError("neighbor_snippet_budget must not be negative")
        if self.matched_facts_per_memory < 1:
            raise ValueError("matched_facts_per_memory must be positive")
        if self.diverse_matched_facts_per_memory < 0:
            raise ValueError("diverse_matched_facts_per_memory must not be negative")
        if self.sibling_facts_per_memory < 0:
            raise ValueError("sibling_facts_per_memory must not be negative")
        if self.temporal_sibling_facts_per_memory < self.sibling_facts_per_memory:
            raise ValueError(
                "temporal_sibling_facts_per_memory must cover sibling_facts_per_memory"
            )
        if self.fact_rerank_max_candidates < 1:
            raise ValueError("fact_rerank_max_candidates must be positive")
        if not (1 <= self.fact_rerank_max_candidates_per_parent <= self.fact_rerank_max_candidates):
            raise ValueError("fact_rerank_max_candidates_per_parent exceeds the global limit")
        if not (
            1
            <= self.fact_rerank_max_selected_per_parent
            <= self.fact_rerank_max_candidates_per_parent
        ):
            raise ValueError(
                "fact_rerank_max_selected_per_parent exceeds the parent candidate limit"
            )
        if self.fact_rerank_max_document_chars < 256:
            raise ValueError("fact_rerank_max_document_chars must be at least 256")
        if self.context_max_chars < 1_000:
            raise ValueError("context_max_chars must be at least 1000")
        if self.context_max_tokens < 256:
            raise ValueError("context_max_tokens must be at least 256")
        if self.context_summary_chars < 1:
            raise ValueError("context_summary_chars must be positive")
        if self.context_snippet_chars < 1:
            raise ValueError("context_snippet_chars must be positive")
        if self.context_snippets_per_memory < 1:
            raise ValueError("context_snippets_per_memory must be positive")
        if self.context_temporal_snippets_per_memory < self.context_snippets_per_memory:
            raise ValueError(
                "context_temporal_snippets_per_memory must cover context_snippets_per_memory"
            )
        evidence_slot_limits = {
            "context_semantic_support_fact_limit": (
                self.context_semantic_support_fact_limit,
                20,
            ),
            "context_quantity_transition_fact_limit": (
                self.context_quantity_transition_fact_limit,
                12,
            ),
            "context_vocative_alias_fact_limit": (
                self.context_vocative_alias_fact_limit,
                2,
            ),
            "context_prior_state_fact_limit": (
                self.context_prior_state_fact_limit,
                4,
            ),
            "context_high_confidence_parent_fact_limit": (
                self.context_high_confidence_parent_fact_limit,
                4,
            ),
        }
        for field_name, (value, hard_ceiling) in evidence_slot_limits.items():
            if type(value) is not int or not 1 <= value <= hard_ceiling:
                raise ValueError(f"{field_name} exceeds its context-slot hard ceiling")
        if (
            isinstance(self.context_high_confidence_parent_score_threshold, bool)
            or not isinstance(
                self.context_high_confidence_parent_score_threshold,
                int | float,
            )
            or not isfinite(float(self.context_high_confidence_parent_score_threshold))
        ):
            raise ValueError("High-confidence parent score threshold must be finite")
        if self.mode != "hierarchy" and self.neighbor_window != 0:
            raise ValueError("Only hierarchy mode may expand temporal neighbors")

    @classmethod
    def for_mode(cls, mode: RecallPlannerMode) -> RecallPlannerConfig:
        return cls(mode=mode, neighbor_window=1 if mode == "hierarchy" else 0)

    @property
    def atomic_fact_enabled(self) -> bool:
        return self.mode != "episode-only"

    @property
    def public_config(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "router": "deterministic-cues-v1",
            "hard_route_cutoff": False,
            "query_sketcher": QUERY_SKETCHER_ID,
            "query_time_llm_calls": 0,
            "primary_candidate_multiplier": self.primary_candidate_multiplier,
            "secondary_candidate_multiplier": self.secondary_candidate_multiplier,
            "minimum_primary_candidates": self.minimum_primary_candidates,
            "minimum_secondary_candidates": self.minimum_secondary_candidates,
            "maximum_channel_candidates": self.maximum_channel_candidates,
            "rerank_candidate_multiplier": self.rerank_candidate_multiplier,
            "minimum_rerank_candidates": self.minimum_rerank_candidates,
            "maximum_rerank_candidates": self.maximum_rerank_candidates,
            "maximum_exploration_results": self.maximum_exploration_results,
            "neighbor_window": self.neighbor_window,
            "temporal_neighbor_window": (
                max(self.neighbor_window, 2) if self.neighbor_window > 0 else 0
            ),
            "neighbor_snippet_budget": self.neighbor_snippet_budget,
            "expansion_contract": "typed-bounded-one-hop-v2",
            "expansion_max_hops": self.expansion_plan.max_hops,
            "expansion_max_total_facts": self.expansion_plan.max_total_facts,
            "expansion_max_entity_facts": self.expansion_plan.max_entity_facts,
            "expansion_max_time_facts": self.expansion_plan.max_time_facts,
            "expansion_max_provenance_facts": self.expansion_plan.max_provenance_facts,
            "temporal_lane": "explicit-calendar-prefix-v2",
            "enrichment_order": "matched-neighbor-then-capacity-aware-dialogue-rerank-v7",
            "matched_facts_per_memory": self.matched_facts_per_memory,
            "diverse_matched_facts_per_memory": self.diverse_matched_facts_per_memory,
            "sibling_facts_per_memory": self.sibling_facts_per_memory,
            "temporal_sibling_facts_per_memory": self.temporal_sibling_facts_per_memory,
            "fact_selector": FACT_SELECTOR_ID,
            "fact_rerank_max_candidates": self.fact_rerank_max_candidates,
            "fact_rerank_max_candidates_per_parent": (self.fact_rerank_max_candidates_per_parent),
            "fact_rerank_max_selected_per_parent": (self.fact_rerank_max_selected_per_parent),
            "fact_rerank_max_document_chars": self.fact_rerank_max_document_chars,
            "context_renderer": CONTEXT_RENDERER_ID,
            "context_direct_match_prior": CONTEXT_DIRECT_MATCH_PRIOR,
            "context_evidence_slot_policy": CONTEXT_EVIDENCE_SLOT_POLICY_ID,
            "context_semantic_support_fact_limit": (self.context_semantic_support_fact_limit),
            "context_quantity_transition_fact_limit": (self.context_quantity_transition_fact_limit),
            "context_vocative_alias_fact_limit": self.context_vocative_alias_fact_limit,
            "context_prior_state_fact_limit": self.context_prior_state_fact_limit,
            "context_high_confidence_parent_fact_limit": (
                self.context_high_confidence_parent_fact_limit
            ),
            "context_high_confidence_parent_score_threshold": (
                self.context_high_confidence_parent_score_threshold
            ),
            "context_max_chars": self.context_max_chars,
            "context_max_tokens": self.context_max_tokens,
            "context_tokenizer": CONTEXT_TOKENIZER_ID,
            "context_summary_chars": self.context_summary_chars,
            "context_snippet_chars": self.context_snippet_chars,
            "context_snippets_per_memory": self.context_snippets_per_memory,
            "context_temporal_snippets_per_memory": (self.context_temporal_snippets_per_memory),
        }


@dataclass(frozen=True, slots=True)
class RecallPlan:
    query_sketch: QuerySketch
    route: RecallRoute
    episode_candidate_limit: int
    atomic_fact_candidate_limit: int
    core_episode_candidate_limit: int
    core_atomic_fact_candidate_limit: int
    rerank_candidate_limit: int
    core_rerank_candidate_limit: int
    exploration_result_limit: int
    expand_neighbors: bool
    neighbor_window: int
    neighbor_snippet_budget: int
    expansion_plan: ExpansionPlan = ExpansionPlan()


class RecallPlanner:
    """Route a query without making either hierarchy level a single point of failure."""

    def __init__(self, config: RecallPlannerConfig | None = None) -> None:
        self.config = config or RecallPlannerConfig()

    def plan(self, query: str, *, limit: int) -> RecallPlan:
        query_sketch = _query_sketch(query, config=self.config)
        route = _route(query)
        primary_limit = min(
            self.config.maximum_channel_candidates,
            max(
                self.config.minimum_primary_candidates,
                limit * self.config.primary_candidate_multiplier,
            ),
        )
        secondary_limit = min(
            self.config.maximum_channel_candidates,
            max(
                self.config.minimum_secondary_candidates,
                limit * self.config.secondary_candidate_multiplier,
            ),
        )
        rerank_limit = min(
            self.config.maximum_rerank_candidates,
            max(
                self.config.minimum_rerank_candidates,
                limit * self.config.rerank_candidate_multiplier,
            ),
        )
        core_rerank_limit = min(rerank_limit, self.config.minimum_rerank_candidates)
        exploration_result_limit = (
            min(self.config.maximum_exploration_results, limit // 5)
            if rerank_limit > core_rerank_limit
            else 0
        )
        episode_limit = primary_limit if route == "episode_first" else secondary_limit
        fact_limit = primary_limit if route == "fact_first" else secondary_limit
        core_episode_limit = (
            self.config.minimum_primary_candidates
            if route == "episode_first"
            else self.config.minimum_secondary_candidates
        )
        core_fact_limit = (
            self.config.minimum_primary_candidates
            if route == "fact_first"
            else self.config.minimum_secondary_candidates
        )
        if not self.config.atomic_fact_enabled:
            fact_limit = 0
        temporal_neighbor_window = (
            max(self.config.neighbor_window, 2)
            if query_sketch.temporal_op != "none" and self.config.neighbor_window > 0
            else self.config.neighbor_window
        )
        return RecallPlan(
            query_sketch=query_sketch,
            route=route,
            episode_candidate_limit=episode_limit,
            atomic_fact_candidate_limit=fact_limit,
            core_episode_candidate_limit=core_episode_limit,
            core_atomic_fact_candidate_limit=core_fact_limit,
            rerank_candidate_limit=rerank_limit,
            core_rerank_candidate_limit=core_rerank_limit,
            exploration_result_limit=exploration_result_limit,
            expand_neighbors=(
                self.config.neighbor_window > 0 and self.config.neighbor_snippet_budget > 0
            ),
            neighbor_window=temporal_neighbor_window,
            neighbor_snippet_budget=self.config.neighbor_snippet_budget,
            expansion_plan=self.config.expansion_plan,
        )


def _query_sketch(
    query: str,
    *,
    config: RecallPlannerConfig,
) -> QuerySketch:
    anchors = tuple(
        dict.fromkeys(
            match.group(0).casefold()
            for match in _NAMED_ANCHOR.finditer(query)
            if match.group(0) not in _ANCHOR_STOPWORDS
            and match.group(0).casefold() not in _MONTH_NUMBERS
        )
    )
    lowered = query.casefold()
    temporal_prefixes = _explicit_temporal_prefixes(query)
    if _TEMPORAL_ORDER_CUES.search(query) is not None:
        temporal_op: TemporalOperation = "order"
    elif any(cue in lowered for cue in ("latest", "most recent", "last")):
        temporal_op = "latest"
    elif any(cue in lowered for cue in ("how long", "duration")):
        temporal_op = "duration"
    elif temporal_prefixes or any(
        cue in lowered for cue in ("when", "date", "time", "year", "month", "day")
    ):
        temporal_op = "point"
    else:
        temporal_op = "none"
    if any(cue in lowered for cue in (" both ", "shared", "common")):
        set_op: SetOperation = "intersection"
    elif any(cue in lowered for cue in (" either ", " or ")):
        set_op = "union"
    else:
        set_op = "none"
    wants_procedure = _PROCEDURE_CUES.search(query) is not None
    return QuerySketch(
        anchors=anchors,
        temporal_op=temporal_op,
        set_op=set_op,
        wants_procedure=wants_procedure,
        coverage_slots=anchors,
        temporal_prefixes=temporal_prefixes,
        coverage_requirements=_coverage_requirements(
            query,
            anchors=anchors,
            temporal_op=temporal_op,
            temporal_prefixes=temporal_prefixes,
            set_op=set_op,
            wants_procedure=wants_procedure,
        ),
        query_variants=_query_variants(
            query,
            anchors=anchors,
            temporal_prefixes=temporal_prefixes,
        ),
        evidence_slots=_context_evidence_slots(
            query,
            anchors=anchors,
            temporal_op=temporal_op,
            set_op=set_op,
            wants_procedure=wants_procedure,
            config=config,
        ),
    )


def _explicit_temporal_prefixes(query: str) -> tuple[str, ...]:
    prefixes: list[str] = []
    full_date_spans: list[tuple[int, int]] = []
    for pattern, month_group, day_group, year_group in (
        (_DAY_MONTH_YEAR, 2, 1, 3),
        (_MONTH_DAY_YEAR, 1, 2, 3),
    ):
        for match in pattern.finditer(query):
            full_date_spans.append(match.span())
            month = _MONTH_NUMBERS[match.group(month_group).casefold()]
            day = int(match.group(day_group))
            year = int(match.group(year_group))
            try:
                parsed = date(year, month, day)
            except ValueError:
                continue
            prefixes.append(parsed.isoformat())
    for match in _MONTH_YEAR.finditer(query):
        if any(match.start() >= start and match.end() <= end for start, end in full_date_spans):
            continue
        prefixes.append(f"{match.group(2)}-{_MONTH_NUMBERS[match.group(1).casefold()]:02d}")
    return tuple(dict.fromkeys(prefixes))


def _context_evidence_slots(
    query: str,
    *,
    anchors: tuple[str, ...],
    temporal_op: TemporalOperation,
    set_op: SetOperation,
    wants_procedure: bool,
    config: RecallPlannerConfig,
) -> tuple[ContextEvidenceSlot, ...]:
    slots: list[ContextEvidenceSlot] = [
        ContextEvidenceSlot(
            kind="high_confidence_parent",
            max_facts=config.context_high_confidence_parent_fact_limit,
            minimum_parent_score=config.context_high_confidence_parent_score_threshold,
        )
    ]
    quantity = _QUANTITY_CUES.search(query) is not None
    alias = _ALIAS_CUES.search(query) is not None
    inference = _INFERENCE_CUES.search(query) is not None
    prior_match = _PRIOR_STATE_CUES.search(query)
    prior_state = (
        prior_match is not None
        and _PRIOR_STATE_PREDICATE_CUES.search(prior_match.group("predicate")) is not None
    )
    if alias and len(anchors) >= 2:
        slots.append(
            ContextEvidenceSlot(
                kind="vocative_alias",
                max_facts=config.context_vocative_alias_fact_limit,
                anchors=anchors,
            )
        )
    if quantity:
        slots.append(
            ContextEvidenceSlot(
                kind="quantity_transition",
                max_facts=config.context_quantity_transition_fact_limit,
                anchors=anchors,
                topic_terms=_query_topic_terms(query, anchors=anchors),
            )
        )
    if prior_state and anchors:
        slots.append(
            ContextEvidenceSlot(
                kind="prior_state",
                max_facts=config.context_prior_state_fact_limit,
                anchors=anchors[:1],
            )
        )
    if (
        quantity
        or alias
        or inference
        or prior_state
        or temporal_op == "order"
        or set_op != "none"
        or wants_procedure
    ):
        slots.append(
            ContextEvidenceSlot(
                kind="semantic_child_support",
                max_facts=config.context_semantic_support_fact_limit,
            )
        )
    return tuple(slots)


def _query_topic_terms(
    query: str,
    *,
    anchors: tuple[str, ...],
) -> tuple[str, ...]:
    anchor_terms = set(anchors)
    terms = (match.group(0).casefold() for match in _TOPIC_TERM.finditer(query))
    return tuple(
        dict.fromkeys(
            _normalize_topic_term(term)
            for term in terms
            if term not in _TOPIC_STOPWORDS and term not in anchor_terms
        )
    )


def _normalize_topic_term(value: str) -> str:
    term = value.casefold()
    if term in {"news", "series", "species"}:
        return term
    if len(term) > 4 and term.endswith("ies"):
        return f"{term[:-3]}y"
    if len(term) > 4 and term.endswith(("sses", "shes", "ches", "xes", "zes")):
        return term[:-2]
    if len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "us", "is")):
        return term[:-1]
    return term


def _coverage_requirements(
    query: str,
    *,
    anchors: tuple[str, ...],
    temporal_op: TemporalOperation,
    temporal_prefixes: tuple[str, ...],
    set_op: SetOperation,
    wants_procedure: bool,
) -> tuple[CoverageRequirement, ...]:
    requirements: list[CoverageRequirement] = [
        EntityCoverageRequirement(entity_key=anchor) for anchor in anchors
    ]
    if temporal_op != "none":
        requirements.append(
            TemporalCoverageRequirement(
                operation=temporal_op,
                prefixes=temporal_prefixes,
            )
        )
    if set_op != "none" and len(anchors) >= 2:
        requirements.append(SetCoverageRequirement(operation=set_op, members=anchors))
    if temporal_op == "order":
        requirements.append(RelationCoverageRequirement(relation="temporal_order"))
    if wants_procedure:
        requirements.append(RelationCoverageRequirement(relation="procedure_order"))
    stages: list[ProvenanceStage] = []
    provenance_cues: tuple[tuple[ProvenanceStage, re.Pattern[str]], ...] = (
        ("failure", _FAILURE_CUES),
        ("change", _CHANGE_CUES),
        ("verification", _VERIFICATION_CUES),
    )
    for stage, cue in provenance_cues:
        if cue.search(query) is not None:
            stages.append(stage)
    if stages:
        requirements.append(ProvenanceCoverageRequirement(stages=tuple(stages)))
    return tuple(requirements)


def _query_variants(
    query: str,
    *,
    anchors: tuple[str, ...],
    temporal_prefixes: tuple[str, ...],
) -> tuple[QueryVariant, ...]:
    variants = [QueryVariant(kind="original", text=" ".join(query.split()))]
    if anchors:
        variants.append(QueryVariant(kind="entity", text=" ".join(anchors)))
    if temporal_prefixes:
        variants.append(
            QueryVariant(
                kind="temporal",
                text=" ".join((*temporal_prefixes, *anchors)),
            )
        )
    return tuple(variants)


def _route(query: str) -> RecallRoute:
    fact_match = _FACT_CUES.search(query)
    episode_match = _EPISODE_CUES.search(query)
    if episode_match is not None and (
        fact_match is None or episode_match.start() <= fact_match.start()
    ):
        return "episode_first"
    if fact_match is not None:
        return "fact_first"
    return "episode_first"
