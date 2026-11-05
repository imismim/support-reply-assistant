import asyncio
import random

from fastapi import APIRouter, Response

from app.schemas import HelpdeskReplyPayload

router = APIRouter(prefix="/mock-helpdesk", tags=["mock-helpdesk"])

# There's no real helpdesk for this task, so this simulates the one described
# in the spec: occasionally slow or failing, but otherwise returns 201.
_FAIL_PROBABILITY = 0.2
_SLOW_PROBABILITY = 0.1


@router.post("/tickets/{ticket_id}/replies", status_code=201)
async def receive_reply(ticket_id: str, payload: HelpdeskReplyPayload) -> Response:
    roll = random.random()

    if roll < _FAIL_PROBABILITY:
        return Response(status_code=random.choice([500, 502, 503]))

    if roll < _FAIL_PROBABILITY + _SLOW_PROBABILITY:
        await asyncio.sleep(15)  # longer than the client's configured timeout

    return Response(status_code=201)
