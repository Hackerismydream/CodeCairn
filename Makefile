.PHONY: check format imports lint source-budget test typecheck

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
	uv run python scripts/source_budget.py --stage v01-007

test:
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck imports source-budget test
