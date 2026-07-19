.PHONY: check format imports lint test typecheck

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

test:
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck imports test
