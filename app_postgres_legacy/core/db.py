"""
PostgreSQL is the single source of truth for the entire system — this module
is the only place that opens a connection to it. Async engine + session
factory, used via the get_db() dependency in deps.py.
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

# asyncpg driver — DATABASE_URL should look like:
#   postgresql+asyncpg://user:pass@host/dbname
# (a plain "postgresql://" URL from Render works after this small rewrite)
_url = settings.DATABASE_URL
if _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(_url, pool_pre_ping=True, pool_size=10, max_overflow=10)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
