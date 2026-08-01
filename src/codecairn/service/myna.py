"""Person-first memory library orchestration over repository runtimes."""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal, Protocol

from codecairn.memory.evolution import MemoryHistory
from codecairn.memory.library import (
    GlobalPreferencePromotion,
    MemoryScope,
    Person,
    RequestingClient,
    SourceContext,
    memory_revision_sha256,
)
from codecairn.memory.models import (
    CandidateSource,
    RankedRecall,
    RecallAdmissionTrace,
    RecallContextTrace,
    RecallOmission,
    RecallResult,
    RecallSidecar,
    RecallSnippet,
    RecallSource,
)
from codecairn.memory.schema import CodingMemory, EvidenceReference, MemoryType, UserPreferencePayload
from codecairn.service.application import MemoryDetail


class MynaError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RecallForRequest:
    query: str
    requesting_client: RequestingClient
    limit: int = 20
    workstream_key: str | None = None
    token_budget: int = 8_192

    def __post_init__(self) -> None:
        if self.requesting_client not in {"cli", "hub", "mcp", "pico"}:
            raise ValueError("requesting_client is invalid")


@dataclass(frozen=True, slots=True)
class PromotionReceipt:
    outcome: str
    promotion: GlobalPreferencePromotion


@dataclass(frozen=True, slots=True)
class LibrarySnapshot:
    person: Person
    repository_key: str
    active_scopes: tuple[MemoryScope, ...]
    promotions: tuple[GlobalPreferencePromotion, ...]


@dataclass(frozen=True, slots=True)
class PreferenceGovernance:
    state: str
    eligible: bool
    promotion_id: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ScopedMemorySummary:
    memory_id: str
    memory_type: MemoryType
    title: str
    status: str
    created_at_ms: int
    effective_scope: MemoryScope
    source: SourceContext
    shadowed_by_memory_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScopedMemoryPage:
    schema_version: int
    repository_key: str
    items: tuple[ScopedMemorySummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class LibraryMemorySelection:
    detail: MemoryDetail
    history: MemoryHistory
    effective_scope: MemoryScope
    source: SourceContext
    governance: PreferenceGovernance | None


@dataclass(frozen=True, slots=True)
class ShadowedPreference:
    promotion_id: str
    subject_key: str
    shadowed_by_memory_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScopedRankedRecall:
    rank: int
    memory_id: str
    memory_type: MemoryType
    title: str
    summary: str
    source_uri: str
    content_sha256: str
    candidate_sources: tuple[CandidateSource, ...]
    final_score: float
    evidence: tuple[EvidenceReference, ...]
    status: str
    pinned: bool
    snippets: tuple[RecallSnippet, ...]
    effective_scope: MemoryScope
    source: SourceContext


@dataclass(frozen=True, slots=True)
class MynaRecallSidecar:
    query: str
    person_id: str
    repository_key: str
    requesting_client: RequestingClient
    active_scopes: tuple[MemoryScope, ...]
    ranked: tuple[ScopedRankedRecall, ...]
    shadowed: tuple[ShadowedPreference, ...]
    context_trace: RecallContextTrace | None
    admission_trace: RecallAdmissionTrace | None
    omissions: tuple[RecallOmission, ...]
    repository_trace: RecallSidecar


@dataclass(frozen=True, slots=True)
class MynaRecallResult:
    markdown: str
    sidecar: MynaRecallSidecar


class LibraryMemory(Protocol):
    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]: ...

    def get_memory(self, *, repo_key: str, memory_id: str) -> MemoryDetail: ...

    def memory_history(self, *, repo_key: str, memory_id: str) -> MemoryHistory: ...

    def memory_truth(self, *, repo_key: str, memory_id: str) -> CodingMemory: ...

    def has_supersession(self, *, repo_key: str, predecessor_id: str, successor_id: str) -> bool: ...

    def has_durable_successor(self, *, repo_key: str, memory_id: str) -> bool: ...

    def recall_across(
        self,
        query: str,
        *,
        current_repo_key: str,
        sources: tuple[RecallSource, ...],
        limit: int = 20,
        workstream_key: str | None = None,
        token_budget: int = 8_192,
    ) -> RecallResult: ...


