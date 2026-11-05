import asyncio
import time

from redis.asyncio import Redis


async def acquire_llm_slot(redis: Redis, limit_per_minute: int) -> None:
    """Block until a service-wide LLM call slot is available this minute.

    Uses a fixed 60s window counted in Redis so the limit is shared across
    every worker process, not just the current one. If the current window is
    full, give back the slot we tentatively took and sleep until the next
    window starts before trying again.
    """
    while True:
        now = time.time()
        window = int(now // 60)
        key = f"llm_rate:{window}"

        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 65)

        if count <= limit_per_minute:
            return

        await redis.decr(key)
        sleep_for = 60 - (now % 60) + 0.05
        await asyncio.sleep(sleep_for)
