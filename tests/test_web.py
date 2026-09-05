from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from gwen.code_worker import CodeTaskTimeout, WorkspaceHasForeignChanges
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
        assert "codeButton" not in home.text

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
        script = client.get("/static/app.js?v=14")
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
        assert "unlockCodeButton" not in home.text
        assert "codeDialog" not in home.text
        web_source = Path("src/gwen/web.py").read_text(encoding="utf-8")
        assert "voice_code_worker = code_worker" in web_source
        voice_source = Path("src/gwen/realtime.py").read_text(encoding="utf-8")
        assert "Sí, ya lo hago. Puede tardar varios minutos" in voice_source
        assert "startCodeProgress" in script.text
        assert "startVoiceCodeProgress" in script.text
        assert "code_progress" in script.text
        assert "code_progress" in voice_source
        assert "Sí, ya lo hago." not in script.text
        assert "gwen-shell-v16" in worker.text


def test_code_endpoints_use_only_the_injected_local_worker() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    worker = SimpleNamespace(
        public_workspaces=lambda: [{"id": "gwen", "label": "Gwen"}],
        plan=AsyncMock(return_value="Plan seguro"),
        execute=AsyncMock(
            return_value={"answer": "Listo", "checks": ["pytest"], "committed": True, "diff": ""}
        ),
    )
    app = create_app(
        settings=settings,
        database=Database(settings.database_url),
        assistant=SimpleNamespace(daily_token_limit=100_000),
        voice=None,
        code_worker=worker,
    )
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/code/workspaces").json()["workspaces"][0]["id"] == "gwen"
        plan = client.post("/api/code/plan", json={"workspace_id": "gwen", "task": "Planifica"})
        execute = client.post(
            "/api/code/execute",
            json={"workspace_id": "gwen", "task": "Implementa", "commit": True},
        )
    assert plan.json() == {"plan": "Plan seguro"}
    assert execute.json()["committed"] is True
    worker.plan.assert_awaited_once_with("gwen", "Planifica")
    worker.execute.assert_awaited_once_with("gwen", "Implementa", True)


def test_normal_chat_routes_explicit_code_requests_to_worker() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assistant = SimpleNamespace(
        reply=AsyncMock(return_value="No debería usarse"),
        daily_token_limit=100_000,
    )
    worker = SimpleNamespace(
        public_workspaces=lambda: [{"id": "california", "label": "californIA"}],
        plan=AsyncMock(return_value="Cambiar la importación del logo."),
        inspect=AsyncMock(return_value="El login usa otro logo."),
        validate=AsyncMock(
            return_value={
                "validation_passed": False,
                "checks": ["FALLÓ: npm test — FAIL src/example.test.js | Tests: 1 failed"],
            }
        ),
        execute=AsyncMock(
            return_value={
                "answer": "Logo actualizado.",
                "checks": ["npm test"],
                "validation_passed": True,
                "committed": False,
                "diff": "",
            }
        ),
    )
    app = create_app(
        settings=settings,
        database=Database(settings.database_url),
        assistant=assistant,
        voice=None,
        code_worker=worker,
    )
    with TestClient(app, base_url="http://localhost") as client:
        intent = client.post(
            "/api/chat/intent",
            json={"message": "Gwen, en el proyecto California cambia la versión a 2.6.1"},
        )
        assert intent.json() == {
            "coding": True,
            "read_only": False,
            "acknowledge": True,
            "workspace": "californIA",
            "validating": False,
        }
        execution = client.post(
            "/api/chat",
            json={
                "message": (
                    "Gwen, en el proyecto California cambia el logo del login "
                    "por logoCDC.jpg que está en assets"
                )
            },
        )
        assert "Listo, ya está hecho" in execution.json()["answer"]
        assert "No hice commit" in execution.json()["answer"]
        assert "Cambiar la importación" not in execution.json()["answer"]
        assert "Logo actualizado" not in execution.json()["answer"]
        inspect = client.post("/api/chat", json={"message": "¿Puedes ver el código de California?"})
        assert "El login usa otro logo" in inspect.json()["answer"]
        validation = client.post(
            "/api/chat",
            json={
                "message": (
                    "En el proyecto California hay unas pruebas que fallan, checa cuáles son porfi"
                )
            },
        )
        assert "example" in validation.json()["answer"]
        assert "npm test" not in validation.json()["answer"]
        history = client.get("/api/state").json()["messages"]
        assert any("todo pasa" in item["content"] for item in history)
    assistant.reply.assert_not_awaited()
    worker.plan.assert_not_awaited()
    worker.execute.assert_awaited_once_with(
        "california",
        "Gwen, en el proyecto California cambia el logo del login "
        "por logoCDC.jpg que está en assets",
        commit=False,
        diagnose=False,
    )


def test_chat_followup_without_project_name_still_reaches_the_worker() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assistant = SimpleNamespace(
        reply=AsyncMock(return_value="No debería usarse"),
        daily_token_limit=100_000,
    )
    failure = "FALLÓ: npm test — FAIL src/example.test.js | Tests: 1 failed"
    worker = SimpleNamespace(
        public_workspaces=lambda: [{"id": "california", "label": "californIA"}],
        plan=AsyncMock(return_value="plan"),
        inspect=AsyncMock(return_value="inspección"),
        validate=AsyncMock(return_value={"validation_passed": False, "checks": [failure]}),
        execute=AsyncMock(
            return_value={
                "answer": "Pruebas corregidas.",
                "checks": ["OK: npm test"],
                "validation_passed": True,
                "committed": False,
                "diff": "",
            }
        ),
    )
    app = create_app(
        settings=settings,
        database=Database(settings.database_url),
        assistant=assistant,
        voice=None,
        code_worker=worker,
    )
    with TestClient(app, base_url="http://localhost") as client:
        first = client.post(
            "/api/chat",
            json={"message": "En el proyecto California, ¿cuáles pruebas fallaron?"},
        )
        assert "example" in first.json()["answer"]
        # Sin nombrar el proyecto: antes caía al chat conversacional y alucinaba.
        intent = client.post("/api/chat/intent", json={"message": "corrige esas pruebas"})
        assert intent.json()["coding"] is True
        second = client.post("/api/chat", json={"message": "corrige esas pruebas"})
        assert "Listo, ya está hecho" in second.json()["answer"]
    assistant.reply.assert_not_awaited()
    workspace_id, task = worker.execute.await_args.args
    assert workspace_id == "california"
    assert task.startswith("corrige esas pruebas")
    # El fallo anterior viaja como contexto a Claude Code.
    assert failure in task


