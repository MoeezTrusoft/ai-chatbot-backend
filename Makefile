.PHONY: install lint type test run up down smoke compose-config

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
