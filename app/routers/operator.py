from arq import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models import KBArticle, OutboundReply, Ticket, TicketStatus
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
    article_titles = dict((await session.execute(select(KBArticle.id, KBArticle.title))).all())
    return templates.TemplateResponse(
        request, "tickets.html", {"tickets": tickets, "article_titles": article_titles}
    )


@router.post("/tickets/{ticket_id}/approve")
async def approve_ticket(
    ticket_id: int,
    session: AsyncSession = Depends(get_session),
    arq_pool: ArqRedis = Depends(get_arq_pool),
):
    ticket = await session.get(Ticket, ticket_id)
    # "approved" is accepted too, so a repeated click can re-enqueue delivery
    # if the first request committed but failed before reaching Redis.
    if ticket is None or ticket.status not in (TicketStatus.pending_review, TicketStatus.approved):
        raise HTTPException(409, "Ticket is not awaiting review")

    ticket.status = TicketStatus.approved

    outbound_insert = (
        pg_insert(OutboundReply)
        .values(ticket_id=ticket_id, status="pending")
        .on_conflict_do_nothing(index_elements=["ticket_id"])
    )
    await session.execute(outbound_insert)
    await session.commit()

    # Double clicks collapse on arq's job id, and send_reply itself refuses to
    # send once the outbound row is "sent".
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
