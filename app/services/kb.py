from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models import KBArticle

# Postgres has no Ukrainian stemming dictionary, so plain tsvector matching
# misses inflected forms (e.g. "пароль" in a ticket vs "пароля" in an
# article). pg_trgm's word_similarity is stemming-free and tolerant of word
# endings, which fits short, inflected customer messages much better.
_MIN_SIMILARITY = 0.2


async def search_relevant_articles(query: str, limit: int = 3) -> list[KBArticle]:
    async with AsyncSessionLocal() as session:
        document = KBArticle.title + " " + KBArticle.content
        score = func.word_similarity(query, document)

        stmt = select(KBArticle, score.label("score")).order_by(score.desc()).limit(limit)
        rows = (await session.execute(stmt)).all()

        matched = [row.KBArticle for row in rows if row.score is not None and row.score >= _MIN_SIMILARITY]
        if matched:
            return matched

        if rows:
            return [row.KBArticle for row in rows]

        fallback_stmt = select(KBArticle).order_by(KBArticle.id).limit(limit)
        return list((await session.execute(fallback_stmt)).scalars().all())
