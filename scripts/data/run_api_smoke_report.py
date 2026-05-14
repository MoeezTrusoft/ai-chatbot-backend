from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    category: str
    status: str
    severity: str
    message: str
    details: dict[str, Any] | None = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run API smoke checks against a running BookCraft chatbot API."
    )
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--output-dir", default="reports/chatbot")
    parser.add_argument("--jwt-signing-key", default=None)
    parser.add_argument(
        "--customer-id",
        default=None,
        help="Existing customer UUID to include in generated JWT.",
    )
    parser.add_argument("--metrics-token", default=None)
    parser.add_argument("--expect-auth", action="store_true")
    parser.add_argument("--expect-metrics-protected", action="store_true")
    parser.add_argument("--rate-limit-probe", action="store_true")
    parser.add_argument("--rate-limit-attempts", type=int, default=35)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = build_report(
        base_url=args.base_url,
        jwt_signing_key=args.jwt_signing_key,
        customer_id=args.customer_id,
        metrics_token=args.metrics_token,
        expect_auth=args.expect_auth,
        expect_metrics_protected=args.expect_metrics_protected,
        rate_limit_probe=args.rate_limit_probe,
        rate_limit_attempts=args.rate_limit_attempts,
    )

    json_path = output_dir / "api_smoke_report.json"
    md_path = output_dir / "api_smoke_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


def build_report(
    *,
    base_url: str | None,
    jwt_signing_key: str | None,
    customer_id: str | None,
    metrics_token: str | None,
    expect_auth: bool,
    expect_metrics_protected: bool,
    rate_limit_probe: bool,
    rate_limit_attempts: int,
) -> dict[str, Any]:
    checks: list[SmokeCheck] = []

    if not base_url:
        checks.append(
            SmokeCheck(
                name="base_url_required",
                category="setup",
                status="skipped",
                severity="info",
                message=(
                    "No --base-url provided. This report is safe/dry-run only. "
                    "Pass --base-url http://localhost:8000 to smoke a running API."
                ),
            )
        )
    else:
        checks.extend(
            run_http_smoke(
                base_url=base_url,
                jwt_signing_key=jwt_signing_key,
                customer_id=customer_id,
                metrics_token=metrics_token,
                expect_auth=expect_auth,
                expect_metrics_protected=expect_metrics_protected,
                rate_limit_probe=rate_limit_probe,
                rate_limit_attempts=rate_limit_attempts,
            )
        )

    error_count = sum(1 for item in checks if item.severity == "error")
    warning_count = sum(1 for item in checks if item.severity == "warning")
    passed_count = sum(1 for item in checks if item.status == "passed")
    skipped_count = sum(1 for item in checks if item.status == "skipped")

    return {
        "schema_version": 1,
        "summary": {
            "valid": error_count == 0,
            "generated_at": datetime.now(UTC).isoformat(),
            "base_url": base_url,
            "check_count": len(checks),
            "passed_count": passed_count,
            "warning_count": warning_count,
            "error_count": error_count,
            "skipped_count": skipped_count,
            "expect_auth": expect_auth,
            "customer_id_provided": customer_id is not None,
            "expect_metrics_protected": expect_metrics_protected,
            "rate_limit_probe": rate_limit_probe,
            "safety_note": (
                "Smoke/report only. It does not call live LLMs directly, does not send "
                "emails, does not create legal documents, does not create Elasticsearch "
                "indices, and does not move aliases."
            ),
        },
        "checks": [check_to_dict(item) for item in checks],
    }


def run_http_smoke(
    *,
    base_url: str,
    jwt_signing_key: str | None,
    customer_id: str | None,
    metrics_token: str | None,
    expect_auth: bool,
    expect_metrics_protected: bool,
    rate_limit_probe: bool,
    rate_limit_attempts: int,
) -> list[SmokeCheck]:
    checks: list[SmokeCheck] = []
    base = base_url.rstrip("/")
    token = create_test_jwt(jwt_signing_key, customer_id=customer_id) if jwt_signing_key else None
    auth_headers = {"Authorization": f"Bearer {token}"} if token else {}

    with httpx.Client(timeout=15.0) as client:
        checks.append(check_get(client, f"{base}/healthz", "healthz"))
        checks.append(check_get(client, f"{base}/readyz", "readyz"))

        if expect_auth:
            checks.append(check_chat_requires_auth(client, base))

        checks.append(check_chat_turn(client, base, headers=auth_headers))

        checks.append(
            check_metrics(
                client,
                base,
                metrics_token=metrics_token,
                expect_protected=expect_metrics_protected,
            )
        )

        if rate_limit_probe:
            checks.append(
                check_rate_limit(
                    client,
                    base,
                    headers=auth_headers,
                    attempts=rate_limit_attempts,
                )
            )
        else:
            checks.append(
                SmokeCheck(
                    name="rate_limit_probe",
                    category="rate_limit",
                    status="skipped",
                    severity="info",
                    message="Rate-limit probe skipped. Pass --rate-limit-probe to enable.",
                )
            )

    return checks


