from datetime import UTC, datetime

import pytest

from gwen.database import Database
from gwen.reminders import parse_reminder_request
from gwen.repository import Repository


def test_parse_relative_reminder_in_guatemala_time() -> None:
    request = parse_reminder_request(
        "recuérdame pagar la renta mañana a las 09:00",
        now=datetime(2026, 9, 7, 22, 30, tzinfo=UTC),
    )

    assert request is not None
    assert request.content == "pagar la renta"
    assert request.due_at == datetime(2026, 9, 8, 15, 0, tzinfo=UTC)
    assert request.timezone == "America/Guatemala"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("recuérdame probar recordatorios hoy a las 12:57 am", (2026, 9, 7, 6, 57)),
        ("recuérdame comer hoy a las 12:00 pm", (2026, 9, 7, 18, 0)),
    ],
)
def test_parse_12_hour_reminder_time(text: str, expected: tuple[int, int, int, int, int]) -> None:
    request = parse_reminder_request(text, now=datetime(2026, 9, 7, 6, 30, tzinfo=UTC))

    assert request is not None
    assert request.due_at == datetime(*expected, tzinfo=UTC)


def test_reminder_phrase_without_time_requires_clarification() -> None:
    request = parse_reminder_request("recuérdame pagar la renta mañana")

    assert request is not None
    assert request.needs_clarification


@pytest.mark.asyncio
async def test_reminder_lifecycle_claims_due_item_only_once() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    due_at = datetime(2026, 9, 7, 15, 0, tzinfo=UTC)
    async with database.session() as session:
        repository = Repository(session)
        created = await repository.add_reminder(42, "pagar la renta", due_at, "America/Guatemala")
        assert [item.id for item in await repository.upcoming_reminders(42)] == [created.id]
        claimed = await repository.claim_due_reminders(42, due_at)
        assert [item.id for item in claimed] == [created.id]
        assert await repository.claim_due_reminders(42, due_at) == []
        assert await repository.upcoming_reminders(42) == []
    await database.close()
