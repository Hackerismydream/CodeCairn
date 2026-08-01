"""Markdown Truth for one local Person and immutable preference promotions."""

from __future__ import annotations

import hashlib
import secrets
import stat
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock

from codecairn.memory.library import (
    GlobalPreferencePromotion,
    Person,
    person_from_dict,
    person_to_dict,
    promotion_from_dict,
    promotion_to_dict,
)
from codecairn.memory.schema import IdentityConflict, SchemaInvalid
from codecairn.storage.markdown import (
    TruthIssue,
    _atomic_create,
    _fsync_directory,
    _mkdir_durable,
    _parse_record,
    _read_bytes,
    _render_record,
    _write_immutable,
)

_PERSON_KEYS = ("schema_version", "record_kind", "person_id", "created_at_ms")
_PROMOTION_KEYS = (
    "schema_version",
    "record_kind",
    "promotion_id",
    "person_id",
    "subject_key",
    "source",
    "replaces_promotion_id",
    "created_at_ms",
)


@dataclass(frozen=True, slots=True)
class PromotionArtifact:
    promotion: GlobalPreferencePromotion
    path: Path
    content_sha256: str


@dataclass(frozen=True, slots=True)
class PersonTruthScan:
    person: Person | None
    present: bool
    issues: tuple[TruthIssue, ...]


@dataclass(frozen=True, slots=True)
class PromotionTruthScan:
    promotions: tuple[PromotionArtifact, ...]
    present: bool
    issues: tuple[TruthIssue, ...]


class MarkdownLibraryStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._library_root = self._root / "library"
        self._lock = FileLock(self._root / ".myna-library.lock")

    def lock(self) -> AbstractContextManager[FileLock]:
        self._root.mkdir(parents=True, exist_ok=True)
        for directory in (self._library_root, self._library_root / "global-preferences"):
            try:
                metadata = directory.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise SchemaInvalid("Myna library directory is unsafe")
        try:
            metadata = Path(self._lock.lock_file).lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SchemaInvalid("Myna library lock is unsafe")
        return self._lock

    def person(self) -> Person:
        with self.lock():
            path = self._library_root / "person.md"
            self._discard_stages(self._library_root, ".person.md.")
            source = _read_bytes(path, missing_ok=True)
            if source is not None:
                return self._parse_person(source)
            promotion_root = self._library_root / "global-preferences"
            if promotion_root.exists() and any(promotion_root.iterdir()):
                raise SchemaInvalid("person_missing")
            person = Person(schema_version=1, person_id=f"person_{secrets.token_hex(32)}", created_at_ms=time.time_ns() // 1_000_000)
            content = self._render_person(person)
            _mkdir_durable(path.parent)
            _atomic_create(path, content, stage_prefix="person")
            stored = _read_bytes(path)
            assert stored is not None
            return self._parse_person(stored)

    def scan_person(self) -> PersonTruthScan:
        path = self._library_root / "person.md"
        try:
            with self.lock():
                self._discard_stages(self._library_root, ".person.md.")
                source = _read_bytes(path, missing_ok=True)
                if source is None:
                    return PersonTruthScan(None, False, ())
                return PersonTruthScan(self._parse_person(source), True, ())
        except (OSError, UnicodeError, SchemaInvalid, ValueError) as error:
            return PersonTruthScan(None, True, (TruthIssue(path, getattr(error, "code", type(error).__name__)),))

    def write_promotion(self, promotion: GlobalPreferencePromotion) -> PromotionArtifact:
        with self.lock():
            artifact = self.prepare_promotion(promotion)
            stored = _write_immutable(
                path=artifact.path,
                content=self._render_promotion(promotion),
                expected_digest=artifact.content_sha256,
                read=self.read_promotion,
                same=lambda item: item.promotion == promotion,
                identity=promotion.promotion_id,
                on_stage=None,
                stage_prefix="promotion",
            )
            return artifact if stored is None else stored

    def prepare_promotion(self, promotion: GlobalPreferencePromotion) -> PromotionArtifact:
        content = self._render_promotion(promotion)
        return PromotionArtifact(promotion, self.path_for_promotion(promotion), hashlib.sha256(content).hexdigest())

    def read_promotion(self, path: Path) -> PromotionArtifact:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self._library_root):
            raise SchemaInvalid("Promotion Markdown escapes the library root")
        source = _read_bytes(resolved)
        assert source is not None
        promotion = self._parse_promotion(source)
        if resolved != self.path_for_promotion(promotion):
            raise SchemaInvalid("Promotion Markdown is not at its canonical path")
        return PromotionArtifact(promotion, resolved, hashlib.sha256(source).hexdigest())

    def scan_promotions(self) -> PromotionTruthScan:
        with self.lock():
            return self._scan_promotions()

    def _scan_promotions(self) -> PromotionTruthScan:
        promotions: list[PromotionArtifact] = []
        issues: list[TruthIssue] = []
        root = self._library_root / "global-preferences"
        if not root.exists():
            return PromotionTruthScan((), False, ())
        paths = tuple(sorted(root.iterdir()))
        for path in paths:
            if path.name.startswith(".promotion_") and ".md." in path.name:
                path.unlink()
                _fsync_directory(root)
                continue
            if not path.name.startswith("promotion_") or path.suffix != ".md":
                issues.append(TruthIssue(path, "unexpected_library_entry"))
                continue
            try:
                promotions.append(self.read_promotion(path))
            except (OSError, UnicodeError, SchemaInvalid, ValueError) as error:
                issues.append(TruthIssue(path, getattr(error, "code", type(error).__name__)))
        ids = tuple(item.promotion.promotion_id for item in promotions)
        if len(ids) != len(set(ids)):
            raise IdentityConflict("Duplicate Global Preference Promotion identity")
        return PromotionTruthScan(tuple(promotions), bool(paths), tuple(issues))

    def promotions(self) -> tuple[GlobalPreferencePromotion, ...]:
        scan = self.scan_promotions()
        if scan.issues:
            raise SchemaInvalid("global_preference_invalid")
        return tuple(item.promotion for item in scan.promotions)

    def path_for_promotion(self, promotion: GlobalPreferencePromotion) -> Path:
        path = (self._library_root / "global-preferences" / f"{promotion.promotion_id}.md").resolve()
        if not path.is_relative_to(self._library_root):
            raise SchemaInvalid("Promotion target escapes the library root")
        return path

    @staticmethod
    def _discard_stages(root: Path, prefix: str) -> None:
        if not root.exists():
            return
        removed = False
        for path in root.iterdir():
            if path.name.startswith(prefix):
                path.unlink()
                removed = True
        if removed:
            _fsync_directory(root)

    @staticmethod
    def _render_person(person: Person) -> bytes:
        return _render_record(
            {**person_to_dict(person), "description": "One local Myna Person."}, _PERSON_KEYS, "person", "description"
        )

    @staticmethod
    def _parse_person(source: bytes) -> Person:
        record = _parse_record(source, _PERSON_KEYS, "person", "description")
        record.pop("description")
        return person_from_dict(record)

    @staticmethod
    def _render_promotion(promotion: GlobalPreferencePromotion) -> bytes:
        body = f"Use {promotion.source.memory_id} from {promotion.source.repository_key} in every repository."
        return _render_record(
            {**promotion_to_dict(promotion), "description": body}, _PROMOTION_KEYS, "global_preference_promotion", "description"
        )

    @staticmethod
    def _parse_promotion(source: bytes) -> GlobalPreferencePromotion:
        record = _parse_record(source, _PROMOTION_KEYS, "global_preference_promotion", "description")
        record.pop("description")
        return promotion_from_dict(record)
