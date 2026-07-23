from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderedEvidence:
    source_fact_id: str
    text: str
    source_uri: str


@dataclass(frozen=True, slots=True)
class GroundedContext:
    markdown: str
    evidence: tuple[RenderedEvidence, ...]
    token_count: int
    token_limit: int
    omitted_source_fact_ids: tuple[str, ...] = ()
    semantic_clause_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    answer: str
    supporting_evidence_ids: tuple[str, ...]
    insufficient: bool


def parse_grounded_answer(text: str, *, context: GroundedContext) -> GroundedAnswer:
    try:
        payload = json.loads(text, object_pairs_hook=_strict_json_object)
    except json.JSONDecodeError as error:
        raise ValueError("Grounded answer does not match the required JSON schema") from error
    expected_fields = {"answer", "supporting_evidence_ids", "insufficient"}
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("Grounded answer does not match the required JSON schema")
    answer = payload["answer"]
    supporting_ids = payload["supporting_evidence_ids"]
    insufficient = payload["insufficient"]
    if (
        not isinstance(answer, str)
        or not isinstance(supporting_ids, list)
        or any(not isinstance(item, str) for item in supporting_ids)
        or type(insufficient) is not bool
    ):
        raise ValueError("Grounded answer does not match the required JSON schema")
    if not answer.strip() or any(not item.strip() for item in supporting_ids):
        raise ValueError("Grounded answer does not match the required JSON schema")
    if len(supporting_ids) != len(set(supporting_ids)):
        raise ValueError("Grounded answer contains duplicate evidence citations")
    if insufficient and supporting_ids:
        raise ValueError("An insufficient answer must not cite supporting evidence")
    if not insufficient and not supporting_ids:
        raise ValueError("A grounded answer must cite evidence unless it is insufficient")
    omitted_ids = set(context.omitted_source_fact_ids)
    if any(item in omitted_ids for item in supporting_ids):
        raise ValueError("Grounded answer cites omitted source evidence")
    semantic_clause_ids = set(context.semantic_clause_ids)
    if any(item in semantic_clause_ids for item in supporting_ids):
        raise ValueError("Grounded answer cites a semantic clause instead of source evidence")
    rendered_ids = {item.source_fact_id for item in context.evidence}
    if any(item not in rendered_ids for item in supporting_ids):
        raise ValueError("Grounded answer cites unknown evidence")
    return GroundedAnswer(
        answer=answer,
        supporting_evidence_ids=tuple(supporting_ids),
        insufficient=insufficient,
    )


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Grounded answer does not match the required JSON schema")
        result[key] = value
    return result
