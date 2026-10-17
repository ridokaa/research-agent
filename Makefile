.PHONY: up down dev run migrate test lint format install

# ─── Docker ──────────────────────────────────────────────────────────────────
up:
	docker compose up -d
	@echo "Waiting for Postgres to be healthy..."
	@until docker compose exec postgres pg_isready -U postgres -d rag_db > /dev/null 2>&1; do sleep 1; done
	@echo "Postgres is ready."

down:
	docker compose down

# ─── Dev server ──────────────────────────────────────────────────────────────
dev:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000

# ─── Database migrations ─────────────────────────────────────────────────────
migrate:
	alembic upgrade head

migrate-create:
	@read -p "Migration name: " name; \
	alembic revision --autogenerate -m "$$name"

# ─── Testing ─────────────────────────────────────────────────────────────────
test:
	pytest -v

test-cov:
	pytest -v --cov=app --cov-report=term-missing

# ─── Linting & formatting ─────────────────────────────────────────────────────
lint:
	ruff check app tests
	mypy app

format:
	ruff format app tests
	ruff check --fix app tests

# ─── Dependency management ───────────────────────────────────────────────────
install:
	@if command -v uv > /dev/null 2>&1; then \
		uv pip install -e ".[dev]"; \
	else \
		pip install -e ".[dev]"; \
	fi

install-uv:
	pip install uv
	uv pip install -e ".[dev]"
