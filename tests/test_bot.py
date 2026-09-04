from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from gwen.bot import GwenBot
from gwen.database import Database


@pytest.mark.asyncio
async def test_voice_402_falls_back_to_text() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    request = httpx.Request("POST", "https://example.invalid/tts")
    response = httpx.Response(402, request=request)
    error = httpx.HTTPStatusError("payment required", request=request, response=response)

    voice = SimpleNamespace(
        transcribe=AsyncMock(return_value="hola"),
        synthesize=AsyncMock(side_effect=error),
    )
    assistant = SimpleNamespace(reply=AsyncMock(return_value="Hola, aquí estoy."))
    message = SimpleNamespace(
        voice=SimpleNamespace(file_id="voice-file", duration=5),
        reply_text=AsyncMock(),
        reply_voice=AsyncMock(),
    )
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=message)
    voice_file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(b"audio")))
    context = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock(return_value=voice_file)))

    bot = GwenBot.__new__(GwenBot)
    bot.allowed_user_id = 42
    bot.database = database
    bot.assistant = assistant
    bot.voice = voice
    bot.daily_voice_seconds_limit = 900
    bot.daily_tts_character_limit = 20_000

    await bot.handle_voice(update, context)

    assert message.reply_text.await_count == 2
    message.reply_text.assert_any_await("Hola, aquí estoy.")
    assert "ElevenLabs requiere saldo" in message.reply_text.await_args_list[1].args[0]
    message.reply_voice.assert_not_awaited()
    await database.close()
