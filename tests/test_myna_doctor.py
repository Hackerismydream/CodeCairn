from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest

from codecairn.bootstrap import create_application, create_myna_application, create_runtime
from codecairn.importers import SessionImporter
from codecairn.memory.library import GlobalPreferencePromotion, SourceContext, memory_revision_sha256
from codecairn.memory.schema import CodingMemory, SourceOrderKey, UserPreferencePayload
from codecairn.service.application import RememberRequest
from codecairn.service.myna import MynaError
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.library_markdown import MarkdownLibraryStore
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState
from tests.retrieval_fakes import TEST_RETRIEVAL

FIXTURE = Path(__file__).parent / "fixtures/codex/failed_command.jsonl"
REPO_A = "github.com/acme/alpha"
REPO_B = "github.com/acme/beta"


def _preference(
    root: Path, *, repo_key: str, subject: str = "response-language", content: str = "Reply in concise Chinese."
) -> CodingMemory:
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
    application.sync_index(worker_id=f"doctor-{repo_key}")
    return preference


def _task_experience(root: Path, *, repo_key: str) -> CodingMemory:
    _preference(root, repo_key=repo_key)
    return next(
        memory
        for memory in create_application(root, repo_key=repo_key).list_memories(repo_key=repo_key)
        if memory.memory_type == "task_experience"
    )


def _ordered_preference(root: Path, *, content: str, event_index: int) -> CodingMemory:
    application = create_application(root, repo_key=REPO_A)
    application.import_session(FIXTURE, repo_key=REPO_A, index=False, boundary_kind="manual_finalize")
    experience = next(memory for memory in application.list_memories(repo_key=REPO_A) if memory.memory_type == "task_experience")
    source_fact = next(fact for fact in experience.facts if fact.role == "user")
    reference = source_fact.reference
    return create_runtime(root).store_memory(
        CodingMemory.create(
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
            source_order_key=SourceOrderKey(None, reference.provider, reference.session_id, reference.source_generation, event_index),
            payload=UserPreferencePayload(subject_key="response-language", preference=content, source_fact_ids=(source_fact.fact_id,)),
        )
    )


def _promotion(
    root: Path, *, source: CodingMemory, subject: str, person_id: str | None = None, revision_sha256: str | None = None
) -> GlobalPreferencePromotion:
    person = create_myna_application(root, repository_key=source.repo_key).person()
    promotion = GlobalPreferencePromotion.create(
        person_id=person.person_id if person_id is None else person_id,
        subject_key=subject,
        source=SourceContext(
            source.repo_key, source.memory_id, memory_revision_sha256(source) if revision_sha256 is None else revision_sha256
        ),
        replaces_promotion_id=None,
        created_at_ms=1,
    )
    MarkdownLibraryStore(root).write_promotion(promotion)
    return promotion


def _doctor(root: Path) -> dict[str, object]:
    return create_application(root, repo_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).doctor()


def _issue_codes(doctor: dict[str, object]) -> set[str]:
    library = cast(dict[str, object], doctor["person_library"])
    issues = cast(tuple[dict[str, str], ...], library["issues"])
    return {issue["code"] for issue in issues}


def test_doctor_projects_unconfigured_and_healthy_person_library(tmp_path: Path) -> None:
    root = tmp_path / "runtime"

    unconfigured = _doctor(root)
    unconfigured_library = cast(dict[str, object], unconfigured["person_library"])
    unconfigured_subsystems = cast(dict[str, dict[str, str]], unconfigured["subsystems"])
    assert unconfigured["status"] == "ok"
    assert unconfigured_library == {
        "status": "not_configured",
        "person_id": None,
        "promotion_count": 0,
        "effective_promotion_count": 0,
        "issues": (),
    }
    assert unconfigured_subsystems["person_library"]["status"] == "not_configured"

    preference = _preference(root, repo_key=REPO_A)
    create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL).promote_preference(preference.memory_id)
    healthy = _doctor(root)
    healthy_library = cast(dict[str, object], healthy["person_library"])
    assert healthy["status"] == "ok"
    assert healthy_library["status"] == "ok"
    assert healthy_library["person_id"]
    assert healthy_library["promotion_count"] == 1
    assert healthy_library["effective_promotion_count"] == 1
    assert healthy_library["issues"] == ()


