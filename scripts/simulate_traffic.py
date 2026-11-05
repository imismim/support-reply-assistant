"""Sends sample helpdesk webhook events to a running instance of the service,
including a duplicate delivery of the same event sent concurrently, to
demonstrate that the webhook dedups correctly.

Run with: python -m scripts.simulate_traffic [base_url]
(default base_url: http://localhost:8000)
"""

import asyncio
import sys
import uuid

import httpx

SAMPLE_MESSAGES = [
    "Не можу експортувати звіт у PDF, кнопка просто не реагує.",
    "Як поділитися дашбордом з колегою, який ще не зареєстрований?",
    "Хочу отримувати звіт на email кожен понеділок, де це налаштувати?",
    "API повертає 429, хоча ми ще не досягли лімітів, у чому справа?",
    "Забув пароль, лист на відновлення не приходить вже 20 хвилин.",
]


def make_payload(base_url: str, ticket_id: str, message: str) -> dict:
    return {
        "event_id": f"evt_{uuid.uuid4().hex[:10]}",
        "ticket_id": ticket_id,
        "customer_email": "user@example.com",
        "message": message,
        "reply_url": f"{base_url}/mock-helpdesk/tickets/{ticket_id}/replies",
    }


async def send_tickets(base_url: str) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        for i, message in enumerate(SAMPLE_MESSAGES, start=1):
            ticket_id = f"T-{1000 + i}"
            payload = make_payload(base_url, ticket_id, message)
            resp = await client.post(f"{base_url}/webhooks/helpdesk", json=payload)
            print(f"{ticket_id}: {resp.status_code}")

        # Duplicate delivery of the same event, sent concurrently, to show
        # that dedup handles the race correctly (only one draft is generated).
        dup_payload = make_payload(base_url, "T-2042", "Не можу експортувати звіт у PDF.")
        results = await asyncio.gather(
            client.post(f"{base_url}/webhooks/helpdesk", json=dup_payload),
            client.post(f"{base_url}/webhooks/helpdesk", json=dup_payload),
            client.post(f"{base_url}/webhooks/helpdesk", json=dup_payload),
        )
        print("Concurrent duplicate delivery statuses:", [r.status_code for r in results])


if __name__ == "__main__":
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    asyncio.run(send_tickets(base_url))
