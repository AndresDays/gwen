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
from gwen.memory import explicit_memory_request
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
    ) -> None:
        self.client = AsyncAnthropic(api_key=api_key, max_retries=2, timeout=60.0)
        self.model = model
        self.history_limit = history_limit
        self.max_input_chars = max_input_chars
        self.daily_token_limit = daily_token_limit

    async def reply(self, user_id: int, text: str, repository: Repository) -> str:
        if len(text) > self.max_input_chars:
            raise InputTooLong

        memory_request = explicit_memory_request(text)
        if memory_request:
            if not memory_request.safe:
                return (
                    "Eso parece información sensible, así que no la guardaré. "
                    "Mejor mantén contraseñas, tokens y datos bancarios fuera de Gwen."
                )
            await repository.add_memory(user_id, memory_request.content)
            return "Lo recordaré."

        zone = ZoneInfo("America/Guatemala")
        today = datetime.now(zone).date()
        used_tokens = await repository.usage_tokens(user_id, today)
        if used_tokens >= self.daily_token_limit:
            raise DailyUsageLimitReached

        memories = await repository.memories(user_id)
        summary = await repository.conversation_summary(user_id)
        history = await repository.recent_messages(user_id, self.history_limit)
        memory_text = "\n".join(f"- {item.content}" for item in memories) or "- Ninguno"
        summary_text = summary or "Sin resumen anterior."
        messages = [{"role": item.role, "content": item.content} for item in history]
        messages.append({"role": "user", "content": text})
        system = (
            f"{SYSTEM_PROMPT}\n\n{current_context()}\n\nRecuerdos:\n{memory_text}"
            f"\n\nContexto anterior compactado:\n{summary_text}"
        )
        estimated_tokens = (len(system) + sum(len(item["content"]) for item in messages)) // 4
        if used_tokens + estimated_tokens + 900 > self.daily_token_limit:
            raise DailyUsageLimitReached

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=900,
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
        await repository.compact_messages(user_id, self.history_limit)
        return answer

    async def stream_reply(
        self, user_id: int, text: str, repository: Repository
    ) -> AsyncIterator[str]:
        """Stream a reply and save it only after it completes."""
        if len(text) > self.max_input_chars:
            raise InputTooLong

        memory_request = explicit_memory_request(text)
        if memory_request:
            yield await self.reply(user_id, text, repository)
            return

        zone = ZoneInfo("America/Guatemala")
        today = datetime.now(zone).date()
        used_tokens = await repository.usage_tokens(user_id, today)
        if used_tokens >= self.daily_token_limit:
            raise DailyUsageLimitReached

        memories = await repository.memories(user_id)
        summary = await repository.conversation_summary(user_id)
        history = await repository.recent_messages(user_id, self.history_limit)
        memory_text = "\n".join(f"- {item.content}" for item in memories) or "- Ninguno"
        summary_text = summary or "Sin resumen anterior."
        messages = [{"role": item.role, "content": item.content} for item in history]
        messages.append({"role": "user", "content": text})
        system = (
            f"{SYSTEM_PROMPT}\n\n{current_context()}\n\nRecuerdos:\n{memory_text}"
            f"\n\nContexto anterior compactado:\n{summary_text}"
        )
        estimated_tokens = (len(system) + sum(len(item["content"]) for item in messages)) // 4
        if used_tokens + estimated_tokens + 900 > self.daily_token_limit:
            raise DailyUsageLimitReached

        chunks: list[str] = []
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=900,
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
        await repository.compact_messages(user_id, self.history_limit)
