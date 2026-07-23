from __future__ import annotations

import os
import re
from importlib.metadata import version

_HF_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_FASTEMBED_VERSION = version("fastembed")
FASTEMBED_INFERENCE_THREADS = 2
FASTEMBED_TOKENIZER_PARALLELISM = False
FASTEMBED_TOKENIZER_THREADS = 1


def configure_fastembed_process() -> None:
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["RAYON_NUM_THREADS"] = str(FASTEMBED_TOKENIZER_THREADS)


def validate_hf_artifact(*, source_id: str, revision: str) -> None:
    if not source_id.strip() or "/" not in source_id:
        raise ValueError("Hugging Face artifact source must be an org/repository ID")
    if not _HF_COMMIT.fullmatch(revision):
        raise ValueError("Hugging Face artifact revision must be a 40-character commit SHA")


def fastembed_version() -> str:
    return _FASTEMBED_VERSION


def download_hf_snapshot(
    *,
    source_id: str,
    revision: str,
    cache_dir: str | None,
) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(
        repo_id=source_id,
        revision=revision,
        cache_dir=cache_dir,
    )
