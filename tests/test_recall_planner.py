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


def test_diagnostic_top_k_uses_bounded_route_aware_candidate_pools() -> None:
    planner = RecallPlanner()

    fact_first = planner.plan("When did it happen?", limit=20)
    episode_first = planner.plan("Summarize the debugging approach", limit=20)

    assert (
        fact_first.episode_candidate_limit,
        fact_first.atomic_fact_candidate_limit,
        fact_first.rerank_candidate_limit,
        fact_first.core_rerank_candidate_limit,
        fact_first.exploration_result_limit,
    ) == (128, 128, 96, 32, 4)
    assert (
        episode_first.episode_candidate_limit,
        episode_first.atomic_fact_candidate_limit,
        episode_first.rerank_candidate_limit,
        episode_first.core_rerank_candidate_limit,
        episode_first.exploration_result_limit,
    ) == (128, 128, 96, 32, 4)


def test_small_top_k_keeps_adaptive_candidate_budgets_bounded() -> None:
    planner = RecallPlanner()

    fact_first = planner.plan("When did it happen?", limit=5)
    episode_first = planner.plan("Summarize the debugging approach", limit=5)

    assert (
        fact_first.episode_candidate_limit,
        fact_first.atomic_fact_candidate_limit,
        fact_first.rerank_candidate_limit,
        fact_first.core_rerank_candidate_limit,
        fact_first.exploration_result_limit,
    ) == (35, 40, 32, 32, 0)
    assert (
        episode_first.episode_candidate_limit,
        episode_first.atomic_fact_candidate_limit,
        episode_first.rerank_candidate_limit,
        episode_first.core_rerank_candidate_limit,
        episode_first.exploration_result_limit,
    ) == (40, 35, 32, 32, 0)


def test_neighbor_expansion_requires_the_full_hierarchy_mode() -> None:
    with pytest.raises(ValueError, match="Only hierarchy mode"):
        RecallPlannerConfig(mode="hierarchy-no-neighbors", neighbor_window=1)
