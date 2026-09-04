import logging
from io import BytesIO

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from gwen.assistant import GwenAssistant
from gwen.database import Database
from gwen.repository import Repository
from gwen.voice import ElevenLabsVoice

logger = logging.getLogger(__name__)


class GwenBot:
    def __init__(
        self,
        token: str,
        allowed_user_id: int,
        database: Database,
        assistant: GwenAssistant,
        voice: ElevenLabsVoice | None,
    ) -> None:
        self.allowed_user_id = allowed_user_id
        self.database = database
        self.assistant = assistant
        self.voice = voice
        self.application = Application.builder().token(token).build()
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("remember", self.remember))
        self.application.add_handler(CommandHandler("memories", self.memories))
        self.application.add_handler(CommandHandler("forget", self.forget))
        self.application.add_handler(CommandHandler("privacy", self.privacy))
        self.application.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text)
        )
        self.application.add_error_handler(self.on_error)

    def authorized(self, update: Update) -> bool:
        return bool(update.effective_user and update.effective_user.id == self.allowed_user_id)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.authorized(update) and update.message:
            await update.message.reply_text("Soy Gwen. Lista cuando tú lo estés.")

    async def remember(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update) or not update.message:
            return
        content = " ".join(context.args).strip()
        if not content:
            await update.message.reply_text("Dime qué recordar: /remember <dato>")
            return
        async with self.database.session() as session:
            await Repository(session).add_memory(self.allowed_user_id, content)
        await update.message.reply_text("Lo recordaré.")

    async def memories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update) or not update.message:
            return
        async with self.database.session() as session:
            items = await Repository(session).memories(self.allowed_user_id)
        text = (
            "\n".join(f"{item.id}. {item.content}" for item in items) or "No guardo recuerdos aún."
        )
        await update.message.reply_text(text)

    async def forget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update) or not update.message:
            return
        text = " ".join(context.args).strip()
        if not text:
            await update.message.reply_text("Indica qué olvidar: /forget <texto>")
            return
        async with self.database.session() as session:
            count = await Repository(session).forget_matching(self.allowed_user_id, text)
        await update.message.reply_text(f"Eliminé {count} recuerdo(s) coincidente(s).")

    async def privacy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.authorized(update) and update.message:
            await update.message.reply_text(
                "Guardo el historial reciente y lo que me pides recordar. No guardes aquí "
                "contraseñas, tokens, claves API ni datos bancarios completos."
            )

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update) or not update.message or not update.message.text:
            return
        async with self.database.session() as session:
            answer = await self.assistant.reply(
                self.allowed_user_id, update.message.text, Repository(session)
            )
        await update.message.reply_text(answer)

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update) or not update.message or not update.message.voice:
            return
        if not self.voice:
            await update.message.reply_text("La voz todavía no está configurada.")
            return
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        audio = bytes(await voice_file.download_as_bytearray())
        transcript = await self.voice.transcribe(audio)
        async with self.database.session() as session:
            answer = await self.assistant.reply(
                self.allowed_user_id, transcript, Repository(session)
            )
        try:
            audio_answer = await self.voice.synthesize(answer)
        except httpx.HTTPStatusError as error:
            logger.warning("Voice synthesis failed with status %s", error.response.status_code)
            await update.message.reply_text(answer)
            if error.response.status_code == 402:
                await update.message.reply_text(
                    "Te escuché bien, pero ElevenLabs requiere saldo o un plan con acceso "
                    "a esta voz. Mientras tanto responderé por texto."
                )
            return
        output = BytesIO(audio_answer)
        output.name = "gwen.mp3"
        await update.message.reply_voice(voice=output)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception("Unhandled Telegram update", exc_info=context.error)

    def run(self) -> None:
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)
