import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    RateLimitError,
)

from gwen.errors import DailyUsageLimitReached, InputTooLong, ProviderUnavailable
from gwen.memory import automatic_preference_request, explicit_memory_request
from gwen.reminders import complete_reminder_time, in_reminder_timezone, parse_reminder_request
from gwen.repository import Repository

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Eres Gwen, la asistente personal privada del usuario.
Habla como una persona cercana, inteligente y espontánea. Adapta el idioma, el tono y
la longitud de la respuesta al usuario. La conversación debe sentirse natural, no como
atención al cliente, un manual ni un chatbot genérico.

En conversaciones casuales, usa frases breves y naturales. No repitas la pregunta ni
expliques lo obvio. Evita títulos, listas, resúmenes y conclusiones formales salvo que
realmente ayuden. No empieces automáticamente con frases prefabricadas como "¡Claro!",
"con gusto" o "estoy aquí para ayudarte". Haz preguntas de seguimiento solo cuando
surjan naturalmente o falte información necesaria.

Puedes tener opiniones ligeras, reaccionar con calidez y usar humor moderado cuando
encaje, pero nunca lo fuerces. Cuando el usuario pida ayuda técnica o una explicación,
sé clara y organizada sin perder una voz personal. No finjas experiencias, emociones
humanas ni acciones que no ejecutaste.

