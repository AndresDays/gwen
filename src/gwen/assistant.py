from datetime import datetime
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic

from gwen.repository import Repository

SYSTEM_PROMPT = """Eres Gwen, la asistente personal privada del usuario.
Hablas español e inglés según el idioma del usuario. Eres directa, inteligente,
cálida, proactiva, usas humor muy seguido y eres bromista. Responde de forma natural y
concisa. No afirmes que ejecutaste acciones que no puedes ejecutar.

Los recuerdos incluidos abajo son datos proporcionados explícitamente por el
usuario. Úsalos cuando sean relevantes. Nunca solicites ni memorices contraseñas,
claves API, tokens o datos bancarios completos. Si el usuario pide recordar algo,
indícale que puede usar /remember; no inventes recuerdos.
"""


def current_context() -> str:
    now = datetime.now(ZoneInfo("America/Guatemala"))
    return (
        "Contexto temporal confiable:\n"
        f"- Fecha y hora actuales: {now:%Y-%m-%d %H:%M:%S}\n"
        "- Zona horaria: America/Guatemala\n"
        "Usa estos datos para calcular edades y fechas relativas. No adivines el año actual."
    )


class GwenAssistant:
    def __init__(self, api_key: str, model: str, history_limit: int) -> None:
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.history_limit = history_limit

    async def reply(self, user_id: int, text: str, repository: Repository) -> str:
        memories = await repository.memories(user_id)
        history = await repository.recent_messages(user_id, self.history_limit)
        memory_text = "\n".join(f"- {item.content}" for item in memories) or "- Ninguno"
        messages = [{"role": item.role, "content": item.content} for item in history]
        messages.append({"role": "user", "content": text})
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=900,
            system=(f"{SYSTEM_PROMPT}\n\n{current_context()}\n\nRecuerdos:\n{memory_text}"),
            messages=messages,  # type: ignore[arg-type]
        )
        answer = "".join(block.text for block in response.content if block.type == "text").strip()
        await repository.add_message(user_id, "user", text)
        await repository.add_message(user_id, "assistant", answer)
        return answer
