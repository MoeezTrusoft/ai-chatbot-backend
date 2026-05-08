from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.documents import (
    AgreementParams,
    DocumentEngine,
    DocumentStatus,
    DocumentTemplateRegistry,
    NDAParams,
)
from bookcraft.infra.config import Settings


def main() -> int:
    client = TestClient(create_app(Settings(app_env="test")))

    first = _chat(client, "I am looking for ghostwriting help for a fantasy novel.")
    thread_id = UUID(first["thread_id"])
    quote_missing = _chat(
        client,
        "How much will it cost and how long will it take?",
        thread_id=thread_id,
    )
    quote_gated = _chat(
        client,
        "Ghostwriting for a 50000 word fantasy manuscript. Please price it and timeline it.",
        thread_id=thread_id,
    )
    portfolio = _chat(
        client,
        "Show me cover design portfolio samples for fantasy books.",
        thread_id=thread_id,
    )
    ghostwriting_portfolio = _chat(client, "Show me ghostwriting samples.", thread_id=thread_id)
    nda_status = _chat(client, "I need an NDA for my manuscript.", thread_id=thread_id)
    agreement_status = _chat(client, "I need a service agreement.", thread_id=thread_id)

    quote_missing_text = _text(quote_missing).lower()
    _assert(any(term in quote_missing_text for term in ["service", "words", "pages"]))
    _assert("$" not in _text(quote_missing))
    _assert(
        any(
            phrase in _text(quote_gated)
            for phrase in ["pricing values are not approved", "timeline values are not approved"]
        )
    )
    _assert("$" not in _text(quote_gated))
    _assert("Returned approved registry samples only" in _text(portfolio))
    _assert("confidential" in _text(ghostwriting_portfolio).lower())
    _assert("approved template" in _text(nda_status))
    _assert("approved template" in _text(agreement_status))

    service = client.app.state.chat_service
    events = [event["event_type"] for event in service.threads[thread_id].events]
    _assert("trimatch.voted" in events)
    _assert("intent.classified" in events)
    _assert("assistant.response" in events)

    with tempfile.TemporaryDirectory(prefix="bookcraft-final-acceptance-") as tmp:
        engine = DocumentEngine(
            registry=DocumentTemplateRegistry("data/templates"),
            output_dir=Path(tmp),
            pdf_rendering_enabled=False,
        )
        nda = engine.generate_nda(_nda_params())
        agreement = engine.generate_agreement(_agreement_params())
        _assert(nda.status == DocumentStatus.VERIFIED)
        _assert(agreement.status == DocumentStatus.VERIFIED)
        _assert(nda.parameter_hash != nda.rendered_hash)
        _assert(agreement.parameter_hash != agreement.rendered_hash)

    print(
        {
            "status": "passed",
            "thread_id": str(thread_id),
            "events": len(service.threads[thread_id].events),
            "pricing_mode": "gated_no_customer_numbers",
            "documents": ["nda_verified", "agreement_verified"],
        }
    )
    return 0


def _chat(client: TestClient, message: str, *, thread_id: UUID | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    response = client.post("/api/v1/chat/turn", json=payload)
    response.raise_for_status()
    return response.json()


def _text(response: dict[str, object]) -> str:
    return " ".join(bubble["text"] for bubble in response["bubbles"])  # type: ignore[index]


def _assert(condition: bool) -> None:
    if not condition:
        msg = "final acceptance assertion failed"
        raise AssertionError(msg)


def _nda_params() -> NDAParams:
    return NDAParams.model_validate(
        {
            "date": "May 8, 2026",
            "authorTitle": "Ms.",
            "authorFullName": "Avery Author",
            "authorPhone": "555-0100",
            "authorEmail": "avery@example.com",
            "signature": "Jerry Miller",
        }
    )


def _agreement_params() -> AgreementParams:
    return AgreementParams.model_validate(
        {
            "logoPath": "",
            "effectiveDate": "May 8, 2026",
            "abbreviation": "Ms.",
            "clientFullName": "Avery Author",
            "clientPhone": "555-0100",
            "clientEmail": "avery@example.com",
            "clientLocation": "Houston, Texas",
            "filteredServices": [
                {
                    "title": "Editing & Proofreading",
                    "items": [
                        {
                            "title": "Copy Editing",
                            "description": "Line-level review from the selected service scope.",
                        }
                    ],
                }
            ],
            "finalFee": "fixture-engine-output",
            "totalFee": "fixture-engine-output",
            "discountPercent": 0,
            "scheduleType": "Engine-approved payment schedule",
            "signature": "Jerry Miller",
            "agreementDate": "May 8, 2026",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