def test_chat_editing_request_about_tests_executes_instead_of_only_validating() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assistant = SimpleNamespace(
        reply=AsyncMock(return_value="No debería usarse"),
        daily_token_limit=100_000,
    )
    worker = SimpleNamespace(
        public_workspaces=lambda: [{"id": "california", "label": "californIA"}],
        plan=AsyncMock(return_value="plan"),
        inspect=AsyncMock(return_value="inspección"),
        validate=AsyncMock(return_value={"validation_passed": True, "checks": []}),
        execute=AsyncMock(
            return_value={
                "answer": "Pruebas corregidas.",
                "checks": ["OK: npm test"],
                "validation_passed": True,
                "committed": False,
                "diff": "",
            }
        ),
    )
    app = create_app(
        settings=settings,
        database=Database(settings.database_url),
        assistant=assistant,
        voice=None,
        code_worker=worker,
    )
    message = "en el proyecto california checa las pruebas que fallaron y corrígelas"
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/chat/intent", json={"message": message}).json() == {
            "coding": True,
            "read_only": False,
            "acknowledge": True,
            "workspace": "californIA",
            "validating": False,
        }
        response = client.post("/api/chat", json={"message": message})
        assert "Listo, ya está hecho" in response.json()["answer"]
    worker.validate.assert_not_awaited()
    # Pedir corregir pruebas exige correr los checks primero: Claude Code no tiene terminal.
    worker.execute.assert_awaited_once_with("california", message, commit=False, diagnose=True)


def _code_app(worker: SimpleNamespace) -> tuple[object, SimpleNamespace]:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assistant = SimpleNamespace(
        reply=AsyncMock(return_value="No debería usarse"),
        daily_token_limit=100_000,
    )
    app = create_app(
        settings=settings,
        database=Database(settings.database_url),
        assistant=assistant,
        voice=None,
        code_worker=worker,
    )
    return app, assistant


def test_foreign_changes_answer_names_the_files_instead_of_a_generic_error() -> None:
    worker = SimpleNamespace(
        public_workspaces=lambda: [{"id": "california", "label": "californIA"}],
        plan=AsyncMock(),
        inspect=AsyncMock(),
        validate=AsyncMock(),
        execute=AsyncMock(
            side_effect=WorkspaceHasForeignChanges(
                "california", "californIA", ("package.json", "src/pages/login.jsx")
            )
        ),
    )
    app, _ = _code_app(worker)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/chat",
            json={"message": "en el proyecto california cambia la versión a 2.6.2"},
        )
        assert response.status_code == 200
        answer = response.json()["answer"]
        assert "package.json" in answer
        assert "src/pages/login.jsx" in answer
        assert "adopta los cambios de california" in answer
        # El aviso queda en el historial, no en un toast que se desvanece.
        history = client.get("/api/state").json()["messages"]
        assert any("package.json" in item["content"] for item in history)


def test_timeout_is_reported_as_a_chat_message() -> None:
    worker = SimpleNamespace(
        public_workspaces=lambda: [{"id": "california", "label": "californIA"}],
        plan=AsyncMock(),
        inspect=AsyncMock(),
        validate=AsyncMock(),
        execute=AsyncMock(side_effect=CodeTaskTimeout("La tarea tardó más de lo permitido.")),
    )
    app, _ = _code_app(worker)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/chat", json={"message": "en el proyecto california corrige el login"}
        )
        assert response.status_code == 200
        assert "tardó más" in response.json()["answer"]


def test_adopting_changes_unblocks_the_workspace() -> None:
    worker = SimpleNamespace(
        public_workspaces=lambda: [{"id": "california", "label": "californIA"}],
        plan=AsyncMock(),
        inspect=AsyncMock(),
        validate=AsyncMock(),
        execute=AsyncMock(),
        adopt_current_changes=AsyncMock(),
    )
    app, assistant = _code_app(worker)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/chat", json={"message": "adopta los cambios de california"})
        assert "punto de partida" in response.json()["answer"]
    worker.adopt_current_changes.assert_awaited_once_with("california")
    worker.execute.assert_not_awaited()
    assistant.reply.assert_not_awaited()


def test_intent_reports_the_workspace_label_for_the_progress_indicator() -> None:
    worker = SimpleNamespace(
        public_workspaces=lambda: [{"id": "california", "label": "californIA"}],
        plan=AsyncMock(),
        inspect=AsyncMock(),
        validate=AsyncMock(),
        execute=AsyncMock(),
    )
    app, _ = _code_app(worker)
    with TestClient(app, base_url="http://localhost") as client:
        intent = client.post(
            "/api/chat/intent",
            json={"message": "en el proyecto california corrige el login"},
        ).json()
        assert intent["workspace"] == "californIA"
        assert intent["validating"] is False
        plain = client.post("/api/chat/intent", json={"message": "¿qué hora es?"}).json()
        assert plain["coding"] is False
        assert plain["workspace"] is None