Nunca afirmes haber editado archivos, corrido pruebas, ejecutado comandos o creado
commits. Tú no tienes acceso al código: las tareas de programación las ejecuta Claude
Code por una ruta aparte y sus resultados llegan al usuario tal cual. Si el usuario pide
un cambio de código y este mensaje llegó hasta ti, significa que no se ejecutó nada. En
ese caso dilo con claridad y pídele que nombre el proyecto ("en el proyecto california,
corrige X"). Nunca inventes resultados de pruebas, números de suites ni diffs.

Lo mismo aplica a Spotify: tú no controlas la reproducción. Poner, pausar, saltar
canciones o cambiar el volumen ocurre en una ruta aparte que nunca pasa por ti. Si un
pedido de música llegó hasta ti, es que no se ejecutó: dilo directamente y sugiere
repetirlo de forma más explícita ("pon <canción> de <artista>"). Nunca digas "listo, la
pausé", "ya la puse" ni nombres una canción como si estuviera sonando.

Los recuerdos incluidos abajo son datos proporcionados explícitamente por el
usuario. Úsalos cuando sean relevantes. Nunca solicites ni memorices contraseñas,
claves API, tokens o datos bancarios completos. Si el usuario pide recordar algo,
indícale que puede usar /remember; no inventes recuerdos.
"""


def current_context(clock: Callable[[], datetime] | None = None) -> str:
    now = (clock or (lambda: datetime.now(ZoneInfo("America/Guatemala"))))()
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo("America/Guatemala"))
    return (
        "Contexto temporal confiable:\n"
        f"- Fecha y hora actuales: {now:%Y-%m-%d %H:%M:%S}\n"
        "- Zona horaria: America/Guatemala\n"
        "Usa estos datos para calcular edades y fechas relativas. No adivines el año actual."
    )


class GwenAssistant:
    def __init__(
        self,
        api_key: str,
        model: str,
        history_limit: int,
        max_input_chars: int = 12_000,
        daily_token_limit: int = 100_000,
        daily_token_limit_enabled: bool = True,
    ) -> None:
        self.client = AsyncAnthropic(api_key=api_key, max_retries=2, timeout=60.0)
        self.model = model
        self.history_limit = history_limit
        self.max_input_chars = max_input_chars
        self.daily_token_limit = daily_token_limit
        self.daily_token_limit_enabled = daily_token_limit_enabled

    async def save_shortcut_turn(
        self, user_id: int, text: str, answer: str, repository: Repository
    ) -> str:
        await repository.add_message(user_id, "user", text)
        await repository.add_message(user_id, "assistant", answer)
        return answer

    async def pending_reminder_request(
        self, user_id: int, text: str, repository: Repository
    ):
        history = await repository.recent_messages(user_id, 2)
        if len(history) != 2 or history[-1].role != "assistant" or history[-2].role != "user":
            return None
        if history[-1].content != "¿A qué hora exacta quieres que te lo recuerde?":
            return None
        return complete_reminder_time(history[-2].content, text)

    async def reply(self, user_id: int, text: str, repository: Repository) -> str:
        if len(text) > self.max_input_chars:
            raise InputTooLong

        memory_request = explicit_memory_request(text)
        if memory_request:
            if not memory_request.safe:
                return await self.save_shortcut_turn(user_id, text, (
                    "Eso parece información sensible, así que no la guardaré. "
                    "Mejor mantén contraseñas, tokens y datos bancarios fuera de Gwen."
                ), repository)
            await repository.add_memory(user_id, memory_request.content)
            return await self.save_shortcut_turn(user_id, text, "Lo recordaré.", repository)

        reminder_request = parse_reminder_request(text) or await self.pending_reminder_request(
            user_id, text, repository
        )
        if reminder_request:
            if reminder_request.needs_clarification:
                return await self.save_shortcut_turn(
                    user_id, text, "¿A qué hora exacta quieres que te lo recuerde?", repository
                )
            reminder = await repository.add_reminder(
                user_id,
                reminder_request.content,
                reminder_request.due_at,
                reminder_request.timezone,
            )
            local_due = in_reminder_timezone(reminder.due_at, reminder.timezone)
            return await self.save_shortcut_turn(
                user_id,
                text,
                f"Listo. Te recordaré {reminder.content} el {local_due:%Y-%m-%d a las %H:%M}.",
                repository,
            )

        zone = ZoneInfo("America/Guatemala")
        today = datetime.now(zone).date()
        used_tokens = await repository.usage_tokens(user_id, today)
        if self.daily_token_limit_enabled and used_tokens >= self.daily_token_limit:
            raise DailyUsageLimitReached

        memories = await repository.memories(user_id)
        automatic_preferences = await repository.personal_memories(user_id)
        summary = await repository.conversation_summary(user_id)
        history = await repository.recent_messages(user_id, self.history_limit)
        memory_text = "\n".join(
            f"- {item.content}" for item in [*memories, *automatic_preferences]
        ) or "- Ninguno"
        summary_text = summary or "Sin resumen anterior."
        messages = [{"role": item.role, "content": item.content} for item in history]
        messages.append({"role": "user", "content": text})
        system = (
            f"{SYSTEM_PROMPT}\n\n{current_context()}\n\nRecuerdos:\n{memory_text}"
            f"\n\nContexto anterior compactado:\n{summary_text}"
        )
        estimated_tokens = (len(system) + sum(len(item["content"]) for item in messages)) // 4
        max_output_tokens = 900
        if self.daily_token_limit_enabled:
            available_output_tokens = self.daily_token_limit - used_tokens - estimated_tokens
            if available_output_tokens < 128:
                raise DailyUsageLimitReached
            max_output_tokens = min(max_output_tokens, available_output_tokens)

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=max_output_tokens,
                system=system,
                messages=messages,  # type: ignore[arg-type]
            )
        except (APIConnectionError, APITimeoutError, RateLimitError) as error:
            logger.warning("Anthropic request failed (%s)", type(error).__name__)
            raise ProviderUnavailable from error
        except APIStatusError as error:
            logger.warning(
                "Anthropic request failed (%s status=%s)",
                type(error).__name__,
                error.status_code,
            )
            raise ProviderUnavailable from error

        answer = "".join(block.text for block in response.content if block.type == "text").strip()
        await repository.add_message(user_id, "user", text)
        await repository.add_message(user_id, "assistant", answer)
        await repository.add_usage(
            user_id,
            today,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        if preference := automatic_preference_request(text):
            await repository.add_personal_memory(user_id, preference, "preference", "automatic")
        await repository.compact_messages(user_id, self.history_limit)
        return answer

    async def stream_reply(
        self, user_id: int, text: str, repository: Repository
    ) -> AsyncIterator[str]:
        """Stream a reply and save it only after it completes."""
        if len(text) > self.max_input_chars:
            raise InputTooLong

        memory_request = explicit_memory_request(text)
        if memory_request or parse_reminder_request(text):
            yield await self.reply(user_id, text, repository)
            return

        zone = ZoneInfo("America/Guatemala")
        today = datetime.now(zone).date()
        used_tokens = await repository.usage_tokens(user_id, today)
        if self.daily_token_limit_enabled and used_tokens >= self.daily_token_limit:
            raise DailyUsageLimitReached

        memories = await repository.memories(user_id)
        automatic_preferences = await repository.personal_memories(user_id)
        summary = await repository.conversation_summary(user_id)
        history = await repository.recent_messages(user_id, self.history_limit)
        memory_text = "\n".join(
            f"- {item.content}" for item in [*memories, *automatic_preferences]
        ) or "- Ninguno"
        summary_text = summary or "Sin resumen anterior."
        messages = [{"role": item.role, "content": item.content} for item in history]
        messages.append({"role": "user", "content": text})
        system = (
            f"{SYSTEM_PROMPT}\n\n{current_context()}\n\nRecuerdos:\n{memory_text}"
            f"\n\nContexto anterior compactado:\n{summary_text}"
        )
        estimated_tokens = (len(system) + sum(len(item["content"]) for item in messages)) // 4
        max_output_tokens = 900
        if self.daily_token_limit_enabled:
            available_output_tokens = self.daily_token_limit - used_tokens - estimated_tokens
            if available_output_tokens < 128:
                raise DailyUsageLimitReached
            max_output_tokens = min(max_output_tokens, available_output_tokens)

        chunks: list[str] = []
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=max_output_tokens,
                system=system,
                messages=messages,  # type: ignore[arg-type]
            ) as stream:
                async for chunk in stream.text_stream:
                    chunks.append(chunk)
                    yield chunk
                response = await stream.get_final_message()
        except (APIConnectionError, APITimeoutError, RateLimitError) as error:
            logger.warning("Anthropic stream failed (%s)", type(error).__name__)
            raise ProviderUnavailable from error
        except APIStatusError as error:
            logger.warning(
                "Anthropic stream failed (%s status=%s)",
                type(error).__name__,
                error.status_code,
            )
            raise ProviderUnavailable from error

        answer = "".join(chunks).strip()
        await repository.add_message(user_id, "user", text)
        await repository.add_message(user_id, "assistant", answer)
        await repository.add_usage(
            user_id,
            today,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        if preference := automatic_preference_request(text):
            await repository.add_personal_memory(user_id, preference, "preference", "automatic")
        await repository.compact_messages(user_id, self.history_limit)
