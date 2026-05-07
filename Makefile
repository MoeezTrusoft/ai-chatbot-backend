.PHONY: install lint type test run up down smoke compose-config rag-build rag-verify rag-index rag-smoke pricing-verify pricing-smoke portfolio-verify portfolio-smoke

PYTHON ?= python3
UV ?= $(PYTHON) -m uv
UV_CACHE_DIR ?= .uv-cache
APP ?= bookcraft.api.main:app

install:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) sync --all-extras

lint:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run ruff check .

type:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run mypy src

test:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run pytest

run:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run uvicorn $(APP) --host 0.0.0.0 --port 8000

up:
	docker compose up -d

down:
	docker compose down

compose-config:
	docker compose config

smoke:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/dev/smoke.py

rag-build:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/extract_bookcraft_knowledge.py
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/build_rag_corpus.py

rag-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/verify_rag_corpus.py

rag-index:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/index_rag_corpus.py

rag-smoke:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/rag_smoke.py

pricing-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/verify_pricing_rules.py

pricing-smoke:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/pricing_smoke.py

portfolio-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/verify_portfolio_registry.py

portfolio-smoke:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/portfolio_smoke.py
