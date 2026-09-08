from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from gwen.alarm_actions import plan_alarm
from gwen.database import Database
from gwen.repository import Repository


@pytest.mark.asyncio
async def test_plan_alarm_returns_valid_native_action() -> None:
    due = (datetime.now(ZoneInfo("America/Guatemala")) + timedelta(hours=1)).isoformat()
    assistant = SimpleNamespace(
        model="test", client=SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(
            return_value=SimpleNamespace(content=[SimpleNamespace(
                type="tool_use", name="alarm_intent", input={
                    "decision": "create",
                    "alarm": {
                        "title": "Despertar", "fire_at": due, "timezone": "America/Guatemala"
                    },
                }
            )])
        )))
    )
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    async with database.session() as session:
        planned = await plan_alarm(
            "pon alarma", "America/Guatemala", assistant, Repository(session), 42
        )
    assert planned is not None
    assert planned[1][0].type == "alarm.create"
    await database.close()
