from app.services import rate_limiter as rl


class FakeClock:
    def __init__(self, start: float) -> None:
        self.now = start

    def time(self) -> float:
        return self.now


async def test_blocks_after_limit_then_allows_next_window(redis_client, monkeypatch):
    clock = FakeClock(1_000_000.0)

    async def fake_sleep(seconds: float) -> None:
        clock.now += seconds

    monkeypatch.setattr(rl.time, "time", clock.time)
    monkeypatch.setattr(rl.asyncio, "sleep", fake_sleep)

    limit = 2
    for _ in range(limit):
        await rl.acquire_llm_slot(redis_client, limit)

    window_before = int(clock.now // 60)
    count_before = int(await redis_client.get(f"llm_rate:{window_before}"))
    assert count_before == limit

    # This call is over the limit for the current window, so it must sleep
    # (fast-forwarding the fake clock into the next window) before returning.
    await rl.acquire_llm_slot(redis_client, limit)

    window_after = int(clock.now // 60)
    assert window_after > window_before
    count_after = int(await redis_client.get(f"llm_rate:{window_after}"))
    assert count_after == 1
