from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, get_ident
from typing import cast

import pytest

from codecairn.bootstrap import create_application, create_myna_application, create_runtime
from codecairn.memory.library import GlobalPreferencePromotion, RequestingClient, SourceContext, memory_revision_sha256
from codecairn.memory.schema import CodingMemory, SchemaInvalid, SourceOrderKey, UserPreferencePayload
from codecairn.service.application import RememberRequest
from codecairn.service.myna import MemoryLibraryApplication, MynaError, RecallForRequest
from codecairn.storage.library_markdown import MarkdownLibraryStore
from codecairn.storage.markdown import MarkdownMemoryStore
from tests.retrieval_fakes import TEST_RETRIEVAL

FIXTURE = Path(__file__).parent / "fixtures/codex/failed_command.jsonl"
REPO_A = "github.com/acme/alpha"
REPO_B = "github.com/acme/beta"


def _preference(root: Path, *, repo_key: str, subject: str, content: str):
    application = create_application(root, repo_key=repo_key, retrieval_adapters=TEST_RETRIEVAL)
    application.import_session(FIXTURE, repo_key=repo_key, index=False, boundary_kind="manual_finalize")
    experience = next(memory for memory in application.list_memories(repo_key=repo_key) if memory.memory_type == "task_experience")
    source_fact = next(fact for fact in experience.facts if fact.role == "user")
    preference = application.remember_direct(
        RememberRequest(
            repo_key=repo_key,
            memory_type="user_preference",
            title=subject.replace("-", " ").title(),
            content=content,
            category="workflow",
            subject_key=subject,
            source_fact_ids=(source_fact.fact_id,),
        )
    )
    application.sync_index(worker_id=f"test-{repo_key}")
    return preference


def _mark_superseded(root: Path, *, repo_key: str, memory_id: str) -> None:
    with sqlite3.connect(root / "state.sqlite3") as connection:
        connection.execute("UPDATE memory_status SET status = 'superseded' WHERE repo_key = ? AND memory_id = ?", (repo_key, memory_id))


def _ordered_preference(root: Path, *, content: str, event_index: int) -> CodingMemory:
    application = create_application(root, repo_key=REPO_A)
    application.import_session(FIXTURE, repo_key=REPO_A, index=False, boundary_kind="manual_finalize")
    experience = next(memory for memory in application.list_memories(repo_key=REPO_A) if memory.memory_type == "task_experience")
    source_fact = next(fact for fact in experience.facts if fact.role == "user")
    reference = source_fact.reference
    memory = CodingMemory.create(
        repo_key=REPO_A,
        memory_type="user_preference",
        title="Response language",
        content=content,
        category="workflow",
        tags=(),
        created_at_ms=event_index,
        episode_id=experience.episode_id,
        evidence=(reference,),
        facts=(source_fact,),
        origin="capture",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=SourceOrderKey(
            trusted_timestamp_ms=None,
            provider=reference.provider,
            session_id=reference.session_id,
            source_generation=reference.source_generation,
            event_index=event_index,
        ),
        payload=UserPreferencePayload(subject_key="response-language", preference=content, source_fact_ids=(source_fact.fact_id,)),
    )
    return create_runtime(root).store_memory(memory)


def test_one_runtime_root_owns_one_stable_random_person(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    first = create_myna_application(first_root, repository_key=REPO_A).person()
    reopened = create_myna_application(first_root, repository_key=REPO_B).person()
    second = create_myna_application(tmp_path / "second", repository_key=REPO_A).person()

    assert first == reopened
    assert first.person_id.startswith("person_")
    assert len(first.person_id) == len("person_") + 64
    assert first.person_id != second.person_id
    assert first_root.as_posix() not in first.person_id
    assert REPO_A not in first.person_id


def test_global_preference_promotion_is_explicit_durable_and_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    preference = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    source_path = next((root / "memory").glob(f"**/{preference.memory_id}.md"))
    before = source_path.read_bytes()
    myna = create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL)

    created = myna.promote_preference(preference.memory_id)
    repeated = myna.promote_preference(preference.memory_id)
    reopened = create_myna_application(root, repository_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL).library()

    assert created.outcome == "created"
    assert repeated.outcome == "already_promoted"
    assert repeated.promotion == created.promotion
    assert reopened.promotions == (created.promotion,)
    assert created.promotion.source.repository_key == REPO_A
    assert created.promotion.source.memory_id == preference.memory_id
    assert source_path.read_bytes() == before
    assert len(tuple((root / "memory").glob("**/*.md"))) == 2


