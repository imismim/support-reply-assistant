from arq import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models import OutboundReply, Ticket, TicketStatus
from app.queue import get_arq_pool

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
async def list_tickets(request: Request, session: AsyncSession = Depends(get_session)):
    stmt = (
        select(Ticket)
        .options(selectinload(Ticket.draft), selectinload(Ticket.outbound_reply))
        .order_by(Ticket.created_at.desc())
    )
    tickets = (await session.execute(stmt)).scalars().all()
    return templates.TemplateResponse(request, "tickets.html", {"tickets": tickets})


@router.post("/tickets/{ticket_id}/approve")
async def approve_ticket(
    ticket_id: int,
    session: AsyncSession = Depends(get_session),
    arq_pool: ArqRedis = Depends(get_arq_pool),
):
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None or ticket.status != TicketStatus.pending_review:
        raise HTTPException(409, "Ticket is not awaiting review")

    ticket.status = TicketStatus.approved

    # Same insert-if-new idempotency pattern as the webhook: only the first
    # Approve click creates the outbound row and enqueues delivery.
    outbound_insert = (
        pg_insert(OutboundReply)
        .values(ticket_id=ticket_id, status="pending")
        .on_conflict_do_nothing(index_elements=["ticket_id"])
    )
    result = await session.execute(outbound_insert)
    await session.commit()

    if result.rowcount > 0:
        await arq_pool.enqueue_job("send_reply", ticket_id, _job_id=f"send:{ticket_id}")

    return RedirectResponse(url="/", status_code=303)


@router.post("/tickets/{ticket_id}/reject")
async def reject_ticket(ticket_id: int, session: AsyncSession = Depends(get_session)):
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None or ticket.status != TicketStatus.pending_review:
        raise HTTPException(409, "Ticket is not awaiting review")

    ticket.status = TicketStatus.rejected
    await session.commit()
    return RedirectResponse(url="/", status_code=303)