class LibraryTruth(Protocol):
    def person(self) -> Person: ...

    def lock(self) -> AbstractContextManager[object]: ...

    def promotions(self) -> tuple[GlobalPreferencePromotion, ...]: ...

    def write_promotion(self, promotion: GlobalPreferencePromotion) -> object: ...


class MemoryLibraryApplication:
    def __init__(self, *, memory: LibraryMemory, truth: LibraryTruth, repository_key: str) -> None:
        if not repository_key:
            raise ValueError("repository_key must not be empty")
        self._memory = memory
        self._truth = truth
        self._repository_key = repository_key

    def person(self) -> Person:
        return self._truth.person()

    def library(self) -> LibrarySnapshot:
        person = self.person()
        promotions = self._effective_promotions(person)
        for promotion in promotions:
            self._validated_source(promotion)
        return LibrarySnapshot(person, self._repository_key, ("global", "repository"), promotions)

    def browse_library(
        self,
        *,
        memory_type: MemoryType | None = None,
        status: Literal["active", "superseded"] | None = None,
        scope: Literal["all", "global", "repository"] = "all",
        limit: int = 20,
        cursor: str | None = None,
    ) -> ScopedMemoryPage:
        if not 1 <= limit <= 100 or scope not in {"all", "global", "repository"}:
            raise ValueError("Library page request is invalid")
        person = self.person()
        promotions = self._effective_promotions(person)
        validated = {item.promotion_id: self._validated_source(item) for item in promotions}
        local_memories = self._memory.list_memories(repo_key=self._repository_key)
        local_subjects = self._local_preferences()
        items: dict[str, ScopedMemorySummary] = {}
        for memory in local_memories:
            detail = self._memory.get_memory(repo_key=self._repository_key, memory_id=memory.memory_id)
            items[memory.memory_id] = self._summary(detail, effective_scope="repository", shadowed_by_memory_ids=())
        for promotion in promotions:
            memory = validated[promotion.promotion_id]
            detail = self._memory.get_memory(repo_key=memory.repo_key, memory_id=memory.memory_id)
            shadowers = tuple(
                sorted(item for item in local_subjects.get(promotion.subject_key, ()) if item != promotion.source.memory_id)
            )
            items[memory.memory_id] = self._summary(detail, effective_scope="global", shadowed_by_memory_ids=shadowers)
        selected = sorted(
            (
                item
                for item in items.values()
                if (memory_type is None or item.memory_type == memory_type)
                and (status is None or item.status == status)
                and (scope == "all" or item.effective_scope == scope)
            ),
            key=lambda item: item.memory_id,
        )
        if cursor is not None:
            try:
                offset = next(index for index, item in enumerate(selected) if item.memory_id == cursor) + 1
            except StopIteration as error:
                raise ValueError("cursor_invalid") from error
            selected = selected[offset:]
        page = tuple(selected[:limit])
        next_cursor = page[-1].memory_id if len(selected) > limit else None
        self._validate_snapshot(person, promotions, validated, local_subjects)
        return ScopedMemoryPage(1, self._repository_key, page, next_cursor)

    def library_memory(self, memory_id: str) -> LibraryMemorySelection:
        person = self.person()
        promotions = self._effective_promotions(person)
        validated = {item.promotion_id: self._validated_source(item) for item in promotions}
        promotion = next((item for item in promotions if item.source.memory_id == memory_id), None)
        if promotion is not None:
            memory = validated[promotion.promotion_id]
            selection = LibraryMemorySelection(
                detail=self._memory.get_memory(repo_key=memory.repo_key, memory_id=memory_id),
                history=self._memory.memory_history(repo_key=memory.repo_key, memory_id=memory_id),
                effective_scope="global",
                source=promotion.source,
                governance=None,
            )
            self._validate_snapshot(person, promotions, validated)
            return selection
        local = next((item for item in self._memory.list_memories(repo_key=self._repository_key) if item.memory_id == memory_id), None)
        if local is not None:
            detail = self._memory.get_memory(repo_key=self._repository_key, memory_id=memory_id)
            return LibraryMemorySelection(
                detail=detail,
                history=self._memory.memory_history(repo_key=self._repository_key, memory_id=memory_id),
                effective_scope="repository",
                source=self._source_context(detail.memory),
                governance=self.preference_governance(memory_id),
            )
        raise MynaError("memory_not_found")

    def promote_preference(self, memory_id: str) -> PromotionReceipt:
        person = self.person()
        with self._truth.lock():
            source = self._eligible_preference(self._repository_key, memory_id, error_code="preference_not_eligible")
            assert isinstance(source.memory.payload, UserPreferencePayload)
            subject = source.memory.payload.subject_key
            effective = {item.subject_key: item for item in self._effective_promotions(person)}
            predecessor = effective.get(subject)
            if predecessor is not None and predecessor.source == self._source_context(source.memory):
                return PromotionReceipt("already_promoted", predecessor)
            if predecessor is not None and not self._may_replace(predecessor, source.memory):
                self._validated_source(predecessor)
                raise MynaError("global_preference_conflict")
            promotion = GlobalPreferencePromotion.create(
                person_id=person.person_id,
                subject_key=subject,
                source=self._source_context(source.memory),
                replaces_promotion_id=None if predecessor is None else predecessor.promotion_id,
                created_at_ms=time.time_ns() // 1_000_000,
            )
            self._truth.write_promotion(promotion)
            self._eligible_preference(self._repository_key, memory_id, error_code="global_preference_invalid")
            return PromotionReceipt("created", promotion)

    def preference_governance(self, memory_id: str) -> PreferenceGovernance:
        try:
            detail = self._memory.get_memory(repo_key=self._repository_key, memory_id=memory_id)
        except KeyError as error:
            raise MynaError("memory_not_found") from error
        if detail.memory.memory_type != "user_preference" or detail.status != "active":
            return PreferenceGovernance("ineligible", False, None, "preference_not_eligible")
        try:
            detail = self._eligible_preference(self._repository_key, memory_id, error_code="preference_not_eligible")
        except MynaError:
            return PreferenceGovernance("ineligible", False, None, "preference_not_eligible")
        assert isinstance(detail.memory.payload, UserPreferencePayload)
        promoted = next(
            (item for item in self._effective_promotions(self.person()) if item.subject_key == detail.memory.payload.subject_key), None
        )
        if promoted is None:
            return PreferenceGovernance("eligible", True, None, None)
        if promoted.source == self._source_context(detail.memory):
            return PreferenceGovernance("promoted", True, promoted.promotion_id, None)
        if self._may_replace(promoted, detail.memory):
            return PreferenceGovernance("eligible", True, None, None)
        self._validated_source(promoted)
        return PreferenceGovernance("conflict", False, promoted.promotion_id, "global_preference_conflict")

    def recall_for(self, request: RecallForRequest) -> MynaRecallResult:
        person = self.person()
        promotions = self._effective_promotions(person)
        validated = {item.promotion_id: self._validated_source(item) for item in promotions}
        local_by_subject = self._local_preferences()
        shadowers = {
            item.promotion_id: tuple(
                sorted(
                    memory_id
                    for memory_id in local_by_subject.get(item.subject_key, ())
                    if memory_id != item.source.memory_id or item.source.repository_key != self._repository_key
                )
            )
            for item in promotions
        }
        shadowed = tuple(
            ShadowedPreference(item.promotion_id, item.subject_key, shadowers[item.promotion_id])
            for item in promotions
            if shadowers[item.promotion_id]
        )
        visible = tuple(item for item in promotions if not shadowers[item.promotion_id])
        selected_by_repo: dict[str, list[str]] = defaultdict(list)
        for item in visible:
            selected_by_repo[item.source.repository_key].append(item.source.memory_id)
        sources = [RecallSource(self._repository_key)]
        for repository_key, memory_ids in sorted(selected_by_repo.items()):
            if repository_key != self._repository_key:
                sources.append(RecallSource(repository_key, tuple(sorted(memory_ids))))
        recalled = self._memory.recall_across(
            request.query,
            current_repo_key=self._repository_key,
            sources=tuple(sources),
            limit=request.limit,
            workstream_key=request.workstream_key,
            token_budget=request.token_budget,
        )
        self._validate_snapshot(person, promotions, validated, local_by_subject)
        global_by_memory_id = {item.source.memory_id: item for item in visible}
        ranked = tuple(self._scoped(item, global_by_memory_id.get(item.memory_id), validated) for item in recalled.sidecar.ranked)
        return MynaRecallResult(
            markdown=recalled.markdown,
            sidecar=MynaRecallSidecar(
                query=recalled.sidecar.query,
                person_id=person.person_id,
                repository_key=self._repository_key,
                requesting_client=request.requesting_client,
                active_scopes=("global", "repository"),
                ranked=ranked,
                shadowed=shadowed,
                context_trace=recalled.sidecar.context_trace,
                admission_trace=recalled.sidecar.admission_trace,
                omissions=recalled.sidecar.omissions,
                repository_trace=recalled.sidecar,
            ),
        )

    def _effective_promotions(self, person: Person) -> tuple[GlobalPreferencePromotion, ...]:
        try:
            promotions = self._truth.promotions()
        except (OSError, UnicodeError, ValueError) as error:
            raise MynaError("global_preference_invalid") from error
        by_id = {item.promotion_id: item for item in promotions}
        if len(by_id) != len(promotions) or any(item.person_id != person.person_id for item in promotions):
            raise MynaError("global_preference_invalid")
        replaced: set[str] = set()
        for item in promotions:
            if item.replaces_promotion_id is None:
                continue
            predecessor = by_id.get(item.replaces_promotion_id)
            if (
                predecessor is None
                or predecessor.subject_key != item.subject_key
                or item.replaces_promotion_id in replaced
                or not self._may_replace(predecessor, self._promotion_source(item).memory)
            ):
                raise MynaError("global_preference_invalid")
            replaced.add(item.replaces_promotion_id)
        effective = tuple(sorted((item for item in promotions if item.promotion_id not in replaced), key=lambda item: item.subject_key))
        if len({item.subject_key for item in effective}) != len(effective):
            raise MynaError("global_preference_invalid")
        return effective

    def _eligible_preference(self, repository_key: str, memory_id: str, *, error_code: str) -> MemoryDetail:
        try:
            truth = self._memory.memory_truth(repo_key=repository_key, memory_id=memory_id)
            detail = self._memory.get_memory(repo_key=repository_key, memory_id=memory_id)
            has_successor = self._memory.has_durable_successor(repo_key=repository_key, memory_id=memory_id)
        except (KeyError, OSError, UnicodeError, ValueError) as error:
            raise MynaError("memory_not_found" if error_code == "preference_not_eligible" else error_code) from error
        if (
            truth != detail.memory
            or detail.memory.memory_type != "user_preference"
            or not isinstance(detail.memory.payload, UserPreferencePayload)
        ):
            raise MynaError(error_code)
        if detail.status != "active" or has_successor:
            raise MynaError(error_code)
        return detail

    def _local_preferences(self) -> dict[str, tuple[str, ...]]:
        selected: dict[str, list[str]] = defaultdict(list)
        for memory in self._memory.list_memories(repo_key=self._repository_key):
            if memory.memory_type != "user_preference" or not isinstance(memory.payload, UserPreferencePayload):
                continue
            try:
                detail = self._memory.get_memory(repo_key=self._repository_key, memory_id=memory.memory_id)
            except KeyError as error:
                raise MynaError("global_preference_invalid") from error
            if detail.status != "active":
                continue
            try:
                detail = self._eligible_preference(self._repository_key, memory.memory_id, error_code="global_preference_invalid")
            except MynaError as error:
                raise MynaError("global_preference_invalid") from error
            assert isinstance(detail.memory.payload, UserPreferencePayload)
            selected[detail.memory.payload.subject_key].append(memory.memory_id)
        return {subject: tuple(sorted(memory_ids)) for subject, memory_ids in selected.items()}

    def _validate_snapshot(
        self,
        person: Person,
        promotions: tuple[GlobalPreferencePromotion, ...],
        validated: dict[str, CodingMemory],
        local_preferences: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        if self._effective_promotions(person) != promotions or any(
            self._validated_source(item) != validated[item.promotion_id] for item in promotions
        ):
            raise MynaError("global_preference_invalid")
        if local_preferences is not None and self._local_preferences() != local_preferences:
            raise MynaError("global_preference_invalid")

    def _validated_source(self, promotion: GlobalPreferencePromotion) -> CodingMemory:
        detail = self._promotion_source(promotion)
        try:
            has_successor = self._memory.has_durable_successor(
                repo_key=promotion.source.repository_key, memory_id=promotion.source.memory_id
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise MynaError("global_preference_invalid") from error
        if detail.status != "active" or has_successor:
            raise MynaError("global_preference_invalid")
        return detail.memory

    def _promotion_source(self, promotion: GlobalPreferencePromotion) -> MemoryDetail:
        try:
            truth = self._memory.memory_truth(repo_key=promotion.source.repository_key, memory_id=promotion.source.memory_id)
            detail = self._memory.get_memory(repo_key=promotion.source.repository_key, memory_id=promotion.source.memory_id)
        except (KeyError, OSError, UnicodeError, ValueError) as error:
            raise MynaError("global_preference_invalid") from error
        if (
            truth != detail.memory
            or not isinstance(detail.memory.payload, UserPreferencePayload)
            or detail.memory.payload.subject_key != promotion.subject_key
            or self._source_context(detail.memory) != promotion.source
        ):
            raise MynaError("global_preference_invalid")
        return detail

    def _may_replace(self, predecessor: GlobalPreferencePromotion, successor: CodingMemory) -> bool:
        try:
            previous = self._promotion_source(predecessor)
        except MynaError:
            return False
        if previous.status != "superseded" or predecessor.source.repository_key != successor.repo_key:
            return False
        try:
            return self._memory.has_supersession(
                repo_key=successor.repo_key, predecessor_id=predecessor.source.memory_id, successor_id=successor.memory_id
            )
        except (OSError, UnicodeError, ValueError):
            return False

    def _scoped(
        self, item: RankedRecall, promotion: GlobalPreferencePromotion | None, validated: dict[str, CodingMemory]
    ) -> ScopedRankedRecall:
        if promotion is None:
            source = self._source_context(self._memory.get_memory(repo_key=self._repository_key, memory_id=item.memory_id).memory)
            scope: MemoryScope = "repository"
        else:
            source = self._source_context(validated[promotion.promotion_id])
            scope = "global"
        return ScopedRankedRecall(
            rank=item.rank,
            memory_id=item.memory_id,
            memory_type=item.memory_type,
            title=item.title,
            summary=item.summary,
            source_uri=item.source_uri,
            content_sha256=item.content_sha256,
            candidate_sources=item.candidate_sources,
            final_score=item.final_score,
            evidence=item.evidence,
            status=item.status,
            pinned=item.pinned,
            snippets=item.snippets,
            effective_scope=scope,
            source=source,
        )

    def _summary(
        self, detail: MemoryDetail, *, effective_scope: MemoryScope, shadowed_by_memory_ids: tuple[str, ...]
    ) -> ScopedMemorySummary:
        return ScopedMemorySummary(
            memory_id=detail.memory.memory_id,
            memory_type=detail.memory.memory_type,
            title=detail.memory.title,
            status=detail.status,
            created_at_ms=detail.memory.created_at_ms,
            effective_scope=effective_scope,
            source=self._source_context(detail.memory),
            shadowed_by_memory_ids=shadowed_by_memory_ids,
        )

    @staticmethod
    def _source_context(memory: CodingMemory) -> SourceContext:
        return SourceContext(memory.repo_key, memory.memory_id, memory_revision_sha256(memory))
