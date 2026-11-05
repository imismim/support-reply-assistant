import asyncio
import logging
import random

from arq import ArqRedis
from fastapi import APIRouter, Depends, Header, Response

from app.queue import get_arq_pool
from app.schemas import HelpdeskReplyPayload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mock-helpdesk", tags=["mock-helpdesk"])

# There's no real helpdesk for this task, so this simulates the one described
# in the spec: occasionally slow or failing, but otherwise returns 201.
_FAIL_PROBABILITY = 0.2
_SLOW_PROBABILITY = 0.1
_IDEMPOTENCY_TTL_SECONDS = 24 * 3600


@router.post("/tickets/{ticket_id}/replies", status_code=201)
async def receive_reply(
    ticket_id: str,
    payload: HelpdeskReplyPayload,
    idempotency_key: str | None = Header(default=None),
    redis: ArqRedis = Depends(get_arq_pool),
) -> Response:
    roll = random.random()

    if roll < _FAIL_PROBABILITY:
        return Response(status_code=random.choice([500, 502, 503]))

    # The reply counts as delivered before the slow path below, which models
    # the hard case: the helpdesk stored it but the client timed out waiting.
    if idempotency_key:
        is_first = await redis.set(
            f"mock_helpdesk:idem:{idempotency_key}", "1", nx=True, ex=_IDEMPOTENCY_TTL_SECONDS
        )
        if not is_first:
            logger.info("Mock helpdesk: duplicate reply for %s ignored (key %s)", ticket_id, idempotency_key)
            return Response(status_code=201)

    logger.info("Mock helpdesk: reply delivered to customer for %s", ticket_id)

    if roll < _FAIL_PROBABILITY + _SLOW_PROBABILITY:
        await asyncio.sleep(15)  # longer than the client's configured timeout

    return Response(status_code=201)
