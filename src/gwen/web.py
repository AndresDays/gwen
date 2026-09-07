import base64
import logging
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gwen.assistant import GwenAssistant
from gwen.calendar_actions import (
    CAPABILITY,
    CalendarAction,
    CalendarResult,
    plan_calendar,
    result_message,
)
from gwen.code_tasks import CodeTaskStore
from gwen.code_voice import (
    CodeContextStore,
    code_commit_requested,
    code_followup_task,
    code_request_is_read_only,
    code_task_needs_diagnosis,
    detect_adopt_request,
    detect_code_request,
    detect_validation_request,
    failed_checks,
)
from gwen.code_worker import (
    ClaudeCodeWorker,
    CodeTaskTimeout,
    WorkspaceHasForeignChanges,
    code_result_answer,
    commit_result_answer,
    validation_result_answer,
)
from gwen.config import Settings, get_settings
from gwen.database import Database
from gwen.errors import DailyUsageLimitReached, InputTooLong, ProviderUnavailable
from gwen.main import configure_logging
from gwen.music import (
    NO_FAVORITES,
    detect_spotify_request,
    favorite_artist,
    spotify_answer,
    spotify_failure,
    wants_history,
)
from gwen.realtime import run_realtime_voice
from gwen.repository import Repository
from gwen.security import (
    COOKIE_NAME,
    PrivateAccessMiddleware,
    SecurityHeadersMiddleware,
    create_session,
    public_hostname,
    verify_password,
    verify_session,
)
from gwen.spotify import SpotifyOAuth
from gwen.voice import ElevenLabsVoice

logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 15 * 1024 * 1024


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class ChatResponse(BaseModel):
    answer: str
    transcript: str | None = None
    audio: str | None = None
    audio_type: str | None = None
    actions: list[CalendarAction] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class CodeTaskRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=12_000)
    commit: bool = False


class CodePlanResponse(BaseModel):
    plan: str


class CodeExecutionResponse(BaseModel):
    answer: str
    checks: list[str]
    committed: bool
    diff: str
    changed: bool = True


def normalized_audio_type(content_type: str | None) -> str:
    value = (content_type or "audio/webm").split(";", 1)[0].strip().lower()
    return value if value.startswith("audio/") else "audio/webm"


def meaningful_transcript(text: str) -> bool:
    normalized = text.strip().lower()
    ignored = {"", "...", "[silence]", "(silence)", "[music]", "(music)"}
    return normalized not in ignored and any(character.isalnum() for character in normalized)


