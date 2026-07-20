from __future__ import annotations

import pytest

from codecairn.memory.recall_planner import RecallPlanner, RecallPlannerConfig


@pytest.mark.parametrize(
    ("query", "route"),
    (
        ("When did Caroline start the new job?", "fact_first"),
        ("Who bought the blue bicycle?", "fact_first"),
        ("How did the agent fix the flaky test?", "episode_first"),
        ("Summarize the debugging approach", "episode_first"),
        ("repository convention", "episode_first"),
    ),
)
def test_recall_planner_routes_queries_deterministically(query: str, route: str) -> None:
    plan = RecallPlanner().plan(query, limit=5)

    assert plan.route == route
    assert plan.episode_candidate_limit > 0
    assert plan.atomic_fact_candidate_limit > 0


def test_episode_only_ablation_disables_children_without_a_hard_query_route() -> None:
    planner = RecallPlanner(RecallPlannerConfig.for_mode("episode-only"))

    plan = planner.plan("When did it happen?", limit=5)

    assert plan.route == "fact_first"
    assert plan.episode_candidate_limit > 0
    assert plan.atomic_fact_candidate_limit == 0
    assert plan.expand_neighbors is False


def test_neighbor_expansion_requires_the_full_hierarchy_mode() -> None:
    with pytest.raises(ValueError, match="Only hierarchy mode"):
        RecallPlannerConfig(mode="hierarchy-no-neighbors", neighbor_window=1)
