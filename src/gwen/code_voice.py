import re
import unicodedata
from dataclasses import dataclass


@dataclass
class PendingCodeTask:
    workspace_id: str
    task: str
    plan: str


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.lower())
    return "".join(character for character in value if not unicodedata.combining(character))


def detect_code_request(text: str) -> tuple[str, str] | None:
    value = normalized(text)
    projects = {
        "california": (
            r"\b(proyecto|codigo|repositorio)\s+(de\s+)?california\b",
            r"\b(trabaja|metete|entra)\s+(en|a)\s+(el\s+proyecto\s+)?california\b",
        ),
        "gwen": (
            r"\b(proyecto|codigo|repositorio)\s+(de\s+)?gwen\b",
            r"\b(trabaja|metete|entra)\s+(en|a)\s+(el\s+proyecto\s+)?gwen\b",
        ),
    }
    verbs = (
        r"\b(edita|modifica|cambia|corrige|implementa|programa|arregla|agrega|anade|"
        r"trabaja|ver|revisa|revisar|mira|analiza|analizar|inspecciona|lee)\b"
    )
    if not re.search(verbs, value):
        return None
    for workspace_id, patterns in projects.items():
        if any(re.search(pattern, value) for pattern in patterns):
            return workspace_id, text.strip()
    return None


def code_request_is_read_only(text: str) -> bool:
    value = normalized(text)
    editing = r"\b(edita|modifica|cambia|corrige|implementa|programa|arregla|agrega|anade)\b"
    reading = r"\b(ver|revisa|revisar|mira|analiza|analizar|inspecciona|lee)\b"
    return bool(re.search(reading, value)) and not re.search(editing, value)


def code_confirmation(text: str) -> tuple[bool, bool] | None:
    value = normalized(text)
    if re.search(r"\b(no|cancela|cancelar|detente)\b", value):
        return False, False
    confirmed = bool(re.search(r"\b(si|hazlo|ejecuta|adelante|confirmo)\b", value))
    if not confirmed:
        return None
    commit = bool(re.search(r"\bcommit|confirma los cambios|guarda el commit\b", value))
    return True, commit