def test_doctor_degrades_for_missing_or_invalid_person_owner_and_duplicate_subject(tmp_path: Path) -> None:
    invalid_person_root = tmp_path / "invalid-person"
    create_myna_application(invalid_person_root, repository_key=REPO_A).person()
    (invalid_person_root / "library/person.md").write_text("not canonical\n")
    invalid_person = _doctor(invalid_person_root)
    assert invalid_person["status"] == "degraded"
    assert "person_truth_invalid" in _issue_codes(invalid_person)

    missing_person_root = tmp_path / "missing-person"
    foreign_person = create_myna_application(tmp_path / "foreign", repository_key=REPO_A).person()
    missing_source = SourceContext(REPO_A, f"mem_{'1' * 64}", "2" * 64)
    MarkdownLibraryStore(missing_person_root).write_promotion(
        GlobalPreferencePromotion.create(
            person_id=foreign_person.person_id,
            subject_key="response-language",
            source=missing_source,
            replaces_promotion_id=None,
            created_at_ms=1,
        )
    )
    missing_person = _doctor(missing_person_root)
    assert missing_person["status"] == "degraded"
    assert {"person_missing", "promotion_source_missing"} <= _issue_codes(missing_person)

    owner_root = tmp_path / "owner"
    preference = _preference(owner_root, repo_key=REPO_A)
    create_myna_application(owner_root, repository_key=REPO_A).person()
    _promotion(owner_root, source=preference, subject="response-language", person_id=foreign_person.person_id)
    owner = _doctor(owner_root)
    assert owner["status"] == "degraded"
    assert "promotion_owner_mismatch" in _issue_codes(owner)

    duplicate_root = tmp_path / "duplicate"
    first = _preference(duplicate_root, repo_key=REPO_A)
    second = _preference(duplicate_root, repo_key=REPO_B)
    _promotion(duplicate_root, source=first, subject="response-language")
    _promotion(duplicate_root, source=second, subject="response-language")
    duplicate = _doctor(duplicate_root)
    assert duplicate["status"] == "degraded"
    assert "duplicate_effective_subject" in _issue_codes(duplicate)


