from __future__ import annotations

import pytest

from codecairn.memory.recall_planner import (
    ContextEvidenceSlot,
    EntityCoverageRequirement,
    ExpansionPlan,
    ProvenanceCoverageRequirement,
    QueryVariant,
    RecallPlanner,
    RecallPlannerConfig,
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

    assert sketch.sketcher_id == "codecairn/deterministic-query-sketch-v4"
    assert sketch.query_time_llm_calls == 0
    assert sketch.evidence_slots == ()


def test_query_sketch_exposes_bounded_context_evidence_slots() -> None:
    quantity = (
        RecallPlanner().plan("How many screenplays has Joanna written?", limit=20).query_sketch
    )
    alias = RecallPlanner().plan("What nickname does Nate use for Joanna?", limit=20).query_sketch
    prior_state = (
        RecallPlanner().plan("Was James lonely before meeting Samantha?", limit=20).query_sketch
    )

    assert quantity.evidence_slots == (
        ContextEvidenceSlot(
            kind="quantity_transition",
            max_facts=12,
            anchors=("joanna",),
            topic_terms=("screenplay", "written"),
        ),
        ContextEvidenceSlot(kind="semantic_child_support", max_facts=16),
    )
    assert alias.evidence_slots == (
        ContextEvidenceSlot(
            kind="vocative_alias",
            max_facts=2,
            anchors=("nate", "joanna"),
        ),
        ContextEvidenceSlot(kind="semantic_child_support", max_facts=16),
    )
    assert prior_state.evidence_slots == (
        ContextEvidenceSlot(
            kind="prior_state",
            max_facts=4,
            anchors=("james",),
        ),
        ContextEvidenceSlot(kind="semantic_child_support", max_facts=16),
    )


def test_query_sketch_does_not_route_substrings_or_unrelated_prior_states() -> None:
    athena = RecallPlanner().plan("What did Athena study?", limit=20).query_sketch
    strengthened = RecallPlanner().plan("What strengthened Alice?", limit=20).query_sketch
    employment = (
        RecallPlanner().plan("Was Alice employed before joining Acme?", limit=20).query_sketch
    )
    friendship_after_boundary = (
        RecallPlanner()
        .plan("Was Alice employed before the friendship started?", limit=20)
        .query_sketch
    )

    assert athena.temporal_op == "none"
    assert athena.evidence_slots == ()
    assert strengthened.temporal_op == "none"
    assert strengthened.evidence_slots == ()
    assert employment.temporal_op == "order"
    assert all(slot.kind != "prior_state" for slot in employment.evidence_slots)
    assert all(slot.kind != "prior_state" for slot in friendship_after_boundary.evidence_slots)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("context_semantic_support_fact_limit", 21),
        ("context_quantity_transition_fact_limit", 13),
        ("context_vocative_alias_fact_limit", 3),
        ("context_prior_state_fact_limit", 5),
    ),
)
def test_context_evidence_slot_limits_are_hard_bounded(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValueError, match="context-slot hard ceiling"):
        RecallPlannerConfig(**{field: value})
