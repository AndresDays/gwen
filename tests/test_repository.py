import pytest

from gwen.database import Database
from gwen.repository import Repository


@pytest.mark.asyncio
async def test_memory_lifecycle() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    async with database.session() as session:
        repository = Repository(session)
        await repository.add_memory(42, "Prefiere café")
        assert [item.content for item in await repository.memories(42)] == ["Prefiere café"]
        assert await repository.forget_matching(42, "café") == 1
        assert await repository.memories(42) == []
    await database.close()


@pytest.mark.asyncio
async def test_recent_messages_are_chronological() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    async with database.session() as session:
        repository = Repository(session)
        await repository.add_message(42, "user", "hola")
        await repository.add_message(42, "assistant", "hola")
        messages = await repository.recent_messages(42, 10)
        assert [message.role for message in messages] == ["user", "assistant"]
    await database.close()
