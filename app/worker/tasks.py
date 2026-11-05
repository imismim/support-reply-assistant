import logging
from datetime import UTC, datetime

from arq import Retry
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Draft, OutboundReply, Ticket, TicketStatus
from app.queue import get_redis_settings
from app.services.kb import search_relevant_articles
from app.services.llm import LLMClient
from app.services.rate_limiter import acquire_llm_slot
from app.services.reply_sender import HelpdeskDeliveryError, send_reply_to_helpdesk

logger = logging.getLogger(__name__)


async def generate_draft(ctx: dict, ticket_id: int) -> None:
    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None or ticket.status != TicketStatus.received:
            # Already processed by an earlier/duplicate run of this job.
            return
        ticket.status = TicketStatus.drafting
        await session.commit()

    articles = await search_relevant_articles(ticket.message, limit=3)

    await acquire_llm_slot(ctx["redis"], settings.llm_rate_limit_per_minute)
    draft_content = await ctx["llm_client"].generate_draft(ticket.message, articles)

    async with AsyncSessionLocal() as session:
        insert_stmt = (
            pg_insert(Draft)
            .values(
                ticket_id=ticket_id,
                content=draft_content,
                cited_article_ids=[article.id for article in articles],
            )
            .on_conflict_do_nothing(index_elements=["ticket_id"])
        )
        await session.execute(insert_stmt)

        ticket = await session.get(Ticket, ticket_id)
        ticket.status = TicketStatus.pending_review
        await session.commit()


async def send_reply(ctx: dict, ticket_id: int) -> None:
    async with AsyncSessionLocal() as session:
        outbound = await session.execute(
            select(OutboundReply).where(OutboundReply.ticket_id == ticket_id).with_for_update()
        )
        outbound = outbound.scalar_one()

        if outbound.status == "sent":
            # A previous attempt already succeeded; never send twice.
            return

        outbound.status = "sending"
        outbound.attempt_count += 1
        await session.commit()

        ticket = await session.get(Ticket, ticket_id)
        draft = await session.execute(select(Draft).where(Draft.ticket_id == ticket_id))
        draft = draft.scalar_one()
        reply_url, ticket_ext_id, message = ticket.reply_url, ticket.ticket_id, draft.content

    try:
        await send_reply_to_helpdesk(reply_url, ticket_ext_id, message)
    except HelpdeskDeliveryError as exc:
        async with AsyncSessionLocal() as session:
            outbound = await session.get(OutboundReply, outbound.id)
            outbound.last_error = str(exc)[:2000]
            job_try = ctx["job_try"]
            if job_try >= settings.helpdesk_max_retries:
                outbound.status = "failed"
                ticket = await session.get(Ticket, ticket_id)
                ticket.status = TicketStatus.failed
                await session.commit()
                logger.error("Giving up sending reply for ticket %s after %s tries", ticket_id, job_try)
                return
            outbound.status = "pending"
            await session.commit()
        raise Retry(defer=min(60, 2**job_try))

    async with AsyncSessionLocal() as session:
        outbound = await session.get(OutboundReply, outbound.id)
        outbound.status = "sent"
        outbound.sent_at = datetime.now(UTC)
        ticket = await session.get(Ticket, ticket_id)
        ticket.status = TicketStatus.sent
        await session.commit()


async def startup(ctx: dict) -> None:
    ctx["llm_client"] = LLMClient()


async def shutdown(ctx: dict) -> None:
    pass


class WorkerSettings:
    functions = [generate_draft, send_reply]
    redis_settings: RedisSettings = get_redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
