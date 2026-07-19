# CodingMemoryBench-20 v2

This fixed suite evaluates whether pre-retrieved coding memory improves an agent run. It contains
20 small repair tasks, two arms (`memory-on` and `memory-off`), and three default repeats, producing
120 isolated runs.

Each run copies the same checked-in starter workspace. The `memory-on` arm receives only that
task's checked-in Recall Context; the `memory-off` arm has no context file or memory environment.
The verifier is not part of the agent workspace: the runner injects it only after agent execution,
runs it inside the workspace, records its source hash, and removes it before hashing the final
agent workspace. This prevents either arm from fitting an implementation to hidden assertions.
All memory-off runs finish before any memory-on run directory is created, so an off-arm process
cannot discover, read, or mutate future on-arm state even though the Codex sandbox permits broad
read access.
The agent never reports success itself: `verify.py` executes inside the copied workspace and its
process exit code determines pass or failure. Provider errors and agent process errors are recorded
as infrastructure failures and excluded from pass-rate denominators.

The suite is deliberately small enough to inspect. It measures the harness and the effect of useful
repository history, not general software-engineering ability. Published values must come from an
immutable run directory tied to a repository commit and provider configuration.

Run the default 120-run suite with an authenticated Codex CLI:

```bash
uv run python -m codecairn.evaluation.coding_cli \
  --suite benchmarks/coding/suite.json \
  --output-root artifacts/coding \
  --experiment-id <immutable-id> \
  --repository-commit <git-commit> \
  --max-workers 4
```
