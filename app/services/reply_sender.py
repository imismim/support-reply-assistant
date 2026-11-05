import httpx

from app.config import settings


class HelpdeskDeliveryError(Exception):
    pass


async def send_reply_to_helpdesk(reply_url: str, ticket_id: str, message: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=settings.helpdesk_timeout_seconds) as client:
            response = await client.post(reply_url, json={"ticket_id": ticket_id, "message": message})
    except httpx.HTTPError as exc:
        raise HelpdeskDeliveryError(f"request failed: {exc}") from exc

    if response.status_code != 201:
        raise HelpdeskDeliveryError(f"unexpected status {response.status_code}: {response.text[:200]}")
