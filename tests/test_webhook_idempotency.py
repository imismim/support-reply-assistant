import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import get_session
from app.main import app
from app.models import Ticket, WebhookEvent
from app.queue import get_arq_pool


class FakeArqPool:
    def __init__(self) -> None:
        self.enqueued: list[tuple] = []

    async def enqueue_job(self, function: str, *args, **kwargs) -> None:
        self.enqueued.append((function, args, kwargs))


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
