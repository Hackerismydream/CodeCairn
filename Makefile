.PHONY: check eval-coding-ab eval-locomo-200 eval-locomo-full eval-retrieval eval-scale eval-smoke evidence-verify format imports lint source-budget test typecheck

EVAL_OUTPUT_ROOT ?= benchmark_results

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
	uv run python scripts/source_budget.py --stage v01-008

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

test:
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck imports source-budget test
