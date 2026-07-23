from __future__ import annotations

import pytest

from codecairn.memory.recall_planner import (
    EntityCoverageRequirement,
    ExpansionPlan,
    ProvenanceCoverageRequirement,
    QueryVariant,
    RecallPlanner,
    RelationCoverageRequirement,
    SetCoverageRequirement,
    TemporalCoverageRequirement,
)


def test_query_sketch_exposes_typed_grounded_coverage_requirements() -> None:
    plan = RecallPlanner().plan(
        (
            "Which file fixed the pytest timeout for Alice before October 2023, "
            "and which command verified it?"
        ),
        limit=5,
    )

    assert plan.query_sketch.coverage_requirements == (
        EntityCoverageRequirement(entity_key="alice"),
        TemporalCoverageRequirement(operation="order", prefixes=("2023-10",)),
        RelationCoverageRequirement(relation="temporal_order"),
        RelationCoverageRequirement(relation="procedure_order"),
        ProvenanceCoverageRequirement(stages=("failure", "change", "verification")),
    )


def test_set_requirement_keeps_legacy_entity_coverage_slots() -> None:
    sketch = (
        RecallPlanner()
        .plan(
            "Which city have both Jean and John visited?",
            limit=5,
        )
        .query_sketch
    )

    assert sketch.coverage_slots == ("jean", "john")
    assert sketch.coverage_requirements == (
        EntityCoverageRequirement(entity_key="jean"),
        EntityCoverageRequirement(entity_key="john"),
        SetCoverageRequirement(operation="intersection", members=("jean", "john")),
    )


def test_query_variants_are_deterministic_and_semantically_bounded() -> None:
    sketch = (
        RecallPlanner()
        .plan(
            "  Which city have both Jean and John visited in October 2023?  ",
            limit=5,
        )
        .query_sketch
    )

    assert sketch.query_variants == (
        QueryVariant(
            kind="original",
            text="Which city have both Jean and John visited in October 2023?",
        ),
        QueryVariant(kind="entity", text="jean john"),
        QueryVariant(kind="temporal", text="2023-10 jean john"),
    )


def test_recall_plan_carries_one_hop_expansion_hard_ceiling() -> None:
    plan = RecallPlanner().plan(
        "Which command verified Alice's timeout fix before October 2023?",
        limit=5,
    )

    assert plan.expansion_plan == ExpansionPlan(
        max_hops=1,
        max_total_facts=24,
        max_entity_facts=12,
        max_time_facts=8,
        max_provenance_facts=8,
    )


@pytest.mark.parametrize(
    "overrides",
    (
        {"max_hops": 2},
        {"max_total_facts": 25},
        {"max_entity_facts": 13},
        {"max_time_facts": 9},
        {"max_provenance_facts": 9},
    ),
)
def test_expansion_plan_rejects_values_above_hard_ceilings(
    overrides: dict[str, int],
) -> None:
    with pytest.raises(ValueError, match="hard ceiling"):
        ExpansionPlan(**overrides)


def test_expansion_lane_cannot_exceed_the_global_plan_limit() -> None:
    with pytest.raises(ValueError, match="global expansion limit"):
        ExpansionPlan(
            max_total_facts=4,
            max_entity_facts=5,
            max_time_facts=4,
            max_provenance_facts=4,
        )


def test_default_query_sketch_is_provider_free() -> None:
    sketch = RecallPlanner().plan("What hobby does Alice enjoy?", limit=5).query_sketch

    assert sketch.sketcher_id == "codecairn/deterministic-query-sketch-v2"
    assert sketch.query_time_llm_calls == 0
