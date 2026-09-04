import asyncio
import base64
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import WebSocket
from websockets.asyncio.client import connect

from gwen.assistant import GwenAssistant
from gwen.code_voice import (
    PendingCodeTask,
    code_confirmation,
    code_request_is_read_only,
    detect_code_request,
)
from gwen.code_worker import ClaudeCodeWorker
from gwen.config import Settings
from gwen.database import Database
from gwen.errors import DailyUsageLimitReached, InputTooLong, ProviderUnavailable
from gwen.repository import Repository
from gwen.voice import ElevenLabsVoice

logger = logging.getLogger(__name__)
_STT_URL = (
    "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
    "?model_id=scribe_v2_realtime&audio_format=pcm_16000&commit_strategy=manual"
)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


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
) -> None:
    await websocket.accept()
    send_lock = asyncio.Lock()
    processing: asyncio.Task[None] | None = None
    generation = 0
    pending_code: PendingCodeTask | None = None

    async def send(kind: str, **payload: object) -> None:
        async with send_lock:
            await websocket.send_json({"type": kind, **payload})

    async def process_turn(transcript: str, turn: int) -> None:
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def speak() -> None:
            while True:
                segment = await queue.get()
                if segment is None:
                    return
                today = datetime.now(ZoneInfo("America/Guatemala")).date()
                async with database.session() as usage_session:
                    usage_repository = Repository(usage_session)
                    _, used_characters = await usage_repository.voice_usage(
                        settings.telegram_allowed_user_id, today
                    )
                    if used_characters + len(segment) > settings.daily_tts_character_limit:
                        return
                spoken = await voice.synthesize(segment)
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
            code_answer: str | None = None
            if code_worker is not None and pending_code is not None:
                confirmation = code_confirmation(transcript)
                if confirmation is None:
                    code_answer = (
                        "Tengo un plan pendiente. Di sí, ejecútalo; sí, y haz commit; o cancela."
                    )
                elif not confirmation[0]:
                    pending_code = None
                    code_answer = "Entendido. Cancelé la tarea de programación."
                else:
                    task = pending_code
                    pending_code = None
                    acknowledgement = "Sí, ya lo hago."
                    await send("answer_delta", generation=turn, text=acknowledgement + " ")
                    await queue.put(acknowledgement)
                    result = await code_worker.execute(
                        task.workspace_id, task.task, commit=confirmation[1]
                    )
                    code_answer = (
                        "Listo, ya está hecho y también creé el commit."
                        if result["committed"]
                        else "Listo, ya está hecho. Las pruebas pasaron correctamente."
                    )
            elif code_worker is not None:
                request = detect_code_request(transcript)
                if request is not None:
                    workspace_id, task = request
                    if code_request_is_read_only(task):
                        answer = await code_worker.inspect(workspace_id, task)
                        code_answer = f"Sí, puedo revisar {workspace_id}. {answer}"
                    else:
                        plan = await code_worker.plan(workspace_id, task)
                        pending_code = PendingCodeTask(workspace_id, task, plan)
                        project = "California" if workspace_id == "california" else "Gwen"
                        code_answer = (
                            f"Sí, ya revisé lo necesario en {project} y puedo hacerlo. "
                            "¿Confirmas que lo ejecute? También puedes decir: "
                            "sí, y haz commit."
                        )
            if code_answer is not None:
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
    async with connect(_STT_URL, additional_headers=headers, open_timeout=10) as stt:
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
