from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from codecairn.memory.models import RecallRoute

RecallPlannerMode = Literal["episode-only", "hierarchy-no-neighbors", "hierarchy"]

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


@dataclass(frozen=True, slots=True)
class RecallPlannerConfig:
    """Auditable knobs for hierarchical retrieval and its ablations."""

    mode: RecallPlannerMode = "hierarchy"
    neighbor_window: int = 1
    matched_facts_per_memory: int = 3
    sibling_facts_per_memory: int = 2

    def __post_init__(self) -> None:
        if self.neighbor_window < 0:
            raise ValueError("neighbor_window must not be negative")
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
            "neighbor_window": self.neighbor_window,
            "matched_facts_per_memory": self.matched_facts_per_memory,
            "sibling_facts_per_memory": self.sibling_facts_per_memory,
        }


@dataclass(frozen=True, slots=True)
class RecallPlan:
    route: RecallRoute
    episode_candidate_limit: int
    atomic_fact_candidate_limit: int
    expand_neighbors: bool


class RecallPlanner:
    """Route a query without making either hierarchy level a single point of failure."""

    def __init__(self, config: RecallPlannerConfig | None = None) -> None:
        self.config = config or RecallPlannerConfig()

    def plan(self, query: str, *, limit: int) -> RecallPlan:
        route = _route(query)
        primary_limit = max(40, limit * 8)
        secondary_limit = max(20, limit * 4)
        episode_limit = primary_limit if route == "episode_first" else secondary_limit
        fact_limit = primary_limit if route == "fact_first" else secondary_limit
        if not self.config.atomic_fact_enabled:
            fact_limit = 0
        return RecallPlan(
            route=route,
            episode_candidate_limit=episode_limit,
            atomic_fact_candidate_limit=fact_limit,
            expand_neighbors=self.config.neighbor_window > 0,
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
