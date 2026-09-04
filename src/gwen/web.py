import base64
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.resources import files
from typing import Annotated
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gwen.assistant import GwenAssistant
from gwen.config import Settings, get_settings
from gwen.database import Database
from gwen.errors import DailyUsageLimitReached, InputTooLong, ProviderUnavailable
from gwen.main import configure_logging
from gwen.realtime import run_realtime_voice
from gwen.repository import Repository
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
) -> FastAPI:
    settings = settings or get_settings()
    database = database or Database(settings.database_url)
    assistant = assistant or GwenAssistant(
        settings.anthropic_api_key,
        settings.anthropic_model,
        settings.history_limit,
        settings.max_input_chars,
        settings.daily_token_limit,
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
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    static_dir = files("gwen").joinpath("web_static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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
                "token_limit": settings.daily_token_limit,
                "voice_seconds": voice_seconds,
                "voice_seconds_limit": settings.daily_voice_seconds_limit,
                "tts_characters": tts_characters,
                "tts_character_limit": settings.daily_tts_character_limit,
            },
            "voice_enabled": voice is not None,
        }

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest) -> ChatResponse:
        try:
            async with database.session() as session:
                answer = await assistant.reply(
                    settings.telegram_allowed_user_id,
                    payload.message.strip(),
                    Repository(session),
                )
            return ChatResponse(answer=answer)
        except Exception as error:
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
            await run_realtime_voice(websocket, settings, database, assistant, voice)
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
        audio: Annotated[UploadFile, File()],
        duration: Annotated[int, Form(ge=0, le=600)] = 0,
    ) -> ChatResponse:
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
                answer = await assistant.reply(
                    settings.telegram_allowed_user_id, transcript, repository
                )
                if tts_characters + len(answer) > settings.daily_tts_character_limit:
                    return ChatResponse(answer=answer)
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
            )
        except HTTPException:
            raise
        except httpx.HTTPError as error:
            logger.warning("Web voice provider failed (%s)", type(error).__name__)
            raise HTTPException(503, "La voz no está disponible ahora mismo.") from None
        except Exception as error:
            raise safe_error(error) from None

    return app


def run() -> None:
    configure_logging()
    uvicorn.run(create_app(), host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    run()
