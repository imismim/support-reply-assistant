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
    result = await session.execute(event_insert)
    is_new_event = result.rowcount > 0

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

    if is_new_event:
        ticket = await session.scalar(select(Ticket).where(Ticket.ticket_id == payload.ticket_id))
        await arq_pool.enqueue_job("generate_draft", ticket.id, _job_id=f"draft:{payload.event_id}")

    return {"status": "accepted"}
