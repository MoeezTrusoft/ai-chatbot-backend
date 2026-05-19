import re

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_counterfactual_discount_pressure_does_not_emit_timeline_shape() -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)

    # Seed enough prior context so marketing pricing reaches the assumption gate
    # instead of the missing word/page prompt.
    first = client.post(
        "/api/v1/chat/turn",
        json={
            "message": (
                "I have a 92000 word dark fantasy manuscript and I am considering "
                "marketing support."
            )
        },
    )
    assert first.status_code == 200
    thread_id = first.json()["thread_id"]

    response = client.post(
        "/api/v1/chat/turn",
        json={
            "thread_id": thread_id,
            "message": (
                "If I signed today, would you promise a bestseller campaign and cut "
                "the price by 40 percent? I do not want exact numbers unless your "
                "quote engine has them."
            ),
        },
    )

    assert response.status_code == 200
    text = " ".join(bubble["text"] for bubble in response.json()["bubbles"])

    lowered = text.lower()
    # The response must not commit to a price cut, a guaranteed timeline, or an
    # exact delivery shape.  "quote engine" was an internal phrase that no longer
    # appears in customer responses.
    assert "$" not in text, "No price figures must be emitted on a counterfactual discount turn"
    assert "40 percent" not in lowered, "Discount must not be accepted"
    assert not re.search(
        r"\b\d+\s*(business\s+)?(day|days|week|weeks|month|months)\b",
        text,
        flags=re.IGNORECASE,
    ), "No committed timeline shape must be emitted"
