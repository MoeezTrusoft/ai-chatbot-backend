from __future__ import annotations

from pathlib import Path


def test_ci_runs_security_and_verifier_gates() -> None:
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    for check in ("verifier-gates", "security-scan", "dependency-scan"):
        assert check in ci
    assert "make ${{ matrix.check }}" in ci
    assert "fail-fast: false" in ci


def test_cd_builds_validates_and_exports_without_deployment() -> None:
    cd = Path(".github/workflows/cd.yml").read_text(encoding="utf-8").casefold()

    assert "build image" in cd
    assert "smoke test" in cd
    assert "push: false" in cd
    assert "docker save" in cd
    assert "actions/upload-artifact" in cd
    assert "deploy staging" not in cd


def test_security_runbook_exists() -> None:
    runbook = Path("docs/runbooks/security-governance.md").read_text(encoding="utf-8")

    assert "make ci-local" in runbook
    assert "Do not commit live credentials" in runbook
