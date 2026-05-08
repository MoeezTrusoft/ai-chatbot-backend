from __future__ import annotations

from pathlib import Path


def test_ci_runs_security_and_verifier_gates() -> None:
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "make verifier-gates" in ci
    assert "make security-scan" in ci
    assert "make dependency-scan" in ci


def test_cd_documents_required_deployment_stages() -> None:
    cd = Path(".github/workflows/cd.yml").read_text(encoding="utf-8").casefold()

    assert "build image" in cd
    assert "run migrations" in cd
    assert "deploy staging" in cd
    assert "smoke test" in cd
    assert "manual production gate" in cd


def test_security_runbook_exists() -> None:
    runbook = Path("docs/runbooks/security-governance.md").read_text(encoding="utf-8")

    assert "make ci-local" in runbook
    assert "Do not commit live credentials" in runbook
