from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.config import settings

_pool: ArqRedis | None = None


def get_redis_settings() -> RedisSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    if redis_settings.ssl:
        # Heroku's Redis addon terminates TLS with a self-signed cert, which
        # arq's default ssl_cert_reqs="required" would reject.
        redis_settings.ssl_cert_reqs = "none"
    return redis_settings


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(get_redis_settings())
    return _pool
