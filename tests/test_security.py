from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from gwen.config import Settings
from gwen.database import Database
from gwen.security import create_session, password_hash, verify_password, verify_session
from gwen.web import create_app


def remote_settings(password: str = "una contraseña bastante larga") -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_allowed_user_id=42,
        anthropic_api_key="test-key",
        database_url="sqlite+aiosqlite:///:memory:",
        web_remote_enabled=True,
        web_password_hash=password_hash(password),
        web_session_secret="session-secret-for-tests",
        web_public_origin="https://gwen.test.ts.net",
    )


def test_password_and_signed_session_validation() -> None:
    encoded = password_hash("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)
    token = create_session("secret")
    assert verify_session(token, "secret")
    assert not verify_session(token, "another-secret")


def test_remote_mode_requires_login_and_rejects_cross_site_writes() -> None:
    password = "una contraseña bastante larga"
    settings = remote_settings(password)
    assistant = SimpleNamespace(reply=AsyncMock(return_value="Hola"))
    app = create_app(
        settings=settings,
        database=Database(settings.database_url),
        assistant=assistant,
        voice=None,
    )
    with TestClient(app, base_url=settings.web_public_origin) as client:
        assert client.get("/", follow_redirects=False).status_code == 303
        assert client.get("/api/state").status_code == 401
        assert (
            client.post("/api/login", data={"password": "contraseña incorrecta"}).status_code == 401
        )
        login = client.post("/api/login", data={"password": password}, follow_redirects=False)
        assert login.status_code == 303
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "Secure" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]
        assert client.get("/api/state").status_code == 200
        assert client.post("/api/chat", json={"message": "hola"}).status_code == 403
        response = client.post(
            "/api/chat",
            json={"message": "hola"},
            headers={"Origin": settings.web_public_origin},
        )
        assert response.status_code == 200

        local_response = client.post(
            "/api/chat",
            json={"message": "hola"},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert local_response.status_code == 200

        hostile_response = client.post(
            "/api/chat",
            json={"message": "hola"},
            headers={"Origin": "https://sitio-ajeno.example"},
        )
        assert hostile_response.status_code == 403


def test_remote_mode_refuses_incomplete_or_non_https_configuration() -> None:
    settings = remote_settings()
    settings.web_public_origin = "http://gwen.test.ts.net"
    settings.web_session_secret = None
    try:
        create_app(settings=settings)
    except RuntimeError as error:
        assert "requiere" in str(error)
    else:
        raise AssertionError("Remote mode must fail closed")