def check_get(client: httpx.Client, url: str, name: str) -> SmokeCheck:
    try:
        response = client.get(url)
        passed = response.status_code < 500
        return SmokeCheck(
            name=name,
            category="http",
            status="passed" if passed else "failed",
            severity="info" if passed else "error",
            message=f"GET {url} returned HTTP {response.status_code}.",
            details=response_details(response),
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeCheck(
            name=name,
            category="http",
            status="failed",
            severity="error",
            message=f"GET {url} failed: {exc.__class__.__name__}: {exc}",
        )


def check_chat_requires_auth(client: httpx.Client, base: str) -> SmokeCheck:
    try:
        response = client.post(
            f"{base}/api/v1/chat/turn",
            json={"message": "Auth smoke test without token."},
        )
        passed = response.status_code == 401
        return SmokeCheck(
            name="chat_requires_auth",
            category="auth",
            status="passed" if passed else "failed",
            severity="info" if passed else "error",
            message=(
                "Unauthenticated chat request was rejected with 401."
                if passed
                else f"Expected 401 but got HTTP {response.status_code}."
            ),
            details=response_details(response),
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeCheck(
            name="chat_requires_auth",
            category="auth",
            status="failed",
            severity="error",
            message=f"Auth smoke failed: {exc.__class__.__name__}: {exc}",
        )


def check_chat_turn(
    client: httpx.Client,
    base: str,
    *,
    headers: dict[str, str],
) -> SmokeCheck:
    try:
        response = client.post(
            f"{base}/api/v1/chat/turn",
            headers={
                **headers,
                "X-Correlation-ID": f"api-smoke-{int(time.time())}",
            },
            json={
                "message": (
                    "Tell me about BookCraft ghostwriting support, but do not "
                    "invent prices or timelines."
                )
            },
        )
        data = safe_json(response)
        passed = (
            response.status_code == 200
            and isinstance(data, dict)
            and isinstance(data.get("thread_id"), str)
            and isinstance(data.get("bubbles"), list)
            and len(data.get("bubbles", [])) > 0
        )
        return SmokeCheck(
            name="chat_turn",
            category="chat",
            status="passed" if passed else "failed",
            severity="info" if passed else "error",
            message=(
                "Chat turn returned thread_id and at least one bubble."
                if passed
                else f"Chat turn returned HTTP {response.status_code}."
            ),
            details={
                **response_details(response),
                "thread_id_present": isinstance(data, dict)
                and isinstance(data.get("thread_id"), str),
                "bubble_count": len(data.get("bubbles", []))
                if isinstance(data, dict) and isinstance(data.get("bubbles"), list)
                else 0,
                "intent": data.get("intent") if isinstance(data, dict) else None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeCheck(
            name="chat_turn",
            category="chat",
            status="failed",
            severity="error",
            message=f"Chat smoke failed: {exc.__class__.__name__}: {exc}",
        )


def check_metrics(
    client: httpx.Client,
    base: str,
    *,
    metrics_token: str | None,
    expect_protected: bool,
) -> SmokeCheck:
    try:
        first = client.get(f"{base}/metrics")
        if expect_protected and first.status_code == 403:
            if not metrics_token:
                return SmokeCheck(
                    name="metrics_protection",
                    category="metrics",
                    status="passed",
                    severity="info",
                    message="Metrics endpoint is protected with HTTP 403.",
                    details=response_details(first),
                )
            second = client.get(
                f"{base}/metrics",
                headers={"Authorization": f"Bearer {metrics_token}"},
            )
            passed = second.status_code == 200
            return SmokeCheck(
                name="metrics_protection",
                category="metrics",
                status="passed" if passed else "failed",
                severity="info" if passed else "error",
                message=(
                    "Metrics endpoint requires token and accepts configured token."
                    if passed
                    else f"Metrics token request returned HTTP {second.status_code}."
                ),
                details=response_details(second),
            )

        if expect_protected:
            return SmokeCheck(
                name="metrics_protection",
                category="metrics",
                status="failed",
                severity="error",
                message=f"Expected metrics protection but got HTTP {first.status_code}.",
                details=response_details(first),
            )

        passed = first.status_code in {200, 403}
        return SmokeCheck(
            name="metrics_endpoint",
            category="metrics",
            status="passed" if passed else "failed",
            severity="info" if passed else "error",
            message=f"Metrics endpoint returned HTTP {first.status_code}.",
            details=response_details(first),
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeCheck(
            name="metrics_endpoint",
            category="metrics",
            status="failed",
            severity="error",
            message=f"Metrics smoke failed: {exc.__class__.__name__}: {exc}",
        )


def check_rate_limit(
    client: httpx.Client,
    base: str,
    *,
    headers: dict[str, str],
    attempts: int,
) -> SmokeCheck:
    statuses: list[int] = []
    for index in range(attempts):
        response = client.post(
            f"{base}/api/v1/chat/turn",
            headers={**headers, "X-Correlation-ID": f"rate-smoke-{index}"},
            json={"message": f"Rate limit smoke message {index}."},
        )
        statuses.append(response.status_code)
        if response.status_code == 429:
            break

    saw_success = any(status == 200 for status in statuses)
    saw_rate_limit = any(status == 429 for status in statuses)
    passed = saw_success and saw_rate_limit

    return SmokeCheck(
        name="rate_limit_probe",
        category="rate_limit",
        status="passed" if passed else "failed",
        severity="info" if passed else "warning",
        message=(
            "Rate-limit probe observed successful requests and HTTP 429."
            if passed
            else "Rate-limit probe did not observe HTTP 429 within attempts."
        ),
        details={
            "attempts": len(statuses),
            "statuses": statuses,
            "saw_success": saw_success,
            "saw_rate_limit": saw_rate_limit,
        },
    )


def create_test_jwt(signing_key: str, *, customer_id: str | None = None) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": "api-smoke",
        "customer_id": normalized_customer_id(customer_id),
        "scope": "chat:write",
        "iat": now,
        "nbf": now - 5,
        "exp": now + 600,
    }
    raw_header = b64url_json(header)
    raw_payload = b64url_json(payload)
    signing_input = f"{raw_header}.{raw_payload}"
    signature = hmac.new(
        signing_key.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{b64url(signature)}"


def normalized_customer_id(customer_id: str | None) -> str:
    if customer_id is None:
        return str(uuid4())

    try:
        return str(UUID(customer_id))
    except ValueError as exc:
        raise ValueError(f"--customer-id must be a valid UUID: {customer_id}") from exc


def b64url_json(value: dict[str, Any]) -> str:
    return b64url(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def response_details(response: httpx.Response) -> dict[str, Any]:
    return {
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type"),
        "correlation_id": response.headers.get("x-correlation-id"),
    }


def check_to_dict(check: SmokeCheck) -> dict[str, Any]:
    return {
        "name": check.name,
        "category": check.category,
        "status": check.status,
        "severity": check.severity,
        "message": check.message,
        "details": check.details or {},
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BookCraft API Smoke Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Base URL: `{summary['base_url']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Checks: `{summary['check_count']}`",
        f"- Passed: `{summary['passed_count']}`",
        f"- Warnings: `{summary['warning_count']}`",
        f"- Errors: `{summary['error_count']}`",
        f"- Skipped: `{summary['skipped_count']}`",
        f"- Expect auth: `{summary['expect_auth']}`",
        f"- Customer ID provided: `{summary['customer_id_provided']}`",
        f"- Expect metrics protected: `{summary['expect_metrics_protected']}`",
        f"- Rate-limit probe: `{summary['rate_limit_probe']}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
        "",
        "## Checks",
        "",
        "| Severity | Status | Category | Name | Message |",
        "|---|---|---|---|---|",
    ]

    for check in report["checks"]:
        lines.append(
            "| `{severity}` | `{status}` | `{category}` | `{name}` | {message} |".format(
                severity=check["severity"],
                status=check["status"],
                category=check["category"],
                name=check["name"],
                message=check["message"].replace("|", "\\|"),
            )
        )

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
