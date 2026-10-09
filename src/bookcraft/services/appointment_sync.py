"""Atomic local booking cancellation from authenticated CSR callbacks."""

from uuid import UUID

from bookcraft.components.consultations.repository import ConsultationRepository
from bookcraft.components.storage.models import SalesConsultationRecord, ThreadRecord, utc_now
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.domain.state import ThreadState


async def cancel_persisted_appointment(
    repository: ThreadRepository,
    thread_id: UUID,
    appointment_id: str | None,
    expected_version: int | None,
) -> bool:
    """Caller holds the thread mutation lock; state and delivery commit together."""
    from bookcraft.services.chat import ChatService

    async with repository.session_factory() as session:
        thread = await session.get(ThreadRecord, thread_id, with_for_update=True)
        if thread is None or thread.deleted_at is not None:
            return False
        state = ThreadState.model_validate(thread.state)
        target = appointment_id or state.sales_actions.consultation.confirmed_appointment_id
        try:
            identifier = UUID(str(target))
        except ValueError:
            identifier = None
        record = await session.get(SalesConsultationRecord, identifier) if identifier else None
        if record is not None:
            if record.thread_id != thread_id:
                return False
            # A delayed legacy callback cannot cancel a newer rescheduled version.
            if record.version != (expected_version if expected_version is not None else 1):
                return False
        if not ChatService._apply_external_consultation_cancellation(state, appointment_id):
            return False
        if record is not None and record.status != "cancelled":
            record.status, record.cancelled_at = "cancelled", utc_now()
            record.version += 1
            record.updated_at = utc_now()
            session.add(record)
            ConsultationRepository(repository.session_factory, enqueue_csr_sync=True)._enqueue(
                session, record
            )
        thread.state = state.model_dump(mode="json")
        thread.version += 1
        thread.updated_at = utc_now()
        session.add(thread)
        await session.commit()
        return True
