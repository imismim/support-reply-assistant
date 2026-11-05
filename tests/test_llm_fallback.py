from unittest.mock import AsyncMock

from app.services import llm as llm_module
from app.services.llm import LLMClient


async def test_fallback_used_when_no_api_key(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "anthropic_api_key", None)

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(llm_module.asyncio, "sleep", fake_sleep)

    client = LLMClient()
    client._client.messages.create = AsyncMock(side_effect=Exception("401 unauthorized"))

    result = await client.generate_draft("Не можу експортувати звіт у PDF", articles=[])

    assert result == llm_module.FALLBACK_DRAFT
    client._client.messages.create.assert_awaited_once()
    assert len(sleep_calls) == 1
    assert 5 <= sleep_calls[0] <= 30


async def test_real_model_called_when_api_key_present(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "anthropic_api_key", "sk-ant-test-key")

    client = LLMClient()
    fake_response = AsyncMock()
    fake_response.content = [type("Block", (), {"text": "готова відповідь"})()]
    client._client.messages.create = AsyncMock(return_value=fake_response)

    result = await client.generate_draft("Не можу експортувати звіт у PDF", articles=[])

    assert result == "готова відповідь"
    client._client.messages.create.assert_awaited_once()
