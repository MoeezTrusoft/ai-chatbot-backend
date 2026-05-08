.PHONY: install lint type test run up down smoke acceptance compose-config rag-build rag-verify rag-index rag-smoke pricing-verify pricing-smoke portfolio-verify portfolio-smoke trimatch-verify trimatch-eval trimatch-smoke funnel-partition funnel-verify funnel-smoke documents-verify documents-smoke monitoring-verify prompt-verify eval-verify ci-cd-verify security-scan dependency-scan verifier-gates ci-local

PYTHON ?= python3
UV ?= uv
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

acceptance:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/dev/final_acceptance.py

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

trimatch-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/verify_trimatch_rules.py

trimatch-eval:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/trimatch_eval.py

trimatch-smoke:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/trimatch_smoke.py

funnel-partition:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/partition_funnel_rules.py

funnel-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/verify_funnel_rules.py

funnel-smoke:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/funnel_smoke.py

documents-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/verify_document_templates.py

documents-smoke:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/document_smoke.py

monitoring-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/ops/verify_monitoring.py

prompt-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/verify_prompt_pack.py

eval-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/data/verify_eval_corpus.py

ci-cd-verify:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/ops/verify_ci_cd.py

security-scan:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/security/secret_scan.py

dependency-scan:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python scripts/security/dependency_scan.py

verifier-gates: rag-verify pricing-verify portfolio-verify documents-verify trimatch-verify funnel-verify monitoring-verify prompt-verify eval-verify ci-cd-verify

ci-local: lint type test verifier-gates security-scan dependency-scan compose-config
