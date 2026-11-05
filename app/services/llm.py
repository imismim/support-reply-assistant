import asyncio
import logging
import random

from anthropic import AsyncAnthropic

from app.config import settings
from app.models import KBArticle

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ти — асистент служби підтримки продукту ReportFlow. На основі повідомлення "
    "клієнта та наданих статей бази знань згенеруй коротку чернетку відповіді "
    "українською мовою для оператора підтримки. Посилайся на статті за назвою, "
    "де це доречно. Якщо жодна стаття не підходить, дай загальну доброзичливу "
    "відповідь і попроси деталі. Не вигадуй функціональність, якої немає в статтях."
)

# Used only when ANTHROPIC_API_KEY is not set, so the rest of the system
# (timeouts, UI states, latency handling) behaves the same with or without a
# real key, per the task spec.
FALLBACK_DRAFT = (
    "Дякуємо за звернення! Ми отримали ваш запит і уважно його розглядаємо. "
    "Будь ласка, перегляньте нашу базу знань — там може бути готове рішення, "
    "а оператор підтримки найближчим часом надасть детальну відповідь."
)


class LLMClient:
    def __init__(self) -> None:
        self._has_key = bool(settings.anthropic_api_key)
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key or "sk-ant-no-key-placeholder")

    async def generate_draft(self, customer_message: str, articles: list[KBArticle]) -> str:
        if self._has_key:
            return await self._call_model(customer_message, articles)

        # No API key configured: still perform a real request to the provider
        # (it will fail authentication) so request-building/network behavior
        # is exercised, then fall back to a static draft with the same
        # realistic latency a real call would have.
        try:
            await self._client.messages.create(
                model=settings.anthropic_model,
                max_tokens=16,
                messages=[{"role": "user", "content": "ping"}],
            )
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
        response = await self._client.messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
