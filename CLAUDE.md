# CLAUDE.md

Конвенції для роботи над цим репозиторієм.

## Стек і структура

- FastAPI (async) + SQLAlchemy 2.0 (async, `Mapped`/`mapped_column`) + asyncpg + Alembic.
- `arq` (не Celery/RQ) для фонових задач — тримає весь стек асинхронним (web і worker діляться тим самим async ORM-кодом).
- LLM — за протоколом `LLMProvider` (`app/services/llm.py`): Gemini за замовчуванням, Anthropic як альтернатива (`LLM_PROVIDER`). Fallback-режим без ключа спільний для обох.
- `app/routers/*` — тільки HTTP-шар (валідація, виклик сервісів/задач, відповідь). Бізнес-логіка — в `app/services/*` і `app/worker/tasks.py`.
- Міграції генеруються через `alembic revision --autogenerate`, не пишуться руками — модель (`app/models.py`) є єдиним джерелом правди для схеми.

## Ідемпотентність — наскрізний паттерн

Для будь-якої операції, яку клієнт/хелпдеск може повторити (вебхук,
клік «Підтвердити»): insert-if-new через `INSERT ... ON CONFLICT DO
NOTHING` на унікальному ключі, і подальша дія (enqueue job) виконується
лише якщо `result.rowcount > 0`. Не вигадувати dedup-логіку на рівні
застосунку (locks, перевірки "чи вже існує" окремим SELECT) — атомарність
дає сама БД. Для доставки клієнту — додатково `SELECT ... FOR UPDATE` на
рядку статусу перед відправкою, щоб паралельні спроби не надіслали
відповідь двічі.

## Тести

- `pytest-asyncio` у режимі `auto` (без `@pytest.mark.asyncio` на кожному тесті).
- Тести з реальною Postgres/Redis (окрема БД `support_reply_test`, окремий Redis DB-індекс), не mock ORM — важливо саме перевірити поведінку БД під час конкурентних запитів (unique constraint race).
- Час (LLM fallback delay, rate-limiter window) — через `monkeypatch` на `asyncio.sleep`/`time.time`, а не реальне очікування в тестах.
- HTTP-залежності (хелпдеск, Gemini/Anthropic) — через `httpx.MockTransport` / `AsyncMock`, не реальні запити. LLM-провайдер мокається на рівні SDK-клієнта всередині `GeminiProvider`/`AnthropicProvider`.

## Інше

- Без коментарів, що описують ЩО робить код — лише WHY, коли це не очевидно (напр. чому `pg_trgm`, а не `tsvector`, чому `word_similarity`, а не `similarity`).
- Коміти в цьому репозиторії — лише від автора-людини, без співавторства чи згадки інструменту в повідомленні коміту.
