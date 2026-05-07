from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    base_url = os.getenv("BOOKCRAFT_SMOKE_BASE_URL", "http://localhost:8000")
    expected = {
        "/healthz": 200,
        "/readyz": 200,
        "/metrics": 200,
    }
    with httpx.Client(timeout=5.0) as client:
        for path, status_code in expected.items():
            response = client.get(f"{base_url}{path}")
            if response.status_code != status_code:
                print(
                    f"{path} returned {response.status_code}, expected {status_code}",
                    file=sys.stderr,
                )
                return 1
    print("bookcraft smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

