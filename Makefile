.PHONY: artifact-check artifact-repro check docs-check eval-coding-ab eval-locomo-200 eval-locomo-full eval-retrieval eval-scale eval-smoke evidence-verify format imports installed-smoke lint source-budget test typecheck

EVAL_OUTPUT_ROOT ?= benchmark_results
SOURCE_BUDGET_STAGE ?= v02-001

format:
	uv run ruff check --fix .
	uv run ruff format .

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

imports:
	uv run lint-imports

source-budget:
	uv run python scripts/source_budget.py --stage "$(SOURCE_BUDGET_STAGE)"

eval-smoke:
	uv run pytest -q \
		tests/test_import_session.py \
		tests/test_semantic_runtime.py \
		tests/test_evolution_runtime.py \
		tests/test_recall_lifecycle.py \
		tests/test_mcp.py \
		tests/test_hooks.py
	uv run python -m codecairn.evaluation.gates smoke --output-root "$(EVAL_OUTPUT_ROOT)" $(if $(RUN_ID),--run-id "$(RUN_ID)",)

eval-scale:
	uv run python -m codecairn.evaluation.gates scale --output-root "$(EVAL_OUTPUT_ROOT)" $(if $(RUN_ID),--run-id "$(RUN_ID)",)

eval-retrieval:
	uv run python -m codecairn.evaluation.gates retrieval --output-root "$(EVAL_OUTPUT_ROOT)" $(if $(RUN_ID),--run-id "$(RUN_ID)",)

eval-locomo-200:
	$(if $(filter 1,$(HELP)),uv run python -m codecairn.evaluation.gates plan locomo-200 --help-only,uv run python -m codecairn.evaluation.gates locomo locomo-200 --output-root "$(EVAL_OUTPUT_ROOT)" --dataset "$(DATASET)")

eval-locomo-full:
	$(if $(filter 1,$(HELP)),uv run python -m codecairn.evaluation.gates plan locomo-full --help-only,uv run python -m codecairn.evaluation.gates locomo locomo-full --output-root "$(EVAL_OUTPUT_ROOT)" --dataset "$(DATASET)")

eval-coding-ab:
	$(if $(filter 1,$(HELP)),uv run python -m codecairn.evaluation.gates plan coding-ab --help-only,uv run python -m codecairn.evaluation.gates coding --output-root "$(EVAL_OUTPUT_ROOT)")

evidence-verify:
	uv run codecairn evidence verify "$(or $(EVIDENCE_BUNDLE),evidence/benchmark-v3)"

artifact-check:
	uv run python scripts/release_artifacts.py verify "$(or $(DIST_DIR),dist)"

artifact-repro:
	uv run python scripts/release_artifacts.py compare --repo . --commit HEAD

installed-smoke:
	uv run python scripts/installed_smoke.py --wheel "$(or $(DIST_DIR),dist)" --evidence "$(or $(EVIDENCE_BUNDLE),evidence/benchmark-v3)"

docs-check:
	uv run python scripts/check_docs.py --commands

test:
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck imports source-budget test