def safe_error(error: Exception) -> HTTPException:
    if isinstance(error, InputTooLong):
        return HTTPException(413, "Ese mensaje es demasiado largo.")
    if isinstance(error, DailyUsageLimitReached):
        return HTTPException(429, "Llegamos al límite diario configurado.")
    if isinstance(error, ProviderUnavailable):
        return HTTPException(503, "Claude no está disponible ahora mismo.")
    logger.warning("Web request failed (%s)", type(error).__name__)
    return HTTPException(503, "Gwen tuvo un problema temporal.")


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    assistant: GwenAssistant | None = None,
    voice: ElevenLabsVoice | None = None,
    code_worker: ClaudeCodeWorker | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    database = database or Database(settings.database_url)
    worker_config = Path("gwen-code-workspaces.json")
    if code_worker is None and worker_config.is_file():
        code_worker = ClaudeCodeWorker(worker_config)
        if not code_worker.workspaces:
            logger.warning("Sin workspaces válidos: las tareas de código quedan desactivadas.")
            code_worker = None
    code_tasks = CodeTaskStore(Path(".gwen-code-task.json"))
    code_context = CodeContextStore(Path(".gwen-code-context.json"))
    spotify = (
        SpotifyOAuth(
            settings.spotify_client_id or "",
            settings.spotify_client_secret or "",
            settings.spotify_redirect_uri,
            Path(".gwen-spotify.json"),
        )
        if settings.spotify_enabled
        else None
    )
    if settings.web_remote_enabled and not (
        settings.web_password_hash
        and settings.web_session_secret
        and settings.web_public_origin.startswith("https://")
        and public_hostname(settings.web_public_origin)
    ):
        raise RuntimeError("El acceso remoto requiere contraseña, secreto de sesión y HTTPS.")
    assistant = assistant or GwenAssistant(
        settings.anthropic_api_key,
        settings.anthropic_model,
        settings.history_limit,
        settings.max_input_chars,
        settings.daily_token_limit,
        settings.daily_token_limit_enabled,
    )
    if voice is None and settings.voice_enabled:
        voice = ElevenLabsVoice(
            settings.elevenlabs_api_key or "",
            settings.elevenlabs_voice_id or "",
            settings.elevenlabs_web_tts_model,
            settings.elevenlabs_web_stt_model,
            "mp3_22050_32",
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await database.initialize()
        yield
        if voice is not None and hasattr(voice, "aclose"):
            await voice.aclose()
        await database.close()

    app = FastAPI(title="Gwen", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(
        PrivateAccessMiddleware,
        enabled=settings.web_remote_enabled,
        secret=settings.web_session_secret or "",
        public_origin=settings.web_public_origin,
    )
    allowed_hosts = ["127.0.0.1", "localhost"]
    if settings.web_remote_enabled:
        allowed_hosts.append(public_hostname(settings.web_public_origin))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.web_remote_enabled)
    static_dir = files("gwen").joinpath("web_static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    login_attempts: dict[str, deque[datetime]] = defaultdict(deque)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(
            str(static_dir.joinpath("manifest.webmanifest")),
            media_type="application/manifest+json",
        )

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        return FileResponse(
            str(static_dir.joinpath("sw.js")),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/login", include_in_schema=False)
    async def login_page() -> FileResponse:
        return FileResponse(str(static_dir.joinpath("login.html")))

    @app.post("/api/login", include_in_schema=False)
    async def login(
        request: Request, password: Annotated[str, Form(min_length=12)]
    ) -> RedirectResponse:
        if not settings.web_remote_enabled:
            return RedirectResponse("/", status_code=303)
        client = request.client.host if request.client else "unknown"
        now = datetime.now(ZoneInfo("America/Guatemala"))
        cutoff = now - timedelta(minutes=15)
        attempts = login_attempts[client]
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= 5:
            raise HTTPException(429, "Demasiados intentos. Espera 15 minutos.")
        if not verify_password(password, settings.web_password_hash or ""):
            attempts.append(now)
            raise HTTPException(401, "Contraseña incorrecta.")
        attempts.clear()
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            create_session(settings.web_session_secret or ""),
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=2_592_000,
            path="/",
        )
        return response

    @app.post("/api/logout", include_in_schema=False)
    async def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")
        return response

    @app.get("/", include_in_schema=False)
    async def home() -> FileResponse:
        return FileResponse(str(static_dir.joinpath("index.html")))

    @app.get("/api/state")
    async def state() -> dict[str, object]:
        today = datetime.now(ZoneInfo("America/Guatemala")).date()
        async with database.session() as session:
            repository = Repository(session)
            messages = await repository.recent_messages(
                settings.telegram_allowed_user_id, settings.history_limit
            )
            tokens = await repository.usage_tokens(settings.telegram_allowed_user_id, today)
            voice_seconds, tts_characters = await repository.voice_usage(
                settings.telegram_allowed_user_id, today
            )
        return {
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "usage": {
                "tokens": tokens,
                "token_limit": (
                    settings.daily_token_limit if settings.daily_token_limit_enabled else None
                ),
                "voice_seconds": voice_seconds,
                "voice_seconds_limit": settings.daily_voice_seconds_limit,
                "tts_characters": tts_characters,
                "tts_character_limit": settings.daily_tts_character_limit,
            },
            "voice_enabled": voice is not None,
            "spotify_enabled": spotify is not None,
            "spotify_connected": bool(spotify and spotify.connected()),
        }

    @app.get("/api/memories")
    async def memories() -> dict[str, object]:
        async with database.session() as session:
            repository = Repository(session)
            explicit = await repository.memories(settings.telegram_allowed_user_id)
            automatic = await repository.personal_memories(settings.telegram_allowed_user_id)
        return {
            "memories": [
                {
                    "id": f"explicit:{item.id}",
                    "content": item.content,
                    "category": "explicit",
                    "source": "explicit",
                    "created_at": item.created_at.isoformat(),
                }
                for item in explicit
            ]
            + [
                {
                    "id": f"automatic:{item.id}",
                    "content": item.content,
                    "category": item.category,
                    "source": item.source,
                    "created_at": item.created_at.isoformat(),
                }
                for item in automatic
            ]
        }

    @app.delete("/api/memories", status_code=204)
    async def clear_memories() -> None:
        async with database.session() as session:
            await Repository(session).clear_memories(settings.telegram_allowed_user_id)

    @app.delete("/api/memories/{memory_id}", status_code=204)
    async def delete_memory(memory_id: str) -> None:
        try:
            source, raw_id = memory_id.split(":", 1)
            item_id = int(raw_id)
        except ValueError:
            raise HTTPException(404, "Recuerdo no encontrado.") from None
        async with database.session() as session:
            repository = Repository(session)
            deleted = (
                await repository.delete_memory(settings.telegram_allowed_user_id, item_id)
                if source == "explicit"
                else await repository.delete_personal_memory(
                    settings.telegram_allowed_user_id, item_id
                )
                if source == "automatic"
                else False
            )
        if not deleted:
            raise HTTPException(404, "Recuerdo no encontrado.")

    @app.get("/api/reminders")
    async def reminders() -> dict[str, object]:
        async with database.session() as session:
            items = await Repository(session).upcoming_reminders(settings.telegram_allowed_user_id)
        return {
            "reminders": [
                {
                    "id": item.id,
                    "content": item.content,
                    "due_at": item.due_at.isoformat(),
                    "timezone": item.timezone,
                }
                for item in items
            ]
        }

    @app.delete("/api/reminders/{reminder_id}", status_code=204)
    async def cancel_reminder(reminder_id: int) -> None:
        async with database.session() as session:
            cancelled = await Repository(session).cancel_reminder(
                settings.telegram_allowed_user_id, reminder_id
            )
        if not cancelled:
            raise HTTPException(404, "Recordatorio no encontrado.")

    @app.get("/api/spotify/connect")
    async def spotify_connect() -> RedirectResponse:
        if spotify is None:
            raise HTTPException(409, "Spotify no está configurado.")
        return RedirectResponse(spotify.authorization_url())

    @app.get("/api/spotify/callback")
    async def spotify_callback(code: str, state: str) -> RedirectResponse:
        if spotify is None:
            raise HTTPException(409, "Spotify no está configurado.")
        try:
            await spotify.complete(code, state)
        except (ValueError, httpx.HTTPError):
            raise HTTPException(400, "No pude completar la autorización de Spotify.") from None
        return RedirectResponse("/?spotify=connected", status_code=303)

    def local_code_worker(request: Request) -> ClaudeCodeWorker:
        if request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
            raise HTTPException(403, "La programación solo está disponible desde esta PC.")
        if code_worker is None:
            raise HTTPException(503, "Claude Code no está configurado.")
        return code_worker

    async def save_chat_exchange(message: str, answer: str) -> None:
        async with database.session() as session:
            repository = Repository(session)
            await repository.add_message(settings.telegram_allowed_user_id, "user", message)
            await repository.add_message(settings.telegram_allowed_user_id, "assistant", answer)
            await repository.compact_messages(
                settings.telegram_allowed_user_id, settings.history_limit
            )

    @app.get("/api/code/workspaces")
    async def code_workspaces(request: Request) -> dict[str, object]:
        worker = local_code_worker(request)
        return {"workspaces": worker.public_workspaces()}

    @app.post("/api/code/plan", response_model=CodePlanResponse)
    async def code_plan(request: Request, payload: CodeTaskRequest) -> CodePlanResponse:
        worker = local_code_worker(request)
        try:
            return CodePlanResponse(plan=await worker.plan(payload.workspace_id, payload.task))
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except Exception as error:
            logger.warning("Claude Code plan failed (%s)", type(error).__name__)
            raise HTTPException(503, "Claude Code no pudo preparar el plan.") from None

    @app.post("/api/code/execute", response_model=CodeExecutionResponse)
    async def code_execute(request: Request, payload: CodeTaskRequest) -> CodeExecutionResponse:
        worker = local_code_worker(request)
        try:
            result = await worker.execute(payload.workspace_id, payload.task, payload.commit)
            return CodeExecutionResponse(**result)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except Exception as error:
            logger.warning("Claude Code execution failed (%s)", type(error).__name__)
            raise HTTPException(
                409, "Claude Code no pudo ejecutar la tarea de forma segura."
            ) from None
    @app.get("/api/code/status")
    async def code_status() -> dict[str, object]:
        return code_tasks.snapshot()

    @app.post("/api/chat/intent")
    async def chat_intent(payload: ChatRequest) -> dict[str, object]:
        context = code_context.current()
        validation = (
            detect_validation_request(payload.message, context) if code_worker is not None else None
        )
        request = (
            detect_code_request(payload.message, context)
            if code_worker is not None and validation is None
            else None
        )
        read_only = request is not None and code_request_is_read_only(request[1])
        workspace_id = validation or (request[0] if request is not None else None)
        label = None
        if workspace_id is not None and code_worker is not None:
            label = next(
                (
                    item["label"]
                    for item in code_worker.public_workspaces()
                    if item["id"] == workspace_id
                ),
                workspace_id,
            )
        return {
            "coding": request is not None or validation is not None,
            "read_only": read_only or validation is not None,
            "acknowledge": validation is not None or (request is not None and not read_only),
            "workspace": label,
            "validating": validation is not None,
        }

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        message = payload.message.strip()
        if request.headers.get("x-gwen-capabilities") == CAPABILITY:
            require_calendar_session(request)
            try:
                async with database.session() as session:
                    planned = await plan_calendar(
                        message,
                        request.headers.get("x-gwen-timezone", ""),
                        assistant,
                        Repository(session),
                        settings.telegram_allowed_user_id,
                    )
                if planned is not None:
                    return ChatResponse(answer=planned[0], actions=planned[1])
            except Exception as error:
                raise safe_error(error) from None
        spotify_history = bool(spotify and wants_history(message))
        music_request = detect_spotify_request(message) if spotify else None
        missing_favorites = False
        if music_request is not None and music_request.needs_favorite:
            async with database.session() as session:
                memories = await Repository(session).memories(settings.telegram_allowed_user_id)
            artist = favorite_artist([memory.content for memory in memories])
            if artist:
                music_request = replace(music_request, query=artist, needs_favorite=False)
            else:
                music_request, missing_favorites = None, True
        spotify_action = music_request.action if music_request else None
        spotify_query = music_request.query if music_request else ""
        context = code_context.current()
        validation_request = (
            detect_validation_request(message, context) if code_worker is not None else None
        )
        code_request = (
            detect_code_request(message, context)
            if code_worker is not None and validation_request is None
            else None
        )
        adopt_request = detect_adopt_request(message, context) if code_worker is not None else None
        try:
            if spotify_action:
                try:
                    result = await spotify.control(spotify_action, spotify_query)  # type: ignore[union-attr]
                except (ValueError, httpx.HTTPStatusError) as error:
                    answer = spotify_failure(error)
                    await save_chat_exchange(message, answer)
                    return ChatResponse(answer=answer)
                answer = spotify_answer(spotify_action, result)
                await save_chat_exchange(message, answer)
                return ChatResponse(answer=answer)
            if missing_favorites:
                await save_chat_exchange(message, NO_FAVORITES)
                return ChatResponse(answer=NO_FAVORITES)
            if spotify_history:
                tracks = await spotify.recently_played()  # type: ignore[union-attr]
                answer = (
                    "Escuchaste recientemente: " + "; ".join(tracks)
                    if tracks
                    else "No encontré reproducciones recientes en Spotify."
                )
                await save_chat_exchange(message, answer)
                return ChatResponse(answer=answer)
            if code_worker is not None and adopt_request is not None:
                await code_worker.adopt_current_changes(adopt_request)
                code_context.remember(adopt_request, context.last_failures if context else ())
                answer = (
                    f"Listo, tomé los cambios actuales de {adopt_request} como punto de "
                    "partida. Ya puedo seguir trabajando ahí."
                )
                await save_chat_exchange(message, answer)
                return ChatResponse(answer=answer)
            if code_worker is not None and validation_request is not None:
                code_tasks.start(validation_request, "validar")
                code_tasks.update("validando")
                result = await code_worker.validate(validation_request)
                code_context.remember(validation_request, failed_checks(result))
                answer = validation_result_answer(result)
                code_tasks.update(
                    "terminada" if result["validation_passed"] else "fallida",
                    checks=result["checks"],
                )
                await save_chat_exchange(message, answer)
                return ChatResponse(answer=answer)
            if code_worker is not None and code_request is not None:
                workspace_id, task = code_request
                if code_request_is_read_only(task):
                    code_tasks.start(workspace_id, "revisar")
                    code_tasks.update("trabajando")
                    answer = await code_worker.inspect(workspace_id, task)
                    code_context.remember(workspace_id, context.last_failures if context else ())
                    answer = f"Sí, puedo revisar {workspace_id}. {answer}"
                    code_tasks.update("terminada")
                    await save_chat_exchange(message, answer)
                    return ChatResponse(answer=answer)
                wants_commit = code_commit_requested(task)
                is_commit_only = wants_commit and len(task.split()) <= 8
                code_tasks.start(workspace_id, "commit" if is_commit_only else "editar")
                code_tasks.update("trabajando")
                if is_commit_only:
                    code_tasks.update("validando")
                    result = await code_worker.commit(workspace_id)
                    answer = commit_result_answer(result)
                else:
                    result = await code_worker.execute(
                        workspace_id,
                        code_followup_task(task, context),
                        commit=wants_commit,
                        diagnose=code_task_needs_diagnosis(task),
                    )
                    answer = code_result_answer(result)
                code_context.remember(workspace_id, failed_checks(result))
                code_tasks.update(
                    "terminada" if result["validation_passed"] else "fallida",
                    checks=result["checks"],
                    files=result.get("files"),
                    commit=result.get("commit"),
                )
                await save_chat_exchange(message, answer)
                return ChatResponse(answer=answer)
            async with database.session() as session:
                answer = await assistant.reply(
                    settings.telegram_allowed_user_id, message, Repository(session)
                )
            return ChatResponse(answer=answer)
        except WorkspaceHasForeignChanges as error:
            answer = error.message()
            code_tasks.update("fallida", error=answer)
            await save_chat_exchange(message, answer)
            return ChatResponse(answer=answer)
        except CodeTaskTimeout as error:
            answer = str(error)
            code_tasks.update("fallida", error=answer)
            await save_chat_exchange(message, answer)
            return ChatResponse(answer=answer)
        except Exception as error:
            if code_request is not None or validation_request is not None:
                logger.warning("Text code request failed (%s)", type(error).__name__)
                code_tasks.update("fallida", error=type(error).__name__)
                raise HTTPException(
                    409,
                    "No pude completar el cambio de forma segura. "
                    "Revisa el registro de Gwen para ver el detalle.",
                ) from None
            raise safe_error(error) from None

    @app.post("/api/new", status_code=204)
    async def new_conversation() -> None:
        async with database.session() as session:
            await Repository(session).clear_messages(settings.telegram_allowed_user_id)

    @app.websocket("/ws/voice")
    async def realtime_voice(websocket: WebSocket) -> None:
        if voice is None:
            await websocket.close(code=1008, reason="La voz no está configurada.")
            return
        try:
            voice_code_worker = code_worker
            await run_realtime_voice(
                websocket,
                settings,
                database,
                assistant,
                voice,
                voice_code_worker,
                code_context,
                spotify,
            )
        except WebSocketDisconnect:
            pass
        except Exception as error:
            logger.warning("Realtime voice connection failed (%s)", type(error).__name__)
            try:
                await websocket.close(code=1011, reason="La voz no está disponible.")
            except RuntimeError:
                pass

    @app.post("/api/voice", response_model=ChatResponse)
    async def voice_chat(
        request: Request,
        audio: Annotated[UploadFile, File()],
        duration: Annotated[int, Form(ge=0, le=600)] = 0,
    ) -> ChatResponse:
        calendar_enabled = request.headers.get("x-gwen-capabilities") == CAPABILITY
        if calendar_enabled:
            require_calendar_session(request)
        if voice is None:
            raise HTTPException(409, "La voz no está configurada.")
        content = await audio.read(MAX_AUDIO_BYTES + 1)
        if not content or len(content) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "El audio está vacío o es demasiado grande.")
        today = datetime.now(ZoneInfo("America/Guatemala")).date()
        try:
            async with database.session() as session:
                repository = Repository(session)
                voice_seconds, tts_characters = await repository.voice_usage(
                    settings.telegram_allowed_user_id, today
                )
                if voice_seconds + duration > settings.daily_voice_seconds_limit:
                    raise HTTPException(429, "Llegamos al límite diario de transcripción.")
                transcript = await voice.transcribe(
                    content,
                    audio.filename or "voice.webm",
                    normalized_audio_type(audio.content_type),
                )
                if not meaningful_transcript(transcript):
                    raise HTTPException(
                        422,
                        "No detecté palabras claras. Acércate al micrófono y vuelve a intentarlo.",
                    )
                await repository.add_usage(
                    settings.telegram_allowed_user_id, today, voice_seconds=duration
                )
                planned = (
                    await plan_calendar(
                        transcript,
                        request.headers.get("x-gwen-timezone", ""),
                        assistant,
                        repository,
                        settings.telegram_allowed_user_id,
                    )
                    if calendar_enabled
                    else None
                )
                actions = planned[1] if planned is not None else None
                answer = (
                    planned[0]
                    if planned is not None
                    else await assistant.reply(
                        settings.telegram_allowed_user_id, transcript, repository
                    )
                )
                if tts_characters + len(answer) > settings.daily_tts_character_limit:
                    return ChatResponse(answer=answer, transcript=transcript, actions=actions)
                spoken = await voice.synthesize(answer)
                await repository.add_usage(
                    settings.telegram_allowed_user_id,
                    today,
                    tts_characters=len(answer),
                )
            return ChatResponse(
                answer=answer,
                transcript=transcript,
                audio=base64.b64encode(spoken).decode("ascii"),
                audio_type="audio/mpeg",
                actions=actions,
            )
        except HTTPException:
            raise
        except httpx.HTTPError as error:
            logger.warning("Web voice provider failed (%s)", type(error).__name__)
            raise HTTPException(503, "La voz no está disponible ahora mismo.") from None
        except Exception as error:
            raise safe_error(error) from None

    def require_calendar_session(request: Request) -> None:
        if not settings.web_remote_enabled:
            raise HTTPException(403, "Calendario requiere acceso privado HTTPS configurado.")
        if not verify_session(request.cookies.get(COOKIE_NAME), settings.web_session_secret):
            raise HTTPException(401, "Inicia sesión para usar Calendario.")
        if request.headers.get("origin", "").rstrip("/") != settings.web_public_origin.rstrip("/"):
            raise HTTPException(403, "Origen no permitido.")

    @app.post("/api/calendar/result", response_model=ChatResponse)
    async def calendar_result(payload: CalendarResult, request: Request) -> ChatResponse:
        require_calendar_session(request)
        answer = result_message(payload)
        response = ChatResponse(answer=answer)
        async with database.session() as session:
            repository = Repository(session)
            await repository.add_message(settings.telegram_allowed_user_id, "assistant", answer)
            await repository.compact_messages(
                settings.telegram_allowed_user_id, settings.history_limit
            )
            if payload.speak and voice is not None:
                today = datetime.now(ZoneInfo("America/Guatemala")).date()
                _, used = await repository.voice_usage(settings.telegram_allowed_user_id, today)
                if used + len(answer) <= settings.daily_tts_character_limit:
                    try:
                        spoken = await voice.synthesize(answer)
                        await repository.add_usage(
                            settings.telegram_allowed_user_id, today, tts_characters=len(answer)
                        )
                        response.audio = base64.b64encode(spoken).decode("ascii")
                        response.audio_type = "audio/mpeg"
                    except httpx.HTTPError:
                        pass  # The save result remains valid even when speech is unavailable.
        return response

    return app


def run() -> None:
    configure_logging()
    uvicorn.run(create_app(), host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    run()