def test_doctor_accepts_one_valid_replacement_chain_per_subject(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    predecessor = _ordered_preference(root, content="Reply in concise Chinese.", event_index=1)
    myna = create_myna_application(root, repository_key=REPO_A, retrieval_adapters=TEST_RETRIEVAL)
    first = myna.promote_preference(predecessor.memory_id)
    successor = _ordered_preference(root, content="Reply in concise English.", event_index=2)
    create_application(root, repo_key=REPO_A).supersede(
        repo_key=REPO_A,
        predecessor_id=predecessor.memory_id,
        successor_id=successor.memory_id,
        reason="The user changed the response language.",
        proposer="user",
    )
    replacement = GlobalPreferencePromotion.create(
        person_id=myna.person().person_id,
        subject_key="response-language",
        source=SourceContext(REPO_A, successor.memory_id, memory_revision_sha256(successor)),
        replaces_promotion_id=first.promotion.promotion_id,
        created_at_ms=2,
    )
    MarkdownLibraryStore(root).write_promotion(replacement)

    doctor = _doctor(root)
    library = cast(dict[str, object], doctor["person_library"])
    assert first.outcome == "created"
    assert replacement.replaces_promotion_id == first.promotion.promotion_id
    assert doctor["status"] == "ok"
    assert library["promotion_count"] == 2
    assert library["effective_promotion_count"] == 1
    assert library["issues"] == ()


@pytest.mark.parametrize(
    ("corruption", "expected_code"),
    (
        ("missing", "promotion_source_missing"),
        ("type", "promotion_source_wrong_type"),
        ("status", "promotion_source_status_invalid"),
        ("subject", "promotion_source_subject_mismatch"),
        ("revision", "promotion_source_revision_mismatch"),
    ),
)
def test_doctor_fails_closed_for_invalid_promotion_source(tmp_path: Path, corruption: str, expected_code: str) -> None:
    root = tmp_path / corruption
    if corruption == "missing":
        person = create_myna_application(root, repository_key=REPO_A).person()
        MarkdownLibraryStore(root).write_promotion(
            GlobalPreferencePromotion.create(
                person_id=person.person_id,
                subject_key="response-language",
                source=SourceContext(REPO_A, f"mem_{'3' * 64}", "4" * 64),
                replaces_promotion_id=None,
                created_at_ms=1,
            )
        )
    elif corruption == "type":
        source = _task_experience(root, repo_key=REPO_A)
        _promotion(root, source=source, subject="response-language")
    else:
        source = _preference(root, repo_key=REPO_A)
        _promotion(
            root,
            source=source,
            subject="other-subject" if corruption == "subject" else "response-language",
            revision_sha256="5" * 64 if corruption == "revision" else None,
        )
        if corruption == "status":
            with sqlite3.connect(root / "state.sqlite3") as connection:
                connection.execute(
                    "UPDATE memory_status SET status = 'superseded' WHERE repo_key = ? AND memory_id = ?", (REPO_A, source.memory_id)
                )

    doctor = _doctor(root)
    subsystems = cast(dict[str, dict[str, str]], doctor["subsystems"])
    assert doctor["status"] == "degraded"
    assert subsystems["person_library"]["status"] == "degraded"
    assert expected_code in _issue_codes(doctor)


def test_doctor_requires_markdown_source_and_replacement_lineage(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing-truth"
    source = _preference(missing_root, repo_key=REPO_A)
    create_myna_application(missing_root, repository_key=REPO_A).promote_preference(source.memory_id)
    next((missing_root / "memory").glob(f"**/{source.memory_id}.md")).unlink()
    assert "promotion_source_truth_missing" in _issue_codes(_doctor(missing_root))

    forged_root = tmp_path / "forged-lineage"
    predecessor = _preference(forged_root, repo_key=REPO_A)
    successor = _preference(forged_root, repo_key=REPO_A, content="Reply in English.")
    myna = create_myna_application(forged_root, repository_key=REPO_A)
    original = myna.promote_preference(predecessor.memory_id)
    with sqlite3.connect(forged_root / "state.sqlite3") as connection:
        connection.execute(
            "UPDATE memory_status SET status = 'superseded' WHERE repo_key = ? AND memory_id = ?", (REPO_A, predecessor.memory_id)
        )
    MarkdownLibraryStore(forged_root).write_promotion(
        GlobalPreferencePromotion.create(
            person_id=myna.person().person_id,
            subject_key="response-language",
            source=SourceContext(REPO_A, successor.memory_id, memory_revision_sha256(successor)),
            replaces_promotion_id=original.promotion.promotion_id,
            created_at_ms=2,
        )
    )

    assert "promotion_replacement_invalid" in _issue_codes(_doctor(forged_root))


def test_doctor_and_library_fail_closed_during_pending_evolution_recovery(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    predecessor = _ordered_preference(root, content="Reply in Chinese.", event_index=1)
    successor = _ordered_preference(root, content="Reply in English.", event_index=2)
    myna = create_myna_application(root, repository_key=REPO_A)
    myna.promote_preference(predecessor.memory_id)

    def crash(stage: str) -> None:
        if stage == "evolution_before_commit":
            raise RuntimeError(stage)

    runtime = MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(root),
        state=SQLiteState(root / "state.sqlite3"),
        fault_injector=crash,
    )
    with pytest.raises(RuntimeError, match="evolution_before_commit"):
        runtime.supersede(
            repo_key=REPO_A,
            predecessor_id=predecessor.memory_id,
            successor_id=successor.memory_id,
            reason="The user changed the response language.",
            proposer="user",
        )

    doctor = _doctor(root)
    assert doctor["status"] == "degraded"
    assert doctor["pending_recovery"] == 1
    assert "promotion_source_status_invalid" in _issue_codes(doctor)
    with pytest.raises(MynaError, match="global_preference_invalid"):
        myna.library()

    unpromoted_root = tmp_path / "unpromoted-runtime"
    old = _ordered_preference(unpromoted_root, content="Reply in Chinese.", event_index=1)
    new = _ordered_preference(unpromoted_root, content="Reply in English.", event_index=2)
    unpromoted = create_myna_application(unpromoted_root, repository_key=REPO_A)
    crashing_runtime = MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(unpromoted_root),
        state=SQLiteState(unpromoted_root / "state.sqlite3"),
        fault_injector=crash,
    )
    with pytest.raises(RuntimeError, match="evolution_before_commit"):
        crashing_runtime.supersede(
            repo_key=REPO_A,
            predecessor_id=old.memory_id,
            successor_id=new.memory_id,
            reason="The user changed the response language.",
            proposer="user",
        )

    assert unpromoted.preference_governance(old.memory_id).state == "ineligible"
    with pytest.raises(MynaError, match="preference_not_eligible"):
        unpromoted.promote_preference(old.memory_id)
    assert tuple((unpromoted_root / "library/global-preferences").glob("*.md")) == ()
