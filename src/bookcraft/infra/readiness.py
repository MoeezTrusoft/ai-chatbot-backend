import asyncio
import inspect
from dataclasses import dataclass

import httpx
import redis.asyncio as redis
from elasticsearch import AsyncElasticsearch
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import SalesError, SalesMcpClient
from bookcraft.infra.schemas import DependencyStatus, ReadinessResponse


@dataclass(slots=True)
class ReadinessChecker:
    settings: Settings
    sales_client: SalesMcpClient | None = None

    async def check(self) -> ReadinessResponse:
        if not self.settings.readiness_check_externals:
            return ReadinessResponse(
                status="ready",
                dependencies={
                    "externals": DependencyStatus(
                        status="skipped",
                        detail="Set READINESS_CHECK_EXTERNALS=true to check local infrastructure.",
                    )
                },
            )

        pending = [
            self._check_postgres(),
            self._check_redis(),
            self._check_elasticsearch(),
            self._check_tei(),
        ]
        if self.settings.sales_integration_mode == "erp_mcp":
            pending.append(self._check_sales())
        checks = await asyncio.gather(*pending)
        dependencies = {name: status for name, status in checks}
        all_dependencies_ok = all(item.status == "ok" for item in dependencies.values())
        overall = "ready" if all_dependencies_ok else "not_ready"
        return ReadinessResponse(status=overall, dependencies=dependencies)

    async def _check_sales(self) -> tuple[str, DependencyStatus]:
        client = self.sales_client or SalesMcpClient(self.settings)
        try:
            await client.check_connection()
            return "sales", DependencyStatus(status="ok")
        except SalesError:
            return "sales", DependencyStatus(status="error", detail="sales_unavailable")
        finally:
            if self.sales_client is None:
                await client.aclose()

    async def _check_postgres(self) -> tuple[str, DependencyStatus]:
        engine = create_async_engine(self.settings.database_url, pool_pre_ping=True)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("select 1"))
            return "postgres", DependencyStatus(status="ok")
        except Exception as exc:  # pragma: no cover - exercised in integration envs
            return "postgres", DependencyStatus(status="error", detail=str(exc))
        finally:
            await engine.dispose()

    async def _check_redis(self) -> tuple[str, DependencyStatus]:
        client = redis.from_url(self.settings.redis_url)
        try:
            ping_result = client.ping()
            if inspect.isawaitable(ping_result):
                await ping_result
            return "redis", DependencyStatus(status="ok")
        except Exception as exc:  # pragma: no cover - exercised in integration envs
            return "redis", DependencyStatus(status="error", detail=str(exc))
        finally:
            await client.aclose()

    async def _check_elasticsearch(self) -> tuple[str, DependencyStatus]:
        client = AsyncElasticsearch(
            self.settings.elasticsearch_url,
            basic_auth=(
                (self.settings.elasticsearch_user, self.settings.elasticsearch_password)
                if self.settings.elasticsearch_user and self.settings.elasticsearch_password
                else None
            ),
        )
        try:
            health = await client.cluster.health()
            return "elasticsearch", DependencyStatus(status="ok", detail=str(health.get("status")))
        except Exception as exc:  # pragma: no cover - exercised in integration envs
            return "elasticsearch", DependencyStatus(status="error", detail=str(exc))
        finally:
            await client.close()

    async def _check_tei(self) -> tuple[str, DependencyStatus]:
        try:
            async with httpx.AsyncClient(timeout=self.settings.tei_timeout_seconds) as client:
                response = await client.get(f"{self.settings.tei_url}/health")
                response.raise_for_status()
            return "tei", DependencyStatus(status="ok")
        except Exception as exc:  # pragma: no cover - exercised in integration envs
            return "tei", DependencyStatus(status="error", detail=str(exc))
