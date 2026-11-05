from arq import ArqRedis
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Ticket, TicketStatus, WebhookEvent
from app.queue import get_arq_pool
from app.schemas import HelpdeskWebhookPayload

router = APIRouter()


@router.post("/webhooks/helpdesk")
async def receive_helpdesk_webhook(
    payload: HelpdeskWebhookPayload,
    session: AsyncSession = Depends(get_session),
    arq_pool: ArqRedis = Depends(get_arq_pool),
) -> dict:
    # Insert-if-new on event_id: a duplicate or concurrently-racing delivery
    # of the same event simply inserts nothing the second time, atomically,
    # thanks to the primary key constraint on webhook_events.event_id.
    event_insert = (
        pg_insert(WebhookEvent)
        .values(
            event_id=payload.event_id,
            ticket_id=payload.ticket_id,
            payload=payload.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    await session.execute(event_insert)

    ticket_insert = (
        pg_insert(Ticket)
        .values(
            ticket_id=payload.ticket_id,
            customer_email=payload.customer_email,
            message=payload.message,
            reply_url=payload.reply_url,
            status=TicketStatus.received,
        )
        .on_conflict_do_nothing(index_elements=["ticket_id"])
    )
    await session.execute(ticket_insert)
    await session.commit()

    # Enqueue based on ticket state, not on whether this delivery was the first:
    # if a previous delivery committed but crashed (or Redis was down) before
    # enqueueing, the helpdesk's retry is our only chance to schedule the
    # draft, and gating on "new event" would drop the ticket forever.
    # Redundant enqueues are collapsed atomically by arq's per-ticket job id.
    ticket = await session.scalar(select(Ticket).where(Ticket.ticket_id == payload.ticket_id))
    if ticket.status in (TicketStatus.received, TicketStatus.drafting):
        await arq_pool.enqueue_job("generate_draft", ticket.id, _job_id=f"draft:{ticket.id}")

    return {"status": "accepted"}
