import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import get_session
from app.main import app
from app.models import Ticket, TicketStatus, WebhookEvent
from app.queue import get_arq_pool


class FakeArqPool:
    """Mirrors arq's contract: a second enqueue with an existing _job_id is
    a no-op returning None (arq does this atomically via WATCH/MULTI)."""

    def __init__(self) -> None:
        self.enqueued: list[tuple] = []
        self.fail_next = False

    async def enqueue_job(self, function: str, *args, _job_id: str | None = None, **kwargs):
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("redis unavailable")
        if _job_id and any(job[2].get("_job_id") == _job_id for job in self.enqueued):
            return None
        self.enqueued.append((function, args, {"_job_id": _job_id, **kwargs}))
        return object()


def _payload(event_id: str, ticket_id: str = "T-1042") -> dict:
    return {
        "event_id": event_id,
        "ticket_id": ticket_id,
        "customer_email": "user@example.com",
        "message": "Не можу експортувати звіт у PDF",
        "reply_url": "https://helpdesk.example.com/tickets/T-1042/replies",
    }


@pytest.fixture
def fake_pool(session_factory):
    pool = FakeArqPool()

    async def _get_session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session_override
    app.dependency_overrides[get_arq_pool] = lambda: pool
    yield pool
    app.dependency_overrides.clear()


async def test_duplicate_event_is_only_enqueued_once(fake_pool, session_factory):
    payload = _payload("evt_dup_1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/webhooks/helpdesk", json=payload)
        r2 = await client.post("/webhooks/helpdesk", json=payload)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(fake_pool.enqueued) == 1

    async with session_factory() as session:
        events = (await session.execute(select(WebhookEvent))).scalars().all()
        tickets = (await session.execute(select(Ticket))).scalars().all()
    assert len(events) == 1
    assert len(tickets) == 1


async def test_concurrent_duplicate_delivery_is_deduped(fake_pool, session_factory):
    payload = _payload("evt_concurrent_1", ticket_id="T-2042")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(*[client.post("/webhooks/helpdesk", json=payload) for _ in range(5)])

    assert all(r.status_code == 200 for r in responses)
    assert len(fake_pool.enqueued) == 1

    async with session_factory() as session:
        events = (await session.execute(select(WebhookEvent))).scalars().all()
    assert len(events) == 1


async def test_retry_after_failed_enqueue_still_schedules_draft(fake_pool, session_factory):
    payload = _payload("evt_redis_down", ticket_id="T-3042")
    fake_pool.fail_next = True

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        first = await client.post("/webhooks/helpdesk", json=payload)
        retry = await client.post("/webhooks/helpdesk", json=payload)

    assert first.status_code == 500
    assert retry.status_code == 200
    assert [job[0] for job in fake_pool.enqueued] == ["generate_draft"]


async def test_redelivery_after_draft_is_ready_does_not_reenqueue(fake_pool, session_factory):
    payload = _payload("evt_late_dup", ticket_id="T-4042")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/webhooks/helpdesk", json=payload)
        async with session_factory() as session:
            ticket = await session.scalar(select(Ticket).where(Ticket.ticket_id == "T-4042"))
            ticket.status = TicketStatus.pending_review
            await session.commit()
        fake_pool.enqueued.clear()
        await client.post("/webhooks/helpdesk", json=payload)

    assert fake_pool.enqueued == []