def test_promotion_rejects_non_preferences_superseded_sources_and_subject_conflicts(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    first = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    second = _preference(root, repo_key=REPO_B, subject="response-language", content="Reply in concise English.")
    application = create_application(root, repo_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL)
    non_preference = next(memory for memory in application.list_memories(repo_key=REPO_B) if memory.memory_type == "task_experience")
    myna_a = create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL)
    myna_b = create_myna_application(root, repository_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL)

    with pytest.raises(MynaError, match="preference_not_eligible"):
        myna_b.promote_preference(non_preference.memory_id)
    myna_a.promote_preference(first.memory_id)
    with pytest.raises(MynaError, match="global_preference_conflict"):
        myna_b.promote_preference(second.memory_id)

    replacement = _preference(root, repo_key=REPO_A, subject="other-language", content="Use English for release notes.")
    _mark_superseded(root, repo_key=REPO_A, memory_id=replacement.memory_id)
    with pytest.raises(MynaError, match="preference_not_eligible"):
        myna_a.promote_preference(replacement.memory_id)


def test_concurrent_promotions_leave_one_effective_preference_per_subject(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    first = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    second = _preference(root, repo_key=REPO_B, subject="response-language", content="Reply in concise English.")
    contenders = (
        (create_myna_application(root, repository_key=REPO_A), first.memory_id),
        (create_myna_application(root, repository_key=REPO_B), second.memory_id),
    )

    def promote(contender):
        application, memory_id = contender
        try:
            return application.promote_preference(memory_id).outcome
        except MynaError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(promote, contenders))

    assert sorted(outcomes) == ["created", "global_preference_conflict"]
    assert len(create_myna_application(root, repository_key=REPO_A).library().promotions) == 1


