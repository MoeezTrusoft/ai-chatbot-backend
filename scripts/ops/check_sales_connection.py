"""Read-only deployment preflight using the configured, standard Sales MCP client."""

import argparse
import asyncio
import json

from pydantic import ValidationError

from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import SalesError, SalesMcpClient


async def check(*, require_booking: bool = False) -> int:
    client = None
    try:
        settings = Settings()
        if settings.sales_integration_mode != "erp_mcp":
            raise SalesError("sales_integration_mode_is_standalone")
        client = SalesMcpClient(settings)
        result = await client.check_connection()
        if require_booking and not result["booking_enabled"]:
            raise SalesError("sales_booking_disabled")
        print(json.dumps(result))
        return 0
    except ValidationError:
        # Pydantic errors can contain secret-valued environment inputs.
        print(json.dumps({"status": "failed", "error": "invalid_sales_configuration"}))
        return 1
    except SalesError as exc:
        print(json.dumps({"status": "failed", "error": exc.code}))
        return 1
    finally:
        if client is not None:
            await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-booking", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check(require_booking=args.require_booking)))
