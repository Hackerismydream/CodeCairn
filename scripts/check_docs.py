#!/usr/bin/env python3
"""Check maintained Markdown links, anchors, and documented command surfaces."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*]\(([^)\s]+)(?:\s+[\"'][^)]*)?\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
COMMANDS = (
    ("uv", "run", "codecairn", "--help"),
    ("uv", "run", "codecairn", "init", "--help"),
    ("uv", "run", "codecairn", "hook", "install", "--help"),
    ("uv", "run", "codecairn", "doctor", "--help"),
    ("uv", "run", "codecairn", "memory", "history", "--help"),
    ("uv", "run", "codecairn", "namespace", "export", "--help"),
    ("uv", "run", "codecairn", "namespace", "reset", "--help"),
    ("make", "eval-locomo-200", "HELP=1"),
    ("make", "eval-locomo-full", "HELP=1"),
    ("make", "eval-coding-ab", "HELP=1"),
)


def markdown_files() -> tuple[Path, ...]:
    candidates = [
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "CONTEXT.md",
        ROOT / "CHANGELOG.md",
        ROOT / "CODE_OF_CONDUCT.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "SECURITY.md",
        *sorted((ROOT / "docs").rglob("*.md")),
        *sorted((ROOT / "benchmarks").glob("*/README.md")),
    ]
    return tuple(dict.fromkeys(path.resolve() for path in candidates if path.is_file()))


def check_links(paths: tuple[Path, ...]) -> dict[str, int]:
    anchors = {path: _anchors(path) for path in paths}
    failures: list[str] = []
    checked = 0
    for source in paths:
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            for raw_target in LINK.findall(line):
                target = raw_target.strip("<>")
                split = urlsplit(target)
                if split.scheme in {"http", "https", "mailto"} or target.startswith("//"):
                    continue
                checked += 1
                destination = source if not split.path else (source.parent / unquote(split.path)).resolve()
                if not destination.exists():
                    failures.append(f"{source.relative_to(ROOT)}:{line_number}: missing {target}")
                    continue
                if split.fragment and destination.is_file():
                    destination_anchors = anchors.setdefault(destination, _anchors(destination))
                    if unquote(split.fragment).casefold() not in destination_anchors:
                        failures.append(f"{source.relative_to(ROOT)}:{line_number}: missing anchor {target}")
    if failures:
        raise ValueError("documentation link failures:\n" + "\n".join(failures))
    return {"file_count": len(paths), "checked_local_link_count": checked}


def command_smoke() -> list[dict[str, object]]:
    outcomes = []
    for command in COMMANDS:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(f"documented command failed ({' '.join(command)}): {result.stderr[-1000:]}")
        outcomes.append({"command": list(command), "exit_code": result.returncode})
    return outcomes


def _anchors(path: Path) -> set[str]:
    if path.suffix.casefold() != ".md":
        return set()
    seen: Counter[str] = Counter()
    anchors: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = HEADING.match(line)
        if match is None:
            continue
        base = _slug(match.group(1))
        ordinal = seen[base]
        seen[base] += 1
        anchors.add(base if ordinal == 0 else f"{base}-{ordinal}")
    return anchors


def _slug(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[`*_~]", "", value)
    value = "".join(
        character for character in unicodedata.normalize("NFKC", value).casefold() if character.isalnum() or character in " -_"
    )
    return re.sub(r"\s+", "-", value.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commands", action="store_true")
    arguments = parser.parse_args()
    paths = markdown_files()
    result: dict[str, object] = {"schema_version": 1, "links": check_links(paths)}
    if arguments.commands:
        result["commands"] = command_smoke()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
