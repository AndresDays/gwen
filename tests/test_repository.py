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


@pytest.mark.asyncio
async def test_clear_messages_preserves_memories_and_other_users() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    async with database.session() as session:
        repository = Repository(session)
        await repository.add_message(42, "user", "contexto")
        await repository.add_message(99, "user", "otro contexto")
        await repository.add_memory(42, "Prefiere café")

        assert await repository.clear_messages(42) == 1
        assert await repository.recent_messages(42, 10) == []
        assert len(await repository.recent_messages(99, 10)) == 1
        assert [item.content for item in await repository.memories(42)] == ["Prefiere café"]
    await database.close()


@pytest.mark.asyncio
async def test_compaction_keeps_recent_messages_and_persists_context() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    async with database.session() as session:
        repository = Repository(session)
        for number in range(7):
            await repository.add_message(42, "user", f"mensaje {number}")

        assert await repository.compact_messages(42, keep=3)
        messages = await repository.recent_messages(42, 10)
        assert [item.content for item in messages] == ["mensaje 4", "mensaje 5", "mensaje 6"]
        summary = await repository.conversation_summary(42)
        assert "mensaje 0" in summary
        assert "mensaje 3" in summary
    await database.close()


@pytest.mark.asyncio
async def test_daily_usage_is_isolated_by_user_and_day() -> None:
    from datetime import date

    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    async with database.session() as session:
        repository = Repository(session)
        day = date(2026, 9, 4)
        await repository.add_usage(42, day, 100, 25)
        await repository.add_usage(42, day, 10, 5)
        await repository.add_usage(99, day, 500, 500)
        assert await repository.usage_tokens(42, day) == 140
    await database.close()
