"""Tenant-bound Sales integration; Sales remains the appointment system of record."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from bookcraft.components.storage.management_models import WorkflowRecord
from bookcraft.components.storage.models import ThreadRecord, utc_now
from bookcraft.domain.state import ThreadState
from bookcraft.infra.sales_mcp import SalesError, SalesMcpClient


class SalesAppointment(BaseModel):
    id: UUID
    tenantId: UUID
    customerId: UUID
    leadId: UUID
    ownerId: str | None = None
    startTime: datetime
    endTime: datetime
    timezone: str
    userTimezone: str
    status: Literal["Proposed", "Scheduled", "Completed", "Canceled"]
    version: int = Field(ge=1)
    offerExpiresAt: datetime | None = None


class SalesService:
    def __init__(self, client: SalesMcpClient, sessions: async_sessionmaker[AsyncSession]):
        self.client = client
        self.sessions = sessions

    async def profile(self, thread_id: UUID) -> dict[str, Any]:
        async with self.sessions() as session:
            thread = await session.get(ThreadRecord, thread_id)
            if thread is None or thread.deleted_at is not None or thread.customer_id is None:
                raise SalesError("sales_customer_identity_required", 422)
            customer_id = str(thread.customer_id)
        value = await self.client.call("sales_customer_get", {"customerId": customer_id})
        if (
            not isinstance(value, dict)
            or value.get("id") != customer_id
            or value.get("tenantId") != self.client.settings.sales_tenant_id
        ):
            raise SalesError("sales_customer_identity_mismatch", 502)
        if value.get("archivedAt") or value.get("anonymizedAt") or value.get("mergedIntoId"):
            raise SalesError("sales_customer_unavailable", 409)
        return value

    async def lead(
        self,
        thread_id: UUID,
        *,
        create: bool = False,
        profile: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        profile = profile if profile is not None else await self.profile(thread_id)
        lead = profile.get("lead")
        if not lead and create:
            # Always attach to the authenticated Sales customer. On retry, the
            # customer read above recovers a lead created before a lost response.
            lead = await self.client.call(
                "sales_lead_create",
                {
                    "customerId": profile["id"],
                    "source": "chatbot",
                },
            )
        if not lead:
            raise SalesError("sales_lead_required_save_contact_first", 422)
        if (
            not isinstance(lead, dict)
            or lead.get("customerId") != profile["id"]
            or lead.get("tenantId") != self.client.settings.sales_tenant_id
        ):
            raise SalesError("sales_lead_identity_mismatch", 502)
        try:
            lead_id = str(UUID(lead["id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise SalesError("sales_invalid_response", 502) from exc
        return profile["id"], lead_id

    async def contact(self, thread_id: UUID, fields: dict[str, Any]) -> dict[str, str]:
        profile = await self.profile(thread_id)
        changes = {key: value for key, value in fields.items() if profile.get(key) != value}
        if changes:
            await self.client.call(
                "sales_customer_update", {"customerId": profile["id"], **changes}
            )
        _, lead_id = await self.lead(thread_id, create=True)
        return {"status": "saved", "lead_id": lead_id}

    async def policy(self) -> dict[str, Any]:
        result = await self.client.call("sales_consultation_config_get", {})
        if not isinstance(result, dict) or not isinstance(result.get("policy"), dict):
            raise SalesError("sales_invalid_response", 502)
        # Customer-facing policy excludes the tenant's complete staff roster.
        return {
            "mode": "erp_mcp",
            "version": result.get("version"),
            "policy": {k: v for k, v in result["policy"].items() if k != "ownerIds"},
            "reminders": "managed_by_sales_communication",
        }

    def appointment(self, value: Any, customer_id: str, lead_id: str) -> SalesAppointment:
        try:
            row = SalesAppointment.model_validate(value)
            if (
                str(row.tenantId) != self.client.settings.sales_tenant_id
                or str(row.customerId) != customer_id
                or str(row.leadId) != lead_id
                or row.startTime.tzinfo is None
                or row.endTime.tzinfo is None
                or row.endTime <= row.startTime
            ):
                raise ValueError("identity or time mismatch")
            ZoneInfo(row.timezone)
            ZoneInfo(row.userTimezone)
            return row
        except (ValidationError, ValueError, KeyError) as exc:
            raise SalesError("sales_invalid_appointment", 502, uncertain=True) from exc

    @staticmethod
    def public(row: SalesAppointment) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "version": row.version,
            "status": row.status.lower(),
            "starts_at_utc": row.startTime.isoformat(),
            "ends_at_utc": row.endTime.isoformat(),
            "customer_timezone": row.userTimezone,
            "customer_display_time": row.startTime.astimezone(
                ZoneInfo(row.userTimezone)
            ).isoformat(),
            # Preserve the meaning of this legacy chat-state field even when
            # the Sales tenant operates in a different business timezone.
            "houston_display_time": row.startTime.astimezone(
                ZoneInfo("America/Chicago")
            ).isoformat(),
            "business_display_time": row.startTime.astimezone(ZoneInfo(row.timezone)).isoformat(),
            "business_timezone": row.timezone,
            "csr_id": row.ownerId,
            "csr_name": "Assigned Sales specialist" if row.ownerId else "Sales team",
            "offer_expires_at": row.offerExpiresAt.isoformat() if row.offerExpiresAt else None,
            "reminders_enabled": False,
            "source": "erp_sales",
        }

    async def appointments(self, thread_id: UUID) -> list[dict[str, Any]]:
        profile = await self.profile(thread_id)
        if not profile.get("lead"):
            return []
        customer_id, lead_id = await self.lead(thread_id, profile=profile)
        data = await self.client.call("sales_consultation_list", {"leadId": lead_id, "limit": 100})
        if not isinstance(data, list):
            raise SalesError("sales_invalid_response", 502)
        rows = [self.appointment(item, customer_id, lead_id) for item in data]
        for row in rows:
            await self.sync(thread_id, row)
        return [self.public(row) for row in rows]

    async def availability(self, thread_id: UUID, day: date, timezone: str) -> list[dict[str, Any]]:
        _, lead_id = await self.lead(thread_id)
        # The selected date is in the customer's timezone. Sales alone applies
        # opening hours, duration, weekdays, notice, horizon and owner eligibility.
        start = datetime.combine(day, time(), ZoneInfo(timezone)).astimezone(UTC)
        start = max(start, datetime.now(UTC))
        value = await self.client.call(
            "sales_consultation_availability",
            {
                "leadId": lead_id,
                "startTime": start.isoformat(),
                "userTimezone": timezone,
                "limit": 20,
            },
        )
        try:
            slots = []
            for item in value["slots"]:
                when = datetime.fromisoformat(item["startTime"].replace("Z", "+00:00"))
                if when.tzinfo is None or not isinstance(item["ownerId"], str):
                    raise ValueError("invalid slot")
                slots.append(
                    {
                        "starts_at": when.isoformat(),
                        "customer_time": when.astimezone(ZoneInfo(timezone)).isoformat(),
                        "csr_id": item["ownerId"],
                        "csr_name": "Sales specialist",
                    }
                )
            return slots
        except (KeyError, ValueError, TypeError) as exc:
            raise SalesError("sales_invalid_response", 502) from exc

    async def mutate(
        self,
        thread_id: UUID,
        request_id: UUID,
        action: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        customer_id, lead_id = await self.lead(thread_id, create=action == "schedule")
        if action == "schedule":
            arguments = {**arguments, "leadId": lead_id}
        elif "consultationId" not in arguments:
            raise SalesError("sales_appointment_required", 422)
        tool = f"sales_consultation_{action}"
        arguments = {**arguments, "requestId": str(request_id)}
        identity = {
            "tenant": self.client.settings.sales_tenant_id,
            "actor": self.client.settings.sales_actor_id,
            "customer": customer_id,
            "tool": tool,
            "arguments": arguments,
        }
        async with self.sessions() as session:
            receipt = await session.get(WorkflowRecord, request_id)
            if receipt:
                if (
                    receipt.thread_id != thread_id
                    or receipt.kind != "sales_operation"
                    or receipt.payload.get("request") != identity
                ):
                    raise SalesError("sales_request_id_reused", 409)
                if receipt.status == "rejected":
                    raise SalesError(receipt.payload["error"], receipt.payload["http_status"])
            else:
                pending = (
                    (
                        await session.execute(
                            select(WorkflowRecord).where(
                                WorkflowRecord.thread_id == thread_id,
                                WorkflowRecord.kind == "sales_operation",
                                WorkflowRecord.status == "pending",
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if pending:
                    raise SalesError("sales_pending_operation_retry_original_request", 409)
                if action == "schedule":
                    existing = await self.client.call(
                        "sales_consultation_list",
                        {
                            "leadId": lead_id,
                            "status": "Scheduled",
                            "limit": 1,
                            "from": datetime.now(UTC).isoformat(),
                        },
                    )
                    if not isinstance(existing, list):
                        raise SalesError("sales_invalid_response", 502)
                    if existing:
                        self.appointment(existing[0], customer_id, lead_id)
                        raise SalesError("sales_existing_appointment_refresh_required", 409)
                if action != "schedule":
                    current = self.appointment(
                        await self.client.call(
                            "sales_consultation_get",
                            {
                                "consultationId": arguments["consultationId"],
                            },
                        ),
                        customer_id,
                        lead_id,
                    )
                    if str(current.id) != arguments["consultationId"]:
                        raise SalesError("sales_invalid_appointment", 502)
                receipt = WorkflowRecord(
                    id=request_id,
                    thread_id=thread_id,
                    kind="sales_operation",
                    status="pending",
                    payload={"request": identity},
                )
                session.add(receipt)
                await session.commit()
            # Persist BEFORE sending. Replaying a pending call uses the identical
            # tool, arguments, tenant and actor, including expectedVersion.
            if receipt.status == "acknowledged":
                row = self.appointment(receipt.payload["result"], customer_id, lead_id)
            else:
                try:
                    result = await self.client.call(tool, arguments)
                    row = self.appointment(result, customer_id, lead_id)
                    if action != "schedule" and str(row.id) != arguments["consultationId"]:
                        raise SalesError("sales_invalid_appointment", 502, uncertain=True)
                except SalesError as exc:
                    if not exc.uncertain and exc.status in {404, 409, 422}:
                        receipt.status = "rejected"
                        receipt.payload = {
                            "request": identity,
                            "error": exc.code,
                            "http_status": exc.status,
                        }
                        session.add(receipt)
                        await session.commit()
                    raise
                receipt.status = "acknowledged"
                receipt.payload = {"request": identity, "result": row.model_dump(mode="json")}
                receipt.updated_at = utc_now()
                session.add(receipt)
                await session.commit()
        # A replay receipt is historical. Fetch current truth so a delayed
        # schedule response cannot resurrect an appointment canceled in Sales.
        current = self.appointment(
            await self.client.call(
                "sales_consultation_get",
                {
                    "consultationId": str(row.id),
                },
            ),
            customer_id,
            lead_id,
        )
        if current.id != row.id or current.version < row.version:
            raise SalesError("sales_invalid_appointment", 502)
        await self.sync(thread_id, current)
        return self.public(current)

    async def sync(self, thread_id: UUID, row: SalesAppointment) -> None:
        from bookcraft.api.chat import ConsultationConfirmedRequest
        from bookcraft.services.chat import ChatService

        async with self.sessions() as session:
            thread = await session.get(ThreadRecord, thread_id, with_for_update=True)
            if thread is None or thread.deleted_at is not None:
                raise SalesError("thread_not_accessible", 404)
            state = ThreadState.model_validate(thread.state)
            if row.status == "Scheduled":
                view = self.public(row)
                ChatService._apply_external_consultation_confirmation(
                    state,
                    ConsultationConfirmedRequest(
                        thread_id=thread_id,
                        appointment_id=str(row.id),
                        csr_name=view["csr_name"],
                        customer_timezone=row.userTimezone,
                        customer_display_time=view["customer_display_time"],
                        houston_display_time=view["houston_display_time"],
                    ),
                    str(row.id),
                )
            elif row.status in {"Canceled", "Completed"}:
                ChatService._apply_external_consultation_cancellation(state, str(row.id))
            # Proposed is explicitly not a booking.
            updated = state.model_dump(mode="json")
            if updated != thread.state:
                thread.state, thread.version, thread.updated_at = (
                    updated,
                    thread.version + 1,
                    utc_now(),
                )
                session.add(thread)
                await session.commit()

    async def callback(self, thread_id: UUID, appointment_id: str | None, status: str) -> None:
        if appointment_id is None:
            raise SalesError("sales_appointment_required", 422)
        try:
            normalized = str(UUID(appointment_id))
        except ValueError as exc:
            raise SalesError("sales_appointment_required", 422) from exc
        customer_id, lead_id = await self.lead(thread_id)
        row = self.appointment(
            await self.client.call(
                "sales_consultation_get",
                {
                    "consultationId": normalized,
                },
            ),
            customer_id,
            lead_id,
        )
        if str(row.id) != normalized or row.status != status:
            raise SalesError("sales_callback_state_mismatch", 409)
        await self.sync(thread_id, row)
