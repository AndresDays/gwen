import asyncio
import logging
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from gwen.assistant import GwenAssistant
from gwen.database import Database
from gwen.errors import DailyUsageLimitReached, InputTooLong, ProviderUnavailable
from gwen.memory import is_safe_memory
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
        daily_voice_seconds_limit: int = 900,
        daily_tts_character_limit: int = 20_000,
    ) -> None:
        self.allowed_user_id = allowed_user_id
        self.database = database
        self.assistant = assistant
        self.voice = voice
        self.daily_voice_seconds_limit = daily_voice_seconds_limit
        self.daily_tts_character_limit = daily_tts_character_limit
        self.reminder_task: asyncio.Task[None] | None = None
        self.application = (
            Application.builder()
            .token(token)
            .post_init(self.start_reminder_delivery)
            .post_shutdown(self.stop_reminder_delivery)
            .build()
        )
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("remember", self.remember))
        self.application.add_handler(CommandHandler("memories", self.memories))
        self.application.add_handler(CommandHandler("forget", self.forget))
        self.application.add_handler(CommandHandler("privacy", self.privacy))
        self.application.add_handler(CommandHandler("new", self.new_conversation))
        self.application.add_handler(CommandHandler("usage", self.usage))
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
        if not is_safe_memory(content):
            await update.message.reply_text(
                "Eso parece información sensible, así que no la guardaré."
            )
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

    async def new_conversation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update) or not update.message:
            return
        async with self.database.session() as session:
            await Repository(session).clear_messages(self.allowed_user_id)
        await update.message.reply_text(
            "Conversación nueva. Borré el contexto reciente, pero conservé tus recuerdos."
        )

    async def usage(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update) or not update.message:
            return
        today = datetime.now(ZoneInfo("America/Guatemala")).date()
        async with self.database.session() as session:
            used = await Repository(session).usage_tokens(self.allowed_user_id, today)
        if self.assistant.daily_token_limit_enabled:
            limit = self.assistant.daily_token_limit
            message = f"Uso de hoy: {used:,} de {limit:,} tokens."
        else:
            message = f"Uso de hoy: {used:,} tokens. Límite diario desactivado."
        await update.message.reply_text(message)

    async def deliver_due_reminders(self) -> None:
        now = datetime.now(ZoneInfo("America/Guatemala"))
        async with self.database.session() as session:
            reminders = await Repository(session).claim_due_reminders(self.allowed_user_id, now)
        for reminder in reminders:
            due = reminder.due_at.astimezone(ZoneInfo(reminder.timezone))
            await self.application.bot.send_message(
                chat_id=self.allowed_user_id,
                text=f"Recordatorio: {reminder.content}\nProgramado para {due:%Y-%m-%d %H:%M}.",
            )

    async def reminder_loop(self) -> None:
        while True:
            try:
                await self.deliver_due_reminders()
            except Exception as error:
                logger.warning("Reminder delivery failed (%s)", type(error).__name__)
            await asyncio.sleep(60)

    async def start_reminder_delivery(self, application: Application) -> None:
        self.reminder_task = asyncio.create_task(self.reminder_loop())

    async def stop_reminder_delivery(self, application: Application) -> None:
        if self.reminder_task:
            self.reminder_task.cancel()
            try:
                await self.reminder_task
            except asyncio.CancelledError:
                pass
            self.reminder_task = None

    async def assistant_answer(self, text: str, repository: Repository) -> str:
        try:
            return await self.assistant.reply(self.allowed_user_id, text, repository)
        except InputTooLong:
            return "Ese mensaje es demasiado largo. Divídelo en partes más pequeñas."
        except DailyUsageLimitReached:
            return "Llegamos al límite diario configurado. Podremos continuar mañana."
        except ProviderUnavailable:
            return "Claude no está disponible ahora mismo. Intenta de nuevo en unos minutos."

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
        today = datetime.now(ZoneInfo("America/Guatemala")).date()
        async with self.database.session() as session:
            voice_seconds, tts_characters = await Repository(session).voice_usage(
                self.allowed_user_id, today
            )
        duration = update.message.voice.duration or 0
        if voice_seconds + duration > self.daily_voice_seconds_limit:
            await update.message.reply_text(
                "Llegamos al límite diario de transcripción de voz. Puedes seguir por texto."
            )
            return
        try:
            voice_file = await context.bot.get_file(update.message.voice.file_id)
            audio = bytes(await voice_file.download_as_bytearray())
            transcript = await self.voice.transcribe(audio)
            async with self.database.session() as session:
                await Repository(session).add_usage(
                    self.allowed_user_id, today, voice_seconds=duration
                )
        except Exception as error:
            logger.warning("Voice transcription failed (%s)", type(error).__name__)
            await update.message.reply_text(
                "No pude procesar ese audio. Intenta otra vez o escríbeme el mensaje."
            )
            return
        async with self.database.session() as session:
            answer = await self.assistant.reply(
                self.allowed_user_id, transcript, Repository(session)
            )
        if tts_characters + len(answer) > self.daily_tts_character_limit:
            await update.message.reply_text(answer)
            await update.message.reply_text(
                "Llegamos al límite diario de voz; por hoy responderé por texto."
            )
            return
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
        except httpx.HTTPError as error:
            logger.warning("Voice synthesis failed (%s)", type(error).__name__)
            await update.message.reply_text(answer)
            await update.message.reply_text(
                "No pude generar el audio esta vez, así que respondí por texto."
            )
            return
        async with self.database.session() as session:
            await Repository(session).add_usage(
                self.allowed_user_id, today, tts_characters=len(answer)
            )
        output = BytesIO(audio_answer)
        output.name = "gwen.mp3"
        await update.message.reply_voice(voice=output)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        error_name = type(context.error).__name__ if context.error else "UnknownError"
        logger.error("Unhandled Telegram update (%s)", error_name)

    def run(self) -> None:
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)
