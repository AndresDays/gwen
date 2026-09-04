import asyncio
import logging

from gwen.assistant import GwenAssistant
from gwen.bot import GwenBot
from gwen.config import get_settings
from gwen.database import Database
from gwen.voice import ElevenLabsVoice


def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    database = Database(settings.database_url)
    asyncio.run(database.initialize())
    assistant = GwenAssistant(
        settings.anthropic_api_key, settings.anthropic_model, settings.history_limit
    )
    voice = None
    if settings.voice_enabled:
        voice = ElevenLabsVoice(
            settings.elevenlabs_api_key or "",
            settings.elevenlabs_voice_id or "",
            settings.elevenlabs_tts_model,
            settings.elevenlabs_stt_model,
        )
    GwenBot(
        settings.telegram_bot_token,
        settings.telegram_allowed_user_id,
        database,
        assistant,
        voice,
    ).run()
