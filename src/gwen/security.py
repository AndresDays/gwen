import base64
import hashlib
import hmac
import time
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE_NAME = "gwen_session"


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or __import__("secrets").token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    salt_encoded = base64.urlsafe_b64encode(salt).decode()
    hash_encoded = base64.urlsafe_b64encode(derived).decode()
    return f"scrypt${salt_encoded}${hash_encoded}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, expected_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        actual = password_hash(password, base64.urlsafe_b64decode(salt_text)).split("$", 2)[2]
        return hmac.compare_digest(actual, expected_text)
    except (ValueError, TypeError):
        return False


def create_session(secret: str, lifetime_seconds: int = 2_592_000) -> str:
    expires = str(int(time.time()) + lifetime_seconds)
    signature = hmac.new(secret.encode(), expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def verify_session(token: str | None, secret: str) -> bool:
    if not token:
        return False
    try:
        expires, signature = token.split(".", 1)
        if int(expires) < int(time.time()):
            return False
        expected = hmac.new(secret.encode(), expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


def cookie_from_scope(scope: Scope) -> str | None:
    cookie = SimpleCookie()
    cookie.load(Headers(scope=scope).get("cookie", ""))
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else None


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def secure_send(message: dict[str, object]) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["x-content-type-options"] = "nosniff"
                headers["x-frame-options"] = "DENY"
                headers["referrer-policy"] = "no-referrer"
                headers["permissions-policy"] = "camera=(), geolocation=()"
                headers["content-security-policy"] = (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "connect-src 'self' ws: wss:; media-src 'self' data: blob:; "
                    "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
                )
                if self.hsts:
                    headers["strict-transport-security"] = "max-age=31536000"
                if not scope.get("path", "").startswith("/static/"):
                    headers["cache-control"] = "no-store"
            await send(message)  # type: ignore[arg-type]

        await self.app(scope, receive, secure_send)  # type: ignore[arg-type]


class PrivateAccessMiddleware:
    def __init__(self, app: ASGIApp, enabled: bool, secret: str, public_origin: str) -> None:
        self.app = app
        self.enabled = enabled
        self.secret = secret
        self.public_origin = public_origin.rstrip("/")

    def origin_is_allowed(self, origin: str) -> bool:
        normalized = origin.rstrip("/")
        return normalized in {
            self.public_origin,
            "http://127.0.0.1:8765",
            "http://localhost:8765",
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in {"/login", "/api/login"} or path.startswith("/static/"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authenticated = verify_session(cookie_from_scope(scope), self.secret)
        origin = headers.get("origin", "")
        if scope["type"] == "websocket":
            if not authenticated or not self.origin_is_allowed(origin):
                await send({"type": "websocket.close", "code": 1008, "reason": "No autorizado"})
                return
        elif not authenticated:
            if path.startswith("/api/"):
                response = JSONResponse({"detail": "Inicia sesión."}, status_code=401)
            else:
                response = RedirectResponse("/login", status_code=303)
            await response(scope, receive, send)
            return
        elif scope.get("method") not in {"GET", "HEAD", "OPTIONS"} and not self.origin_is_allowed(
            origin
        ):
            response = JSONResponse({"detail": "Origen no autorizado."}, status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def public_hostname(origin: str) -> str:
    return urlparse(origin).hostname or ""
