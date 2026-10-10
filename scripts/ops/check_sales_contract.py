"""Call an isolated sales_contract_server with the actual chatbot MCP client."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import TOOL_SCOPES, SalesMcpClient


async def check(connection: dict[str, str]) -> None:
    settings = Settings(
        app_env="test",
        api_auth_mode="jwt",
        jwt_signing_key="synthetic-contract-probe-key-for-tests",  # noqa: S106
        sales_integration_mode="erp_mcp",
        **connection,
    )
    client = SalesMcpClient(settings)
    customer, lead, appointment, request = [str(uuid4()) for _ in range(4)]
    mutation = {"consultationId": appointment, "requestId": request, "expectedVersion": 1}
    time_args = {"startTime": "2030-10-12T22:15:00+05:00", "userTimezone": "Asia/Karachi"}
    payloads = {
        "sales_customer_get": {"customerId": customer},
        "sales_customer_update": {
            "customerId": customer,
            "name": "Synthetic",
            "phone": "+12025550123",
        },
        "sales_lead_create": {"customerId": customer, "source": "chatbot"},
        "sales_consultation_config_get": {},
        "sales_consultation_list": {"leadId": lead, "limit": 100},
        "sales_consultation_get": {"consultationId": appointment},
        "sales_consultation_availability": {"leadId": lead, **time_args, "limit": 20},
        "sales_consultation_schedule": {"leadId": lead, **time_args, "requestId": request},
        "sales_consultation_reschedule": {**mutation, **time_args, "preferredOwnerId": "test-csr"},
        "sales_consultation_confirm": mutation,
        "sales_consultation_cancel": {**mutation, "reason": "Customer requested cancellation"},
    }
    assert set(payloads) == set(TOOL_SCOPES)
    try:
        for name, arguments in payloads.items():
            result = await client.call(name, arguments)
            assert result["accepted"] is True
            assert result["tenantId"] == connection["sales_tenant_id"]
        print(
            json.dumps(
                {
                    "passed": len(payloads),
                    "scope": "actual Sales MCP lifecycle, auth and input schemas",
                }
            )
        )
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(check(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))))
