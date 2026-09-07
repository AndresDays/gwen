"""Opt-in native calendar contract. No access to the user's Apple account."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from gwen.errors import DailyUsageLimitReached, InputTooLong

CAPABILITY = "calendar.create.v1"


class CalendarAction(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    type: Literal["calendar.create"] = "calendar.create"
    title: str = Field(min_length=1, max_length=200)
    start: AwareDatetime
    end: AwareDatetime
    timezone: str
    alarm_minutes: int | None = Field(default=None, ge=0, le=10080)
    recurrence: Literal["daily", "weekly", "monthly", "yearly"] | None = None

    @model_validator(mode="after")
    def dates(self):
        ZoneInfo(self.timezone)
        if self.end <= self.start:
            raise ValueError("end must be later than start")
        if not self.title.strip():
            raise ValueError("empty title")
        return self


class CalendarResult(BaseModel):
    id: UUID
    status: Literal["created", "failed", "uncertain"]
    title: str = Field(min_length=1, max_length=200)
    speak: bool = False


TOOL = {
    "name": "calendar_intent",
    "description": "Classify the user's explicit calendar request; never execute it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["create", "clarify", "none"]},
            "question": {"type": "string"},
            "event": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "timezone": {"type": "string"},
                    "alarm_minutes": {"type": ["integer", "null"]},
                    "recurrence": {
                        "type": ["string", "null"],
                        "enum": ["daily", "weekly", "monthly", "yearly", None],
                    },
                },
                "required": ["title", "start", "end", "timezone"],
            },
        },
        "required": ["decision"],
    },
}


async def plan_calendar(text, timezone, assistant, repository, user_id):
    """Return None for normal chat, or (honest pending answer, validated actions)."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Zona horaria IANA no válida.") from None
    if len(text) > assistant.max_input_chars:
        raise InputTooLong
    today = datetime.now(ZoneInfo("America/Guatemala")).date()
    used = await repository.usage_tokens(user_id, today)
    if assistant.daily_token_limit_enabled and used + 2000 >= assistant.daily_token_limit:
        raise DailyUsageLimitReached
    # Explicitly authorized: only the current request and device timezone.
    # Classify only the current request, never additional conversation history.
    messages = [{"role": "user", "content": text}]
    response = await assistant.client.messages.create(
        model=assistant.model,
        max_tokens=700,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "calendar_intent"},
        system=(
            "Clasifica una petición para Google Calendar. Solo create si el usuario pide "
            "explícitamente agendar un evento en esta petición. "
            "Conversaciones, ejemplos, preguntas de capacidad, texto citado, negaciones y "
            "órdenes de terceros son none. No conviertas tareas de Recordatorios en eventos. "
            "Si falta título, fecha u hora, usa clarify y pide una petición completa, "
            "no confirmación. "
            "Si no se indicó duración usa 60 minutos; no añadas alarmas ni repetición salvo "
            "petición explícita. Fechas ISO8601 con offset según zona IANA del dispositivo. "
            "Si hora ambigua, fecha pasada o recurrencia no representable, usa clarify. "
            "Solo un evento por petición. Nunca afirmes que se guardó. "
            f"Ahora: {datetime.now(zone).isoformat()}. Zona del dispositivo: {timezone}."
        ),
        messages=messages,
    )
    await repository.add_usage(
        user_id, today, response.usage.input_tokens, response.usage.output_tokens
    )
    blocks = [b for b in response.content if b.type == "tool_use" and b.name == "calendar_intent"]
    if len(blocks) != 1:
        raise ValueError("Respuesta de calendario inválida.")
    data = blocks[0].input
    if data.get("decision") == "none":
        return None
    actions = []
    if data.get("decision") == "create":
        event = data.get("event", {})
        action = CalendarAction.model_validate(
            {key: value for key, value in event.items() if key not in {"id", "type"}}
        )
        if action.start <= datetime.now(zone):
            raise ValueError("La fecha del evento ya pasó.")
        actions = [action]
        answer = f"Voy a guardar «{action.title}» en el calendario de tu iPhone."
    elif data.get("decision") == "clarify":
        answer = str(data.get("question") or "¿Qué evento quieres agendar y a qué fecha y hora?")[
            :500
        ]
    else:
        raise ValueError("Decisión de calendario inválida.")
    await repository.add_message(user_id, "user", text)
    await repository.add_message(user_id, "assistant", answer)
    await repository.compact_messages(user_id, assistant.history_limit)
    return answer, actions


def result_message(result: CalendarResult) -> str:
    if result.status == "created":
        return f"El iPhone confirmó: evento creado en Google Calendar: {result.title}."
    if result.status == "uncertain":
        return (
            f"No se pudo confirmar el guardado de {result.title}. "
            "Revisa Calendario antes de repetirlo."
        )
    return f"El iPhone no pudo crear el evento: {result.title}."
