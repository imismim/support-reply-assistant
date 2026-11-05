import httpx
import pytest

from app.services.reply_sender import HelpdeskDeliveryError, send_reply_to_helpdesk


def _patch_transport(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr("app.services.reply_sender.httpx.AsyncClient", patched_client)


async def test_raises_on_5xx(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(503))

    with pytest.raises(HelpdeskDeliveryError):
        await send_reply_to_helpdesk("https://helpdesk.example.com/tickets/T-1/replies", "T-1", "hello", idempotency_key="reply-1")


async def test_raises_on_unexpected_status(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(200))

    with pytest.raises(HelpdeskDeliveryError):
        await send_reply_to_helpdesk("https://helpdesk.example.com/tickets/T-1/replies", "T-1", "hello", idempotency_key="reply-1")


async def test_succeeds_on_201(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(201))

    await send_reply_to_helpdesk("https://helpdesk.example.com/tickets/T-1/replies", "T-1", "hello", idempotency_key="reply-1")


async def test_sends_idempotency_key(monkeypatch):
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("Idempotency-Key")
        return httpx.Response(201)

    _patch_transport(monkeypatch, handler)

    await send_reply_to_helpdesk("https://helpdesk.example.com/tickets/T-1/replies", "T-1", "hello", idempotency_key="reply-7")

    assert seen["key"] == "reply-7"