def test_explicit_successor_promotion_appends_an_auditable_replacement(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    predecessor = _ordered_preference(root, content="Reply in concise Chinese.", event_index=1)
    successor = _ordered_preference(root, content="Reply in concise English.", event_index=2)
    myna = create_myna_application(root, repository_key=REPO_A)
    first = myna.promote_preference(predecessor.memory_id)
    create_application(root, repo_key=REPO_A).supersede(
        repo_key=REPO_A,
        predecessor_id=predecessor.memory_id,
        successor_id=successor.memory_id,
        reason="The user changed the preferred response language.",
        proposer="user",
    )

    governance = myna.preference_governance(successor.memory_id)
    replacement = myna.promote_preference(successor.memory_id)
    snapshot = myna.library()

    assert governance.state == "eligible"
    assert governance.eligible is True
    assert replacement.outcome == "created"
    assert replacement.promotion.replaces_promotion_id == first.promotion.promotion_id
    assert snapshot.promotions == (replacement.promotion,)
    assert len(tuple((root / "library/global-preferences").glob("*.md"))) == 2


def test_replacement_must_follow_durable_memory_evolution(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    predecessor = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in Chinese.")
    unrelated = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in English.")
    myna = create_myna_application(root, repository_key=REPO_A)
    first = myna.promote_preference(predecessor.memory_id)
    _mark_superseded(root, repo_key=REPO_A, memory_id=predecessor.memory_id)
    forged = GlobalPreferencePromotion.create(
        person_id=myna.person().person_id,
        subject_key="response-language",
        source=SourceContext(REPO_A, unrelated.memory_id, memory_revision_sha256(unrelated)),
        replaces_promotion_id=first.promotion.promotion_id,
        created_at_ms=2,
    )
    MarkdownLibraryStore(root).write_promotion(forged)

    with pytest.raises(MynaError, match="global_preference_invalid"):
        myna.library()


def test_replacement_can_repair_to_an_active_descendant_and_continue(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    first = _ordered_preference(root, content="Reply in Chinese.", event_index=1)
    second = _ordered_preference(root, content="Reply in English.", event_index=2)
    third = _ordered_preference(root, content="Reply in concise English.", event_index=3)
    myna = create_myna_application(root, repository_key=REPO_A)
    original = myna.promote_preference(first.memory_id)
    application = create_application(root, repo_key=REPO_A)
    application.supersede(
        repo_key=REPO_A, predecessor_id=first.memory_id, successor_id=second.memory_id, reason="First change.", proposer="user"
    )
    application.supersede(
        repo_key=REPO_A, predecessor_id=second.memory_id, successor_id=third.memory_id, reason="Second change.", proposer="user"
    )

    repaired = myna.promote_preference(third.memory_id)

    assert repaired.promotion.replaces_promotion_id == original.promotion.promotion_id
    assert myna.library().promotions == (repaired.promotion,)


def test_three_promotion_chain_keeps_historical_sources_valid(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    first = _ordered_preference(root, content="Reply in Chinese.", event_index=1)
    second = _ordered_preference(root, content="Reply in English.", event_index=2)
    third = _ordered_preference(root, content="Reply in concise English.", event_index=3)
    myna = create_myna_application(root, repository_key=REPO_A)
    application = create_application(root, repo_key=REPO_A)
    myna.promote_preference(first.memory_id)
    application.supersede(
        repo_key=REPO_A, predecessor_id=first.memory_id, successor_id=second.memory_id, reason="First change.", proposer="user"
    )
    myna.promote_preference(second.memory_id)
    application.supersede(
        repo_key=REPO_A, predecessor_id=second.memory_id, successor_id=third.memory_id, reason="Second change.", proposer="user"
    )

    latest = myna.promote_preference(third.memory_id)

    assert myna.library().promotions == (latest.promotion,)


def test_evolution_and_initial_promotion_are_serialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runtime"
    predecessor = _ordered_preference(root, content="Reply in Chinese.", event_index=1)
    successor = _ordered_preference(root, content="Reply in English.", event_index=2)
    application = create_application(root, repo_key=REPO_A)
    myna = create_myna_application(root, repository_key=REPO_A)
    evolution_paused, promotion_attempted, release = Event(), Event(), Event()
    promotion_thread: dict[str, int] = {}
    original_write = MarkdownMemoryStore.write_evolution
    original_lock = MarkdownLibraryStore.lock

    def paused_write(store, record, **kwargs):
        evolution_paused.set()
        assert release.wait(5)
        return original_write(store, record, **kwargs)

    def observed_lock(store):
        if promotion_thread.get("id") == get_ident():
            promotion_attempted.set()
        return original_lock(store)

    monkeypatch.setattr(MarkdownMemoryStore, "write_evolution", paused_write)
    monkeypatch.setattr(MarkdownLibraryStore, "lock", observed_lock)

    def supersede():
        return application.supersede(
            repo_key=REPO_A,
            predecessor_id=predecessor.memory_id,
            successor_id=successor.memory_id,
            reason="The user changed the response language.",
            proposer="user",
        )

    def promote():
        promotion_thread["id"] = get_ident()
        return myna.promote_preference(predecessor.memory_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        evolution = executor.submit(supersede)
        assert evolution_paused.wait(5)
        promotion = executor.submit(promote)
        assert promotion_attempted.wait(5)
        assert not promotion.done()
        release.set()
        evolution.result()
        with pytest.raises(MynaError, match="preference_not_eligible"):
            promotion.result()

    assert tuple((root / "library/global-preferences").glob("*.md")) == ()


def test_recall_uses_global_and_repository_scopes_without_caller_selected_owner(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    global_preference = _preference(
        root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese for every repository."
    )
    create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).promote_preference(
        global_preference.memory_id
    )
    myna = create_myna_application(root, repository_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL)

    result = myna.recall_for(RecallForRequest(query="Which language should the response use?", requesting_client="pico"))

    selected = next(item for item in result.sidecar.ranked if item.memory_id == global_preference.memory_id)
    assert selected.effective_scope == "global"
    assert selected.source.repository_key == REPO_A
    assert result.sidecar.person_id == myna.person().person_id
    assert result.sidecar.repository_key == REPO_B
    assert result.sidecar.requesting_client == "pico"
    assert result.sidecar.active_scopes == ("global", "repository")
    assert global_preference.content in result.markdown
    assert not hasattr(RecallForRequest, "person_id")
    assert not hasattr(RecallForRequest, "scopes")
    assert not hasattr(RecallForRequest, "repository_key")


def test_recall_request_rejects_unknown_client_labels() -> None:
    with pytest.raises(ValueError, match="requesting_client is invalid"):
        RecallForRequest(query="response language", requesting_client=cast(RequestingClient, "evil"))


def test_global_recall_preflights_only_explicitly_promoted_foreign_memories(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    promoted = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese for every repository.")
    create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).promote_preference(promoted.memory_id)
    unrelated = create_application(root, repo_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).remember_direct(
        RememberRequest(
            repo_key=REPO_A,
            memory_type="repository_knowledge",
            title="Private deployment topology",
            content="This unrelated repository memory must not enter Myna global preflight.",
            subject_key="private-deployment-topology",
        )
    )
    with sqlite3.connect(root / "state.sqlite3") as connection:
        connection.execute(
            "UPDATE index_jobs SET status = 'failed', attempt_count = 3 WHERE repo_key = ? AND memory_id = ?",
            (REPO_A, unrelated.memory_id),
        )

    result = create_myna_application(root, repository_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL).recall_for(
        RecallForRequest(query="Which language should the response use?", requesting_client="pico")
    )

    assert promoted.memory_id in {item.memory_id for item in result.sidecar.ranked}
    assert unrelated.memory_id not in {item.memory_id for item in result.sidecar.ranked}


def test_repository_preference_shadows_same_subject_global_preference(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    global_preference = _preference(
        root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese for every repository."
    )
    local_preference = _preference(
        root, repo_key=REPO_B, subject="response-language", content="For this repository, write release notes in English."
    )
    create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).promote_preference(
        global_preference.memory_id
    )

    result = create_myna_application(root, repository_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL).recall_for(
        RecallForRequest(query="Which language should release notes use?", requesting_client="hub")
    )

    assert local_preference.memory_id in {item.memory_id for item in result.sidecar.ranked}
    assert global_preference.memory_id not in {item.memory_id for item in result.sidecar.ranked}
    assert result.sidecar.shadowed == (result.sidecar.shadowed[0],)
    assert result.sidecar.shadowed[0].promotion_id
    assert result.sidecar.shadowed[0].subject_key == "response-language"
    assert result.sidecar.shadowed[0].shadowed_by_memory_ids == (local_preference.memory_id,)


def test_local_shadow_policy_requires_markdown_truth(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    global_preference = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in Chinese.")
    local_preference = _preference(root, repo_key=REPO_B, subject="response-language", content="Reply in English.")
    create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).promote_preference(
        global_preference.memory_id
    )
    next((root / "memory").glob(f"**/{local_preference.memory_id}.md")).unlink()

    with pytest.raises(MynaError, match="global_preference_invalid"):
        create_myna_application(root, repository_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL).recall_for(
            RecallForRequest(query="response language", requesting_client="pico")
        )


def test_recall_rejects_promotion_or_shadow_changes_during_retrieval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evolution_root = tmp_path / "evolution"
    old = _ordered_preference(evolution_root, content="Reply in Chinese.", event_index=1)
    new = _ordered_preference(evolution_root, content="Reply in English.", event_index=2)
    source = create_application(evolution_root, repo_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL)
    source.sync_index(worker_id="source")
    create_myna_application(evolution_root, repository_key=REPO_A).promote_preference(old.memory_id)
    memory = create_application(evolution_root, repo_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL)
    myna = MemoryLibraryApplication(memory=memory, truth=MarkdownLibraryStore(evolution_root), repository_key=REPO_B)
    original_recall = memory.recall_across

    def evolve_during_recall(*args, **kwargs):
        source.supersede(
            repo_key=REPO_A,
            predecessor_id=old.memory_id,
            successor_id=new.memory_id,
            reason="The preference changed during recall.",
            proposer="user",
        )
        return original_recall(*args, **kwargs)

    monkeypatch.setattr(memory, "recall_across", evolve_during_recall)
    with pytest.raises(MynaError, match="global_preference_invalid"):
        myna.recall_for(RecallForRequest(query="response language", requesting_client="pico"))

    shadow_root = tmp_path / "shadow"
    promoted = _preference(shadow_root, repo_key=REPO_A, subject="response-language", content="Reply in Chinese.")
    create_myna_application(shadow_root, repository_key=REPO_A).promote_preference(promoted.memory_id)
    shadow_memory = create_application(shadow_root, repo_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL)
    shadow_myna = MemoryLibraryApplication(memory=shadow_memory, truth=MarkdownLibraryStore(shadow_root), repository_key=REPO_B)
    original_shadow_recall = shadow_memory.recall_across

    def add_shadow_during_recall(*args, **kwargs):
        _preference(shadow_root, repo_key=REPO_B, subject="response-language", content="Reply in English.")
        return original_shadow_recall(*args, **kwargs)

    monkeypatch.setattr(shadow_memory, "recall_across", add_shadow_during_recall)
    with pytest.raises(MynaError, match="global_preference_invalid"):
        shadow_myna.recall_for(RecallForRequest(query="response language", requesting_client="pico"))


@pytest.mark.parametrize(("operation", "trigger"), (("browse", 5), ("select", 2)))
def test_library_reads_reject_source_evolution_during_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, trigger: int
) -> None:
    root = tmp_path / operation
    old = _ordered_preference(root, content="Reply in Chinese.", event_index=1)
    new = _ordered_preference(root, content="Reply in English.", event_index=2)
    writer = create_application(root, repo_key=REPO_A)
    memory = create_application(root, repo_key=REPO_A)
    create_myna_application(root, repository_key=REPO_A).promote_preference(old.memory_id)
    myna = MemoryLibraryApplication(memory=memory, truth=MarkdownLibraryStore(root), repository_key=REPO_A)
    original_get = memory.get_memory
    calls = 0

    def evolve_during_get(*, repo_key: str, memory_id: str):
        nonlocal calls
        if memory_id == old.memory_id:
            calls += 1
            if calls == trigger:
                writer.supersede(
                    repo_key=REPO_A,
                    predecessor_id=old.memory_id,
                    successor_id=new.memory_id,
                    reason="The preference changed during projection.",
                    proposer="user",
                )
        return original_get(repo_key=repo_key, memory_id=memory_id)

    monkeypatch.setattr(memory, "get_memory", evolve_during_get)
    with pytest.raises(MynaError, match="global_preference_invalid"):
        myna.browse_library() if operation == "browse" else myna.library_memory(old.memory_id)


def test_library_browse_projects_global_references_without_copying_sources(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    promoted = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese for every repository.")
    local = _preference(root, repo_key=REPO_B, subject="test-command", content="Run pytest before committing in this repository.")
    create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).promote_preference(promoted.memory_id)
    myna = create_myna_application(root, repository_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL)

    all_memories = myna.browse_library(limit=100)
    global_memories = myna.browse_library(scope="global", limit=100)
    repository_memories = myna.browse_library(scope="repository", limit=100)
    selected = myna.library_memory(promoted.memory_id)

    by_id = {item.memory_id: item for item in all_memories.items}
    assert by_id[promoted.memory_id].effective_scope == "global"
    assert by_id[promoted.memory_id].source.repository_key == REPO_A
    assert by_id[local.memory_id].effective_scope == "repository"
    assert {item.memory_id for item in global_memories.items} == {promoted.memory_id}
    assert promoted.memory_id not in {item.memory_id for item in repository_memories.items}
    assert local.memory_id in {item.memory_id for item in repository_memories.items}
    assert selected.detail.memory == promoted
    assert selected.effective_scope == "global"
    assert selected.source.repository_key == REPO_A
    assert selected.governance is None


def test_recall_fails_closed_when_promoted_source_is_no_longer_active(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    promoted = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).promote_preference(promoted.memory_id)
    unrelated = _preference(root, repo_key=REPO_B, subject="response-language", content="Reply in concise English.")
    _mark_superseded(root, repo_key=REPO_A, memory_id=promoted.memory_id)
    myna = create_myna_application(root, repository_key=REPO_B, retrieval_adapters=TEST_RETRIEVAL)

    with pytest.raises(MynaError, match="global_preference_invalid"):
        myna.preference_governance(unrelated.memory_id)
    with pytest.raises(MynaError, match="global_preference_invalid"):
        myna.promote_preference(unrelated.memory_id)
    with pytest.raises(MynaError, match="global_preference_invalid"):
        myna.recall_for(RecallForRequest(query="Which language should the response use?", requesting_client="pico"))


def test_promoted_source_requires_matching_markdown_truth(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    promoted = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    myna = create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL)
    myna.promote_preference(promoted.memory_id)
    next((root / "memory").glob(f"**/{promoted.memory_id}.md")).unlink()

    governance = myna.preference_governance(promoted.memory_id)
    assert governance.state == "ineligible"
    assert governance.error_code == "preference_not_eligible"
    for read in (myna.library, lambda: myna.recall_for(RecallForRequest(query="response language", requesting_client="pico"))):
        with pytest.raises(MynaError):
            read()


def test_library_rejects_link_escapes_without_modifying_external_files(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink_root = tmp_path / "symlink-runtime"
    symlink_root.mkdir()
    (symlink_root / "library").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SchemaInvalid):
        create_myna_application(symlink_root, repository_key=REPO_A).person()
    assert not (outside / "person.md").exists()

    hardlink_root = tmp_path / "hardlink-runtime"
    hardlink_root.mkdir()
    external_lock = outside / "external-lock"
    external_lock.write_text("must remain intact")
    os.link(external_lock, hardlink_root / ".myna-library.lock")

    with pytest.raises(SchemaInvalid):
        create_myna_application(hardlink_root, repository_key=REPO_A).person()
    assert external_lock.read_text() == "must remain intact"


def test_crash_stage_files_are_ignored_but_unrelated_entries_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    preference = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in Chinese.")
    myna = create_myna_application(root, repository_key=REPO_A)
    receipt = myna.promote_preference(preference.memory_id)
    promotion_root = root / "library/global-preferences"
    canonical = promotion_root / f"{receipt.promotion.promotion_id}.md"
    os.link(canonical, promotion_root / f".{receipt.promotion.promotion_id}.md.crash-stage")
    os.link(root / "library/person.md", root / "library/.person.md.crash-stage")

    assert myna.library().promotions == (receipt.promotion,)
    assert myna.person().person_id == receipt.promotion.person_id
    assert canonical.stat().st_nlink == 1
    (promotion_root / "unrelated.txt").write_text("unexpected")
    with pytest.raises(MynaError, match="global_preference_invalid"):
        myna.library()


def test_namespace_reset_is_blocked_while_global_scope_references_a_memory(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    preference = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).promote_preference(preference.memory_id)
    application = create_application(root, repo_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL)

    with pytest.raises(ValueError, match="memory_referenced_by_global_scope"):
        application.reset_namespace(confirm=REPO_A, dry_run=False)

    assert application.get_memory(repo_key=REPO_A, memory_id=preference.memory_id).memory == preference
