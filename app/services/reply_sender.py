import httpx

from app.config import settings


class HelpdeskDeliveryError(Exception):
    pass


async def send_reply_to_helpdesk(reply_url: str, ticket_id: str, message: str, idempotency_key: str) -> None:
    # A timeout doesn't tell us whether the helpdesk stored the reply, so the
    # retry carries the same key and the helpdesk drops it if it already has it.
    headers = {"Idempotency-Key": idempotency_key}
    try:
        async with httpx.AsyncClient(timeout=settings.helpdesk_timeout_seconds) as client:
            response = await client.post(
                reply_url, json={"ticket_id": ticket_id, "message": message}, headers=headers
            )
    except httpx.HTTPError as exc:
        raise HelpdeskDeliveryError(f"request failed: {exc}") from exc

    if response.status_code != 201:
        raise HelpdeskDeliveryError(f"unexpected status {response.status_code}: {response.text[:200]}")
