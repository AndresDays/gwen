import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gwen.config import Settings
from gwen.database import Database
from gwen.security import COOKIE_NAME, create_session
from gwen.web import create_app


def make_app(remote=True, voice=None):
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test",
        telegram_allowed_user_id=42,
        anthropic_api_key="test",
        database_url="sqlite+aiosqlite:///:memory:",
        web_remote_enabled=remote,
        web_password_hash="test-hash",
        web_session_secret="test-session-secret",
        web_public_origin="https://gwen.test.ts.net",
    )
    provider = AsyncMock(
        return_value=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=10),
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="calendar_intent",
                    input={
                        "decision": "create",
                        "event": {
                            "title": "Dentista",
                            "start": "2099-09-08T10:00:00-06:00",
                            "end": "2099-09-08T11:00:00-06:00",
                            "timezone": "America/Mexico_City",
                        },
                    },
                )
            ],
        )
    )
    assistant = SimpleNamespace(
        client=SimpleNamespace(messages=SimpleNamespace(create=provider)),
        model="test",
        max_input_chars=12000,
        daily_token_limit_enabled=False,
        daily_token_limit=100000,
        history_limit=20,
        reply=AsyncMock(return_value="Hola"),
    )
    return (
        create_app(
            settings=settings,
            database=Database(settings.database_url),
            assistant=assistant,
            voice=voice,
        ),
        settings,
        provider,
    )


def test_calendar_opt_in_and_current_request_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app, settings, provider = make_app()
    with TestClient(app, base_url=settings.web_public_origin) as client:
        client.cookies.set(COOKIE_NAME, create_session(settings.web_session_secret))
        headers = {
            "Origin": settings.web_public_origin,
            "X-Gwen-Capabilities": "calendar.create.v1",
            "X-Gwen-Timezone": "America/Mexico_City",
        }
        response = client.post(
            "/api/chat",
            headers=headers,
            json={"message": "Agenda dentista el 8 de septiembre de 2099 a las 10"},
        )
        assert response.status_code == 200
        assert response.json()["actions"][0]["type"] == "calendar.create"
        assert provider.call_args.kwargs["messages"] == [
            {"role": "user", "content": "Agenda dentista el 8 de septiembre de 2099 a las 10"}
        ]
        legacy = client.post(
            "/api/chat", headers={"Origin": settings.web_public_origin}, json={"message": "Hola"}
        )
        assert legacy.json()["answer"] == "Hola"
        assert provider.await_count == 1


@pytest.mark.parametrize(
    "remote,authenticated,expected", [(False, False, 403), (True, False, 401), (True, True, 200)]
)
def test_calendar_result_requires_private_session(
    tmp_path, monkeypatch, remote, authenticated, expected
):
    monkeypatch.chdir(tmp_path)
    app, settings, _ = make_app(remote)
    with TestClient(
        app, base_url=settings.web_public_origin if remote else "http://localhost"
    ) as client:
        if authenticated:
            client.cookies.set(COOKIE_NAME, create_session(settings.web_session_secret))
        response = client.post(
            "/api/calendar/result",
            headers={"Origin": settings.web_public_origin},
            json={"id": str(uuid4()), "status": "created", "title": "Dentista"},
        )
        assert response.status_code == expected
        if expected == 200:
            assert "confirmó" in response.json()["answer"]


def test_voice_returns_action_and_spoken_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    voice = SimpleNamespace(
        transcribe=AsyncMock(return_value="Agenda dentista el 8 de septiembre de 2099 a las 10"),
        synthesize=AsyncMock(return_value=b"mp3"),
    )
    app, settings, provider = make_app(voice=voice)
    with TestClient(app, base_url=settings.web_public_origin) as client:
        client.cookies.set(COOKIE_NAME, create_session(settings.web_session_secret))
        headers = {
            "Origin": settings.web_public_origin,
            "X-Gwen-Capabilities": "calendar.create.v1",
            "X-Gwen-Timezone": "America/Mexico_City",
        }
        response = client.post(
            "/api/voice",
            headers=headers,
            files={"audio": ("voice.wav", b"fake", "audio/wav")},
            data={"duration": "2"},
        )
        assert response.status_code == 200
        action = response.json()["actions"][0]
        assert response.json()["transcript"] == provider.call_args.kwargs["messages"][0]["content"]
        result = client.post(
            "/api/calendar/result",
            headers=headers,
            json={"id": action["id"], "title": action["title"], "status": "failed", "speak": True},
        )
        assert result.status_code == 200
        assert "no pudo" in result.json()["answer"]
        assert result.json()["audio_type"] == "audio/mpeg"


def test_result_rejects_foreign_origin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app, settings, _ = make_app()
    with TestClient(app, base_url=settings.web_public_origin) as client:
        client.cookies.set(COOKIE_NAME, create_session(settings.web_session_secret))
        response = client.post(
            "/api/calendar/result",
            headers={"Origin": "https://untrusted.example"},
            json={"id": str(uuid4()), "status": "created", "title": "Dentista"},
        )
        assert response.status_code == 403


def test_realtime_delivers_action_after_pending_speech(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class Transcriber:
        def __init__(self):
            self.queue = asyncio.Queue()

        async def send(self, payload):
            if json.loads(payload).get("commit"):
                await self.queue.put(
                    json.dumps(
                        {
                            "message_type": "committed_transcript",
                            "text": "Agenda dentista mañana a las 10",
                        }
                    )
                )

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.queue.get()

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield Transcriber()

    monkeypatch.setattr("gwen.realtime.connect", connect)
    voice = SimpleNamespace(api_key="test", synthesize=AsyncMock(return_value=b"mp3"))
    app, settings, provider = make_app(voice=voice)
    with TestClient(app, base_url=settings.web_public_origin) as client:
        client.cookies.set(COOKIE_NAME, create_session(settings.web_session_secret))
        with client.websocket_connect(
            "wss://gwen.test.ts.net/ws/voice",
            headers={
                "Origin": settings.web_public_origin,
                "X-Gwen-Capabilities": "calendar.create.v1",
                "X-Gwen-Timezone": "America/Mexico_City",
            },
        ) as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "commit", "duration": 2})
            events = []
            for _ in range(8):
                event = ws.receive_json()
                events.append(event)
                if event["type"] in {"turn_done", "error"}:
                    break
            kinds = [event["type"] for event in events]
            assert kinds == [
                "transcript",
                "answer_delta",
                "audio",
                "calendar_actions",
                "answer_done",
                "turn_done",
            ]
            assert events[3]["actions"][0]["title"] == "Dentista"
            assert provider.await_count == 1
            ws.send_json({"type": "close"})
