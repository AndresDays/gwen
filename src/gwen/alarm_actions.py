from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, Field, model_validator

CAPABILITY = "alarm.create.v1"


class AlarmAction(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    type: Literal["alarm.create"] = "alarm.create"
    title: str = Field(min_length=1, max_length=120)
    fire_at: AwareDatetime
    timezone: str

    @model_validator(mode="after")
    def valid(self):
        ZoneInfo(self.timezone)
        if self.fire_at <= datetime.now(ZoneInfo(self.timezone)):
            raise ValueError("alarm must be future")
        return self


TOOL = {
    "name": "alarm_intent",
    "description": "Create an iPhone app alarm only for an explicit user request.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["create", "clarify", "none"]},
            "question": {"type": "string"},
            "alarm": {"type": "object"},
        },
        "required": ["decision"],
    },
}


async def plan_alarm(text, timezone, assistant, repository, user_id):
    try:
        zone = ZoneInfo(timezone)
    except Exception as error:
        raise ValueError("Zona horaria IANA no válida.") from error
    response = await assistant.client.messages.create(
        model=assistant.model, max_tokens=400, tools=[TOOL],
        tool_choice={"type": "tool", "name": "alarm_intent"},
        system=("Clasifica solo peticiones explícitas para poner una alarma nativa de Gwen. "
                "No uses Calendar ni Recordatorios. Si falta fecha u hora, clarify. "
                "Devuelve hora ISO8601 con offset de la zona del iPhone. "
                f"Ahora: {datetime.now(zone).isoformat()}; zona: {timezone}."),
        messages=[{"role": "user", "content": text}],
    )
    blocks = [b for b in response.content if b.type == "tool_use" and b.name == "alarm_intent"]
    if len(blocks) != 1:
        raise ValueError("Respuesta de alarma inválida.")
    data = blocks[0].input
    if data.get("decision") == "none":
        return None
    if data.get("decision") == "clarify":
        return str(data.get("question") or "¿A qué fecha y hora quieres la alarma?"), []
    action = AlarmAction.model_validate(data.get("alarm", {}))
    return f"Voy a poner la alarma «{action.title}» en tu iPhone.", [action]
