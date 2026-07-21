from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from codecairn.memory.models import RecallRoute

RecallPlannerMode = Literal["episode-only", "hierarchy-no-neighbors", "hierarchy"]
TemporalOperation = Literal["none", "point", "duration", "order", "latest"]
SetOperation = Literal["none", "union", "intersection"]

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
_NAMED_ANCHOR = re.compile(r"\b[A-Z][A-Za-z0-9_-]{1,63}\b")
_MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{4})\b",
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
}


@dataclass(frozen=True, slots=True)
class QuerySketch:
    """Small deterministic query contract used for coverage, never hard routing."""

    anchors: tuple[str, ...]
    temporal_op: TemporalOperation
    set_op: SetOperation
    wants_procedure: bool
    coverage_slots: tuple[str, ...]
    temporal_prefixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecallPlannerConfig:
    """Auditable knobs for hierarchical retrieval and its ablations."""

    mode: RecallPlannerMode = "hierarchy"
    primary_candidate_multiplier: int = 7
    secondary_candidate_multiplier: int = 7
    minimum_primary_candidates: int = 40
    minimum_secondary_candidates: int = 20
    maximum_channel_candidates: int = 128
    rerank_candidate_multiplier: int = 5
    minimum_rerank_candidates: int = 32
    maximum_rerank_candidates: int = 96
    maximum_exploration_results: int = 4
    neighbor_window: int = 1
    neighbor_snippet_budget: int = 20
    matched_facts_per_memory: int = 3
    sibling_facts_per_memory: int = 2

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
        if self.sibling_facts_per_memory < 0:
            raise ValueError("sibling_facts_per_memory must not be negative")
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
            "temporal_lane": "explicit-month-prefix-v1",
            "enrichment_order": "matched-adjacency-rerank-top-k-neighbors-v2",
            "matched_facts_per_memory": self.matched_facts_per_memory,
            "sibling_facts_per_memory": self.sibling_facts_per_memory,
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


class RecallPlanner:
    """Route a query without making either hierarchy level a single point of failure."""

    def __init__(self, config: RecallPlannerConfig | None = None) -> None:
        self.config = config or RecallPlannerConfig()

    def plan(self, query: str, *, limit: int) -> RecallPlan:
        query_sketch = _query_sketch(query)
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
        )


def _query_sketch(query: str) -> QuerySketch:
    anchors = tuple(
        dict.fromkeys(
            match.group(0).casefold()
            for match in _NAMED_ANCHOR.finditer(query)
            if match.group(0) not in _ANCHOR_STOPWORDS
            and match.group(0).casefold() not in _MONTH_NUMBERS
        )
    )
    lowered = query.casefold()
    temporal_prefixes = tuple(
        dict.fromkeys(
            f"{match.group(2)}-{_MONTH_NUMBERS[match.group(1).casefold()]:02d}"
            for match in _MONTH_YEAR.finditer(query)
        )
    )
    if any(cue in lowered for cue in ("before", "after", "first", "then", "order")):
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
    return QuerySketch(
        anchors=anchors,
        temporal_op=temporal_op,
        set_op=set_op,
        wants_procedure=any(
            cue in lowered for cue in ("how did", "steps", "procedure", "fix", "verify")
        ),
        coverage_slots=anchors,
        temporal_prefixes=temporal_prefixes,
    )


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
