from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gwen.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    def session(self) -> AsyncSession:
        return self.sessions()

    async def close(self) -> None:
        await self.engine.dispose()
