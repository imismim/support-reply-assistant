import logging
from datetime import UTC, datetime, timedelta

from arq import Retry, cron
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
        # "drafting" is resumable: a worker that crashed mid-call leaves the
        # ticket there, and arq (or the recovery cron) re-runs this job. Only
        # one job per ticket can exist thanks to the "draft:{id}" job id.
        if ticket is None or ticket.status not in (TicketStatus.received, TicketStatus.drafting):
            return
        ticket.status = TicketStatus.drafting
        await session.commit()

    articles = await search_relevant_articles(ticket.message, limit=3)

    await acquire_llm_slot(ctx["redis"], settings.llm_rate_limit_per_minute)
    try:
        draft_content = await ctx["llm_client"].generate_draft(ticket.message, articles)
    except Exception:
        # Free-tier LLMs return 429/503 often enough that this is a normal
        # path, not an edge case.
        job_try = ctx["job_try"]
        if job_try >= settings.llm_max_retries:
            async with AsyncSessionLocal() as session:
                ticket = await session.get(Ticket, ticket_id)
                ticket.status = TicketStatus.failed
                await session.commit()
            logger.exception("Giving up generating draft for ticket %s after %s tries", ticket_id, job_try)
            return
        logger.warning("Draft generation failed for ticket %s (try %s), retrying", ticket_id, job_try, exc_info=True)
        raise Retry(defer=min(60, 5 * 2**job_try))

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
        # The row lock is held across the HTTP call on purpose: a concurrent
        # attempt blocks here until this one commits, then sees "sent".
        # Releasing it before the call would let two attempts send in parallel.
        outbound = await session.scalar(
            select(OutboundReply).where(OutboundReply.ticket_id == ticket_id).with_for_update()
        )
        if outbound is None or outbound.status in ("sent", "failed"):
            return

        ticket = await session.get(Ticket, ticket_id)
        draft = await session.scalar(select(Draft).where(Draft.ticket_id == ticket_id))
        outbound.attempt_count += 1

        try:
            # Stable per reply, so a retry after "helpdesk stored it but we
            # timed out" is recognised as a duplicate on the helpdesk side.
            await send_reply_to_helpdesk(
                ticket.reply_url, ticket.ticket_id, draft.content, idempotency_key=f"reply-{outbound.id}"
            )
        except HelpdeskDeliveryError as exc:
            outbound.last_error = str(exc)[:2000]
            job_try = ctx["job_try"]
            if job_try >= settings.helpdesk_max_retries:
                outbound.status = "failed"
                ticket.status = TicketStatus.failed
                await session.commit()
                logger.error("Giving up sending reply for ticket %s after %s tries", ticket_id, job_try)
                return
            await session.commit()
            raise Retry(defer=min(60, 2**job_try))

        outbound.status = "sent"
        outbound.sent_at = datetime.now(UTC)
        outbound.last_error = None
        ticket.status = TicketStatus.sent
        await session.commit()


async def requeue_stuck_tickets(ctx: dict) -> None:
    """Safety net for work whose job vanished: Redis lost it (restart without
    persistence, eviction) or an enqueue failed after the DB commit and no
    retry from the helpdesk/operator followed. Re-enqueueing a ticket that
    still has a live job is a no-op thanks to the per-ticket job id.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.stuck_ticket_after_seconds)
    async with AsyncSessionLocal() as session:
        to_draft = await session.scalars(
            select(Ticket.id).where(
                Ticket.status.in_([TicketStatus.received, TicketStatus.drafting]), Ticket.updated_at < cutoff
            )
        )
        to_send = await session.scalars(
            select(Ticket.id).where(Ticket.status == TicketStatus.approved, Ticket.updated_at < cutoff)
        )
        to_draft, to_send = list(to_draft), list(to_send)

    for ticket_id in to_draft:
        if await ctx["redis"].enqueue_job("generate_draft", ticket_id, _job_id=f"draft:{ticket_id}"):
            logger.warning("Re-enqueued draft generation for stuck ticket %s", ticket_id)
    for ticket_id in to_send:
        if await ctx["redis"].enqueue_job("send_reply", ticket_id, _job_id=f"send:{ticket_id}"):
            logger.warning("Re-enqueued reply delivery for stuck ticket %s", ticket_id)


async def startup(ctx: dict) -> None:
    ctx["llm_client"] = LLMClient()


async def shutdown(ctx: dict) -> None:
    pass


class WorkerSettings:
    functions = [generate_draft, send_reply]
    cron_jobs = [cron(requeue_stuck_tickets, second=0)]
    redis_settings: RedisSettings = get_redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    # arq's default (5) would silently cap both retry settings.
    max_tries = max(settings.helpdesk_max_retries, settings.llm_max_retries)
