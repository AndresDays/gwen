from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from gwen.config import Settings
from gwen.database import Database
from gwen.web import create_app, meaningful_transcript, normalized_audio_type


def test_web_interface_serves_private_chat_and_state() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    database = Database(settings.database_url)
    assistant = SimpleNamespace(
        reply=AsyncMock(return_value="Hola desde Gwen."),
        daily_token_limit=settings.daily_token_limit,
    )
    app = create_app(settings=settings, database=database, assistant=assistant, voice=None)

    with TestClient(app, base_url="http://localhost") as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "CANAL PRIVADO ESTABLECIDO" in home.text
        assert "microphoneSelect" in home.text
        assert "sessionButton" in home.text

        response = client.post("/api/chat", json={"message": "hola"})
        assert response.status_code == 200
        assert response.json() == {
            "answer": "Hola desde Gwen.",
            "transcript": None,
            "audio": None,
            "audio_type": None,
        }

        state = client.get("/api/state")
        assert state.status_code == 200
        assert state.json()["voice_enabled"] is False

        assert client.post("/api/new").status_code == 204
    assistant.reply.assert_awaited_once()


def test_web_interface_rejects_untrusted_hosts() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    app = create_app(
        settings=settings,
        database=Database(settings.database_url),
        assistant=SimpleNamespace(daily_token_limit=100_000),
        voice=None,
    )
    with TestClient(app) as client:
        assert client.get("/").status_code == 400


def test_web_voice_rejects_empty_transcript_without_calling_claude() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    database = Database(settings.database_url)
    assistant = SimpleNamespace(
        reply=AsyncMock(return_value="Hola desde Gwen."),
        daily_token_limit=settings.daily_token_limit,
    )
    voice = SimpleNamespace(transcribe=AsyncMock(return_value=""), synthesize=AsyncMock())
    app = create_app(settings=settings, database=database, assistant=assistant, voice=voice)

    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/voice",
            files={"audio": ("voice.webm", b"fake-audio", "audio/webm")},
            data={"duration": "3"},
        )

    assert response.status_code == 422
    assert "No detecté palabras claras" in response.json()["detail"]
    assistant.reply.assert_not_awaited()


def test_audio_helpers_normalize_browser_mime_and_reject_silence() -> None:
    assert normalized_audio_type("audio/webm;codecs=opus") == "audio/webm"
    assert normalized_audio_type("application/octet-stream") == "audio/webm"
    assert meaningful_transcript("Hola Gwen")
    assert not meaningful_transcript("(silence)")


def test_voice_endpoint_preserves_mime_and_returns_transcript() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    database = Database(settings.database_url)
    assistant = SimpleNamespace(
        reply=AsyncMock(return_value="Te escuché."),
        daily_token_limit=settings.daily_token_limit,
    )
    voice = SimpleNamespace(
        transcribe=AsyncMock(return_value="Hola Gwen"),
        synthesize=AsyncMock(return_value=b"mp3"),
    )
    app = create_app(settings=settings, database=database, assistant=assistant, voice=voice)

    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/voice",
            files={"audio": ("voice.webm", b"webm-audio", "audio/webm;codecs=opus")},
            data={"duration": "2"},
        )

    assert response.status_code == 200
    assert response.json()["transcript"] == "Hola Gwen"
    assert response.json()["answer"] == "Te escuché."
    assert voice.transcribe.await_args.args[2] == "audio/webm"


def test_pwa_assets_are_installable_without_caching_private_data() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    app = create_app(
        settings=settings,
        database=Database(settings.database_url),
        assistant=SimpleNamespace(daily_token_limit=100_000),
        voice=None,
    )
    with TestClient(app, base_url="http://localhost") as client:
        manifest = client.get("/manifest.webmanifest")
        worker = client.get("/sw.js")
        script = client.get("/static/app.js?v=7")
        home = client.get("/")
        assert manifest.status_code == 200
        assert manifest.json()["display"] == "standalone"
        assert worker.status_code == 200
        assert "'/api/'" not in worker.text
        assert "pathname.startsWith('/static/')" in worker.text
        assert worker.headers["service-worker-allowed"] == "/"
        assert "reconnectRealtime" in script.text
        assert "gwen_voice_latency_v1" in script.text
        assert "mobileVoiceDock" in home.text
        assert "syncStandaloneLayout" in script.text
        assert "gwen-shell-v7" in worker.text
