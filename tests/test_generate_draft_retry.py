from unittest.mock import AsyncMock

import pytest
from arq import Retry

from app.models import Draft, Ticket, TicketStatus
from app.worker import tasks


async def _create_ticket(session_factory) -> int:
    async with session_factory() as session:
        ticket = Ticket(
            ticket_id="T-2001",
            customer_email="user@example.com",
            message="Не можу експортувати звіт у PDF",
            reply_url="https://helpdesk.example.com/tickets/T-2001/replies",
        )
        session.add(ticket)
        await session.commit()
        return ticket.id


@pytest.fixture
def no_kb_search(monkeypatch, session_factory):
    monkeypatch.setattr(tasks, "search_relevant_articles", AsyncMock(return_value=[]))
    # The app-wide engine keeps pooled connections bound to the event loop of
    # whichever test used it first; a per-test factory avoids cross-loop reuse.
    monkeypatch.setattr(tasks, "AsyncSessionLocal", session_factory)


async def _status(session_factory, ticket_id: int) -> TicketStatus:
    async with session_factory() as session:
        return (await session.get(Ticket, ticket_id)).status


async def test_llm_error_retries_and_resumes_from_drafting(session_factory, redis_client, no_kb_search):
    ticket_id = await _create_ticket(session_factory)
    llm = AsyncMock()
    llm.generate_draft.side_effect = RuntimeError("503 UNAVAILABLE")
    ctx = {"redis": redis_client, "llm_client": llm, "job_try": 1}

    with pytest.raises(Retry):
        await tasks.generate_draft(ctx, ticket_id)

    assert await _status(session_factory, ticket_id) == TicketStatus.drafting

    llm.generate_draft.side_effect = None
    llm.generate_draft.return_value = "готова відповідь"
    await tasks.generate_draft({**ctx, "job_try": 2}, ticket_id)

    assert await _status(session_factory, ticket_id) == TicketStatus.pending_review
    async with session_factory() as session:
        draft = await session.get(Draft, 1)
        assert draft.content == "готова відповідь"


async def test_llm_error_marks_failed_after_max_retries(session_factory, redis_client, no_kb_search, monkeypatch):
    monkeypatch.setattr(tasks.settings, "llm_max_retries", 3)
    ticket_id = await _create_ticket(session_factory)
    llm = AsyncMock()
    llm.generate_draft.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")

    await tasks.generate_draft({"redis": redis_client, "llm_client": llm, "job_try": 3}, ticket_id)

    assert await _status(session_factory, ticket_id) == TicketStatus.failed


async def test_ticket_left_in_drafting_by_crashed_worker_is_resumed(session_factory, redis_client, no_kb_search):
    ticket_id = await _create_ticket(session_factory)
    async with session_factory() as session:
        (await session.get(Ticket, ticket_id)).status = TicketStatus.drafting
        await session.commit()
    llm = AsyncMock()
    llm.generate_draft.return_value = "відповідь після рестарту"

    await tasks.generate_draft({"redis": redis_client, "llm_client": llm, "job_try": 1}, ticket_id)

    assert await _status(session_factory, ticket_id) == TicketStatus.pending_review


async def test_already_reviewed_ticket_is_not_redrafted(session_factory, redis_client, no_kb_search):
    ticket_id = await _create_ticket(session_factory)
    async with session_factory() as session:
        (await session.get(Ticket, ticket_id)).status = TicketStatus.pending_review
        await session.commit()
    llm = AsyncMock()

    await tasks.generate_draft({"redis": redis_client, "llm_client": llm, "job_try": 1}, ticket_id)

    llm.generate_draft.assert_not_called()
