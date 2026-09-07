from datetime import UTC, datetime

from gwen.assistant import SYSTEM_PROMPT, current_context


def test_current_context_uses_guatemala_time() -> None:
    context = current_context(lambda: datetime(2026, 9, 4, 18, 30, tzinfo=UTC))
    assert "America/Guatemala" in context
    assert "Fecha y hora actuales: 2026-09-04 12:30:00" in context
    assert "No adivines el año actual" in context


def test_system_prompt_requests_natural_conversation() -> None:
    assert "chatbot genérico" in SYSTEM_PROMPT
    assert "humor moderado" in SYSTEM_PROMPT
    assert "Evita títulos, listas" in SYSTEM_PROMPT
    assert "No finjas experiencias" in SYSTEM_PROMPT


async def _memory_repository():
    from gwen.database import Database

    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    return database


async def test_assistant_stores_natural_memory_without_calling_provider() -> None:
    from unittest.mock import AsyncMock

    from gwen.assistant import GwenAssistant
    from gwen.repository import Repository

    database = await _memory_repository()
    assistant = GwenAssistant("test-key", "test-model", 10)
    assistant.client = AsyncMock()
    async with database.session() as session:
        repository = Repository(session)
        answer = await assistant.reply(42, "recuerda que prefiero té", repository)
        assert answer == "Lo recordaré."
        assert [item.content for item in await repository.memories(42)] == ["prefiero té"]
        assistant.client.messages.create.assert_not_awaited()
    await database.close()


async def test_assistant_creates_reminder_without_calling_provider() -> None:
    from unittest.mock import AsyncMock

    from gwen.assistant import GwenAssistant
    from gwen.repository import Repository

    database = await _memory_repository()
    assistant = GwenAssistant("test-key", "test-model", 10)
    assistant.client = AsyncMock()
    async with database.session() as session:
        repository = Repository(session)
        answer = await assistant.reply(
            42, "recuérdame pagar la renta mañana a las 09:00", repository
        )
        assert answer == "Listo. Te recordaré pagar la renta el 2026-09-08 a las 09:00."
        assert [item.content for item in await repository.upcoming_reminders(42)] == [
            "pagar la renta"
        ]
        assistant.client.messages.create.assert_not_awaited()
    await database.close()


async def test_assistant_completes_pending_reminder_and_saves_shortcut_turns() -> None:
    from unittest.mock import AsyncMock

    from gwen.assistant import GwenAssistant
    from gwen.repository import Repository

    database = await _memory_repository()
    assistant = GwenAssistant("test-key", "test-model", 10)
    assistant.client = AsyncMock()
    async with database.session() as session:
        repository = Repository(session)
        assert await assistant.reply(42, "recuérdame probar recordatorios mañana", repository) == (
            "¿A qué hora exacta quieres que te lo recuerde?"
        )
        answer = await assistant.reply(42, "1:02 am", repository)
        assert answer.startswith("Listo. Te recordaré probar recordatorios")
        assert [item.content for item in await repository.upcoming_reminders(42)] == [
            "probar recordatorios"
        ]
        assert [item.content for item in await repository.recent_messages(42, 10)] == [
            "recuérdame probar recordatorios mañana",
            "¿A qué hora exacta quieres que te lo recuerde?",
            "1:02 am",
            answer,
        ]
        assistant.client.messages.create.assert_not_awaited()
    await database.close()


async def test_assistant_stores_only_safe_automatic_preferences() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from gwen.assistant import GwenAssistant
    from gwen.repository import Repository

    database = await _memory_repository()
    assistant = GwenAssistant("test-key", "test-model", 10)
    assistant.client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Entendido.")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )
    )
    async with database.session() as session:
        repository = Repository(session)
        assert await assistant.reply(42, "prefiero respuestas breves", repository) == "Entendido."
        assert [item.content for item in await repository.personal_memories(42)] == [
            "prefiero respuestas breves"
        ]
        await assistant.reply(42, "mi hermana Ana vive en Madrid", repository)
        await assistant.reply(42, "prefiero usar mi password secreto", repository)
        assert [item.content for item in await repository.personal_memories(42)] == [
            "prefiero respuestas breves"
        ]
    await database.close()


async def test_assistant_enforces_daily_token_limit_before_provider_call() -> None:
    from datetime import datetime
    from unittest.mock import AsyncMock
    from zoneinfo import ZoneInfo

    import pytest

    from gwen.assistant import GwenAssistant
    from gwen.errors import DailyUsageLimitReached
    from gwen.repository import Repository

    database = await _memory_repository()
    assistant = GwenAssistant("test-key", "test-model", 10, daily_token_limit=100)
    assistant.client = AsyncMock()
    async with database.session() as session:
        repository = Repository(session)
        await repository.add_usage(42, datetime.now(ZoneInfo("America/Guatemala")).date(), 100, 0)
        with pytest.raises(DailyUsageLimitReached):
            await assistant.reply(42, "hola", repository)
        assistant.client.messages.create.assert_not_awaited()
    await database.close()
