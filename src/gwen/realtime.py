import asyncio
import base64
import json
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import WebSocket
from websockets.asyncio.client import connect

from gwen.assistant import GwenAssistant
from gwen.calendar_actions import CAPABILITY, plan_calendar
from gwen.code_voice import (
    CodeContextStore,
    code_commit_requested,
    code_followup_task,
    code_request_is_read_only,
    code_task_needs_diagnosis,
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
from gwen.config import Settings
from gwen.database import Database
from gwen.errors import DailyUsageLimitReached, InputTooLong, ProviderUnavailable
from gwen.music import (
    NO_FAVORITES,
    detect_spotify_request,
    favorite_artist,
    spotify_answer,
    spotify_failure,
    wants_history,
)
from gwen.repository import Repository
from gwen.spotify import SpotifyOAuth
from gwen.voice import ElevenLabsVoice

logger = logging.getLogger(__name__)
_STT_URL = (
    "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
    "?model_id=scribe_v2_realtime&audio_format=pcm_16000&commit_strategy=manual"
)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def workspace_label(code_worker: ClaudeCodeWorker, workspace_id: str) -> str:
    return next(
        (item["label"] for item in code_worker.public_workspaces() if item["id"] == workspace_id),
        workspace_id,
    )


def split_speech(buffer: str, force: bool = False) -> tuple[list[str], str]:
    parts = _SENTENCE_END.split(buffer)
    ready = parts[:-1]
    remainder = parts[-1]
    if len(remainder) >= 110:
        split_at = remainder.rfind(" ", 65, 111)
        if split_at > 0:
            ready.append(remainder[:split_at])
            remainder = remainder[split_at + 1 :]
    if force and remainder.strip():
        ready.append(remainder)
        remainder = ""
    return [part.strip() for part in ready if part.strip()], remainder


async def run_realtime_voice(
    websocket: WebSocket,
    settings: Settings,
    database: Database,
    assistant: GwenAssistant,
    voice: ElevenLabsVoice,
    code_worker: ClaudeCodeWorker | None = None,
    code_context: CodeContextStore | None = None,
    spotify: SpotifyOAuth | None = None,
) -> None:
    code_context = code_context if code_context is not None else CodeContextStore()
    await websocket.accept()
    send_lock = asyncio.Lock()
    processing: asyncio.Task[None] | None = None
    generation = 0

    async def send(kind: str, **payload: object) -> None:
        async with send_lock:
            await websocket.send_json({"type": kind, **payload})

    async def music_answer(transcript: str) -> str | None:
        """Atiende por voz las mismas peticiones de Spotify que el chat de texto."""
        if spotify is None:
            return None
        if wants_history(transcript):
            try:
                tracks = await spotify.recently_played()
            except (ValueError, httpx.HTTPStatusError) as error:
                return spotify_failure(error)
            return (
                "Escuchaste recientemente: " + "; ".join(tracks)
                if tracks
                else "No encontré reproducciones recientes en Spotify."
            )
        request = detect_spotify_request(transcript)
        if request is None:
            return None
        if request.needs_favorite:
            async with database.session() as session:
                memories = await Repository(session).memories(settings.telegram_allowed_user_id)
            artist = favorite_artist([memory.content for memory in memories])
            if not artist:
                return NO_FAVORITES
            request = replace(request, query=artist, needs_favorite=False)
        try:
            result = await spotify.control(request.action, request.query)
        except (ValueError, httpx.HTTPStatusError) as error:
            return spotify_failure(error)
        return spotify_answer(request.action, result)

    async def process_turn(transcript: str, turn: int) -> None:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        muted = False

        async def speak() -> None:
            """Locuta la respuesta; si la voz falla, el turno sigue solo con texto."""
            nonlocal muted
            while True:
                segment = await queue.get()
                if segment is None:
                    return
                if muted:
                    continue
                today = datetime.now(ZoneInfo("America/Guatemala")).date()
                async with database.session() as usage_session:
                    usage_repository = Repository(usage_session)
                    _, used_characters = await usage_repository.voice_usage(
                        settings.telegram_allowed_user_id, today
                    )
                    if used_characters + len(segment) > settings.daily_tts_character_limit:
                        muted = True
                        continue
                try:
                    spoken = await voice.synthesize(segment)
                except httpx.HTTPError as error:
                    muted = True
                    logger.warning("TTS unavailable (%s)", type(error).__name__)
                    await send(
                        "voice_muted",
                        generation=turn,
                        message="Me quedé sin voz; te sigo respondiendo por texto.",
                    )
                    continue
                async with database.session() as usage_session:
                    await Repository(usage_session).add_usage(
                        settings.telegram_allowed_user_id,
                        today,
                        tts_characters=len(segment),
                    )
                await send(
                    "audio",
                    generation=turn,
                    audio=base64.b64encode(spoken).decode("ascii"),
                    audio_type="audio/mpeg",
                )

        speaker = asyncio.create_task(speak())
        buffer = ""
        try:
            await send("transcript", generation=turn, text=transcript)
            if (
                settings.web_remote_enabled
                and websocket.headers.get("x-gwen-capabilities") == CAPABILITY
            ):
                async with database.session() as session:
                    planned = await plan_calendar(
                        transcript,
                        websocket.headers.get("x-gwen-timezone", ""),
                        assistant,
                        Repository(session),
                        settings.telegram_allowed_user_id,
                    )
                if planned is not None:
                    await send("answer_delta", generation=turn, text=planned[0])
                    await queue.put(planned[0])
                    await queue.put(None)
                    await speaker
                    await send(
                        "calendar_actions",
                        generation=turn,
                        actions=[action.model_dump(mode="json") for action in planned[1]],
                    )
                    await send("answer_done", generation=turn)
                    await send("turn_done", generation=turn)
                    return
            code_answer: str | None = await music_answer(transcript)
            if code_answer is None and code_worker is not None:
                context = code_context.current()
                validation_request = detect_validation_request(transcript, context)
                request = (
                    detect_code_request(transcript, context) if validation_request is None else None
                )
                if validation_request is not None:
                    acknowledgement = "Sí, reviso las pruebas."
                    await send("answer_delta", generation=turn, text=acknowledgement + " ")
                    await queue.put(acknowledgement)
                    await send(
                        "code_progress",
                        generation=turn,
                        workspace=workspace_label(code_worker, validation_request),
                        validating=True,
                    )
                    result = await code_worker.validate(validation_request)
                    code_context.remember(validation_request, failed_checks(result))
                    code_answer = validation_result_answer(result)
                elif request is not None:
                    workspace_id, task = request
                    if code_request_is_read_only(task):
                        answer = await code_worker.inspect(workspace_id, task)
                        code_context.remember(
                            workspace_id, context.last_failures if context else ()
                        )
                        code_answer = f"Sí, puedo revisar {workspace_id}. {answer}"
                    else:
                        acknowledgement = (
                            "Sí, ya lo hago. Puede tardar varios minutos "
                            "porque también corro las pruebas."
                        )
                        await send("answer_delta", generation=turn, text=acknowledgement + " ")
                        await queue.put(acknowledgement)
                        await send(
                            "code_progress",
                            generation=turn,
                            workspace=workspace_label(code_worker, workspace_id),
                            validating=False,
                        )
                        try:
                            if code_commit_requested(task) and len(task.split()) <= 8:
                                result = await code_worker.commit(workspace_id)
                                code_answer = commit_result_answer(result)
                            else:
                                result = await code_worker.execute(
                                    workspace_id,
                                    code_followup_task(task, context),
                                    commit=code_commit_requested(task),
                                    diagnose=code_task_needs_diagnosis(task),
                                )
                                code_answer = code_result_answer(result)
                        except WorkspaceHasForeignChanges as conflict:
                            code_answer = conflict.message()
                        except CodeTaskTimeout as expired:
                            code_answer = str(expired)
                        else:
                            code_context.remember(workspace_id, failed_checks(result))
            if code_answer is not None:
                async with database.session() as history_session:
                    history = Repository(history_session)
                    await history.add_message(settings.telegram_allowed_user_id, "user", transcript)
                    await history.add_message(
                        settings.telegram_allowed_user_id, "assistant", code_answer
                    )
                    await history.compact_messages(
                        settings.telegram_allowed_user_id, settings.history_limit
                    )
                await send("answer_delta", generation=turn, text=code_answer)
                buffer = code_answer
                ready, buffer = split_speech(buffer)
                for segment in ready:
                    await queue.put(segment)
            else:
                async with database.session() as session:
                    repository = Repository(session)
                    async for delta in assistant.stream_reply(
                        settings.telegram_allowed_user_id, transcript, repository
                    ):
                        await send("answer_delta", generation=turn, text=delta)
                        buffer += delta
                        ready, buffer = split_speech(buffer)
                        for segment in ready:
                            await queue.put(segment)
            ready, _ = split_speech(buffer, force=True)
            for segment in ready:
                await queue.put(segment)
            await send("answer_done", generation=turn)
            await queue.put(None)
            await speaker
            await send("turn_done", generation=turn)
        except asyncio.CancelledError:
            speaker.cancel()
            await asyncio.gather(speaker, return_exceptions=True)
            raise
        except (
            httpx.HTTPError,
            ProviderUnavailable,
            DailyUsageLimitReached,
            InputTooLong,
        ) as error:
            speaker.cancel()
            await asyncio.gather(speaker, return_exceptions=True)
            if isinstance(error, DailyUsageLimitReached):
                message = "Llegamos al límite diario de Claude configurado."
            elif isinstance(error, InputTooLong):
                message = "Ese mensaje es demasiado largo."
            elif isinstance(error, ProviderUnavailable):
                message = "Claude no está disponible ahora mismo."
            else:
                message = "La voz no está disponible ahora mismo."
            await send("error", generation=turn, code=type(error).__name__, message=message)
        except Exception as error:
            speaker.cancel()
            await asyncio.gather(speaker, return_exceptions=True)
            logger.warning("Realtime voice turn failed (%s)", type(error).__name__)
            if isinstance(error, DailyUsageLimitReached):
                message = "Llegamos al límite diario de Claude configurado."
            elif isinstance(error, InputTooLong):
                message = "Ese mensaje es demasiado largo."
            elif isinstance(error, ProviderUnavailable):
                message = "Claude no está disponible ahora mismo."
            else:
                message = "La voz no está disponible ahora mismo."
            await send("error", generation=turn, code=type(error).__name__, message=message)

    headers = {"xi-api-key": voice.api_key}
    stack = AsyncExitStack()
    try:
        stt = await stack.enter_async_context(
            connect(_STT_URL, additional_headers=headers, open_timeout=10)
        )
    except Exception as error:
        # Sin dictado no hay micrófono: avisamos y cerramos para que el cliente no reintente.
        logger.warning("Realtime STT unavailable (%s)", type(error).__name__)
        await send(
            "error",
            fatal=True,
            code=type(error).__name__,
            message=(
                "El dictado de ElevenLabs no está disponible; "
                "revisa los créditos de la cuenta."
            ),
        )
        await websocket.close()
        return
    async with stack:
        await send("ready")

        async def from_provider() -> None:
            nonlocal processing, generation
            async for raw in stt:
                event = json.loads(raw)
                event_type = event.get("message_type")
                text = str(event.get("text", "")).strip()
                if event_type == "partial_transcript":
                    await send("partial", text=text)
                elif (
                    event_type == "committed_transcript"
                    and text
                    and any(character.isalnum() for character in text)
                ):
                    generation += 1
                    if processing and not processing.done():
                        processing.cancel()
                        await asyncio.gather(processing, return_exceptions=True)
                    processing = asyncio.create_task(process_turn(text, generation))

        provider_task = asyncio.create_task(from_provider())
        try:
            while True:
                event = await websocket.receive_json()
                kind = event.get("type")
                if kind == "audio":
                    await stt.send(
                        json.dumps(
                            {
                                "message_type": "input_audio_chunk",
                                "audio_base_64": event.get("audio", ""),
                                "sample_rate": 16000,
                            }
                        )
                    )
                elif kind == "commit":
                    duration = max(0, min(600, int(event.get("duration", 0))))
                    today = datetime.now(ZoneInfo("America/Guatemala")).date()
                    async with database.session() as session:
                        repository = Repository(session)
                        used, _ = await repository.voice_usage(
                            settings.telegram_allowed_user_id, today
                        )
                        if used + duration > settings.daily_voice_seconds_limit:
                            await send("error", message="Llegamos al límite diario de voz.")
                            continue
                        await repository.add_usage(
                            settings.telegram_allowed_user_id, today, voice_seconds=duration
                        )
                    await stt.send(
                        json.dumps(
                            {
                                "message_type": "input_audio_chunk",
                                "audio_base_64": "",
                                "sample_rate": 16000,
                                "commit": True,
                            }
                        )
                    )
                elif kind == "interrupt":
                    if processing and not processing.done():
                        processing.cancel()
                        await asyncio.gather(processing, return_exceptions=True)
                    await send("interrupted", generation=generation)
                elif kind == "close":
                    break
        finally:
            provider_task.cancel()
            if processing:
                processing.cancel()
            tasks = [provider_task]
            if processing is not None:
                tasks.append(processing)
            await asyncio.gather(*tasks, return_exceptions=True)
