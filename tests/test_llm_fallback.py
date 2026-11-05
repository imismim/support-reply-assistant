from unittest.mock import AsyncMock

import pytest

from app.services import llm as llm_module
from app.services.llm import AnthropicProvider, GeminiProvider, LLMClient


@pytest.fixture
def sleep_calls(monkeypatch) -> list[float]:
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(llm_module.asyncio, "sleep", fake_sleep)
    return calls


async def test_gemini_fallback_used_when_no_api_key(sleep_calls):
    provider = GeminiProvider(api_key=None, model="gemini-3.8-flash")
    provider.client.aio.models.generate_content = AsyncMock(side_effect=Exception("400 API key not valid"))

    result = await LLMClient(provider).generate_draft("Не можу експортувати звіт у PDF", articles=[])

    assert result == llm_module.FALLBACK_DRAFT
    provider.client.aio.models.generate_content.assert_awaited_once()
    assert len(sleep_calls) == 1
    assert 5 <= sleep_calls[0] <= 30


async def test_gemini_called_when_api_key_present(sleep_calls):
    provider = GeminiProvider(api_key="test-key", model="gemini-3.8-flash")
    fake_response = type("Response", (), {"text": "готова відповідь", "candidates": None})()
    provider.client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    result = await LLMClient(provider).generate_draft("Не можу експортувати звіт у PDF", articles=[])

    assert result == "готова відповідь"
    call = provider.client.aio.models.generate_content.await_args
    assert call.kwargs["model"] == "gemini-3.8-flash"
    assert call.kwargs["config"].system_instruction == llm_module.SYSTEM_PROMPT
    assert sleep_calls == []


async def test_gemini_empty_response_raises():
    provider = GeminiProvider(api_key="test-key", model="gemini-3.8-flash")
    fake_response = type("Response", (), {"text": None, "candidates": None})()
    provider.client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    with pytest.raises(RuntimeError):
        await LLMClient(provider).generate_draft("Не можу експортувати звіт у PDF", articles=[])


async def test_anthropic_fallback_used_when_no_api_key(sleep_calls):
    provider = AnthropicProvider(api_key=None, model="claude-3-5-haiku-20241022")
    provider.client.messages.create = AsyncMock(side_effect=Exception("401 unauthorized"))

    result = await LLMClient(provider).generate_draft("Не можу експортувати звіт у PDF", articles=[])

    assert result == llm_module.FALLBACK_DRAFT
    provider.client.messages.create.assert_awaited_once()
    assert len(sleep_calls) == 1


async def test_anthropic_called_when_api_key_present():
    provider = AnthropicProvider(api_key="sk-ant-test-key", model="claude-3-5-haiku-20241022")
    fake_response = AsyncMock()
    fake_response.content = [type("Block", (), {"text": "готова відповідь"})()]
    provider.client.messages.create = AsyncMock(return_value=fake_response)

    result = await LLMClient(provider).generate_draft("Не можу експортувати звіт у PDF", articles=[])

    assert result == "готова відповідь"
    provider.client.messages.create.assert_awaited_once()


@pytest.mark.parametrize(
    ("provider_name", "expected_cls"),
    [("gemini", GeminiProvider), ("anthropic", AnthropicProvider)],
)
def test_provider_selected_from_settings(monkeypatch, provider_name, expected_cls):
    monkeypatch.setattr(llm_module.settings, "llm_provider", provider_name)

    assert isinstance(LLMClient().provider, expected_cls)
