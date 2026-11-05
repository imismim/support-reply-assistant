import asyncio
import logging
import random
from typing import Protocol

from anthropic import AsyncAnthropic
from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.models import KBArticle

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ти — асистент служби підтримки продукту ReportFlow. Склади чернетку відповіді "
    "клієнту українською мовою; оператор надішле її від імені підтримки.\n"
    "Правила:\n"
    "- Відповідай по суті прямо в тексті: конкретні кроки й пояснення з наданих статей. "
    "Клієнт не повинен нічого шукати чи читати сам.\n"
    "- Ніколи не відсилай клієнта до бази знань, статей чи документації замість відповіді "
    "(жодних «перегляньте базу знань», «пошукайте серед статей», «детальніше читайте в статті»). "
    "Не згадуй статті й не цитуй їхні назви — це внутрішні матеріали підтримки.\n"
    "- Не вигадуй функціональність, якої немає в статтях. Якщо статті не відповідають на "
    "питання, чесно скажи, що передаєш його фахівцю, і попроси деталі, потрібні для розв'язання.\n"
    "- Пиши звичайним текстом без Markdown (без **, #, *); нумерований список 1. 2. 3. допустимий.\n"
    "- Поверни лише текст відповіді клієнту — без вступу, пояснень для оператора чи розділювачів."
)

# Used only when the selected provider's API key is not set, so the rest of
# the system (timeouts, UI states, latency handling) behaves the same with or
# without a real key, per the task spec.
FALLBACK_DRAFT = (
    "Дякуємо за звернення! Ми вже розбираємося з вашим питанням "
    "і найближчим часом повернемося з детальною відповіддю."
)


class LLMProvider(Protocol):
    has_key: bool

    async def complete(self, system: str, user: str, max_tokens: int) -> str: ...


class AnthropicProvider:
    def __init__(self, api_key: str | None, model: str) -> None:
        self.has_key = bool(api_key)
        self.model = model
        self.client = AsyncAnthropic(api_key=api_key or "sk-ant-no-key-placeholder")

    async def complete(self, system: str, user: str, max_tokens: int) -> str:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text


class GeminiProvider:
    def __init__(self, api_key: str | None, model: str) -> None:
        self.has_key = bool(api_key)
        self.model = model
        self.client = genai.Client(api_key=api_key or "no-key-placeholder")

    async def complete(self, system: str, user: str, max_tokens: int) -> str:
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=user,
            config=genai_types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        if not response.text:
            # Gemini returns no text when the answer is blocked by safety
            # filters or the token budget is exhausted by thinking; failing
            # loudly beats saving an empty draft for the operator.
            raise RuntimeError(f"Gemini returned no text (finish_reason={_finish_reason(response)})")
        return response.text


def _finish_reason(response: genai_types.GenerateContentResponse) -> str | None:
    if response.candidates:
        return str(response.candidates[0].finish_reason)
    return None


def build_provider() -> LLMProvider:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)
    return GeminiProvider(settings.gemini_api_key, settings.gemini_model)


class LLMClient:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or build_provider()

    async def generate_draft(self, customer_message: str, articles: list[KBArticle]) -> str:
        if self.provider.has_key:
            return await self._call_model(customer_message, articles)

        # No API key configured: still perform a real request to the provider
        # (it will fail authentication) so request-building/network behavior
        # is exercised, then fall back to a static draft with the same
        # realistic latency a real call would have.
        try:
            await self.provider.complete(system="", user="ping", max_tokens=16)
        except Exception as exc:
            logger.info("LLM call without API key failed as expected: %s", exc)

        await asyncio.sleep(random.uniform(5, 30))
        return FALLBACK_DRAFT

    async def _call_model(self, customer_message: str, articles: list[KBArticle]) -> str:
        articles_block = (
            "\n\n".join(f"### {a.title}\n{a.content}" for a in articles) or "(релевантних статей не знайдено)"
        )
        user_prompt = (
            f"Повідомлення клієнта:\n{customer_message}\n\n"
            f"Релевантні статті бази знань:\n{articles_block}"
        )
        # Gemini 2.5+ models count internal "thinking" tokens against the
        # output budget, so it needs more headroom than the visible draft.
        return await self.provider.complete(system=SYSTEM_PROMPT, user=user_prompt, max_tokens=2048)
