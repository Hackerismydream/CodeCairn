from __future__ import annotations

import argparse
import json
from pathlib import Path

from codecairn.evaluation.coding import (
    CodexExecAgent,
    CodingRunConfig,
    run_coding_evaluation,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run immutable CodingMemoryBench memory-on/off experiments."
    )
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--agent-timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    artifact = run_coding_evaluation(
        CodingRunConfig(
            suite_path=args.suite,
            output_root=args.output_root,
            experiment_id=args.experiment_id,
            repository_commit=args.repository_commit,
            repeats=args.repeats,
            seed=args.seed,
            max_workers=args.max_workers,
        ),
        agent=CodexExecAgent(
            executable=args.codex_executable,
            model=args.model,
            timeout_seconds=args.agent_timeout_seconds,
        ),
    )
    print(json.dumps(artifact.summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
